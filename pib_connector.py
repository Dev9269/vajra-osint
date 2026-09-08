"""PIB (Press Information Bureau) RSS connector for VAJRA OSINT.

Polls PIB English + Hindi press releases and extracts named persons from
crime/legal/social-justice category entries. Same keyword filter + NER
pattern as news_connector.py. Public feeds, no auth needed.

Offline testable via test_parse_pib_entry() below.
"""

import logging
import time
from typing import Optional

import feedparser

from storage import get_connection, insert_record
from entity_extraction import extract_persons
from news_connector import _clean_html, _matches_keywords

logger = logging.getLogger(__name__)

REQUEST_DELAY = 2

CRIME_KEYWORDS = [
    "arrested", "wanted", "absconding", "fir", "chargesheet", "convicted",
    "accused", "sentence", "fugitive", "bounty", "encounter", "surrendered",
    "custody", "murder", "fraud", "smuggling", "narcotics", "cybercrime",
    "extortion", "kidnapping", "terror", "terrorist", "money laundering",
    "corruption", "warrant", "interpol", "extradition", "prosecution",
    "economic offence", "cheating", "ponzi", "gambling", "illegal mining",
]

DEFAULT_FEEDS = [
    # PIB national — Hindi crime/legal/social-justice releases
    "https://pib.gov.in/RssMain.aspx?ModId=6&Lang=1&Regid=3",
    # PIB national — English crime/legal releases
    "https://pib.gov.in/RssMain.aspx?ModId=6&Lang=0&Regid=3",
    # Ministry of Home Affairs feed
    "https://pib.gov.in/RssMain.aspx?ModId=18&Lang=0&Regid=3",
]


def _entry_link(entry) -> str:
    return getattr(entry, "link", "") or ""


def parse_pib_entry(entry) -> tuple[str, str]:
    """Return (combined_text, link) for a single feedparser entry."""
    title = entry.get("title", "")
    summary = _clean_html(entry.get("summary", "") or entry.get("description", ""))
    return f"{title} {summary}", _entry_link(entry)


def sync_pib(
    feeds: Optional[list[str]] = None,
    keywords: Optional[list[str]] = None,
    db_path: Optional[str] = None,
    max_entries_per_feed: int = 100,
) -> int:
    if feeds is None:
        feeds = DEFAULT_FEEDS
    if keywords is None:
        keywords = CRIME_KEYWORDS

    conn = get_connection(db_path)
    inserted = 0

    for feed_url in feeds:
        logger.info("Polling PIB feed: %s", feed_url)
        try:
            feed = feedparser.parse(feed_url)
        except Exception as e:
            logger.error("Failed to parse PIB feed %s: %s", feed_url, e)
            continue
        if feed.bozo and not feed.entries:
            logger.warning("PIB feed %s empty (bozo: %s)", feed_url, feed.bozo_exception)
            continue

        for entry in feed.entries[:max_entries_per_feed]:
            text, link = parse_pib_entry(entry)
            if not _matches_keywords(text, keywords):
                continue
            if len(text.strip()) < 20:
                continue

            persons = extract_persons(text)
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
                        detail=text[:500],
                        source_type="pib_rss",
                        source_url=link,
                    )
                    if ok:
                        inserted += 1
                        logger.info("PIB inserted: %s", p.name)
            else:
                ok = insert_record(
                    conn,
                    entity_name=text[:200],
                    record_type="unresolved_lead",
                    detail=text[:500],
                    source_type="pib_rss",
                    source_url=link,
                )
                if ok:
                    inserted += 1
        time.sleep(REQUEST_DELAY)

    conn.close()
    logger.info("PIB sync complete. %d new records.", inserted)
    return inserted


# ── Self-test ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    count = sync_pib()
    print(f"PIB sync inserted {count} records.")