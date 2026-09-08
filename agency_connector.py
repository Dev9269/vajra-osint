"""Financial/agency press-release connector for VAJRA OSINT.

Covers SEBI, ED (Enforcement Directorate), and CBI (Central Bureau of
Investigation) public press-release feeds and listing pages. Uses RSS
where available (SEBI), plain page-scraping elsewhere (ED, CBI). No auth
required; public data only.

Offline-testable: parse functions are pure on raw HTML strings.
"""

import logging
import re
import time
from typing import Optional

import feedparser
import requests
from bs4 import BeautifulSoup

from storage import get_connection, insert_record
from entity_extraction import extract_persons

logger = logging.getLogger(__name__)

REQUEST_DELAY = 2
USER_AGENT = "VAJRA-OSINT/1.0 (SIH 2026 hackathon project; educational use)"

# RSS feeds (SEBI has one, ED/CBI list pages have none).
AGENCY_FEEDS = {
    "SEBI": "https://www.sebi.gov.in/rss.xml",
}

# HTML listing pages for agencies that lack RSS (selectors per site).
AGENCY_PAGES = [
    {
        "name": "ED Press Releases",
        "url": "https://www.ed.gov.in/press-releases",
        "selector": ".view-content .views-row, .press-release-item, article",
    },
    {
        "name": "CBI Press Releases",
        "url": "https://cbi.gov.in/press-releases",
        "selector": ".news-item, .press-release, table.table tbody tr",
    },
    {
        "name": "SEBI Orders (recent)",
        "url": "https://www.sebi.gov.in/sebiweb/home/HomeAction.do?doOrder=yes&intmId=33",
        "selector": ".list-data tr, table tbody tr",
    },
]

CRIME_KEYWORDS = [
    "arrested", "wanted", "fraud", "money laundering", "bribery", "corruption",
    "ponzi", "scam", "embezzlement", "shell company", "securities fraud",
    "insider trading", "prohibition order", "disgorgement", "attachment",
    "prevention of money", "foreign exchange violation", "benami",
    "prosecution", "chargesheet", "custody", "interpol", "extradition",
    "narcotics", "hawala", "terror financing", "sanctions",
]


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()[:800]


def parse_page_blocks(html: str) -> list[str]:
    """Extract listing-row text blocks from an agency press-release page."""
    soup = BeautifulSoup(html, "html.parser")
    blocks = []
    for tag in soup.select("article, .views-row, .press-release-item, .list-data tr, table tbody tr"):
        text = tag.get_text(" ", strip=True)
        if len(text) > 20:
            blocks.append(_clean(text))
    return blocks


def _matches_keywords(text: str) -> bool:
    low = text.lower()
    return any(kw in low for kw in CRIME_KEYWORDS)


def _fetch(url: str) -> Optional[BeautifulSoup]:
    try:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "html.parser")
    except requests.RequestException as e:
        logger.error("Agency page fetch failed (%s): %s", url, e)
        return None


def sync_agency_feeds(db_path: Optional[str] = None) -> int:
    """Poll RSS feeds for SEBI and crime keywords."""
    conn = get_connection(db_path)
    inserted = 0
    for label, url in AGENCY_FEEDS.items():
        logger.info("Polling %s feed: %s", label, url)
        try:
            feed = feedparser.parse(url)
        except Exception as e:
            logger.error("Feed parse failed %s: %s", label, e)
            continue
        for entry in feed.entries[:100]:
            title = entry.get("title", "")
            summary = re.sub(r"<[^>]+>", "", entry.get("summary", "") or entry.get("description", ""))
            text = f"{title} {summary}"
            link = getattr(entry, "link", url)
            if not _matches_keywords(text):
                continue
            persons = extract_persons(text)
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
                    source_type="agency_rss",
                    source_url=link,
                )
                if ok:
                    inserted += 1
        time.sleep(REQUEST_DELAY)
    conn.close()
    return inserted


def sync_agency_pages(db_path: Optional[str] = None) -> int:
    """Scrape static listing pages for ED/CBI/SEBI orders."""
    conn = get_connection(db_path)
    inserted = 0
    for page in AGENCY_PAGES:
        logger.info("Scraping %s: %s", page["name"], page["url"])
        soup = _fetch(page["url"])
        if soup is None:
            continue
        containers = soup.select(page["selector"])
        for tag in containers:
            text = _clean(tag.get_text(" ", strip=True))
            if len(text) < 20 or not _matches_keywords(text):
                continue
            persons = extract_persons(text)
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
                    source_type="agency_page",
                    source_url=page["url"],
                )
                if ok:
                    inserted += 1
        time.sleep(REQUEST_DELAY)
    conn.close()
    return inserted


def sync_agency(db_path: Optional[str] = None) -> int:
    total = sync_agency_feeds(db_path) + sync_agency_pages(db_path)
    logger.info("Agency sync complete. %d new records.", total)
    return total


# ── Self-test ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    sample = ("Enforcement Directorate arrested Rakesh Verma in a money "
              "laundering case linked to a shell company in Mumbai.")
    print("Keyword match:", _matches_keywords(sample))
    print("NER:", [p.name for p in extract_persons(sample)])
    print("Agency connector ready.")