"""Entity resolution for VAJRA OSINT aggregator.

Pairwise fuzzy matching of distinct entity names using rapidfuzz
token_set_ratio (handles extra middle names/initials).

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
"""

import logging
from itertools import combinations
from pathlib import Path

from rapidfuzz import fuzz

from storage import (
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


def _token_normalise(name: str) -> set[str]:
    return set(name.lower().split())


def _is_strict_subset(a: str, b: str) -> bool:
    """True if one name's tokens are a strict subset of the other's."""
    ta, tb = _token_normalise(a), _token_normalise(b)
    if not ta or not tb:
        return False
    if ta == tb:
        return False
    return ta < tb or tb < ta


def classify_score(name_a: str, name_b: str, raw_score: float):
    """Return (effective_score, match_status) for a pair of names.

    Applies the strict-subset penalty: subset pairs can never be treated as
    auto-confirmed, no matter what token_set_ratio said.
    """
    if _is_strict_subset(name_a, name_b):
        effective = min(raw_score, _SUBSET_PIN)
        status = "needs_review"
    elif raw_score >= REVIEW_THRESHOLD:
        effective = raw_score
        status = "auto_confirmed"
    else:
        effective = raw_score
        status = "needs_review"
    return effective, status


def resolve_entities(db_path=None) -> dict:
    """Run fuzzy matching on all distinct entity names.

    Returns summary dict with counts.
    """
    conn = get_connection(db_path)
    names = get_distinct_entity_names(conn)

    logger.info("Resolving entities: %d distinct names", len(names))

    stats = {
        "total_pairs_checked": 0,
        "links_created": 0,
        "links_skipped_duplicate": 0,
        "needs_review": 0,
        "auto_confirmed": 0,
    }

    for name_a, name_b in combinations(names, 2):
        stats["total_pairs_checked"] += 1

        raw_score = fuzz.token_set_ratio(name_a, name_b)

        # Cheap pre-filter before the map/fuzzy work below
        if raw_score < MATCH_THRESHOLD:
            continue

        effective_score, match_status = classify_score(name_a, name_b, raw_score)

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
                logger.info("Auto-confirmed: %s <-> %s (%.1f)", name_a, name_b, effective_score)
            else:
                stats["needs_review"] += 1
                reason = "subset" if _is_strict_subset(name_a, name_b) else "below threshold"
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
    for name in test_names:
        insert_record(
            conn,
            entity_name=name,
            detail=f"Test record for {name}",
            source_type="test",
            source_url=f"https://example.com/{name.replace(' ', '-').lower()}",
        )
    conn.close()

    stats = resolve_entities(db_path=test_db)

    print(f"\nResults:")
    print(f"  Pairs checked: {stats['total_pairs_checked']}")
    print(f"  Links created: {stats['links_created']}")
    print(f"  Auto-confirmed (>= {REVIEW_THRESHOLD}): {stats['auto_confirmed']}")
    print(f"  Needs review (< {REVIEW_THRESHOLD}): {stats['needs_review']}")

    # #3 regression: the stored decision for subset pairs must be needs_review,
    # never auto_confirmed, even though token_set_ratio == 100.
    conn = get_connection(db_path=test_db)
    test_links = get_entity_links(conn, "Pradeep Singh")
    ps_pairs = []
    for l in test_links:
        others = [x for x in (l["entity_a"], l["entity_b"]) if x != "Pradeep Singh"]
        for o in others:
            if o == "Pradeep Singh Rajput":
                ps_pairs.append((l["match_score"], l["match_status"]))
    print(f"\n  Pradeep Singh <-> Pradeep Singh Rajput records: {ps_pairs}")
    check(len(ps_pairs) == 1, "exactly one Pradeep Singh <-> Pradeep Singh Rajput link exists")
    if ps_pairs:
        score, status = ps_pairs[0]
        check(score < REVIEW_THRESHOLD, f"subset link score capped below {REVIEW_THRESHOLD} (got {score})")
        check(status == "needs_review", f"subset link stored as needs_review (got {status})")

    # #4: near-identical non-subset names may still be flagged needs_review via
    # the below-threshold path, but a strict-subset pair must NEVER be
    # auto_confirmed. Verify 'R. Verma' <-> 'Rakesh Verma' is a strict subset
    # and stored as needs_review (this is the conservative, correct default).
    rv_links = []
    for l in get_entity_links(conn, "R. Verma"):
        others = [x for x in (l["entity_a"], l["entity_b"]) if x != "R. Verma"]
        for o in others:
            if o == "Rakesh Verma":
                rv_links.append((l["match_score"], l["match_status"]))
    print(f"  R. Verma <-> Rakesh Verma: {rv_links}")
    if rv_links:
        score, status = rv_links[0]
        check(score < REVIEW_THRESHOLD, f"initial-vs-full subset capped below {REVIEW_THRESHOLD} (got {score})")
        check(status == "needs_review", f"initial-vs-full subset stored as needs_review (got {status})")
    conn.close();

    print(f"\n{'ALL RESOLUTION TESTS PASSED' if failures == 0 else f'{failures} TEST(S) FAILED'}")
    sys.exit(1 if failures else 0)
