"""Government portal scraper for VAJRA OSINT aggregator.

CHANGELOG 2026-09-06: rebuilt to parse structured fields (age, gender,
crime_type, status, location, fir_number) from each portal card/detail.

CHANGELOG 2026-09-06 (bugfix round, #6): fallback records now use
record_type 'unresolved_lead' consistently (was 'person' for raw entries).

Template-based — each portal has different HTML, so selectors must be
filled in per target site after inspecting live HTML.

Enforces robots.txt check, 2s rate limiting, descriptive User-Agent,
and per-portal error isolation (one broken portal doesn't kill the run).
"""

import logging
import time
from typing import Optional
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

from storage import get_connection, insert_record
from entity_extraction import extract_persons

logger = logging.getLogger(__name__)

USER_AGENT = "VAJRA-OSINT/1.0 (SIH 2026 hackathon project; educational use)"
REQUEST_DELAY = 2


PORTALS = [
    # {
    #     "name": "Delhi Police - Wanted Persons",
    #     "url": "https://delhipolice.gov.in/wanted-persons",
    #     "selectors": {
    #         "container": ".card, .wanted-item, tr",
    #         "text": ".detail, .description, td",  # full text of the entry
    #     },
    # },
    # Add real portals after inspecting live HTML:
    # {
    #     "name": "State Police - Wanted",
    #     "url": "https://statepolice.gov.in/wanted",
    #     "selectors": {
    #         "container": "...",
    #         "text": "...",
    #     },
    # },
]


def _can_scrape(url: str, user_agent: str = USER_AGENT) -> bool:
    parsed = urlparse(url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    try:
        rp = RobotFileParser()
        rp.set_url(robots_url)
        rp.read()
        return rp.can_fetch(user_agent, url)
    except Exception as e:
        logger.warning("Could not read robots.txt for %s: %s — assuming disallowed", robots_url, e)
        return False


def _fetch_page(url: str) -> Optional[BeautifulSoup]:
    if not _can_scrape(url):
        logger.warning("robots.txt disallows scraping %s — skipping", url)
        return None
    try:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "html.parser")
    except requests.RequestException as e:
        logger.error("Failed to fetch %s: %s", url, e)
        return None


def scrape_portal(portal: dict, db_path: Optional[str] = None) -> int:
    """Scrape a single gov portal, extract structured persons, store them.

    Returns number of new records inserted.
    """
    conn = get_connection(db_path)
    inserted = 0
    name = portal["name"]
    url = portal["url"]
    selectors = portal["selectors"]

    logger.info("Scraping portal: %s (%s)", name, url)

    soup = _fetch_page(url)
    if soup is None:
        conn.close()
        return 0

    containers = soup.select(selectors["container"])
    if not containers:
        logger.warning("No containers found for %s — selectors may need updating", name)
        conn.close()
        return 0

    logger.info("Found %d entries in %s", len(containers), name)

    for container in containers:
        # Grab the full text of the entry
        entry_text = container.get_text(" ", strip=True)
        if len(entry_text) < 5:
            continue

        # Extract structured person records
        persons = extract_persons(entry_text)

        if persons:
            for person in persons:
                ok = insert_record(
                    conn,
                    entity_name=person.name,
                    record_type="person",
                    age=person.age,
                    gender=person.gender,
                    crime_type=person.crime_type,
                    crime_description=person.crime_description,
                    status=person.status,
                    location=person.location,
                    fir_number=person.fir_number,
                    plate_number=person.plate_number,
                    phone_number=person.phone_number,
                    detail=entry_text[:500],
                    source_type="gov_portal",
                    source_url=url,
                )
                if ok:
                    inserted += 1
                    logger.info("Inserted: %s", person.name)
        else:
            # No person detected — store raw entry (if it has a name-like token)
            ok = insert_record(
                conn,
                entity_name=entry_text[:200],
                record_type="unresolved_lead",
                detail=entry_text[:500],
                source_type="gov_portal",
                source_url=url,
            )
            if ok:
                inserted += 1
                logger.info("Inserted raw entry: %s", entry_text[:60])

    conn.close()
    return inserted


def sync_gov_portals(
    portals: Optional[list[dict]] = None,
    db_path: Optional[str] = None,
) -> int:
    """Scrape all configured portals. One failure doesn't stop the others."""
    if portals is None:
        portals = PORTALS

    total_inserted = 0
    for portal in portals:
        try:
            count = scrape_portal(portal, db_path=db_path)
            total_inserted += count
        except Exception as e:
            logger.error("Portal '%s' failed: %s — continuing", portal["name"], e)
        time.sleep(REQUEST_DELAY)

    logger.info("Gov portal sync complete. %d new records.", total_inserted)
    return total_inserted


# ── Self-test ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    print("Running gov portal connector test...")
    if not PORTALS:
        print("No portals configured - add real portal selectors to PORTALS list.")
        print("This module is a template; edit 'PORTALS' with real site selectors.")
    else:
        for portal in PORTALS:
            can = _can_scrape(portal["url"])
            print(f"  {portal['name']}: {'ALLOWED' if can else 'BLOCKED'}")
        count = sync_gov_portals()
        print(f"Inserted {count} new records.")
