"""SQLite storage layer for VAJRA OSINT aggregator.

CHANGELOG 2026-09-06: schema redesigned — added structured person fields
(age, crime_type, crime_description, status, gender, fir_number, plate_number,
phone_number). UNIQUE constraint changed to entity_name+source_url+fir_number
to allow same person appearing in different sources with different case numbers.
"""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

DB_PATH = Path(__file__).parent / "vajra.db"

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_name TEXT NOT NULL,
    record_type TEXT NOT NULL DEFAULT 'person',
    age INTEGER,
    gender TEXT,
    crime_type TEXT,
    crime_description TEXT,
    status TEXT,
    location TEXT,
    fir_number TEXT,
    plate_number TEXT,
    phone_number TEXT,
    detail TEXT,
    source_type TEXT NOT NULL,
    source_url TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    UNIQUE(entity_name, source_url, fir_number)
);

CREATE TABLE IF NOT EXISTS entity_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_a TEXT NOT NULL,
    entity_b TEXT NOT NULL,
    match_score REAL NOT NULL,
    linked_at TEXT NOT NULL,
    UNIQUE(entity_a, entity_b)
);

CREATE INDEX IF NOT EXISTS idx_records_entity ON records(entity_name);
CREATE INDEX IF NOT EXISTS idx_records_source ON records(source_type);
CREATE INDEX IF NOT EXISTS idx_records_status ON records(status);
CREATE INDEX IF NOT EXISTS idx_records_crime ON records(crime_type);
CREATE INDEX IF NOT EXISTS idx_entity_links_a ON entity_links(entity_a);
CREATE INDEX IF NOT EXISTS idx_entity_links_b ON entity_links(entity_b);
"""


def get_connection(db_path: Optional[Path] = None) -> sqlite3.Connection:
    path = db_path or DB_PATH
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_SQL)
    return conn


def insert_record(
    conn: sqlite3.Connection,
    *,
    entity_name: str,
    record_type: str = "person",
    age: Optional[int] = None,
    gender: Optional[str] = None,
    crime_type: Optional[str] = None,
    crime_description: Optional[str] = None,
    status: Optional[str] = None,
    location: Optional[str] = None,
    fir_number: Optional[str] = None,
    plate_number: Optional[str] = None,
    phone_number: Optional[str] = None,
    detail: Optional[str] = None,
    source_type: str,
    source_url: str,
    fetched_at: Optional[str] = None,
) -> bool:
    """Insert a record. Returns True if inserted, False if duplicate."""
    if fetched_at is None:
        fetched_at = datetime.now(timezone.utc).isoformat()
    try:
        conn.execute(
            """INSERT INTO records
               (entity_name, record_type, age, gender, crime_type, crime_description,
                status, location, fir_number, plate_number, phone_number, detail,
                source_type, source_url, fetched_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (entity_name, record_type, age, gender, crime_type, crime_description,
             status, location, fir_number, plate_number, phone_number, detail,
             source_type, source_url, fetched_at),
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False


def insert_entity_link(
    conn: sqlite3.Connection,
    *,
    entity_a: str,
    entity_b: str,
    match_score: float,
    linked_at: Optional[str] = None,
) -> bool:
    if linked_at is None:
        linked_at = datetime.now(timezone.utc).isoformat()
    if entity_a.lower() > entity_b.lower():
        entity_a, entity_b = entity_b, entity_a
    try:
        conn.execute(
            """INSERT INTO entity_links (entity_a, entity_b, match_score, linked_at)
               VALUES (?, ?, ?, ?)""",
            (entity_a, entity_b, match_score, linked_at),
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False


# ── Queries ─────────────────────────────────────────────────────────────────

def get_all_entities(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT entity_name, COUNT(*) as record_count FROM records GROUP BY entity_name ORDER BY entity_name"
    ).fetchall()
    return [dict(r) for r in rows]


def get_entity_records(conn: sqlite3.Connection, entity_name: str) -> list[dict]:
    rows = conn.execute(
        """SELECT id, entity_name, record_type, age, gender, crime_type,
                  crime_description, status, location, fir_number, plate_number,
                  phone_number, detail, source_type, source_url, fetched_at
           FROM records
           WHERE entity_name = ?
           ORDER BY fetched_at DESC""",
        (entity_name,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_entity_links(conn: sqlite3.Connection, entity_name: str) -> list[dict]:
    rows = conn.execute(
        """SELECT entity_a, entity_b, match_score, linked_at
           FROM entity_links
           WHERE entity_a = ? OR entity_b = ?
           ORDER BY match_score DESC""",
        (entity_name, entity_name),
    ).fetchall()
    return [dict(r) for r in rows]


def search_entities(conn: sqlite3.Connection, query: str, limit: int = 50) -> list[dict]:
    rows = conn.execute(
        """SELECT entity_name, COUNT(*) as record_count
           FROM records
           WHERE entity_name LIKE ?
           GROUP BY entity_name
           ORDER BY record_count DESC
           LIMIT ?""",
        (f"%{query}%", limit),
    ).fetchall()
    return [dict(r) for r in rows]


def get_distinct_entity_names(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT DISTINCT entity_name FROM records ORDER BY entity_name"
    ).fetchall()
    return [r["entity_name"] for r in rows]


def search_by_status(conn: sqlite3.Connection, status: str) -> list[dict]:
    rows = conn.execute(
        """SELECT entity_name, crime_type, status, location, source_url
           FROM records WHERE status = ? ORDER BY entity_name""",
        (status,),
    ).fetchall()
    return [dict(r) for r in rows]


def search_by_crime(conn: sqlite3.Connection, crime_type: str) -> list[dict]:
    rows = conn.execute(
        """SELECT entity_name, crime_type, status, location, source_url
           FROM records WHERE crime_type LIKE ? ORDER BY entity_name""",
        (f"%{crime_type}%",),
    ).fetchall()
    return [dict(r) for r in rows]


# ── Self-test ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("Testing storage.py (new schema)...")
    conn = get_connection(db_path=Path(":memory:"))

    ok = insert_record(
        conn,
        entity_name="Rakesh Kumar Verma",
        age=34,
        gender="male",
        crime_type="cyber fraud",
        crime_description="FIR 123/2026 — accused in online banking fraud targeting senior citizens",
        status="absconding",
        location="New Delhi",
        fir_number="123/2026",
        plate_number="DL 01 AB 1234",
        phone_number="+919876543210",
        detail="FIR 123/2026 — accused in cyber fraud case",
        source_type="wikipedia",
        source_url="https://en.wikipedia.org/wiki/Rakesh_Verma",
    )
    assert ok, "First insert should succeed"

    ok2 = insert_record(
        conn,
        entity_name="Rakesh Kumar Verma",
        source_type="news",
        source_url="https://example.com/verma-arrested",
        fir_number="123/2026",
    )
    assert ok2, "Same person, different source — should succeed"

    ok3 = insert_record(
        conn,
        entity_name="Rakesh Kumar Verma",
        source_type="wikipedia",
        source_url="https://en.wikipedia.org/wiki/Rakesh_Verma",
        fir_number="123/2026",
    )
    assert not ok3, "Exact duplicate should fail"

    entities = get_all_entities(conn)
    assert len(entities) == 1
    assert entities[0]["record_count"] == 2

    recs = get_entity_records(conn, "Rakesh Kumar Verma")
    assert len(recs) == 2
    assert recs[0]["age"] == 34 or recs[1]["age"] == 34
    assert recs[0]["status"] == "absconding" or recs[1]["status"] == "absconding"

    results = search_entities(conn, "Verma")
    assert len(results) == 1

    print("All storage tests passed.")
