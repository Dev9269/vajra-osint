"""Scheduler structure + job-wrapping tests (Part 3)."""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture()
def scheduler_module():
    import scheduler
    return scheduler


@pytest.mark.parametrize("job_id", [
    "news_sync", "wikipedia_sync", "gov_portal_sync", "pib_sync",
    "agency_sync", "regional_news_sync", "state_police_wanted",
    "ecourts_sync", "ncrb_sync", "entity_resolution", "case_research",
    "wayback_recovery",
])
def test_scheduler_job_configured(scheduler_module, job_id):
    scheduler = scheduler_module.create_scheduler()
    try:
        job_ids = [job.id for job in scheduler.get_jobs()]
        assert job_id in job_ids
    finally:
        scheduler.remove_all_jobs()


def test_job_wrapper_logs_failure(scheduler_module, caplog):
    def boom():
        raise RuntimeError("connector exploded")

    with caplog.at_level("ERROR", logger="scheduler"):
        result = scheduler_module._run_sync_job("boom", boom)
    assert result is None  # wrapper swallows the exception
    assert "JOB FAILED: boom" in caplog.text


def test_job_wrapper_returns_result(scheduler_module, caplog):
    with caplog.at_level("INFO", logger="scheduler"):
        result = scheduler_module._run_sync_job("ok_job", lambda: {"ok": True})
    assert result == {"ok": True}
    assert "JOB SUCCESS: ok_job" in caplog.text


def test_case_queries_defined_for_all_cases(scheduler_module):
    from cases.ingest_evidence import list_case_dirs

    for case_id in list_case_dirs():
        assert case_id in scheduler_module.CASE_QUERIES
        assert len(scheduler_module.CASE_QUERIES[case_id]) > 0