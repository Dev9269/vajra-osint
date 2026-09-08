"""Shared fixtures for VAJRA tests."""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture()
def empty_db(tmp_path):
    """A brand-new SQLite DB path + connection."""
    from storage import get_connection

    db_path = tmp_path / "vajra_test.db"
    conn = get_connection(db_path=db_path)
    try:
        yield conn, db_path
    finally:
        conn.close()


@pytest.fixture()
def seeded_db(empty_db):
    """DB with known entities + one corroborated near-duplicate pair."""
    conn, db_path = empty_db
    from storage import insert_record

    records = [
        # (name, fir, location, source_url)
        ("Rakesh Verma", "FIR 111/2026", "New Delhi", "https://example.com/a"),
        ("Rakesh Kumar Verma", "FIR 111/2026", "New Delhi", "https://example.com/b"),
        ("Pradeep Singh", "FIR 222/2026", "Lucknow", "https://example.com/c"),
        ("Pradeep Singh Rajput", "FIR 333/2026", "Lucknow", "https://example.com/d"),
        ("Totally Different Person", None, "Mumbai", "https://example.com/e"),
    ]
    for name, fir, location, url in records:
        insert_record(
            conn,
            entity_name=name,
            detail=f"record for {name}",
            source_type="test",
            source_url=url,
            fir_number=fir,
            location=location,
        )
    return conn, db_path