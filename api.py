"""Query API for VAJRA OSINT aggregator.

FastAPI REST layer — frontend consumes this, never touches the DB directly.

CHANGELOG 2026-09-06: added structured-field endpoints:
  /search?q=  (name search, legacy)
  /by-status/{status}
  /by-crime/{crime_type}
The /entities/{name} response now includes structured fields (age, gender,
crime_type, status, location, fir_number, plate_number, phone_number).
"""

import logging

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from storage import (
    get_all_entities,
    get_connection,
    get_entity_links,
    get_entity_records,
    search_by_status,
    search_by_crime,
    search_entities,
)

logger = logging.getLogger(__name__)

app = FastAPI(title="VAJRA OSINT API", description="Query layer for VAJRA OSINT — SIH 2026", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    return {"status": "ok", "service": "vajra-osint-api"}


@app.get("/entities")
def list_entities(limit: int = Query(100, ge=1, le=1000)):
    conn = get_connection()
    try:
        entities = get_all_entities(conn)
        return {"count": len(entities), "entities": entities[:limit]}
    finally:
        conn.close()


@app.get("/entities/{name}")
def get_entity(name: str):
    conn = get_connection()
    try:
        records = get_entity_records(conn, name)
        if not records:
            raise HTTPException(status_code=404, detail=f"Entity '{name}' not found")
        return {
            "entity_name": name,
            "record_count": len(records),
            "records": records,
        }
    finally:
        conn.close()


@app.get("/entities/{name}/links")
def get_links(name: str):
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
def search(q: str = Query(..., min_length=1), limit: int = Query(50, ge=1, le=200)):
    conn = get_connection()
    try:
        results = search_entities(conn, q, limit=limit)
        return {"query": q, "result_count": len(results), "results": results}
    finally:
        conn.close()


@app.get("/by-status/{status}")
def by_status(status: str):
    conn = get_connection()
    try:
        rows = search_by_status(conn, status)
        return {"status": status, "result_count": len(rows), "results": rows}
    finally:
        conn.close()


@app.get("/by-crime/{crime_type}")
def by_crime(crime_type: str):
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
