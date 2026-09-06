"""Query API for VAJRA OSINT aggregator.

FastAPI REST layer — the frontend consumes this, never touches the DB directly.

Endpoints:
  GET /entities — list all known entities
  GET /entities/<name> — all records for one entity with source citations
  GET /entities/<name>/links — fuzzy-matched related entities
  GET /search?q=<query> — search entities by partial name
  GET /health — health check
"""

import logging
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from storage import (
    get_all_entities,
    get_connection,
    get_entity_links,
    get_entity_records,
    search_entities,
)

logger = logging.getLogger(__name__)

app = FastAPI(
    title="VAJRA OSINT API",
    description="Query layer for VAJRA OSINT aggregator — SIH 2026",
    version="1.0.0",
)

# CORS for frontend dashboard
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    """Health check endpoint."""
    return {"status": "ok", "service": "vajra-osint-api"}


@app.get("/entities")
def list_entities(limit: int = Query(100, ge=1, le=1000)):
    """List all known entities with record counts."""
    conn = get_connection()
    try:
        entities = get_all_entities(conn)
        return {"count": len(entities), "entities": entities[:limit]}
    finally:
        conn.close()


@app.get("/entities/{name}")
def get_entity(name: str):
    """Get all records for a specific entity, with source citations."""
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
    """Get fuzzy-matched related entities."""
    conn = get_connection()
    try:
        links = get_entity_links(conn, name)
        if not links:
            return {
                "entity_name": name,
                "link_count": 0,
                "links": [],
                "message": "No related entities found",
            }
        return {
            "entity_name": name,
            "link_count": len(links),
            "links": links,
        }
    finally:
        conn.close()


@app.get("/search")
def search(q: str = Query(..., min_length=1), limit: int = Query(50, ge=1, le=200)):
    """Search entities by partial name match."""
    conn = get_connection()
    try:
        results = search_entities(conn, q, limit=limit)
        return {
            "query": q,
            "result_count": len(results),
            "results": results,
        }
    finally:
        conn.close()


# ── Self-test ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    print("Starting VAJRA API server at http://localhost:8000")
    print("Docs at http://localhost:8000/docs")
    uvicorn.run(app, host="0.0.0.0", port=8000)
