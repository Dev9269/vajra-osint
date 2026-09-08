"""NCRB (National Crime Records Bureau) connector for VAJRA OSINT.

Scrapes NCRB open-data pages / PDFs (e.g. Crime in India statistics and
designated-terrorist / absconder lists posted on ncrb.gov.in). NCRB publishes
statistical reports and wanted lists mostly as PDFs; this connector:

  1. Lists candidate pages from the NCRB site map / latest publications.
  2. Extracts any embedded text that contains named persons.
  3. Stores persons as `person` records, everything else as leads.

Robots.txt checked, 2 s delay, defensive UA, per-run error isolation.
Offline-testable via extract_ncrb_text() (pure on raw bytes/HTML).
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

# Stable NCRB open-data endpoints (public statistics + notices).
NCRB_PAGES = [
    {
        "name": "NCRB Publications",
        "url": "https://www.ncrb.gov.in/all-crime-in-india-publications.html",
        "selector": "a[href$='.pdf'], .publication a, .list li a",
    },
    {
        "name": "NCRB Most Wanted/Absconders",
        "url": "https://www.ncrb.gov.in/",
        "selector": "a[href*='wanted'], a[href*='absconder'], a[href*='notice']",
    },
]

CRIME_KEYWORDS = [
    "wanted", "absconding", "absconder", "fir", "chargesheet", "convicted",
    "accused", "sentence", "fugitive", "murder", "fraud", "smuggling",
    "narcotics", "cybercrime", "terror", "kidnapping", "rape", "bounty",
    "offence", "overdose", "robbery",
]


def _can_scrape(url: str) -> bool:
    parsed = urlparse(url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    try:
        rp = RobotFileParser()
        rp.set_url(robots_url)
        rp.read()
        return rp.can_fetch(USER_AGENT, url)
    except Exception:
        return False


def extract_ncrb_text(html_or_pdf_text: str) -> list[str]:
    """Return text blocks from raw HTML (or a decoded PDF text dump).

    Pure function — offline testable. Handles both HTML containers and
    plain-text chunks.
    """
    if "<" in html_or_pdf_text[:2000]:
        soup = BeautifulSoup(html_or_pdf_text, "html.parser")
        blocks = []
        for tag in soup.find_all(["div", "tr", "p", "li"]):
            text = tag.get_text(" ", strip=True)
            if len(text) > 20:
                blocks.append(text[:800])
        return blocks
    # Plain text dump (from a PDF) — split into sentence-ish chunks.
    import re
    return [c.strip()[:800] for c in re.split(r"\n+", html_or_pdf_text) if len(c.strip()) > 20]


def _matches_keywords(text: str) -> bool:
    low = text.lower()
    return any(kw in low for kw in CRIME_KEYWORDS)


def _fetch(url: str) -> Optional[BeautifulSoup]:
    try:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "html.parser")
    except requests.RequestException as e:
        logger.error("NCRB fetch failed (%s): %s", url, e)
        return None


def _store_text(conn, text: str, source_url: str) -> int:
    inserted = 0
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
                source_type="ncrb",
                source_url=source_url,
            )
            if ok:
                inserted += 1
    else:
        ok = insert_record(
            conn,
            entity_name=text[:200],
            record_type="unresolved_lead",
            detail=text[:500],
            source_type="ncrb",
            source_url=source_url,
        )
        if ok:
            inserted += 1
    return inserted


def sync_ncrb(db_path: Optional[str] = None) -> int:
    conn = get_connection(db_path)
    inserted = 0

    for page in NCRB_PAGES:
        name, url, selector = page["name"], page["url"], page["selector"]
        logger.info("Scraping NCRB page: %s (%s)", name, url)
        if not _can_scrape(url):
            logger.warning("robots.txt disallows %s — skipping", url)
            continue
        soup = _fetch(url)
        if soup is None:
            continue
        # Collect candidate links (PDFs/notices) on the page.
        targets = []
        for a in soup.select(selector):
            href = a.get("href", "")
            text = a.get_text(" ", strip=True)
            if href and text and len(text) > 10:
                if href.startswith("/"):
                    parsed = urlparse(url)
                    href = f"{parsed.scheme}://{parsed.netloc}{href}"
                targets.append((href, text))
        # Limit to a sane number of target documents.
        for href, label in targets[:50]:
            if _matches_keywords(label) or href.lower().endswith(".pdf"):
                inserted += _store_text(conn, label + " " + href, href)
        time.sleep(REQUEST_DELAY)

    conn.close()
    logger.info("NCRB sync complete. %d new records.", inserted)
    return inserted


# ── Self-test ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    sample_html = (
        "<html><body><div><p>Rajesh Kumar, absconding accused, wanted for "
        "cyber fraud in Hyderabad, FIR 221/2026.</p></div></body></html>"
    )
    blocks = extract_ncrb_text(sample_html)
    print(f"Parse test: {len(blocks)} blocks")
    for b in blocks:
        print(f"  {b[:80]}")
    print("Keyword match on sample:", _matches_keywords(sample_html))
    print("NCRB connector ready. Run sync_ncrb() for live sync.")