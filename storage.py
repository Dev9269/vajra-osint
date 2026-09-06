"""SQLite storage layer for VAJRA OSINT aggregator.

Schema is designed for SQLite now, portable to Postgres later.
Every record MUST carry source_url + fetched_at — no unsourced claims.
"""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

DB_PATH = Path(__file__).parent / "vajra.db"

# ── Schema ──────────────────────────────────────────────────────────────────

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_name TEXT NOT NULL,
    record_type TEXT NOT NULL DEFAULT 'person',
    detail TEXT,
    location TEXT,
    source_type TEXT NOT NULL,
    source_url TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    UNIQUE(entity_name, source_url, detail)
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
CREATE INDEX IF NOT EXISTS idx_entity_links_a ON entity_links(entity_a);
CREATE INDEX IF NOT EXISTS idx_entity_links_b ON entity_links(entity_b);
"""


# ── Connection ──────────────────────────────────────────────────────────────

def get_connection(db_path: Optional[Path] = None) -> sqlite3.Connection:
    """Return a connection to the SQLite database, creating it if needed."""
    path = db_path or DB_PATH
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_SQL)
    return conn


# ── Inserts ─────────────────────────────────────────────────────────────────

def insert_record(
    conn: sqlite3.Connection,
    *,
    entity_name: str,
    record_type: str = "person",
    detail: Optional[str] = None,
    location: Optional[str] = None,
    source_type: str,
    source_url: str,
    fetched_at: Optional[str] = None,
) -> bool:
    """Insert a record. Returns True if inserted, False if duplicate (UNIQUE hit)."""
    if fetched_at is None:
        fetched_at = datetime.now(timezone.utc).isoformat()
    try:
        conn.execute(
            """INSERT INTO records
               (entity_name, record_type, detail, location, source_type, source_url, fetched_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (entity_name, record_type, detail, location, source_type, source_url, fetched_at),
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
    """Insert a fuzzy-matched entity link. Returns True if inserted, False if duplicate."""
    if linked_at is None:
        linked_at = datetime.now(timezone.utc).isoformat()
    # Normalize order so (A,B) and (B,A) don't create separate rows
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


# ── Queries (for Agent B / api.py) ──────────────────────────────────────────

def get_all_entities(conn: sqlite3.Connection) -> list[dict]:
    """Return distinct entity names with their record counts."""
    rows = conn.execute(
        "SELECT entity_name, COUNT(*) as record_count FROM records GROUP BY entity_name ORDER BY entity_name"
    ).fetchall()
    return [dict(r) for r in rows]


def get_entity_records(conn: sqlite3.Connection, entity_name: str) -> list[dict]:
    """Return all records for a given entity, with source citation."""
    rows = conn.execute(
        """SELECT id, entity_name, record_type, detail, location,
                  source_type, source_url, fetched_at
           FROM records
           WHERE entity_name = ?
           ORDER BY fetched_at DESC""",
        (entity_name,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_entity_links(conn: sqlite3.Connection, entity_name: str) -> list[dict]:
    """Return all fuzzy-matched links involving this entity."""
    rows = conn.execute(
        """SELECT entity_a, entity_b, match_score, linked_at
           FROM entity_links
           WHERE entity_a = ? OR entity_b = ?
           ORDER BY match_score DESC""",
        (entity_name, entity_name),
    ).fetchall()
    return [dict(r) for r in rows]


def search_entities(conn: sqlite3.Connection, query: str, limit: int = 50) -> list[dict]:
    """Search entities by partial name match."""
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
    """Return all distinct entity names in the database."""
    rows = conn.execute(
        "SELECT DISTINCT entity_name FROM records ORDER BY entity_name"
    ).fetchall()
    return [r["entity_name"] for r in rows]


# ── Self-test ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("Testing storage.py...")
    conn = get_connection(db_path=Path(":memory:"))

    # Insert a record
    ok = insert_record(
        conn,
        entity_name="Rakesh Kumar Verma",
        detail="FIR 123/2026 — accused in cyber fraud case",
        location="New Delhi",
        source_type="news",
        source_url="https://example.com/news/verma-arrest",
    )
    assert ok, "First insert should succeed"

    # Duplicate should fail silently
    ok2 = insert_record(
        conn,
        entity_name="Rakesh Kumar Verma",
        detail="FIR 123/2026 — accused in cyber fraud case",
        location="New Delhi",
        source_type="news",
        source_url="https://example.com/news/verma-arrest",
    )
    assert not ok2, "Duplicate insert should return False"

    # Query
    entities = get_all_entities(conn)
    assert len(entities) == 1
    assert entities[0]["entity_name"] == "Rakesh Kumar Verma"
    assert entities[0]["record_count"] == 1

    # Entity link
    ok_link = insert_entity_link(
        conn, entity_a="Rakesh Kumar Verma", entity_b="R. Verma", match_score=87.5
    )
    assert ok_link

    links = get_entity_links(conn, "Rakesh Kumar Verma")
    assert len(links) == 1
    assert links[0]["match_score"] == 87.5

    # Search
    results = search_entities(conn, "Verma")
    assert len(results) == 1

    print("All storage tests passed.")
