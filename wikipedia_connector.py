"""Wikipedia connector for VAJRA OSINT aggregator.

Uses Wikipedia's official Action API — NOT raw HTML scraping.
Pulls page titles from relevant categories, then fetches plain-text intro
extracts and parses them into structured person records.

CHANGELOG 2026-09-06: rebuilt to parse structured fields (age, gender,
crime_type, status, location, fir_number) from each page's intro using
entity_extraction.extract_persons().

NOTE: Wikipedia entries are leads to verify, not primary sources.
They must be cross-referenced against official government records.
"""

import logging
import time
from typing import Optional

import requests

from storage import get_connection, insert_record
from entity_extraction import extract_persons, extract_best_person_name

logger = logging.getLogger(__name__)

WIKI_API = "https://en.wikipedia.org/w/api.php"
USER_AGENT = "VAJRA-OSINT/1.0 (SIH 2026 hackathon project; educational use)"
REQUEST_DELAY = 2

# Verified to have content (Sep 2026)
DEFAULT_CATEGORIES = [
    "Category:Fugitives_wanted_by_India",
    "Category:People_convicted_of_murder_by_India",
    "Category:Indian_people_convicted_of_murder",
]

_session = requests.Session()
_session.headers.update({"User-Agent": USER_AGENT})


def _get_category_members(category: str, limit: int = 50) -> list[str]:
    titles = []
    params = {
        "action": "query",
        "list": "categorymembers",
        "cmtitle": category,
        "cmlimit": str(min(limit, 50)),
        "cmtype": "page",
        "format": "json",
    }
    try:
        resp = _session.get(WIKI_API, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        for member in data.get("query", {}).get("categorymembers", []):
            titles.append(member["title"])
    except requests.RequestException as e:
        logger.error("Failed to fetch category %s: %s", category, e)
    return titles


def _get_page_extract(title: str) -> Optional[str]:
    params = {
        "action": "query",
        "titles": title,
        "prop": "extracts",
        "exintro": "true",
        "explaintext": "true",
        "format": "json",
    }
    try:
        resp = _session.get(WIKI_API, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        for page in data.get("query", {}).get("pages", {}).values():
            extract = page.get("extract", "")
            if extract:
                return extract
    except requests.RequestException as e:
        logger.error("Failed to fetch extract for %s: %s", title, e)
    return None


def sync_wikipedia(
    categories: Optional[list[str]] = None,
    db_path: Optional[str] = None,
    max_pages_per_category: int = 20,
) -> int:
    """Sync entries from Wikipedia, parsing into structured person records.

    Returns number of new records inserted.
    """
    if categories is None:
        categories = DEFAULT_CATEGORIES

    conn = get_connection(db_path)
    inserted = 0

    for category in categories:
        logger.info("Fetching members of %s...", category)
        titles = _get_category_members(category, limit=max_pages_per_category)

        for title in titles:
            time.sleep(REQUEST_DELAY)
            extract = _get_page_extract(title)
            if not extract:
                logger.warning("No extract for %s, skipping", title)
                continue

            source_url = f"https://en.wikipedia.org/wiki/{title.replace(' ', '_')}"

            # Parse structured person data from the intro
            persons = extract_persons(extract)

            if persons:
                # Use the best extracted person name if available
                person = persons[0]
                entity_name = person.name or title
                ok = insert_record(
                    conn,
                    entity_name=entity_name,
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
                    detail=extract[:2000],
                    source_type="wikipedia",
                    source_url=source_url,
                )
            else:
                # No person extracted — store as lead with page title as name
                # (Wikipedia page title is a person's name for these categories)
                ok = insert_record(
                    conn,
                    entity_name=title,
                    record_type="unresolved_lead",
                    detail=extract[:2000],
                    source_type="wikipedia",
                    source_url=source_url,
                )

            if ok:
                inserted += 1
                logger.info("Inserted: %s", title)
            else:
                logger.debug("Duplicate skipped: %s", title)

    conn.close()
    logger.info("Wikipedia sync complete. %d new records inserted.", inserted)
    return inserted


# ── Self-test ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    print("Running Wikipedia connector test (single category, 5 pages max)...")
    count = sync_wikipedia(
        categories=["Category:Fugitives_wanted_by_India"],
        max_pages_per_category=5,
    )
    print(f"Inserted {count} new records.")
