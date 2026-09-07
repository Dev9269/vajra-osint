"""Query API for VAJRA OSINT aggregator.

FastAPI REST layer — frontend consumes this, never touches the DB directly.

CHANGELOG 2026-09-06: added structured-field endpoints:
  /search?q=  (name search, legacy)
  /by-status/{status}
  /by-crime/{crime_type}
The /entities/{name} response now includes structured fields (age, gender,
crime_type, status, fir_number, plate_number, phone_number).

CHANGELOG 2026-09-06 (bugfix round, #7):
  - CORS: no longer allows all origins; explicit list from VAJRA_CORS_ORIGINS
    env var (default http://localhost:3000,http://localhost:8080).
    allow_credentials=False.
  - API key auth: X-API-Key header required on all routes except /health.
    Key set via VAJRA_API_KEY env var; auto-generated and printed on startup
    if not set.
"""

import logging
import os
import secrets

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from storage import (
    get_all_entities,
    get_case_evidence,
    get_case_references,
    get_connection,
    get_entity_links,
    get_entity_records,
    search_by_status,
    search_by_crime,
    search_entities,
)

logger = logging.getLogger(__name__)

# ── API key ─────────────────────────────────────────────────────────────────
API_KEY = os.environ.get("VAJRA_API_KEY") or secrets.token_urlsafe(32)
if not os.environ.get("VAJRA_API_KEY"):
    print(f"[VAJRA] VAJRA_API_KEY not set — generated ephemeral key for this run: {API_KEY}")
    print("        Set VAJRA_API_KEY environment variable for a stable key.")


def verify_api_key(x_api_key: str = Header(None)):
    if not x_api_key or not secrets.compare_digest(x_api_key, API_KEY):
        raise HTTPException(
            status_code=401,
            detail="Invalid or missing X-API-Key header.",
        )


# ── CORS ────────────────────────────────────────────────────────────────────
_cors_origins_raw = os.environ.get(
    "VAJRA_CORS_ORIGINS", "http://localhost:3000,http://localhost:8080"
)
cors_origins = [o.strip() for o in _cors_origins_raw.split(",") if o.strip()]

app = FastAPI(title="VAJRA OSINT API", description="Query layer for VAJRA OSINT — SIH 2026", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["X-API-Key"],
)


@app.get("/health")
def health():
    return {"status": "ok", "service": "vajra-osint-api"}


def _case_id_from_source(source_type: str) -> str | None:
    """'case_CASE-001' -> 'CASE-001'; None for non-case sources."""
    if source_type and source_type.startswith("case_"):
        return source_type[len("case_"):]
    return None


@app.get("/entities")
def list_entities(limit: int = Query(100, ge=1, le=1000), _key: str = Depends(verify_api_key)):
    conn = get_connection()
    try:
        entities = get_all_entities(conn)
        return {"count": len(entities), "entities": entities[:limit]}
    finally:
        conn.close()


@app.get("/entities/{name}")
def get_entity(name: str, _key: str = Depends(verify_api_key)):
    conn = get_connection()
    try:
        records = get_entity_records(conn, name)
        if not records:
            raise HTTPException(status_code=404, detail=f"Entity '{name}' not found")

        evidence: list[dict] = []
        references: list[dict] = []
        related_cases: list[str] = []
        for rec in records:
            case_id = _case_id_from_source(rec.get("source_type"))
            if case_id and case_id not in related_cases:
                related_cases.append(case_id)

        for case_id in related_cases:
            evidence.extend(get_case_evidence(conn, case_id))
            references.extend(get_case_references(conn, case_id))

        return {
            "entity_name": name,
            "record_count": len(records),
            "records": records,
            "related_cases": related_cases,
            "evidence": evidence,
            "references": references,
        }
    finally:
        conn.close()


@app.get("/cases/{case_id}")
def get_case(case_id: str, _key: str = Depends(verify_api_key)):
    conn = get_connection()
    try:
        evidence = get_case_evidence(conn, case_id)
        references = get_case_references(conn, case_id)
        if not evidence and not references:
            raise HTTPException(status_code=404, detail=f"Case '{case_id}' not found in API")
        return {
            "case_id": case_id,
            "evidence": evidence,
            "references": references,
        }
    finally:
        conn.close()


@app.get("/entities/{name}/links")
def get_links(name: str, _key: str = Depends(verify_api_key)):
    conn = get_connection()
    try:
        links = get_entity_links(conn, name)
        return {
            "entity_name": name,
            "link_count": len(links),
            "links": links,
        }
    finally:
        conn.close()


@app.get("/search")
def search(q: str = Query(..., min_length=1), limit: int = Query(50, ge=1, le=200), _key: str = Depends(verify_api_key)):
    conn = get_connection()
    try:
        results = search_entities(conn, q, limit=limit)
        return {"query": q, "result_count": len(results), "results": results}
    finally:
        conn.close()


@app.get("/by-status/{status}")
def by_status(status: str, _key: str = Depends(verify_api_key)):
    conn = get_connection()
    try:
        rows = search_by_status(conn, status)
        return {"status": status, "result_count": len(rows), "results": rows}
    finally:
        conn.close()


@app.get("/by-crime/{crime_type}")
def by_crime(crime_type: str, _key: str = Depends(verify_api_key)):
    conn = get_connection()
    try:
        rows = search_by_crime(conn, crime_type)
        return {"crime_type": crime_type, "result_count": len(rows), "results": rows}
    finally:
        conn.close()


# ── Self-test ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    print("Starting VAJRA API at http://localhost:8000 (docs: /docs)")
    uvicorn.run(app, host="0.0.0.0", port=8000)
