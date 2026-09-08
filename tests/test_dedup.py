"""Deduplication regression tests (storage dedup_key + idempotent re-sync)."""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture()
def conn(empty_db):
    return empty_db[0]


def test_duplicate_record_is_rejected(conn):
    from storage import insert_record

    ok1 = insert_record(
        conn,
        entity_name="Rakesh Verma",
        source_type="test",
        source_url="https://example.com/x",
        fir_number="FIR 100/2026",
    )
    ok2 = insert_record(
        conn,
        entity_name="Rakesh Verma",
        source_type="test",
        source_url="https://example.com/x",
        fir_number="FIR 100/2026",
    )
    assert ok1 is True
    assert ok2 is False  # same entity + url + fir -> dedup


def test_same_entity_different_url_allowed(conn):
    from storage import insert_record

    insert_record(
        conn, entity_name="Rakesh Verma", source_type="test",
        source_url="https://example.com/1", fir_number="FIR 100/2026",
    )
    ok = insert_record(
        conn, entity_name="Rakesh Verma", source_type="test",
        source_url="https://example.com/2", fir_number="FIR 100/2026",
    )
    assert ok is True  # different source, same person — valid new evidence


def test_null_fir_re_sync_is_idempotent(conn):
    """#8 regression: a re-sync WITHOUT fir_number must still dedup."""
    from storage import insert_record

    ok1 = insert_record(
        conn, entity_name="Amit Shah", source_type="test",
        source_url="https://example.com/nullfir",
    )
    ok2 = insert_record(
        conn, entity_name="Amit Shah", source_type="test",
        source_url="https://example.com/nullfir",
    )
    assert ok1 is True
    assert ok2 is False  # COALESCE('') keeps the key stable across re-syncs


def test_dedup_key_semantics():
    from storage import dedup_key_for

    k1 = dedup_key_for("Rakesh Verma", "https://example.com/x", "FIR 1/2026")
    k2 = dedup_key_for("Rakesh Verma", "https://example.com/x", None)
    k3 = dedup_key_for("Rakesh Verma", "https://example.com/x", "")
    assert k1 != k2
    assert k2 == k3  # NULL and empty normalize identically -> stable dedup


def test_get_all_entities_pagination_counts(conn):
    from storage import insert_record, get_all_entities

    for i in range(12):
        insert_record(
            conn, entity_name=f"Person {i}", source_type="test",
            source_url=f"https://example.com/{i}",
        )
    page1, total = get_all_entities(conn, limit=5, offset=0)
    page2, total2 = get_all_entities(conn, limit=5, offset=5)
    assert len(page1) == 5
    assert len(page2) == 5
    assert total == 12 and total2 == 12
    names1 = {e["entity_name"] for e in page1}
    names2 = {e["entity_name"] for e in page2}
    assert names1.isdisjoint(names2)