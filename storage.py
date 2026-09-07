"""SQLite storage layer for VAJRA OSINT aggregator.

CHANGELOG 2026-09-06: schema redesigned — added structured person fields
(age, crime_type, crime_description, status, gender, fir_number, plate_number,
phone_number). UNIQUE constraint changed to entity_name+source_url+fir_number
to allow same person appearing in different sources with different case numbers.

CHANGELOG 2026-09-06 (bugfix round):
  - #8: dedup now uses a NON-NULLABLE dedup_key (sha256 of entity_name +
    source_url + detail) because SQLite treats NULL fir_number as always
    distinct in a UNIQUE constraint, so re-syncs duplicated rows.
  - #4: entity_links gained a match_status column ('auto_confirmed' /
    'needs_review') so the review decision is stored, not recomputed.
"""

import hashlib
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
    image_url TEXT,
    dedup_key TEXT NOT NULL UNIQUE,
    source_type TEXT NOT NULL,
    source_url TEXT NOT NULL,
    fetched_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS entity_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_a TEXT NOT NULL,
    entity_b TEXT NOT NULL,
    match_score REAL NOT NULL,
    match_status TEXT NOT NULL DEFAULT 'needs_review',
    relation TEXT,
    description TEXT,
    linked_at TEXT NOT NULL,
    UNIQUE(entity_a, entity_b)
);

CREATE TABLE IF NOT EXISTS case_evidence (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id TEXT NOT NULL,
    evidence_id TEXT NOT NULL,
    media_type TEXT NOT NULL,
    subtype TEXT,
    title TEXT,
    url TEXT NOT NULL,
    source TEXT,
    date TEXT,
    status TEXT,
    available INTEGER NOT NULL DEFAULT 0,
    meta TEXT,
    fetched_at TEXT NOT NULL,
    UNIQUE(case_id, evidence_id)
);

CREATE TABLE IF NOT EXISTS case_references (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id TEXT NOT NULL,
    source_type TEXT NOT NULL,
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    snippet TEXT,
    meta TEXT,
    fetched_at TEXT NOT NULL,
    UNIQUE(source_type, url)
);

CREATE INDEX IF NOT EXISTS idx_case_evidence_case ON case_evidence(case_id);
CREATE INDEX IF NOT EXISTS idx_case_references_case ON case_references(case_id);

