"""State police wanted-persons pages for VAJRA OSINT.

Scrapes static 'wanted persons' pages from state police websites. These
are the most stable government sources — usually a table of cards with
photos, names, crimes, and reward amounts.

Uses the gov_portal_connector pattern: robots.txt check, 2 s delay,
per-portal isolation. Connectors are added to PORTALS as live HTML is
inspected. TODO: selectors need filling in per site after manual audit.
"""

import logging
import time
from typing import Optional

from gov_portal_connector import (
    REQUEST_DELAY,
    USER_AGENT,
    PORTALS,
    _can_scrape,
    _fetch_page,
)
from storage import get_connection, insert_record
from entity_extraction import extract_persons

logger = logging.getLogger(__name__)

WANTED_PORTALS = [
    # ─── Delhi Police ────────────────────────────────────────────────────
    {
        "name": "Delhi Police - Most Wanted",
        "url": "https://delhipolice.gov.in/most-wanted",
        "selectors": {
            "container": ".views-row, .card, tr.wanted-person",
            "text": ".views-field, .card-body, td",
        },
    },
    # ─── Maharashtra Anti-Terrorism Squad ───────────────────────────────
    {
        "name": "Maharashtra ATS - Wanted",
        "url": "https://maharashtra.gov.in/ats/wanted",
        "selectors": {
            "container": ".item, .row, tr",
            "text": ".description, .detail, td",
        },
    },
    # ─── UP Police ─────────────────────────────────────────────────────
    {
        "name": "UP Police - History Sheeters",
        "url": "https://uppolice.gov.in/pages/en/top/criminals/history-sheeter",
        "selectors": {
            "container": ".card, .panel, tr",
            "text": ".card-body, .panel-body, td",
        },
    },
    # ─── NIA Most Wanted ────────────────────────────────────────────────
    {
        "name": "NIA Most Wanted Terrorists",
        "url": "https://www.nia.gov.in/most-wanted.htm",
        "selectors": {
            "container": ".table-content, .views-row, table tbody tr",
            "text": "td, .field-content",
        },
    },
    # ─── CBI Wanted ─────────────────────────────────────────────────────
    {
        "name": "CBI Most Wanted",
        "url": "https://cbi.gov.in/what-is-new/most-wanted",
        "selectors": {
            "container": ".views-row, .table-content tr, article",
            "text": ".field-content, td, .body",
        },
    },
    # ─── Rajasthan Police ──────────────────────────────────────────────
    {
        "name": "Rajasthan Police - Wanted",
        "url": "https://police.rajasthan.gov.in/wanted-persons",
        "selectors": {
            "container": ".card, .row, tr",
            "text": ".card-text, .detail, td",
        },
    },
    # ─── Tamil Nadu Police ─────────────────────────────────────────────
    {
        "name": "Tamil Nadu CB-CID - Wanted",
        "url": "https://www.tnpolice.gov.in/cbcid-wanted",
        "selectors": {
            "container": ".card, .list-item, tr",
            "text": ".card-body, .detail, td",
        },
    },
    # ─── Karnataka Police ──────────────────────────────────────────────
    {
        "name": "Karnataka CCB - Most Wanted",
        "url": "https://karnataka.gov.in/ccb/most-wanted",
        "selectors": {
            "container": ".card, .panel, tr",
            "text": ".card-text, .panel-body, td",
        },
    },
]


def scrape_wanted_page(
    portal: dict,
    db_path: Optional[str] = None,
) -> int:
    """Scrape a single state-police wanted page, extract persons, store them.

    Returns number of new records inserted.
    """
    conn = get_connection(db_path)
    inserted = 0
    name, url, selectors = portal["name"], portal["url"], portal["selectors"]

    if not _can_scrape(url):
        logger.warning("robots.txt disallows %s — skipping", url)
        conn.close()
        return 0

    soup = _fetch_page(url)
    if soup is None:
        conn.close()
        return 0

    containers = soup.select(selectors["container"])
    if not containers:
        logger.warning("No entries found on %s — selectors may need updating", name)
        conn.close()
        return 0

    logger.info("Found %d entries on %s", len(containers), name)

    for container in containers:
        entry_text = container.get_text(" ", strip=True)
        if len(entry_text) < 5:
            continue
        persons = extract_persons(entry_text)
        if persons:
            for p in persons:
                ok = insert_record(
                    conn,
                    entity_name=p.name,
                    record_type="person",
                    age=p.age, gender=p.gender,
                    crime_type=p.crime_type,
                    crime_description=p.crime_description,
                    status=p.status, location=p.location,
                    fir_number=p.fir_number,
                    plate_number=p.plate_number,
                    phone_number=p.phone_number,
                    detail=entry_text[:500],
                    source_type="state_police_wanted",
                    source_url=url,
                )
                if ok:
                    inserted += 1
        else:
            ok = insert_record(
                conn,
                entity_name=entry_text[:200],
                record_type="unresolved_lead",
                detail=entry_text[:500],
                source_type="state_police_wanted",
                source_url=url,
            )
            if ok:
                inserted += 1

    conn.close()
    return inserted


def sync_state_police_wanted(
    portals: Optional[list[dict]] = None,
    db_path: Optional[str] = None,
) -> int:
    """Scrape all configured state police wanted pages.

    One portal failure doesn't stop the rest.
    """
    if portals is None:
        portals = WANTED_PORTALS

    total = 0
    for portal in portals:
        try:
            total += scrape_wanted_page(portal, db_path=db_path)
        except Exception as e:
            logger.error("Wanted page '%s' failed: %s — continuing", portal["name"], e)
        time.sleep(REQUEST_DELAY)

    logger.info("State police wanted sync complete. %d new records.", total)
    return total


# ── Self-test ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    print(f"Configured {len(WANTED_PORTALS)} state police wanted portals.")
    for p in WANTED_PORTALS:
        can = _can_scrape(p["url"])
        print(f"  {p['name']}: {'ALLOWED' if can else 'BLOCKED'}")
    print("Run sync_state_police_wanted() for live scraping.")