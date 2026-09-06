"""Government portal scraper for VAJRA OSINT aggregator.

Template-based scraper — each portal has different HTML structure,
so selectors must be filled in per target site after inspecting live HTML.

Enforces:
- robots.txt check before scraping
- 2+ second rate limiting between requests
- Descriptive User-Agent
- Graceful error handling per portal (one broken portal ≠ whole sync dies)
"""

import logging
import time
from typing import Optional
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

from storage import get_connection, insert_record

logger = logging.getLogger(__name__)

USER_AGENT = "VAJRA-OSINT/1.0 (SIH 2026 hackathon project; educational use)"
REQUEST_DELAY = 2  # seconds between requests to same host


# ── Portal Definitions ──────────────────────────────────────────────────────
# Each portal is a dict with:
#   name: human-readable label
#   url: base URL of the wanted-persons page
#   selectors: CSS selectors for extracting data
#     - container: selector for each person/card element
#     - name: selector for the person's name within the container
#     - detail: selector for charges/description
#     - location: selector for location (optional)
# To add a new portal: inspect the live HTML, fill in selectors, test.
# ─────────────────────────────────────────────────────────────────────────────

PORTALS = [
    {
        "name": "Delhi Police — Wanted Persons",
        "url": "https://delhipolice.gov.in/wanted-persons",
        "selectors": {
            "container": ".card, .wanted-item, tr",
            "name": "h3, h4, .name, td:first-child",
            "detail": ".detail, .charges, td:nth-child(2)",
            "location": ".location, td:nth-child(3)",
        },
    },
    {
        "name": "NCRB — Most Wanted",
        "url": "https://ncrb.gov.in/most-wanted",
        "selectors": {
            "container": ".card, .list-item, tr",
            "name": "h3, h4, .name, td:first-child",
            "detail": ".detail, .description, td:nth-child(2)",
            "location": ".location, td:nth-child(3)",
        },
    },
    # Add more state portals here after inspecting their HTML structure.
    # Format:
    # {
    #     "name": "State Police — Wanted",
    #     "url": "https://statepolice.gov.in/wanted",
    #     "selectors": {
    #         "container": "...",
    #         "name": "...",
    #         "detail": "...",
    #         "location": "...",
    #     },
    # },
]


# ── robots.txt + Rate Limiting ──────────────────────────────────────────────

def _can_scrape(url: str, user_agent: str = USER_AGENT) -> bool:
    """Check robots.txt programmatically before scraping."""
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
    """Fetch a page with rate limiting and User-Agent."""
    if not _can_scrape(url):
        logger.warning("robots.txt disallows scraping %s — skipping", url)
        return None

    try:
        resp = requests.get(
            url,
            headers={"User-Agent": USER_AGENT},
            timeout=30,
        )
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "html.parser")
    except requests.RequestException as e:
        logger.error("Failed to fetch %s: %s", url, e)
        return None


# ── Portal Scraper ──────────────────────────────────────────────────────────

def scrape_portal(portal: dict, db_path: Optional[str] = None) -> int:
    """Scrape a single government portal and store records.

    Returns the number of new records inserted.
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

    # Find all person/card containers
    containers = soup.select(selectors["container"])
    if not containers:
        logger.warning("No containers found for %s — selectors may need updating", name)
        conn.close()
        return 0

    logger.info("Found %d entries in %s", len(containers), name)

    for container in containers:
        # Extract name
        name_el = container.select_one(selectors["name"])
        entity_name = name_el.get_text(strip=True) if name_el else None

        if not entity_name or len(entity_name) < 2:
            continue

        # Extract detail/charges
        detail_el = container.select_one(selectors.get("detail", ""))
        detail = detail_el.get_text(strip=True) if detail_el else None

        # Extract location (optional)
        location_el = container.select_one(selectors.get("location", ""))
        location = location_el.get_text(strip=True) if location_el else None

        # Store with source attribution
        ok = insert_record(
            conn,
            entity_name=entity_name,
            record_type="person",
            detail=detail,
            location=location,
            source_type="gov_portal",
            source_url=url,
        )
        if ok:
            inserted += 1
            logger.info("Inserted: %s", entity_name)

    conn.close()
    return inserted


def sync_gov_portals(
    portals: Optional[list[dict]] = None,
    db_path: Optional[str] = None,
) -> int:
    """Scrape all configured government portals.

    Returns total new records inserted.
    One portal failing does NOT stop the others.
    """
    if portals is None:
        portals = PORTALS

    total_inserted = 0

    for portal in portals:
        try:
            count = scrape_portal(portal, db_path=db_path)
            total_inserted += count
        except Exception as e:
            logger.error("Portal '%s' failed with error: %s — continuing to next", portal["name"], e)
        time.sleep(REQUEST_DELAY)

    logger.info("Gov portal sync complete. %d total new records inserted.", total_inserted)
    return total_inserted


# ── Self-test ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    print("Running gov portal connector test...")
    print("Checking robots.txt for each portal...")

    for portal in PORTALS:
        can = _can_scrape(portal["url"])
        print(f"  {portal['name']}: {'ALLOWED' if can else 'BLOCKED by robots.txt'}")

    print("\nAttempting scrape (portals that allow it)...")
    count = sync_gov_portals()
    print(f"Inserted {count} new records.")
    print("\nTo add a new portal:")
    print("  1. Inspect the live HTML at the portal URL")
    print("  2. Fill in the CSS selectors in the PORTALS list")
    print("  3. Verify robots.txt allows scraping")
    print("  4. Run this test again to verify extraction")
