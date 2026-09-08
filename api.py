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

CHANGELOG 2026-09-08 (security round + dashboard):
  - Rate limiting via slowapi: 60 req/min per API key, 10 req/min per IP on
    the open routes (/health, /docs). Returns 429 with Retry-After header.
  - Every failed API-key auth attempt is logged at WARNING with client IP,
    endpoint, timestamp and persisted to the auth_failures table.
  - GET /security/auth-failures (admin) exposes brute-force attempts.
  - GET /entities pagination pushed into SQL: limit + offset params, returns
    total_count (no full-table load in Python).
  - host/port configurable via VAJRA_HOST / VAJRA_PORT; binds 127.0.0.1 by
    default, only 0.0.0.0 when VAJRA_ENV=production AND VAJRA_API_KEY is set.
  - POST /links/{entity_a}/{entity_b}/review — confirm/reject needs_review
    links (auth required), logs reviewer + timestamp to link_reviews.
"""

import logging
import os
import secrets
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from storage import (
    count_auth_failures,
    get_all_entities,
    get_auth_failures,
    get_case_evidence,
    get_case_references,
    get_connection,
    get_entity_links,
    get_entity_records,
    log_auth_failure,
    review_link,
    search_by_status,
    search_by_crime,
    search_entities,
)
from entity_extraction import report_spacy_model

logger = logging.getLogger(__name__)


# ── .env loading ────────────────────────────────────────────────────────────
# Dependency-free loader: reads KEY=VALUE lines from a .env file next to this
# module into os.environ ONLY for keys that aren't already set (a real
# exported env var always wins). Lines starting with '#' are comments.
def _load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip().strip('"').strip("'")
            if key:
                os.environ.setdefault(key, value)
    except OSError:
        logger.warning("Could not read .env file at %s", path)


_load_dotenv(Path(__file__).resolve().parent / ".env")

# ── API key ─────────────────────────────────────────────────────────────────
VAJRA_API_KEY = os.environ.get("VAJRA_API_KEY")
VAJRA_ENV = (os.environ.get("VAJRA_ENV") or "development").strip().lower()
# Admin identity used to stamp link reviews. In production set this to the
# dashboard operator's handle; defaults to a generic label.
REVIEWER = (os.environ.get("VAJRA_REVIEWER") or "api-admin").strip()


def _ephemeral_key_warning(key: str) -> None:
    """Print a visible banner when running with a throwaway key."""
    line = "=" * 60
    print(line)
    print(" WARNING: VAJRA_API_KEY is not set.")
    print(" Using a TEMPORARY key that will change on every restart:")
    print(f"   {key}")
    print(" This will break any dashboard/client using a saved key.")
    print(" Set VAJRA_API_KEY in your .env for a permanent key.")
    print("   Generate one:   python generate_api_key.py")
    print(line)


if VAJRA_API_KEY:
    API_KEY = VAJRA_API_KEY
    print(f"[VAJRA] Using VAJRA_API_KEY from environment/.env (permanent).")
elif VAJRA_ENV == "production":
    print("\n" + "=" * 60)
    print(" REFUSING TO START: VAJRA_ENV=production but VAJRA_API_KEY is not set.")
    print(" Generating a throwaway key in production would rotate it on every")
    print(" restart and strand every dashboard/client using a saved key.")
    print(" Fix: python generate_api_key.py, then put the value in .env as")
    print("      VAJRA_API_KEY=<key> and restart.")
    print("=" * 60)
    raise SystemExit("VAJRA_API_KEY is required when VAJRA_ENV=production")
else:
    API_KEY = secrets.token_urlsafe(32)
    # Deliberately before logging.basicConfig (module import time) so the
    # warning cannot be swallowed by a quiet log config.
    _ephemeral_key_warning(API_KEY)


def _client_ip(request: Request) -> str:
    """Best-effort client IP (handles proxy headers if present)."""
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return get_remote_address(request)


# ── Rate limiting ───────────────────────────────────────────────────────────
# Per-IP limits apply to the OPEN routes (/health, /docs) so a scraper can't
# hammer them; per-key limits (below) constrain authenticated data routes.
rate_limiter = Limiter(key_func=_client_ip)


def _key_rate_limit(request: Request):
    """slowapi key func: authenticate the request, key = API key when present."""
    key = request.headers.get("X-API-Key")
    return key if key else f"open:{_client_ip(request)}"


key_limiter = Limiter(key_func=_key_rate_limit)


def rate_limit_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    """429 JSON response with an accurate Retry-After header.

    slowapi's bundled handler relies on storage.get_window_stats(), which the
    bundled in-memory storage does not implement for every strategy — it can
    silently drop the Retry-After header. We compute reset time directly from
    the rate-limit item (storage-independent, always 1..N seconds to window
    end), so clients always know when to retry.
    """
    response = JSONResponse(
        {"error": f"Rate limit exceeded: {exc.detail}"},
        status_code=429,
    )
    try:
        response.headers["Retry-After"] = str(int(exc.limit.get_expiry()) + 1)
    except Exception:
        response.headers["Retry-After"] = "60"
    return response


app = FastAPI(title="VAJRA OSINT API", description="Query layer for VAJRA OSINT — SIH 2026", version="2.1.0")
app.state.limiter = key_limiter
app.add_exception_handler(RateLimitExceeded, rate_limit_handler)

# ── Static dashboard ─────────────────────────────────────────────────────────
# Serves the browser dashboard from /static (needs_review queue, search,
# links) on the same origin as the API so no CORS is needed. Binds to
# 127.0.0.1 by default (see __main__).
from fastapi.staticfiles import StaticFiles
import os as _os

_STATIC_DIR = _os.path.join(_os.path.dirname(__file__), "static")
if _os.path.isdir(_STATIC_DIR):
    app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")

    @app.get("/", include_in_schema=False)
    @rate_limiter.limit("10/minute")
    def dashboard_root(request: Request):
        """Redirect / to the dashboard."""
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url="/static/index.html")

# ── CORS ────────────────────────────────────────────────────────────────────
_cors_origins_raw = os.environ.get(
    "VAJRA_CORS_ORIGINS", "http://localhost:3000,http://localhost:8080"
)
cors_origins = [o.strip() for o in _cors_origins_raw.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["X-API-Key"],
)


def verify_api_key(x_api_key: str = Header(None)):
    if not x_api_key or not secrets.compare_digest(x_api_key, API_KEY):
        raise HTTPException(
            status_code=401,
            detail="Invalid or missing X-API-Key header.",
        )


# ── Auth failure logging dependency ─────────────────────────────────────────
# Wraps verify_api_key: on rejection it logs WARNING with IP/endpoint/timestamp
# and persists the attempt for the admin brute-force view.
def verify_api_key_logged(request: Request, x_api_key: str = Header(None)):
    if not x_api_key or not secrets.compare_digest(x_api_key, API_KEY):
        ip = _client_ip(request)
        endpoint = request.url.path
        logger.warning(
            "FAILED API KEY AUTH from ip=%s endpoint=%s timestamp=%s key_present=%s",
            ip, endpoint, __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
            bool(x_api_key),
        )
        conn = get_connection()
        try:
            log_auth_failure(conn, ip, endpoint)
        finally:
            conn.close()
        raise HTTPException(
            status_code=401,
            detail="Invalid or missing X-API-Key header.",
        )


# ── Health / open docs ──────────────────────────────────────────────────────

@app.get("/health")
@rate_limiter.limit("10/minute")
def health(request: Request):
    return {"status": "ok", "service": "vajra-osint-api"}


# ── Framework docs routes (/docs, /redoc, /openapi.json) ───────────────────
# FastAPI owns these — we can't decorate them, so a lightweight middleware
# rate-limits them per IP (same 10/min budget as /health). Keeps the Swagger
# surface from being scraped/hammered without opening an auth hole.
_DOCS_RATE_WINDOW_SECONDS = 60
_DOCS_RATE_MAX = 10
# Module-level so tests can clear it for isolation.
_docs_hits = {}  # (ip, window_start) -> count


class DocsRateLimitMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        if path not in ("/docs", "/redoc", "/openapi.json"):
            await self.app(scope, receive, send)
            return

        global _docs_hits
        import time

        client = scope.get("client")
        ip = client[0] if client else "unknown"
        now = int(time.time())
        window_start = now - (now % _DOCS_RATE_WINDOW_SECONDS)
        key = (ip, window_start)
        count = _docs_hits.get(key, 0)
        _docs_hits[key] = count + 1
        # Prune stale windows so the dict doesn't grow unboundedly.
        if len(_docs_hits) > 10_000:
            for k in [k for k in _docs_hits if k[1] < window_start]:
                _docs_hits.pop(k, None)

        if count >= _DOCS_RATE_MAX:
            response = JSONResponse(
                {"error": f"Rate limit exceeded: {_DOCS_RATE_MAX} per 1 minute"},
                status_code=429,
            )
            response.headers["Retry-After"] = str(
                _DOCS_RATE_WINDOW_SECONDS - (now % _DOCS_RATE_WINDOW_SECONDS) + 1
            )
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)


app.add_middleware(DocsRateLimitMiddleware)


# ── Admin ───────────────────────────────────────────────────────────────────

@app.get("/security/auth-failures")
@key_limiter.limit("60/minute")
def security_auth_failures(request: Request, _key: str = Depends(verify_api_key_logged)):
    """Admin view of brute-force attempts (latest first)."""
    conn = get_connection()
    try:
        failures = get_auth_failures(conn, limit=200)
        return {"count": len(failures), "failures": failures}
    finally:
        conn.close()


# ── Entities ────────────────────────────────────────────────────────────────

@app.get("/entities")
@key_limiter.limit("60/minute")
def list_entities(
    request: Request,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    _key: str = Depends(verify_api_key_logged),
):
    conn = get_connection()
    try:
        entities, total_count = get_all_entities(conn, limit=limit, offset=offset)
        return {"count": len(entities), "total_count": total_count, "entities": entities}
    finally:
        conn.close()


@app.get("/entities/{name}")
@key_limiter.limit("60/minute")
def get_entity(name: str, request: Request, _key: str = Depends(verify_api_key_logged)):
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


@app.get("/entities/{name}/links")
@key_limiter.limit("60/minute")
def get_links(name: str, request: Request, _key: str = Depends(verify_api_key_logged)):
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


# ── Link review (Part 6 dashboard) ─────────────────────────────────────────

@app.post("/links/{entity_a}/{entity_b}/review")
@key_limiter.limit("60/minute")
def link_review(
    entity_a: str,
    entity_b: str,
    request: Request,
    action: str = Query(..., pattern="^(confirm|reject)$"),
    _key: str = Depends(verify_api_key_logged),
):
    conn = get_connection()
    try:
        ok, message = review_link(
            conn,
            entity_a=entity_a,
            entity_b=entity_b,
            action=action,
            reviewer=REVIEWER,
        )
        if not ok:
            raise HTTPException(status_code=404, detail=message)
        return {"ok": True, "message": message, "action": action}
    finally:
        conn.close()


# ── Cases ───────────────────────────────────────────────────────────────────

@app.get("/cases/{case_id}")
@key_limiter.limit("60/minute")
def get_case(case_id: str, request: Request, _key: str = Depends(verify_api_key_logged)):
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


# ── Search / filter ─────────────────────────────────────────────────────────

@app.get("/search")
@key_limiter.limit("60/minute")
def search(
    request: Request,
    q: str = Query(..., min_length=1),
    limit: int = Query(50, ge=1, le=200),
    _key: str = Depends(verify_api_key_logged),
):
    conn = get_connection()
    try:
        results = search_entities(conn, q, limit=limit)
        return {"query": q, "result_count": len(results), "results": results}
    finally:
        conn.close()


@app.get("/by-status/{status}")
@key_limiter.limit("60/minute")
def by_status(status: str, request: Request, _key: str = Depends(verify_api_key_logged)):
    conn = get_connection()
    try:
        rows = search_by_status(conn, status)
        return {"status": status, "result_count": len(rows), "results": rows}
    finally:
        conn.close()


@app.get("/by-crime/{crime_type}")
@key_limiter.limit("60/minute")
def by_crime(crime_type: str, request: Request, _key: str = Depends(verify_api_key_logged)):
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
    # Startup NER model check — visible WARNING if en_core_web_trf missing.
    report_spacy_model()

    host = os.environ.get("VAJRA_HOST", "127.0.0.1")
    port = int(os.environ.get("VAJRA_PORT", "8000"))
    if host == "0.0.0.0" and not (VAJRA_ENV == "production" and VAJRA_API_KEY):
        print("VAJRA_Host warning: 0.0.0.0 requires VAJRA_ENV=production AND VAJRA_API_KEY.")
        print("Falling back to 127.0.0.1 — this prevents exposing an unauthenticated server.")
        host = "127.0.0.1"

    print(f"Starting VAJRA API at http://{host}:{port} (docs: /docs)")
    uvicorn.run(app, host=host, port=port)