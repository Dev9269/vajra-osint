"""Entity resolution for VAJRA OSINT aggregator.

Pairwise fuzzy matching of distinct entity names using rapidfuzz
token_set_ratio (handles extra middle names/initials) on transliteration-
normalised tokens, with O(n) blocking instead of O(n^2) full cross product.

Matches >= 85 stored in entity_links with a stored match_status
('auto_confirmed' / 'needs_review').

CHANGELOG 2026-09-06 (bugfix round, #3/#4):
  - A STRICT SUBSET match ("Pradeep Singh" vs "Pradeep Singh Rajput") is no
    longer auto-confirmed. token_set_ratio returns 100 for those even though
    the people may be different (a common name + a rare full name). We detect
    the subset relation and cap the score just below REVIEW_THRESHOLD and
    store match_status='needs_review'. Only near-identical names (small
    edit distance, not pure subset) can reach auto_confirmed.
  - Entity links now persist the decision as match_status instead of relying
    on later score re-interpretation.

CHANGELOG 2026-09-08 (resolution round, Part 2):
  - Corroboration rule: auto-confirm requires score >= 95 AND at least one
    shared verifiable identifier (FIR number, plate, phone, location)
    between the two entities. A high string score alone is never enough —
    reviewers confirm the rest.
  - Blocking: candidates come from first/last-token blocks (plus single-
    letter initial blocks), turning the O(n^2) combinations() scan into a
    near-linear one. Pairs are de-duplicated by unordered pair.
  - Transliteration table: common Latin-script Indian-name variant spellings
    normalise to one canonical token before matching
    (e.g. Mohammed/Muhammad/Mohd, Chaudhary/Chowdhury, Sharma/Sarma).
  - Initials handling: a single-letter token (e.g. "V." in "Rakesh V.") is
    expanded against full tokens of the other name ("Verma") so abbreviated
    names still reach review / corroborated auto-confirm instead of being
    silently dropped below MATCH_THRESHOLD.
"""

import logging
from collections import defaultdict
from pathlib import Path

from rapidfuzz import fuzz

from storage import (
    entity_identifier_sets,
    get_connection,
    get_distinct_entity_names,
    insert_entity_link,
)

logger = logging.getLogger(__name__)

MATCH_THRESHOLD = 85
REVIEW_THRESHOLD = 95

# token_set_ratio returns 100 for any exact token-set equality, including a
# strict subset (missing a middle/first name). Rare full names sharing a common
# first+last pair are usually DIFFERENT people, so a strict subset can never
# be auto-confirmed. Subset scores are pinned to just under REVIEW_THRESHOLD.
_SUBSET_PIN = REVIEW_THRESHOLD - 0.5

# Latin-script variant spellings of common Indian names, mapped to a single
# canonical token before matching. Additive — variants are rare enough that a
# wrong normalisation is harmless: auto-confirm still requires corroboration.
TRANSLITERATION = {
    # Muhammad group
    "mohammed": "muhammad",
    "mohammad": "muhammad",
    "mohamed": "muhammad",
    "muhammed": "muhammad",
    "mohd": "muhammad",
    # Chaudhary group
    "choudhary": "chaudhary",
    "chowdhury": "chaudhary",
    "chowdhary": "chaudhary",
    "choudhury": "chaudhary",
    "chaudhari": "chaudhary",
    # Sharma group
    "sarma": "sharma",
    # Mishra group
    "misra": "mishra",
    # Prasad group
    "prasaad": "prasad",
    # Das group
    "dass": "das",
    "dhas": "das",
}


def _tokens(name: str) -> list[str]:
    """Lowercase, split on whitespace/slash, strip '.' -> token list."""
    toks = []
    for part in name.lower().split():
        for tok in part.strip(".").split("/"):
            tok = tok.strip().strip(".")
            if tok:
                toks.append(TRANSLITERATION.get(tok, tok))
    return toks


def _initials_expand(tokens: list[str], reference: list[str]) -> list[str]:
    """Expand single-letter tokens against full tokens of the other name.

    "v" expands to ["v", "verma"] when "verma" is in the other name, so
    "Rakesh V." and "Rakesh Verma" share tokens and reach review instead of
    being dropped below the threshold.
    """
    out = []
    for tok in tokens:
        out.append(tok)
        if len(tok) == 1:
            matches = [r for r in reference if r.startswith(tok)]
            out.extend(matches)
    return out


def _is_strict_subset(set_a: set, set_b: set) -> bool:
    """True if one token set is a strict subset of the other."""
    if not set_a or not set_b:
        return False
    if set_a == set_b:
        return False
    return set_a < set_b or set_b < set_a


