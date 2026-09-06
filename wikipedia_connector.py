"""Wikipedia connector for VAJRA OSINT aggregator.

Uses Wikipedia's official Action API — NOT raw HTML scraping.
Pulls page titles from relevant categories, then fetches plain-text intro extracts.

NOTE: Wikipedia entries are leads to verify, not primary sources.
They should be cross-referenced against official government records before
any action is taken.
"""

import logging
import time
from typing import Optional

import requests

from storage import get_connection, insert_record

logger = logging.getLogger(__name__)

WIKI_API = "https://en.wikipedia.org/w/api.php"
USER_AGENT = "VAJRA-OSINT/1.0 (SIH 2026 hackathon project; educational use)"
REQUEST_DELAY = 2  # seconds between API calls — Wikipedia allows faster but we're polite

# Categories to pull from — verified to have content on English Wikipedia (Sep 2026).
# 'ifugitives' was removed from this list because it has no direct members.
DEFAULT_CATEGORIES = [
    "Category:Fugitives_wanted_by_India",
    "Category:People_convicted_of_murder_by_India",
    "Category:Indian_people_convicted_of_murder",
]

# Rate-limit: don't hammer the API
_session = requests.Session()
_session.headers.update({"User-Agent": USER_AGENT})


def _get_category_members(category: str, limit: int = 50) -> list[str]:
    """Fetch page titles from a Wikipedia category via Action API."""
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
    """Fetch the plain-text intro extract for a Wikipedia page."""
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
        pages = data.get("query", {}).get("pages", {})
        for page in pages.values():
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
    """Sync wanted/convicted person entries from Wikipedia.

    Returns the number of new records inserted.
    """
    if categories is None:
        categories = DEFAULT_CATEGORIES

    conn = get_connection(db_path)
    inserted = 0

    for category in categories:
        logger.info("Fetching members of %s...", category)
        titles = _get_category_members(category, limit=max_pages_per_category)
        logger.info("Found %d pages in %s", len(titles), category)

        for title in titles:
            time.sleep(REQUEST_DELAY)

            extract = _get_page_extract(title)
            if not extract:
                logger.warning("No extract for %s, skipping", title)
                continue

            # Build the Wikipedia URL for source attribution
            source_url = f"https://en.wikipedia.org/wiki/{title.replace(' ', '_')}"

            # The entity name is the page title (person's name)
            entity_name = title

            # Store the intro extract as the detail
            ok = insert_record(
                conn,
                entity_name=entity_name,
                record_type="person",
                detail=extract[:2000],  # cap at 2000 chars to keep DB lean
                location=None,  # Wikipedia doesn't reliably separate location
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
    print("Running Wikipedia connector test (single category, 3 pages max)...")
    count = sync_wikipedia(
        categories=["Category:Fugitives_wanted_by_India"],
        max_pages_per_category=3,
    )
    print(f"Inserted {count} new records.")
    print("Verify against: https://en.wikipedia.org/wiki/Category:Fugitives_wanted_by_India")