CREATE INDEX IF NOT EXISTS idx_records_entity ON records(entity_name);
CREATE INDEX IF NOT EXISTS idx_records_source ON records(source_type);
CREATE INDEX IF NOT EXISTS idx_records_status ON records(status);
CREATE INDEX IF NOT EXISTS idx_records_crime ON records(crime_type);
CREATE INDEX IF NOT EXISTS idx_entity_links_a ON entity_links(entity_a);
CREATE INDEX IF NOT EXISTS idx_entity_links_b ON entity_links(entity_b);
"""


def dedup_key_for(entity_name: Optional[str], source_url: str, fir_number: Optional[str]) -> str:
    """Stable dedup key. FIR number is COALESCE'd to '' so NULL fir_number no
    longer defeats the UNIQUE constraint (the #8 bug)."""
    base = "|".join([entity_name or "", source_url or "", fir_number or ""])
    return hashlib.sha256(base.encode("utf-8")).hexdigest()


def _migrate(conn: sqlite3.Connection) -> None:
    """Bring an older database up to the current schema.

    v1 -> v2: records table gained a NON-NULL dedup_key and dropped the
    broken UNIQUE(entity_name, source_url, fir_number); entity_links gained
    match_status. Existing rows are backfilled with their dedup_key and
    deduplicated (latest fetched_at wins) if past NULL-fir `INSERT OR
    REPLACE`-style duplication already happened.
    """
    rcols = {r[1] for r in conn.execute("PRAGMA table_info(records)")}
    records_exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='records'"
    ).fetchone() is not None
    if records_exists and "dedup_key" not in rcols:
        old_cols = [r[1] for r in conn.execute("PRAGMA table_info(records)")]
        rows = conn.execute("SELECT * FROM records ORDER BY fetched_at DESC").fetchall()
        seen = set()
        keep = []
        for r in rows:
            d = dict(zip(old_cols, r))
            key = dedup_key_for(d.get("entity_name"), d.get("source_url") or "", d.get("fir_number"))
            if key in seen:
                continue
            seen.add(key)
            keep.append(d)
        conn.execute("DROP TABLE records")
        conn.executescript("""
            CREATE TABLE records (
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
                image_url TEXT,
                dedup_key TEXT NOT NULL UNIQUE,
                source_type TEXT NOT NULL,
                source_url TEXT NOT NULL,
                fetched_at TEXT NOT NULL
            )
        """)
        for d in keep:
            conn.execute(
                """INSERT INTO records
                   (entity_name, record_type, age, gender, crime_type, crime_description,
                    status, location, fir_number, plate_number, phone_number, detail,
                    image_url, dedup_key, source_type, source_url, fetched_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (d.get("entity_name"), d.get("record_type") or "person", d.get("age"),
                 d.get("gender"), d.get("crime_type"), d.get("crime_description"),
                 d.get("status"), d.get("location"), d.get("fir_number"), d.get("plate_number"),
                 d.get("phone_number"), d.get("detail"), d.get("image_url"),
                 dedup_key_for(d.get("entity_name"), d.get("source_url") or "", d.get("fir_number")),
                 d.get("source_type"), d.get("source_url"), d.get("fetched_at")),
            )
        conn.commit()

    ecols = {r[1] for r in conn.execute("PRAGMA table_info(entity_links)")}
    links_exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='entity_links'"
    ).fetchone() is not None
    if links_exists and "match_status" not in ecols:
        conn.execute("ALTER TABLE entity_links ADD COLUMN match_status TEXT NOT NULL DEFAULT 'needs_review'")
        conn.commit()
    if links_exists and "relation" not in ecols:
        conn.execute("ALTER TABLE entity_links ADD COLUMN relation TEXT")
        conn.commit()
    if links_exists and "description" not in ecols:
        conn.execute("ALTER TABLE entity_links ADD COLUMN description TEXT")
        conn.commit()

    if records_exists and "image_url" not in rcols:
        conn.execute("ALTER TABLE records ADD COLUMN image_url TEXT")
        conn.commit()

    # case_references.meta: carries the sentence-scoped crime/status attribution
    # decision for web-search references (see web_search._attribution_for_article).
    refs_exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='case_references'"
    ).fetchone() is not None
    if refs_exists:
        refcols = {r[1] for r in conn.execute("PRAGMA table_info(case_references)")}
        if "meta" not in refcols:
            conn.execute("ALTER TABLE case_references ADD COLUMN meta TEXT")
            conn.commit()


def get_connection(db_path: Optional[Path] = None) -> sqlite3.Connection:
    path = db_path or DB_PATH
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    _migrate(conn)
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
    image_url: Optional[str] = None,
    source_type: str,
    source_url: str,
    fetched_at: Optional[str] = None,
) -> bool:
    """Insert a record. Returns True if inserted, False if duplicate."""
    if fetched_at is None:
        fetched_at = datetime.now(timezone.utc).isoformat()
    dedup_key = dedup_key_for(entity_name, source_url, fir_number)
    try:
        conn.execute(
            """INSERT INTO records
               (entity_name, record_type, age, gender, crime_type, crime_description,
                status, location, fir_number, plate_number, phone_number, detail,
                image_url, dedup_key, source_type, source_url, fetched_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (entity_name, record_type, age, gender, crime_type, crime_description,
             status, location, fir_number, plate_number, phone_number, detail,
             image_url, dedup_key, source_type, source_url, fetched_at),
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
    match_status: str = "needs_review",
    relation: Optional[str] = None,
    description: Optional[str] = None,
    linked_at: Optional[str] = None,
) -> bool:
    if match_status not in ("auto_confirmed", "needs_review"):
        raise ValueError("match_status must be 'auto_confirmed' or 'needs_review'")
    if linked_at is None:
        linked_at = datetime.now(timezone.utc).isoformat()
    if entity_a.lower() > entity_b.lower():
        entity_a, entity_b = entity_b, entity_a
    try:
        conn.execute(
            """INSERT INTO entity_links (entity_a, entity_b, match_score, match_status, relation, description, linked_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(entity_a, entity_b) DO UPDATE SET
                   match_score = excluded.match_score,
                   match_status = excluded.match_status,
                   relation = excluded.relation,
                   description = excluded.description,
                   linked_at = excluded.linked_at""",
            (entity_a, entity_b, match_score, match_status, relation, description, linked_at),
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
                  phone_number, detail, image_url, source_type, source_url, fetched_at
           FROM records
           WHERE entity_name = ?
           ORDER BY fetched_at DESC""",
        (entity_name,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_entity_links(conn: sqlite3.Connection, entity_name: str) -> list[dict]:
    rows = conn.execute(
        """SELECT entity_a, entity_b, match_score, match_status, relation, description, linked_at
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


def insert_case_evidence(
    conn: sqlite3.Connection,
    *,
    case_id: str,
    evidence_id: str,
    media_type: str,
    url: str,
    title: Optional[str] = None,
    subtype: Optional[str] = None,
    source: Optional[str] = None,
    date: Optional[str] = None,
    status: Optional[str] = None,
    available: int = 0,
    meta: Optional[str] = None,
    fetched_at: Optional[str] = None,
) -> bool:
    """Insert a case evidence/link item. Returns True if inserted, False if duplicate."""
    if fetched_at is None:
        fetched_at = datetime.now(timezone.utc).isoformat()
    try:
        conn.execute(
            """INSERT INTO case_evidence
               (case_id, evidence_id, media_type, subtype, title, url, source,
                date, status, available, meta, fetched_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (case_id, evidence_id, media_type, subtype, title, url, source,
             date, status, available, meta, fetched_at),
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False


def get_case_evidence(conn: sqlite3.Connection, case_id: str) -> list[dict]:
    rows = conn.execute(
        """SELECT evidence_id, media_type, subtype, title, url, source, date,
                  status, available, meta
           FROM case_evidence
           WHERE case_id = ?
           ORDER BY media_type, evidence_id""",
        (case_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_all_evidence(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """SELECT case_id, evidence_id, media_type, subtype, title, url, source,
                  date, status, available
           FROM case_evidence
           ORDER BY case_id, media_type, evidence_id"""
    ).fetchall()
    return [dict(r) for r in rows]


def insert_case_reference(
    conn: sqlite3.Connection,
    *,
    case_id: str,
    source_type: str,
    title: str,
    url: str,
    snippet: Optional[str] = None,
    meta: Optional[str] = None,
    fetched_at: Optional[str] = None,
) -> bool:
    """Insert a case web-search reference. Returns True if inserted, False if duplicate."""
    if fetched_at is None:
        fetched_at = datetime.now(timezone.utc).isoformat()
    try:
        conn.execute(
            """INSERT INTO case_references
               (case_id, source_type, title, url, snippet, meta, fetched_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (case_id, source_type, title, url, snippet, meta, fetched_at),
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False


def get_case_references(conn: sqlite3.Connection, case_id: str) -> list[dict]:
    rows = conn.execute(
        """SELECT id, source_type, title, url, snippet, meta, fetched_at
           FROM case_references
           WHERE case_id = ?
           ORDER BY fetched_at DESC""",
        (case_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def search_by_status(conn: sqlite3.Connection, status: str) -> list[dict]:
    rows = conn.execute(
        """SELECT entity_name, crime_type, status, location, source_url, image_url
           FROM records WHERE status = ? ORDER BY entity_name""",
        (status,),
    ).fetchall()
    return [dict(r) for r in rows]


def search_by_crime(conn: sqlite3.Connection, crime_type: str) -> list[dict]:
    rows = conn.execute(
        """SELECT entity_name, crime_type, status, location, source_url, image_url
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

    # #8 regression: NULL fir_number must still dedup on re-sync
    ok4 = insert_record(
        conn,
        entity_name="Amit Kumar",
        source_type="news",
        source_url="https://example.com/amit-article",
    )
    assert ok4, "First insert without fir_number should succeed"
    ok5 = insert_record(
        conn,
        entity_name="Amit Kumar",
        source_type="news",
        source_url="https://example.com/amit-article",
    )
    assert not ok5, "Re-sync without fir_number should be deduplicated (dedup_key)"

    # #4: match_status is stored and returned
    ok_link = insert_entity_link(
        conn,
        entity_a="Pradeep Singh",
        entity_b="Pradeep Singh Rajput",
        match_score=94.5,
        match_status="needs_review",
    )
    assert ok_link, "Entity link insert should succeed"
    ok_link2 = insert_entity_link(
        conn,
        entity_a="Rakesh Verma",
        entity_b="Rakesh Kumar Verma",
        match_score=96.0,
        match_status="auto_confirmed",
    )
    assert ok_link2
    links = get_entity_links(conn, "Pradeep Singh")
    assert any(l["match_status"] == "needs_review" for l in links), "match_status needs_review returned"
    links = get_entity_links(conn, "Rakesh Verma")
    assert any(l["match_status"] == "auto_confirmed" for l in links), "match_status auto_confirmed returned"

    entities = get_all_entities(conn)
    assert len(entities) == 2
    counts = {e["entity_name"]: e["record_count"] for e in entities}
    assert counts["Rakesh Kumar Verma"] == 2
    assert counts["Amit Kumar"] == 1

    recs = get_entity_records(conn, "Rakesh Kumar Verma")
    assert len(recs) == 2
    assert recs[0]["age"] == 34 or recs[1]["age"] == 34
    assert recs[0]["status"] == "absconding" or recs[1]["status"] == "absconding"

    results = search_entities(conn, "Verma")
    assert len(results) == 1
    search_amit = search_entities(conn, "Amit")
    assert len(search_amit) == 1

    # Case evidence + references round-trip
    ok_ev = insert_case_evidence(
        conn,
        case_id="CASE-001",
        evidence_id="FIR001",
        media_type="official_document",
        subtype="FIR",
        title="FIR copy",
        url="https://example.com/fir.pdf",
        source="Police",
        status="official_source",
        available=1,
    )
    assert ok_ev, "First case evidence insert should succeed"
    assert not insert_case_evidence(
        conn,
        case_id="CASE-001",
        evidence_id="FIR001",
        media_type="official_document",
        url="https://example.com/fir.pdf",
    ), "Duplicate case evidence should fail (case_id+evidence_id)"
    ok_ev2 = insert_case_evidence(
        conn,
        case_id="CASE-001",
        evidence_id="VID001",
        media_type="video_reference",
        title="News video",
        url="https://example.com/video",
        status="media_reference",
    )
    assert ok_ev2
    ev = get_case_evidence(conn, "CASE-001")
    assert len(ev) == 2
    assert any(e["media_type"] == "official_document" and e["subtype"] == "FIR" for e in ev)

    ok_ref = insert_case_reference(
        conn,
        case_id="CASE-001",
        source_type="google_news",
        title="Loan app case",
        url="https://example.com/article",
        snippet="ED arrests...",
    )
    assert ok_ref, "First case reference insert should succeed"
    assert not insert_case_reference(
        conn,
        case_id="CASE-001",
        source_type="google_news",
        title="Loan app case",
        url="https://example.com/article",
    ), "Duplicate case reference should fail (source_type+url)"
    refs = get_case_references(conn, "CASE-001")
    assert len(refs) == 1
    assert refs[0]["source_type"] == "google_news"

    ok_ref2 = insert_case_reference(
        conn,
        case_id="CASE-001",
        source_type="google_news",
        title="Attributed article",
        url="https://example.com/article2",
        snippet="Arrested for fraud...",
        meta='{"crime_type": "fraud", "status": "arrested"}',
    )
    assert ok_ref2, "Reference with meta should insert"
    refs2 = get_case_references(conn, "CASE-001")
    assert len(refs2) == 2
    assert refs2[-1]["meta"] == '{"crime_type": "fraud", "status": "arrested"}'

    print("All storage tests passed.")
