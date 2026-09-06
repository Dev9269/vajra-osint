"""Entity resolution for VAJRA OSINT aggregator.

Pairwise fuzzy matching of distinct entity names using rapidfuzz
token_set_ratio (handles extra middle names/initials).

Matches >= 85 stored in entity_links; < 95 flagged for human review.
A single name can have multiple links.
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

        score = fuzz.token_set_ratio(name_a, name_b)

        if score < MATCH_THRESHOLD:
            continue

        entity_a, entity_b = (
            (name_a, name_b) if name_a.lower() <= name_b.lower() else (name_b, name_a)
        )

        ok = insert_entity_link(
            conn,
            entity_a=entity_a,
            entity_b=entity_b,
            match_score=score,
        )

        if ok:
            stats["links_created"] += 1
            if score >= REVIEW_THRESHOLD:
                stats["auto_confirmed"] += 1
                logger.info("Auto-confirmed: %s <-> %s (%.1f)", name_a, name_b, score)
            else:
                stats["needs_review"] += 1
                logger.warning(
                    "NEEDS REVIEW: %s <-> %s (%.1f) — below %d",
                    name_a, name_b, score, REVIEW_THRESHOLD,
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
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    import tempfile
    from storage import insert_record

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
    print(f"  Duplicates skipped: {stats['links_skipped_duplicate']}")
