"""News RSS connector for VAJRA OSINT aggregator.

CHANGELOG 2026-09-06: rebuilt to run entity extraction on each headline +
summary and store the extracted PERSON as entity_name instead of storing
the whole headline as the name. Structured fields (crime_type, status,
location, fir_number) extracted too.

Stores only syndicated headlines/summaries from public RSS feeds — never
fetches full article HTML (copyright-safe, always links to original).
"""

import logging
import re
import time
from typing import Optional

import feedparser

from storage import get_connection, insert_record
from entity_extraction import extract_persons

logger = logging.getLogger(__name__)

REQUEST_DELAY = 2

DEFAULT_KEYWORDS = [
    "arrested", "wanted", "absconding", "fir", "chargesheet", "convicted",
    "sentence", "fugitive", "bounty", "interpol", "encounter", "surrendered",
    "acquitted", "bail", "custody", "police", "crime", "murder", "fraud",
    "smuggling", "narcotics", "cybercrime", "drug", "extortion", "kidnapping",
    "terror", "fired from", "warrant",
]

DEFAULT_FEEDS = [
    "https://feeds.feedburner.com/ndtvnews-latest",
    "https://timesofindia.indiatimes.com/rssfeedstopstories.cms",
    "https://www.thehindu.com/news/national/feeder/default.rss",
    "https://www.deccanherald.com/rss/crime",
    "https://indianexpress.com/section/india/feed/",
]


def _matches_keywords(text: str, keywords: list[str]) -> bool:
    text_lower = text.lower()
    return any(kw.lower() in text_lower for kw in keywords)


def _clean_html(html_text: str) -> str:
    clean = re.sub(r"<[^>]+>", "", html_text)
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean[:500]


def sync_news(
    feeds: Optional[list[str]] = None,
    keywords: Optional[list[str]] = None,
    db_path: Optional[str] = None,
    max_entries_per_feed: int = 100,
) -> int:
    """Poll news RSS feeds, extract structured person records.

    Returns number of new records inserted.
    """
    if feeds is None:
        feeds = DEFAULT_FEEDS
    if keywords is None:
        keywords = DEFAULT_KEYWORDS

    conn = get_connection(db_path)
    inserted = 0

    for feed_url in feeds:
        logger.info("Polling feed: %s", feed_url)
        try:
            feed = feedparser.parse(feed_url)
        except Exception as e:
            logger.error("Failed to parse feed %s: %s", feed_url, e)
            continue

        if feed.bozo and not feed.entries:
            logger.warning("Feed %s returned no entries (bozo: %s)", feed_url, feed.bozo_exception)
            continue

        for entry in feed.entries[:max_entries_per_feed]:
            title = entry.get("title", "")
            summary_raw = entry.get("summary", "") or entry.get("description", "")
            summary = _clean_html(summary_raw)
            link = entry.get("link", "")
            combined_text = f"{title} {summary}"

            if not _matches_keywords(combined_text, keywords):
                continue
            if len(title.strip()) < 10:
                continue

            # Extract structured person records from the headline+summary
            persons = extract_persons(combined_text)

            # Include the crime/story context as detail
            detail = f"{title}. {summary}" if summary else title

            if persons:
                # Store each extracted person with source citation
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
                        detail=detail[:500],
                        source_type="news",
                        source_url=link,
                    )
                    if ok:
                        inserted += 1
                        logger.info("Inserted person: %s (%s)", person.name, link[:60])
            else:
                # No person extracted but keyword matched — store headline as
                # a lead record with detail context (no fabricated name)
                ok = insert_record(
                    conn,
                    entity_name=title[:200],
                    record_type="news_headline",
                    detail=detail[:500],
                    source_type="news",
                    source_url=link,
                )
                if ok:
                    inserted += 1
                    logger.info("Inserted headline (no person found): %s", title[:60])

        time.sleep(REQUEST_DELAY)

    conn.close()
    logger.info("News sync complete. %d new records inserted.", inserted)
    return inserted


# ── Self-test ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    print("Running news connector test (1 feed)...")
    count = sync_news(
        feeds=[DEFAULT_FEEDS[0]],
        keywords=["arrested", "wanted", "fir", "convicted", "murder", "fraud"],
        max_entries_per_feed=50,
    )
    print(f"Inserted {count} new records.")
