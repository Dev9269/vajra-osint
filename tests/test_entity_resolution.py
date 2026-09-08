"""Entity resolution regression tests (Part 2).

Verifies the corroboration rule, transliteration table, initials expansion
and blocking behaviour through resolve_entities on a seeded DB.
"""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


def _links_between(conn, a, b):
    from storage import get_entity_links

    for link in get_entity_links(conn, a):
        others = [x for x in (link["entity_a"], link["entity_b"]) if x != a]
        for o in others:
            if o == b:
                return link["match_score"], link["match_status"]
    return None


@pytest.fixture()
def resolution_db(tmp_path, empty_db):
    from storage import get_connection, insert_record

    conn, db_path = empty_db
    records = [
        # name, fir (corroboration), source_url
        ("Mohammed Khan", "FIR-TR1", "https://example.com/1"),
        ("Muhammad Khan", "FIR-TR1", "https://example.com/2"),
        ("Suresh Prasad", None, "https://example.com/3"),
        ("Suresh Prasaad", "FIR-TR2", "https://example.com/4"),
        ("Rakesh V.", None, "https://example.com/5"),
        ("Rakesh Verma", None, "https://example.com/6"),
        ("Rakesh Kumar Verma", "FIR-TR3", "https://example.com/7"),
    ]
    for name, fir, url in records:
        insert_record(
            conn, entity_name=name, detail=f"record for {name}",
            source_type="test", source_url=url, fir_number=fir,
        )
    return conn, db_path


def test_resolution_runs_and_returns_stats(resolution_db):
    from entity_resolution import resolve_entities

    _, db_path = resolution_db
    stats = resolve_entities(db_path=db_path)
    assert stats["links_created"] > 0
    assert stats["total_pairs_checked"] > 0


def test_transliteration_plus_corroboration_auto_confirms(resolution_db):
    from entity_resolution import resolve_entities

    conn, db_path = resolution_db
    resolve_entities(db_path=db_path)
    link = _links_between(conn, "Mohammed Khan", "Muhammad Khan")
    assert link is not None
    score, status = link
    assert score >= 95
    assert status == "auto_confirmed"


def test_high_score_without_corroboration_stays_needs_review(resolution_db):
    from entity_resolution import resolve_entities

    conn, db_path = resolution_db
    resolve_entities(db_path=db_path)
    link = _links_between(conn, "Suresh Prasad", "Suresh Prasaad")
    assert link is not None
    score, status = link
    assert score >= 90
    assert status == "needs_review"


def test_initials_name_reaches_review(resolution_db):
    from entity_resolution import resolve_entities

    conn, db_path = resolution_db
    resolve_entities(db_path=db_path)
    link = _links_between(conn, "Rakesh V.", "Rakesh Verma")
    assert link is not None  # initials expansion makes the pair visible
    assert link[1] in ("auto_confirmed", "needs_review")


def test_strict_subset_never_auto_confirms(resolution_db):
    from storage import insert_record
    from entity_resolution import resolve_entities, REVIEW_THRESHOLD

    conn, db_path = resolution_db
    insert_record(
        conn, entity_name="Pradeep Singh", source_type="test",
        source_url="https://example.com/a", fir_number="FIR-X",
    )
    insert_record(
        conn, entity_name="Pradeep Singh Rajput", source_type="test",
        source_url="https://example.com/b", fir_number="FIR-X",
    )
    resolve_entities(db_path=db_path)
    link = _links_between(conn, "Pradeep Singh", "Pradeep Singh Rajput")
    assert link is not None
    score, status = link
    assert score < REVIEW_THRESHOLD
    assert status == "needs_review"


def test_unrelated_names_produce_no_link(resolution_db):
    from storage import insert_record
    from entity_resolution import resolve_entities

    conn, db_path = resolution_db
    insert_record(
        conn, entity_name="ZZZ Completely Different", source_type="test",
        source_url="https://example.com/zzz",
    )
    resolve_entities(db_path=db_path)
    assert _links_between(conn, "ZZZ Completely Different", "Mohammed Khan") is None