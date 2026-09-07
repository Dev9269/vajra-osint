"""Ingest case evidence/media manifests into the VAJRA API.

Scans cases/CASE-*/evidence/evidence_metadata/media_manifest.json and
stores every item (official FIR/court/press documents, photos, video
references) in the case_evidence table.

Links only — never downloads or stores media/binaries in the repo.
Court/prosecution documents are stored with their own status labels
(official_source / court_record / media_report / media_reference /
representational_only) and are NOT converted into convictions.

CHANGELOG 2026-09-07: new feature — FIR copies, CCTV photo references,
video references, and official documents surfaced through the API.
"""

import json
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from storage import get_connection, insert_case_evidence  # noqa: E402

CASES_DIR = Path(__file__).resolve().parent

EVIDENCE_META_REL = "evidence/evidence_metadata/media_manifest.json"


def _load_manifest(manifest_path: Path) -> dict:
    with open(manifest_path, encoding="utf-8") as f:
        return json.load(f)


def ingest_case_evidence(case_id: str, db_path: Optional[Path] = None) -> int:
    """Load one case's evidence manifest into the DB. Returns items inserted."""
    case_dir = CASES_DIR / case_id
    manifest_path = case_dir / EVIDENCE_META_REL
    if not manifest_path.exists():
        print(f"[{case_id}] No evidence manifest at {manifest_path} — skipping")
        return 0

    manifest = _load_manifest(manifest_path)
    items = manifest.get("items", [])
    conn = get_connection(db_path)
    inserted = 0
    for item in items:
        ok = insert_case_evidence(
            conn,
            case_id=case_id,
            evidence_id=item.get("evidence_id", ""),
            media_type=item.get("type", "reference"),
            subtype=item.get("subtype"),
            title=item.get("title"),
            url=item.get("url", ""),
            source=item.get("source"),
            date=item.get("date"),
            status=item.get("status"),
        )
        if ok:
            inserted += 1
    conn.close()
    print(f"[{case_id}] {inserted} evidence items ingested ({len(items)} in manifest).")
    return inserted


def list_case_dirs() -> list[str]:
    return sorted(
        d.name for d in CASES_DIR.iterdir()
        if d.is_dir() and d.name.startswith("CASE-")
    )


if __name__ == "__main__":
    cases = sys.argv[1:] or list_case_dirs()
    total = 0
    for cid in cases:
        total += ingest_case_evidence(cid)
    print(f"Ingest complete. {total} evidence items added across {len(cases)} case(s).")