def classify_score(name_a: str, name_b: str, raw_score: float, corroborated: bool = False):
    """Return (effective_score, match_status) for a pair of names.

    Auto-confirm requires BOTH: raw_score >= REVIEW_THRESHOLD on the
    transliteration/initials-normalised tokens AND shared corroboration.
    Subset pairs are always pinned below the threshold.
    """
    tokens_a = _tokens(name_a)
    tokens_b = _tokens(name_b)
    set_a = set(_initials_expand(tokens_a, tokens_b))
    set_b = set(_initials_expand(tokens_b, tokens_a))

    if _is_strict_subset(set_a, set_b):
        effective = min(raw_score, _SUBSET_PIN)
        status = "needs_review"
    elif raw_score >= REVIEW_THRESHOLD and corroborated:
        effective = raw_score
        status = "auto_confirmed"
    else:
        effective = raw_score
        status = "needs_review"
    return effective, status


def _block_candidates(names: list[str]):
    """Yield (name, candidate) pairs from token blocks, de-duplicated.

    Block keys: full first token, full last token, and — only when a name
    starts/ends with a single-letter initial — that letter. Candidates for a
    name are every other name sharing a first/last block. Unordered pairs
    are emitted once via a seen-set.
    """
    names = list(names)
    if len(names) < 2:
        return

    # token tuple per name (full first token, full last token, first letter-or-None, last letter-or-None)
    info = {}
    for name in names:
        toks = _tokens(name)
        if not toks:
            continue
        first, last = toks[0], toks[-1]
        info[name] = (first, last, first if len(first) == 1 else None, last if len(last) == 1 else None)

    blocks = defaultdict(list)
    for name, (first, last, f_init, l_init) in info.items():
        blocks[("first", first)].append(name)
        blocks[("last", last)].append(name)
        if f_init:
            blocks[("first", f_init)].append(name)
        if l_init:
            blocks[("last", l_init)].append(name)

    seen = set()
    for name, (first, last, f_init, l_init) in info.items():
        candidates = set()
        for tok in (first, last, f_init, l_init):
            if tok is None:
                continue
            candidates.update(blocks.get(("first", tok), ()))
            candidates.update(blocks.get(("last", tok), ()))
        candidates.discard(name)
        for other in candidates:
            key = frozenset((name, other))
            if key in seen:
                continue
            seen.add(key)
            yield name, other


def resolve_entities(db_path=None) -> dict:
    """Run fuzzy matching on all distinct entity names.

    Returns summary dict with counts.
    """
    conn = get_connection(db_path)
    names = get_distinct_entity_names(conn)
    # Corroboration field sets computed once, not per pair.
    id_sets = entity_identifier_sets(conn, names)

    logger.info("Resolving entities: %d distinct names", len(names))

    stats = {
        "total_pairs_checked": 0,
        "links_created": 0,
        "links_skipped_duplicate": 0,
        "needs_review": 0,
        "auto_confirmed": 0,
    }

    for name_a, name_b in _block_candidates(names):
        stats["total_pairs_checked"] += 1

        tokens_a = _tokens(name_a)
        tokens_b = _tokens(name_b)
        ea = _initials_expand(tokens_a, tokens_b)
        eb = _initials_expand(tokens_b, tokens_a)
        raw_score = fuzz.token_set_ratio(" ".join(ea), " ".join(eb))

        # Cheap pre-filter before link insert work
        if raw_score < MATCH_THRESHOLD:
            continue

        corroborated = bool(id_sets.get(name_a, set()) & id_sets.get(name_b, set()))
        effective_score, match_status = classify_score(
            name_a, name_b, raw_score, corroborated=corroborated
        )

        entity_a, entity_b = (
            (name_a, name_b) if name_a.lower() <= name_b.lower() else (name_b, name_a)
        )

        ok = insert_entity_link(
            conn,
            entity_a=entity_a,
            entity_b=entity_b,
            match_score=effective_score,
            match_status=match_status,
        )

        if ok:
            stats["links_created"] += 1
            if match_status == "auto_confirmed":
                stats["auto_confirmed"] += 1
                logger.info(
                    "Auto-confirmed: %s <-> %s (%.1f, corroborated)", name_a, name_b, effective_score
                )
            else:
                stats["needs_review"] += 1
                set_a = set(ea)
                set_b = set(eb)
                reason = (
                    "subset"
                    if _is_strict_subset(set_a, set_b)
                    else ("no corroboration" if effective_score >= 95 else "below threshold")
                )
                logger.warning(
                    "NEEDS REVIEW: %s <-> %s (%.1f, %s)",
                    name_a, name_b, effective_score, reason,
                )
        else:
            stats["links_skipped_duplicate"] += 1

    conn.close()

    logger.info(
        "Resolution complete: %d pairs, %d links (%d auto, %d review), %d dups",
        stats["total_pairs_checked"], stats["links_created"],
        stats["auto_confirmed"], stats["needs_review"],
        stats["links_skipped_duplicate"],
    )
    return stats


