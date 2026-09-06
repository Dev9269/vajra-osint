"""Scheduler for VAJRA OSINT aggregator.

CHANGELOG 2026-09-06: simplified — connectors take an optional db_path
(unused here; default used). Ran initial sync on startup, then schedule:
  news 4h, wikipedia daily @02:00, gov daily @03:00, resolution nightly @04:00 IST.
"""

import logging
import sys
from datetime import datetime, timezone

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from wikipedia_connector import sync_wikipedia
from news_connector import sync_news
from gov_portal_connector import sync_gov_portals
from entity_resolution import resolve_entities

logger = logging.getLogger(__name__)


def _run_sync_job(job_name: str, func, **kwargs):
    logger.info("JOB START: %s", job_name)
    try:
        result = func(**kwargs)
        logger.info("JOB SUCCESS: %s -> ok=%s", job_name, result)
        return result
    except Exception as e:
        logger.error("JOB FAILED: %s — %s", job_name, e)
        return None


def job_news_sync():
    return _run_sync_job("news_sync", sync_news)


def job_wikipedia_sync():
    return _run_sync_job("wikipedia_sync", sync_wikipedia)


def job_gov_portal_sync():
    return _run_sync_job("gov_portal_sync", sync_gov_portals)


def job_entity_resolution():
    return _run_sync_job("entity_resolution", resolve_entities)


def run_initial_sync():
    logger.info("=== Initial sync on startup ===")
    job_wikipedia_sync()
    job_news_sync()
    job_gov_portal_sync()
    job_entity_resolution()
    logger.info("=== Initial sync complete ===")


def create_scheduler() -> BlockingScheduler:
    scheduler = BlockingScheduler(timezone="Asia/Kolkata")

    scheduler.add_job(job_news_sync, IntervalTrigger(hours=4),
                      id="news_sync", name="News RSS Sync", replace_existing=True)
    scheduler.add_job(job_wikipedia_sync, CronTrigger(hour=2, minute=0),
                      id="wikipedia_sync", name="Wikipedia Sync", replace_existing=True)
    scheduler.add_job(job_gov_portal_sync, CronTrigger(hour=3, minute=0),
                      id="gov_portal_sync", name="Gov Portal Sync", replace_existing=True)
    scheduler.add_job(job_entity_resolution, CronTrigger(hour=4, minute=0),
                      id="entity_resolution", name="Entity Resolution", replace_existing=True)

    return scheduler


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler("vajra_scheduler.log")],
    )
    logger.info("VAJRA Scheduler starting at %s", datetime.now(timezone.utc).isoformat())

    run_initial_sync()

    scheduler = create_scheduler()
    logger.info("Scheduler running. Jobs: news=4h, wikipedia=daily@02:00, gov=daily@03:00, resolution=daily@04:00")
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Scheduler shutting down")
        scheduler.shutdown()


if __name__ == "__main__":
    main()
