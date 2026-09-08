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
from entity_extraction import report_spacy_model
from web_search import search_case
from fir_scraper import check_case_documents
from cases.ingest_evidence import ingest_case_evidence, list_case_dirs
from pib_connector import sync_pib
from agency_connector import sync_agency
from regional_news_connector import sync_regional_news
from ecourts_connector import sync_ecourts
from state_police_wanted import sync_state_police_wanted
from wayback_connector import resolve_dead_links
from ncrb_connector import sync_ncrb

logger = logging.getLogger(__name__)

# Research queries per case — broad Duck-typed: who, what, where, outcome.
CASE_QUERIES = {
    "CASE-001": [
        "Chinese loan app money laundering ED Kerala arrests",
        "Kerala cluster case mule bank accounts ED chargesheet",
        "fake Chinese loan app scam ED prosecution",
    ],
    "CASE-002": [
        "Mundra port drug haul NIA heroin seizure 3000 kg",
        "Mundra drug case NIA LeT terror funding",
        "Harpreet Talwar Kabir Mundra heroin case court",
    ],
}


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


def job_pib_sync():
    return _run_sync_job("pib_sync", sync_pib)


def job_agency_sync():
    return _run_sync_job("agency_sync", sync_agency)


def job_regional_news_sync():
    return _run_sync_job("regional_news_sync", sync_regional_news)


def job_state_police_wanted():
    return _run_sync_job("state_police_wanted", sync_state_police_wanted)


def job_ecourts_sync():
    return _run_sync_job("ecourts_sync", sync_ecourts)


def job_wayback_recovery():
    return _run_sync_job("wayback_recovery", resolve_dead_links)


def job_ncrb_sync():
    return _run_sync_job("ncrb_sync", sync_ncrb)


def job_entity_resolution():
    return _run_sync_job("entity_resolution", resolve_entities)


def job_case_research():
    """Daily: re-ingest case evidence, re-run legal web research + FIR availability."""
    results = {}
    for case_id in list_case_dirs():
        ingested = _run_sync_job(f"{case_id} evidence_ingest", ingest_case_evidence, case_id=case_id)
        checked = _run_sync_job(f"{case_id} fir_check", check_case_documents, case_id=case_id)
        queried = _run_sync_job(
            f"{case_id} web_research",
            search_case,
            case_id=case_id,
            queries=CASE_QUERIES.get(case_id, [case_id]),
        )
        results[case_id] = {"evidence": ingested, "fir_checked": checked, "references": queried}
    return results


def run_initial_sync():
    logger.info("=== Initial sync on startup ===")
    job_wikipedia_sync()
    job_news_sync()
    job_gov_portal_sync()
    job_pib_sync()
    job_agency_sync()
    job_regional_news_sync()
    job_state_police_wanted()
    job_ecourts_sync()
    job_ncrb_sync()
    job_entity_resolution()
    job_case_research()
    job_wayback_recovery()
    logger.info("=== Initial sync complete ===")


def create_scheduler() -> BlockingScheduler:
    scheduler = BlockingScheduler(timezone="Asia/Kolkata")

    scheduler.add_job(job_news_sync, IntervalTrigger(hours=4),
                      id="news_sync", name="News RSS Sync", replace_existing=True)
    scheduler.add_job(job_wikipedia_sync, CronTrigger(hour=2, minute=0),
                      id="wikipedia_sync", name="Wikipedia Sync", replace_existing=True)
    scheduler.add_job(job_gov_portal_sync, CronTrigger(hour=3, minute=0),
                      id="gov_portal_sync", name="Gov Portal Sync", replace_existing=True)
    scheduler.add_job(job_pib_sync, CronTrigger(hour=3, minute=30),
                      id="pib_sync", name="PIB RSS Sync", replace_existing=True)
    scheduler.add_job(job_agency_sync, CronTrigger(hour=4, minute=0),
                      id="agency_sync", name="Agency (SEBI/ED/CBI) Sync", replace_existing=True)
    scheduler.add_job(job_regional_news_sync, IntervalTrigger(hours=6),
                      id="regional_news_sync", name="Regional News Sync", replace_existing=True)
    scheduler.add_job(job_state_police_wanted, CronTrigger(hour=4, minute=30),
                      id="state_police_wanted", name="State Police Wanted", replace_existing=True)
    scheduler.add_job(job_ecourts_sync, IntervalTrigger(hours=12),
                      id="ecourts_sync", name="eCourts Sync", replace_existing=True)
    scheduler.add_job(job_ncrb_sync, CronTrigger(hour=4, minute=45),
                      id="ncrb_sync", name="NCRB Sync", replace_existing=True)
    scheduler.add_job(job_entity_resolution, CronTrigger(hour=5, minute=0),
                      id="entity_resolution", name="Entity Resolution", replace_existing=True)
    scheduler.add_job(job_case_research, CronTrigger(hour=5, minute=30),
                      id="case_research", name="Case Evidence+Web Research", replace_existing=True)
    scheduler.add_job(job_wayback_recovery, CronTrigger(hour=6, minute=0),
                      id="wayback_recovery", name="Dead Link Recovery", replace_existing=True)

    return scheduler


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler("vajra_scheduler.log")],
    )
    logger.info("VAJRA Scheduler starting at %s", datetime.now(timezone.utc).isoformat())

    # Startup NER model check — visible WARNING if en_core_web_trf missing.
    report_spacy_model()

    run_initial_sync()

    scheduler = create_scheduler()
    logger.info("Scheduler running. Jobs: news=4h, wikipedia=daily@02:00, gov=daily@03:00, resolution=daily@04:00, case_research=daily@05:30")
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Scheduler shutting down")
        scheduler.shutdown()


if __name__ == "__main__":
    main()
