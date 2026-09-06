"""Scheduler for VAJRA OSINT aggregator.

APScheduler long-lived process that runs sync jobs on a schedule:
- News sync: every 4 hours
- Wikipedia sync: daily
- Gov portal sync: daily (staggered from Wikipedia)
- Entity resolution: nightly, after all syncs complete

Runs an initial sync immediately on startup.
"""

import logging
import sys
import time
from datetime import datetime, timezone

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

# Connector imports — use actual function names from Agent A's committed files
from wikipedia_connector import sync_wikipedia
from news_connector import sync_news
from gov_portal_connector import sync_gov_portals
from entity_resolution import resolve_entities

logger = logging.getLogger(__name__)


def _run_sync_job(job_name: str, func, **kwargs):
    """Wrapper that logs job start/success/failure."""
    start = time.time()
    logger.info("JOB START: %s", job_name)
    try:
        result = func(**kwargs)
        elapsed = time.time() - start
        logger.info("JOB SUCCESS: %s — %d records in %.1fs", job_name, result, elapsed)
        return result
    except Exception as e:
        elapsed = time.time() - start
        logger.error("JOB FAILED: %s — %s (%.1fs)", job_name, e, elapsed)
        return 0


def job_news_sync():
    """Sync news RSS feeds."""
    return _run_sync_job("news_sync", sync_news)


def job_wikipedia_sync():
    """Sync Wikipedia wanted persons."""
    return _run_sync_job("wikipedia_sync", sync_wikipedia)


def job_gov_portal_sync():
    """Sync government portals."""
    return _run_sync_job("gov_portal_sync", sync_gov_portals)


def job_entity_resolution():
    """Run fuzzy entity resolution."""
    return _run_sync_job("entity_resolution", resolve_entities)


def run_initial_sync():
    """Run all syncs immediately on startup."""
    logger.info("=== Running initial sync on startup ===")
    job_wikipedia_sync()
    job_news_sync()
    job_gov_portal_sync()
    job_entity_resolution()
    logger.info("=== Initial sync complete ===")


def create_scheduler() -> BlockingScheduler:
    """Create and configure the APScheduler."""
    scheduler = BlockingScheduler(timezone="Asia/Kolkata")

    # News: every 4 hours
    scheduler.add_job(
        job_news_sync,
        trigger=IntervalTrigger(hours=4),
        id="news_sync",
        name="News RSS Sync",
        replace_existing=True,
    )

    # Wikipedia: daily at 02:00 IST
    scheduler.add_job(
        job_wikipedia_sync,
        trigger=CronTrigger(hour=2, minute=0),
        id="wikipedia_sync",
        name="Wikipedia Sync",
        replace_existing=True,
    )

    # Gov portals: daily at 03:00 IST (staggered from Wikipedia)
    scheduler.add_job(
        job_gov_portal_sync,
        trigger=CronTrigger(hour=3, minute=0),
        id="gov_portal_sync",
        name="Gov Portal Sync",
        replace_existing=True,
    )

    # Entity resolution: nightly at 04:00 IST (after all syncs)
    scheduler.add_job(
        job_entity_resolution,
        trigger=CronTrigger(hour=4, minute=0),
        id="entity_resolution",
        name="Entity Resolution",
        replace_existing=True,
    )

    return scheduler


def main():
    """Entry point — initial sync then hold to schedule."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler("vajra_scheduler.log"),
        ],
    )

    logger.info("VAJRA Scheduler starting at %s", datetime.now(timezone.utc).isoformat())

    # Run initial sync
    run_initial_sync()

    # Start scheduled jobs
    scheduler = create_scheduler()
    logger.info("Scheduler running. Jobs: news=4h, wikipedia=daily@02:00, gov=daily@03:00, resolution=daily@04:00")

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Scheduler shutting down")
        scheduler.shutdown()


if __name__ == "__main__":
    main()
