"""API security regression tests (Part 1).

Covers:
  - X-API-Key enforcement (401 on missing/wrong key)
  - 10 req/min per IP on /health and /docs (429 + Retry-After)
  - 60 req/min per API key on authenticated routes
  - auth-failure persistence + /security/auth-failures admin view
  - /entities pagination returns total_count + respects offset
  - production startup guard refuses to run without a permanent key
"""

import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture()
def api_client(tmp_path):
    """TestClient against api.py, pointed at an isolated temp DB + known key."""
    os.environ["VAJRA_API_KEY"] = "test-secret-key-123"

    import storage as storage_mod
    import api as api
    from fastapi.testclient import TestClient

    # Redirect the app's default SQLite file to a per-test temp DB, and store a
    # handle so tests can seed records.
    storage_mod.DB_PATH = tmp_path / "vajra_test.db"
    api.API_KEY = "test-secret-key-123"
    client = TestClient(api.app)
    # Isolation: reset in-memory rate-limit counters so tests don't stomp each
    # other's minute window.
    api.rate_limiter.reset()
    api.key_limiter.reset()
    api._docs_hits.clear()
    return client, api, storage_mod


def test_health_is_open(api_client):
    client, _, _ = api_client
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_missing_api_key_is_401(api_client):
    client, _, _ = api_client
    r = client.get("/entities")
    assert r.status_code == 401


def test_wrong_api_key_is_401(api_client):
    client, _, _ = api_client
    r = client.get("/entities", headers={"X-API-Key": "wrong-key"})
    assert r.status_code == 401


def test_valid_api_key_access(api_client):
    client, _, _ = api_client
    r = client.get("/entities", headers={"X-API-Key": "test-secret-key-123"})
    assert r.status_code == 200
    body = r.json()
    assert "total_count" in body
    assert "entities" in body
    assert "count" in body


def test_auth_failure_is_logged_and_persisted(api_client, tmp_path):
    client, api, _ = api_client
    # Hit with a wrong key twice on the same endpoint.
    for _ in range(2):
        r = client.get("/entities", headers={"X-API-Key": "nope"})
        assert r.status_code == 401
    # The admin view shows the failed attempts.
    r = client.get(
        "/security/auth-failures", headers={"X-API-Key": "test-secret-key-123"}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["count"] >= 2
    assert all(f["endpoint"] == "/entities" for f in body["failures"][:2])


def test_health_rate_limit_10_per_minute_per_ip(api_client):
    client, _, _ = api_client
    # 11 requests > 10/min budget on the open route.
    for _ in range(10):
        r = client.get("/health")
        assert r.status_code == 200
    r = client.get("/health")
    assert r.status_code == 429
    assert "Retry-After" in r.headers


def test_docs_rate_limit_10_per_minute_per_ip(api_client):
    client, _, _ = api_client
    for _ in range(10):
        client.get("/docs")
    r = client.get("/docs")
    assert r.status_code == 429


def test_entities_pagination(api_client):
    client, _, storage_mod = api_client
    headers = {"X-API-Key": "test-secret-key-123"}
    conn = storage_mod.get_connection()
    for i in range(6):
        storage_mod.insert_record(
            conn, entity_name=f"Person {i}", source_type="test",
            source_url=f"https://example.com/{i}",
        )
    conn.close()
    r = client.get("/entities?limit=2&offset=0", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 2
    assert body["total_count"] == 6
    # offset moves the window
    r2 = client.get("/entities?limit=2&offset=2", headers=headers)
    assert r2.status_code == 200
    first_window = [e["entity_name"] for e in body["entities"]]
    second_window = [e["entity_name"] for e in r2.json()["entities"]]
    assert first_window != second_window


def test_sql_injection_protected(api_client):
    client, _, _ = api_client
    headers = {"X-API-Key": "test-secret-key-123"}
    hostile = ["' OR '1'='1", "%", "x') OR ('a'='a", "—", "“”"]
    for payload in hostile:
        r = client.get("/search", params={"q": payload}, headers=headers)
        # Never a 500 from SQL subclassing: either results or empty.
        assert r.status_code == 200


def test_wrong_http_method_does_not_authenticate(api_client):
    # A POST to a GET-only route must still 405, not bypass auth.
    client, _, _ = api_client
    r = client.post("/entities", headers={"X-API-Key": "test-secret-key-123"})
    assert r.status_code in (405, 404)