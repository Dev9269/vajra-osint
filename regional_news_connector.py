"""Regional Hindi/regional-language news RSS connector for VAJRA OSINT.

Polls Hindi and regional-language news feeds that carry crime/legal coverage.
Since the NER pipeline is English-centric, Hindi/Marathi headlines are stored
as `unresolved_lead` records with the full headline and link. They provide
leads that can be manually reviewed or enriched by future multilingual NER.

Public RSS feeds only, no auth needed.
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
    "accused", "sentence", "fugitive", "murder", "fraud", "smuggling",
    "narcotics", "cybercrime", "kidnapping", "terror", "money laundering",
    "corruption", "warrant", "encounter", "custody", "bail",
    # Hindi equivalents (Devanagari)
    "गिरफ्तार", "फरार", "वांछित", "एफआईआर", "दोषी", "हत्या",
    "धोखाधड़ी", "तस्करी", "आतंक", "भ्रष्टाचार", "जमानत",
]

REGIONAL_FEEDS = [
    # Hindi
    ("Hindi", "https://feeds.feedburner.com/ndtvnews-latest"),
    ("Hindi-Dainik", "https://www.jagran.com/rss/crime"),
    # Bengali
    ("Bengali", "https://www.anandabazar.com/rss/crime"),
    # Marathi
    ("Marathi", "https://www.loksatta.com/rss/"),
    # Tamil
    ("Tamil", "https://www.dinamalar.com/rss/crime"),
    # Telugu
    ("Telugu", "https://www.eenadu.net/rss/crime"),
    # Kannada
    ("Kannada", "https://www.vijaykarnataka.com/rss/crime"),
]


def _matches_keywords_regional(text: str) -> bool:
    low = text.lower()
    for kw in CRIME_KEYWORDS:
        if kw in low:
            return True
    return False


def _entry_link(entry) -> str:
    return getattr(entry, "link", "") or ""


def sync_regional_news(
    feeds: Optional[list[tuple[str, str]]] = None,
    db_path: Optional[str] = None,
    max_entries_per_feed: int = 50,
) -> int:
    """Poll regional news feeds, store headlines as leads or persons.

    Returns number of new records inserted.
    """
    if feeds is None:
        feeds = REGIONAL_FEEDS

    conn = get_connection(db_path)
    inserted = 0

    for label, feed_url in feeds:
        logger.info("Polling regional feed [%s]: %s", label, feed_url)
        try:
            feed = feedparser.parse(feed_url)
        except Exception as e:
            logger.error("Regional feed parse failed [%s]: %s", label, e)
            continue
        if feed.bozo and not feed.entries:
            logger.warning("Regional feed [%s] empty (bozo: %s)", label, feed.bozo_exception)
            continue

        for entry in feed.entries[:max_entries_per_feed]:
            title = entry.get("title", "")
            summary = _clean_html(entry.get("summary", "") or entry.get("description", ""))
            combined = f"{title} {summary}"
            link = _entry_link(entry)

            if not _matches_keywords_regional(combined):
                continue
            if len(title.strip()) < 10:
                continue

            # Try English NER first — works for transliterated names.
            persons = extract_persons(combined)
            detail = f"[{label}] {title}. {summary}" if summary else f"[{label}] {title}"

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
                        detail=detail[:500],
                        source_type=f"regional_news_{label}",
                        source_url=link,
                    )
                    if ok:
                        inserted += 1
            else:
                ok = insert_record(
                    conn,
                    entity_name=f"[{label}] {title[:180]}",
                    record_type="unresolved_lead",
                    detail=detail[:500],
                    source_type=f"regional_news_{label}",
                    source_url=link,
                )
                if ok:
                    inserted += 1

        time.sleep(REQUEST_DELAY)

    conn.close()
    logger.info("Regional news sync complete. %d new records.", inserted)
    return inserted


# ── Self-test ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    test_texts = [
        "राजेश शर्मा को गिरफ्तार किया गया है धोखाधड़ी के मामले में",
        "Police arrested Rakesh Verma in Mumbai fraud case",
        "बिहार में फरार अपराधी की तलाश जारी",
        "No crime keywords here",
    ]
    for t in test_texts:
        match = _matches_keywords_regional(t)
        persons = extract_persons(t) if match else []
        names = [p.name for p in persons]
        print(f"  [{'HIT' if match else '---'}] {t[:50]:50} NER: {names}")
    print("Regional news connector ready.")