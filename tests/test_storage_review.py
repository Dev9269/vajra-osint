"""Link-review and admin-view storage tests (Parts 1 & 6)."""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture()
def conn(empty_db):
    return empty_db[0]


def test_auth_failure_log_and_count(conn):
    from storage import log_auth_failure, count_auth_failures, get_auth_failures

    log_auth_failure(conn, "203.0.113.5", "/entities")
    log_auth_failure(conn, "203.0.113.5", "/search")
    assert count_auth_failures(conn) == 2
    failures = get_auth_failures(conn)
    assert len(failures) == 2
    assert failures[0]["endpoint"] == "/search"  # newest first


def test_link_review_confirm_promotes(conn):
    from storage import insert_entity_link, review_link, get_entity_links

    insert_entity_link(
        conn, entity_a="Amit Sharma", entity_b="Amit Kumar Sharma",
        match_score=93.0, match_status="needs_review",
    )
    ok, message = review_link(
        conn, entity_a="Amit Sharma", entity_b="Amit Kumar Sharma",
        action="confirm", reviewer="ops",
    )
    assert ok, message
    links = get_entity_links(conn, "Amit Sharma")
    assert any(l["match_status"] == "auto_confirmed" for l in links)


def test_link_review_reject_deletes(conn):
    from storage import insert_entity_link, review_link, get_entity_links

    insert_entity_link(
        conn, entity_a="Rohit B", entity_b="Rohit Bansal",
        match_score=92.0, match_status="needs_review",
    )
    ok, _ = review_link(
        conn, entity_a="Rohit B", entity_b="Rohit Bansal",
        action="reject", reviewer="ops",
    )
    assert ok
    assert get_entity_links(conn, "Rohit B") == []


def test_link_review_refuses_confirmed(conn):
    from storage import insert_entity_link, review_link

    insert_entity_link(
        conn, entity_a="Suresh K", entity_b="Suresh Kumar",
        match_score=97.0, match_status="auto_confirmed",
    )
    ok, message = review_link(
        conn, entity_a="Suresh K", entity_b="Suresh Kumar",
        action="reject", reviewer="ops",
    )
    assert not ok
    assert "already confirmed" in message


def test_link_review_refuses_unknown(conn):
    from storage import review_link

    ok, message = review_link(
        conn, entity_a="Ghost A", entity_b="Ghost B",
        action="confirm", reviewer="ops",
    )
    assert not ok