# ── Self-test ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import logging
    import sys
    import tempfile
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    from storage import get_entity_links, insert_record

    failures = 0

    def check(cond, label):
        global failures
        print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
        if not cond:
            failures += 1

    print("Testing entity resolution with varied name formats...")
    test_db = Path(tempfile.mkdtemp()) / "test_resolution.db"
    conn = get_connection(db_path=test_db)

    test_names = [
        "Rakesh Verma",
        "Rakesh Kumar Verma",
        "R. Verma",
        "Rakesh K. Verma",
        "Pradeep Singh",
        "Pradeep Singh Rajput",
        "P. Singh",
        "Amit Sharma",
        "Amit Kumar Sharma",
        "Completely Different Person",
    ]
    # Transliteration + initials + corroboration fixtures
    more_names = [
        ("Mohammed Khan", "fir-TR1"),
        ("Muhammad Khan", "fir-TR1"),
        ("Suresh Prasad", None),
        ("Suresh Prasaad", "fir-TR2"),
        ("Rakesh V.", "fir-TR3"),
    ]
    for name in test_names:
        insert_record(
            conn,
            entity_name=name,
            detail=f"Test record for {name}",
            source_type="test",
            source_url=f"https://example.com/{name.replace(' ', '-').lower()}",
        )
    for name, fir in more_names:
        insert_record(
            conn,
            entity_name=name,
            detail=f"Test record for {name}",
            source_type="test",
            source_url=f"https://example.com/{name.replace(' ', '-').lower()}",
            fir_number=fir,
        )
    conn.close()

    stats = resolve_entities(db_path=test_db)

    print(f"\nResults:")
    print(f"  Pairs checked: {stats['total_pairs_checked']}")
    print(f"  Links created: {stats['links_created']}")
    print(f"  Auto-confirmed (>= {REVIEW_THRESHOLD} + corroboration): {stats['auto_confirmed']}")
    print(f"  Needs review (< {REVIEW_THRESHOLD}): {stats['needs_review']}")

    def links_between(conn, a, b):
        for l in get_entity_links(conn, a):
            others = [x for x in (l["entity_a"], l["entity_b"]) if x != a]
            for o in others:
                if o == b:
                    return l["match_score"], l["match_status"]
        return None

    conn = get_connection(db_path=test_db)

    # #3 regression: subset pair must be needs_review, never auto_confirmed.
    ps = links_between(conn, "Pradeep Singh", "Pradeep Singh Rajput")
    check(ps is not None, "Pradeep Singh <-> Pradeep Singh Rajput link exists")
    if ps:
        score, status = ps
        check(score < REVIEW_THRESHOLD, f"subset link score capped below {REVIEW_THRESHOLD} (got {score})")
        check(status == "needs_review", f"subset link stored as needs_review (got {status})")

    # #4: initial-vs-full subset stays conservatively needs_review.
    rv = links_between(conn, "R. Verma", "Rakesh Verma")
    check(rv is not None, "R. Verma <-> Rakesh Verma link exists")
    if rv:
        score, status = rv
        check(status == "needs_review", f"initial-vs-full subset stored as needs_review (got {status})")

    # Transliteration: Mohammed/Muhammad + shared FIR -> auto-confirmed.
    mo = links_between(conn, "Mohammed Khan", "Muhammad Khan")
    check(mo is not None, "Mohammed Khan <-> Muhammad Khan link exists")
    if mo:
        score, status = mo
        check(score >= REVIEW_THRESHOLD, f"transliterated pair reaches threshold (got {score})")
        check(status == "auto_confirmed", f"transliterated + corroborated -> auto_confirmed (got {status})")

    # High score without corroboration: stays needs_review.
    su = links_between(conn, "Suresh Prasad", "Suresh Prasaad")
    check(su is not None, "Suresh Prasad <-> Suresh Prasaad link exists")
    if su:
        score, status = su
        check(score >= 90, f"transliterated-identical pair scores high (got {score})")
        check(status == "needs_review", f"identical transliteration without corroboration -> needs_review (got {status})")

    # Initials expansion: "Rakesh V." now reaches review (was silently < 85).
    rk = links_between(conn, "Rakesh V.", "Rakesh Verma")
    check(rk is not None, "Rakesh V. <-> Rakesh Verma link exists (initials expansion)")
    if rk:
        score, status = rk
        check(status in ("auto_confirmed", "needs_review"),
              f"Rakesh V. pair correctly linked (got {status})")

    conn.close()

    print(f"\n{'ALL RESOLUTION TESTS PASSED' if failures == 0 else f'{failures} TEST(S) FAILED'}")
    sys.exit(1 if failures else 0)