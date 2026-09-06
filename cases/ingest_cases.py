#!/usr/bin/env python3
"""Ingest real OSINT cases (CASE-001, CASE-002) into the VAJRA database.

Maps each case's curated entities/sources/relationships into the same
records + entity_links schema the API serves, preserving status and
provenance (court record vs agency claim vs media report are NOT
converted into convictions).

Usage:
    python cases/ingest_cases.py                       # ingest both cases
    python cases/ingest_cases.py --cases CASE-001      # one case only
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
CASES_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

logger = logging.getLogger("ingest_cases")


def load_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def load_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def status_label(raw: str) -> str:
    """Map case status vocabulary to VAJRA status strings."""
    v = raw.casefold()
    if v in ("accused", "designated_accused", "accused at large"):
        return "accused"
    if v in ("convicted", "convicted_person"):
        return "convicted"
    if "wanted" in v or v == "absconding":
        return "wanted"
    if "associate" in v or "accountant" in v or "enabler" in v:
        return "associate"
    if "court" in v or "named in court" in v:
        return "named_in_court_record"
    return raw or "inferred"


def crime_from_case(case: Dict[str, Any]) -> tuple[Optional[str], str]:
    """Return (crime_type, description) from case metadata."""
    crime_types = case.get("crime_types") or []
    title = case.get("title", "")
    desc = f"{title} — {case.get('source_scope', '')}".strip(" —")
    if isinstance(crime_types, list) and crime_types:
        return crime_types[0], desc
    if isinstance(crime_types, str) and crime_types:
        return crime_types, desc
    if "loan" in title.casefold():
        return "financial_fraud", desc
    if "drug" in title.casefold() or "narcotic" in title.casefold():
        return "narcotics", desc
    return None, desc


def ingest_case(case_dir: Path, conn) -> int:
    from storage import insert_record, insert_entity_link

    case = load_json(case_dir / "case.json")
    entities = load_jsonl(case_dir / "entities.jsonl")
    relationships = load_jsonl(case_dir / "relationships.jsonl")
    sources = {s["source_id"]: s for s in load_jsonl(case_dir / "sources.jsonl")}

    if not entities:
        logger.warning("No entities in %s — skipping", case_dir.name)
        return 0

    crime_type, crime_desc = crime_from_case(case)
    url_by_src = lambda src_ids: next(
        (sources[s]["url"] for s in (src_ids or []) if s in sources and sources[s].get("url")), ""
    )
    source_name = case_dir.name

    # --- pass 1: entities -> records ---
    inserted = 0
    for e in entities:
        name = (e.get("canonical_name") or e.get("name") or "").strip()
        if not name:
            continue
        legal = e.get("legal_status") or e.get("status") or ""
        details = " | ".join(
            p for p in [
                e.get("role") or "",
                e.get("notes") or "",
                (f"aliases: " + ", ".join(e.get("aliases") or [])) if e.get("aliases") else "",
            ] if p
        )
        ok = insert_record(
            conn,
            entity_name=name,
            record_type="person",
            crime_type=crime_type,
            crime_description=crime_desc,
            status=status_label(legal),
            detail=details,
            source_type="case_" + case.get("case_id", source_name),
            source_url=url_by_src(e.get("source_ids")),
            fetched_at=None,
        )
        if ok:
            inserted += 1
    logger.info("%s: inserted %d entity records", case_dir.name, inserted)

    # --- pass 2: relationships -> entity_links ---
    links = 0
    id_to_name = {e["entity_id"]: (e.get("canonical_name") or e.get("name")) for e in entities}
    for r in relationships:
        a_id = r.get("source_entity"); b_id = r.get("target_entity")
        a = id_to_name.get(a_id); b = id_to_name.get(b_id)
        if not a or not b or a == b:
            continue
        ok = insert_entity_link(
            conn,
            entity_a=a,
            entity_b=b,
            match_score=float(r.get("confidence") or 0.5),
            match_status="needs_review",
            relation=r.get("relation") or r.get("relationship") or "linked",
            description=(r.get("description") or "") + (f" [status: {r.get('status')}]" if r.get("status") else ""),
        )
        if ok:
            links += 1
    logger.info("%s: inserted %d entity links", case_dir.name, links)
    return inserted


def main() -> int:
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(console)
    logger.setLevel(logging.INFO)

    ap = argparse.ArgumentParser(description="Ingest real OSINT cases into VAJRA DB")
    ap.add_argument("--cases", nargs="*", default=["CASE-001", "CASE-002"], help="case dir names")
    args = ap.parse_args()

    from storage import get_connection
    conn = get_connection()
    total = 0
    for name in args.cases:
        total += ingest_case(CASES_DIR / name, conn)
        conn.commit()
    logger.info("Done. Total new records inserted: %d", total)
    conn.close()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        logger.exception("Ingest failed: %s", e)
        sys.exit(1)