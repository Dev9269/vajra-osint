"""News RSS connector for VAJRA OSINT aggregator.

Polls configurable RSS feed URLs, filters by crime-related keywords,
and stores headlines + short summaries with full source attribution.

Does NOT fetch or store full article HTML — only syndicated feed content.
"""

import logging
import re
import time
from datetime import datetime, timezone
from typing import Optional

import feedparser

from storage import get_connection, insert_record

logger = logging.getLogger(__name__)

REQUEST_DELAY = 2  # seconds between feed fetches

# Keywords that indicate a wanted/convicted person story
DEFAULT_KEYWORDS = [
    "arrested",
    "wanted",
    "absconding",
    "fir",
    "chargesheet",
    "convicted",
    "sentence",
    "fugitive",
    "bounty",
    "interpol",
    "encounter",
    "surrendered",
    "acquitted",
    "bail",
    "custody",
    "police",
    "crime",
    "murder",
    "fraud",
    "smuggling",
    "narcotics",
    "cybercrime",
]

# Default Indian news RSS feeds (publicly syndicated)
DEFAULT_FEEDS = [
    # National
    "https://feeds.feedburner.com/ndtvnews-latest",
    "https://timesofindia.indiatimes.com/rssfeedstopstories.cms",
    "https://www.thehindu.com/news/national/feeder/default.rss",
    # Crime/security focused
    "https://www.deccanherald.com/rss/crime",
    "https://indianexpress.com/section/india/feed/",
]

USER_AGENT = "VAJRA-OSINT/1.0 (SIH 2026 hackathon project; educational use)"


def _matches_keywords(text: str, keywords: list[str]) -> bool:
    """Check if any keyword appears in the text (case-insensitive)."""
    text_lower = text.lower()
    return any(kw.lower() in text_lower for kw in keywords)


def _clean_html(html_text: str) -> str:
    """Strip HTML tags from feed summary content."""
    clean = re.sub(r"<[^>]+>", "", html_text)
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean[:500]  # cap summary length


def sync_news(
    feeds: Optional[list[str]] = None,
    keywords: Optional[list[str]] = None,
    db_path: Optional[str] = None,
) -> int:
    """Poll RSS feeds and store keyword-matched entries.

    Returns the number of new records inserted.
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

        for entry in feed.entries:
            title = entry.get("title", "")
            summary_raw = entry.get("summary", "") or entry.get("description", "")
            summary = _clean_html(summary_raw)
            link = entry.get("link", "")

            combined_text = f"{title} {summary}"

            if not _matches_keywords(combined_text, keywords):
                continue

            # Extract entity names from the title — we store the headline
            # as a detail record. Entity extraction (NER) is Agent B's job.
            # For now, store the headline itself as an unextracted record.
            # Agent B's entity_extraction.py will process these later.
            entity_name = title  # temporary — will be refined by NER

            # Only store if we have a meaningful title
            if len(title.strip()) < 10:
                continue

            ok = insert_record(
                conn,
                entity_name=entity_name,
                record_type="news_headline",
                detail=f"{title}. {summary}" if summary else title,
                location=None,
                source_type="news",
                source_url=link,
            )
            if ok:
                inserted += 1
                logger.info("Inserted: %s", title[:80])

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
        keywords=["arrested", "wanted", "fir", "convicted"],
    )
    print(f"Inserted {count} new records.")
