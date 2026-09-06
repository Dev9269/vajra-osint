"""Entity resolution for VAJRA OSINT aggregator.

Pairwise fuzzy matching of distinct entity names using rapidfuzz.
Matches >= 85 score stored in entity_links; < 95 flagged for human review.
"""

import logging
from itertools import combinations

from rapidfuzz import fuzz

from storage import get_connection, get_distinct_entity_names, insert_entity_link

logger = logging.getLogger(__name__)

# Thresholds
MATCH_THRESHOLD = 85    # minimum score to store a link
REVIEW_THRESHOLD = 95   # below this = needs human review


def resolve_entities(db_path=None) -> dict:
    """Run fuzzy matching on all distinct entity names.

    Returns a summary dict:
        {
            "total_pairs_checked": int,
            "links_created": int,
            "links_skipped_duplicate": int,
            "needs_review": int,  # score < REVIEW_THRESHOLD
            "auto_confirmed": int,  # score >= REVIEW_THRESHOLD
        }
    """
    conn = get_connection(db_path)
    names = get_distinct_entity_names(conn)

    logger.info("Resolving entities: %d distinct names to compare", len(names))

    stats = {
        "total_pairs_checked": 0,
        "links_created": 0,
        "links_skipped_duplicate": 0,
        "needs_review": 0,
        "auto_confirmed": 0,
    }

    for name_a, name_b in combinations(names, 2):
        stats["total_pairs_checked"] += 1

        # Use token_set_ratio — handles extra middle names/initials
        score = fuzz.token_set_ratio(name_a, name_b)

        if score < MATCH_THRESHOLD:
            continue

        # Normalize entity order for consistent storage
        entity_a, entity_b = (name_a, name_b) if name_a.lower() <= name_b.lower() else (name_b, name_a)

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
                logger.info("Auto-confirmed: %s <-> %s (score: %.1f)", name_a, name_b, score)
            else:
                stats["needs_review"] += 1
                logger.warning(
                    "NEEDS REVIEW: %s <-> %s (score: %.1f) — below %d threshold",
                    name_a, name_b, score, REVIEW_THRESHOLD,
                )
        else:
            stats["links_skipped_duplicate"] += 1

    conn.close()

    logger.info(
        "Entity resolution complete: %d pairs checked, %d links created "
        "(%d auto-confirmed, %d needs review), %d duplicates skipped",
        stats["total_pairs_checked"],
        stats["links_created"],
        stats["auto_confirmed"],
        stats["needs_review"],
        stats["links_skipped_duplicate"],
    )

    return stats


# ── Self-test ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    from pathlib import Path
    from storage import insert_record

    print("Testing entity resolution with deliberately varied name formats...")

    # Use a temp file so data persists across connections
    import tempfile
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

    # Insert test records
    for name in test_names:
        insert_record(
            conn,
            entity_name=name,
            detail=f"Test record for {name}",
            source_type="test",
            source_url=f"https://example.com/{name.replace(' ', '-').lower()}",
        )

    conn.close()

    # Run resolution
    stats = resolve_entities(db_path=test_db)

    print(f"\nResults:")
    print(f"  Pairs checked: {stats['total_pairs_checked']}")
    print(f"  Links created: {stats['links_created']}")
    print(f"  Auto-confirmed (>= {REVIEW_THRESHOLD}): {stats['auto_confirmed']}")
    print(f"  Needs review (< {REVIEW_THRESHOLD}): {stats['needs_review']}")
    print(f"  Duplicates skipped: {stats['links_skipped_duplicate']}")

    # Verify specific matches
    print("\nExpected matches:")
    print("  Rakesh Verma <-> Rakesh Kumar Verma: high score (token_set handles middle name)")
    print("  Rakesh Kumar Verma <-> Rakesh K. Verma: should link (~89, below 95 -> review)")
    print("  R. Verma <-> Rakesh Verma: KNOWN LIMITATION — single-initial abbreviations")
    print("       score low (<85) with token_set_ratio; left unraveled on purpose")
    print("  Pradeep Singh <-> Pradeep Singh Rajput: high score")
    print("  Amit Sharma <-> Amit Kumar Sharma: high score")
    print("  Completely Different Person: should NOT match anyone")

    print("\nEntity resolution test complete.")
