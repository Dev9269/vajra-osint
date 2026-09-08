"""eCourts case-status connector for VAJRA OSINT.

Searches the eCourts public case-status API (names search) for wanted
persons. Returns are keyed by party name; the connector runs
entity_extraction on the case-text blob and stores whatever structured
person records it finds.

Offline-testable: parse_ecourts_html() / parse_ecourts_api_response() are
pure functions. Live fetch requires no auth but is rate-limited (2s).

NOTE: eCourts is intermittently down / slow — failures are logged and
swallowed so the scheduler keeps running.
"""

import logging
import time
from typing import Optional

import requests
from bs4 import BeautifulSoup

from storage import get_connection, insert_record
from entity_extraction import extract_persons

logger = logging.getLogger(__name__)

REQUEST_DELAY = 2
USER_AGENT = "VAJRA-OSINT/1.0 (SIH 2026 hackathon project; educational use)"

# eCourts public API endpoint (no auth required).
_BASE = "https://services.ecourts.gov.in/ecourtindia_v6/"


def _fetch_page(url: str) -> Optional[BeautifulSoup]:
    try:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "html.parser")
    except requests.RequestException as e:
        logger.error("eCourts fetch failed: %s", e)
        return None


def parse_ecourts_html(html: str) -> list[str]:
    """Extract text blocks from a raw eCourts search-results HTML page.

    Each block is a free-text blob representing one case that we hand off
    to NER.  Returns list of text strings (possibly empty).
    """
    soup = BeautifulSoup(html, "html.parser")
    # eCourts wraps case rows in <div class="court-listing"> or similar.
    containers = (
        soup.select(".court-listing, .case-details, table.table-responsive tr")
    )
    texts = []
    for c in containers:
        text = c.get_text(" ", strip=True)
        if len(text) > 20:
            texts.append(text[:800])
    return texts


def search_by_name(
    name: str,
    court_code: str = "0",  # 0 = all courts
    case_type: str = "",
) -> int:
    """Search eCourts by party name and store results. Returns insert count."""
    conn = get_connection()
    inserted = 0

    try:
        # POST to the API with the party-name search.
        payload = {
            "searchType": "PartyName",
            "searchText": name,
            "courtComplexCode": court_code,
        }
        resp = requests.post(
            _BASE,
            data=payload,
            headers={"User-Agent": USER_AGENT},
            timeout=30,
        )
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        case_blocks = (
            soup.select(".court-listing, .case-details, .list-data, table tr")
        )
    except requests.RequestException as e:
        logger.error("eCourts API search failed for '%s': %s", name, e)
        return 0
    except Exception as e:
        logger.error("eCourts parse error for '%s': %s", name, e)
        return 0

    source_url = f"{_BASE}SearchByParty.php?name={name}"
    for block in case_blocks:
        text = block.get_text(" ", strip=True)
        if len(text) < 20:
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
                source_type="ecourts",
                source_url=source_url,
            )
            if ok:
                inserted += 1
                logger.info("eCourts inserted: %s", p.name)

    conn.close()
    return inserted


def sync_ecourts(
    names: Optional[list[str]] = None,
    db_path: Optional[str] = None,
) -> int:
    """Run a batch of party-name searches on eCourts.

    names defaults to distinct entity names already in the DB (re-check
    records for possible case-status enrichment).
    """
    if names is None:
        from storage import get_distinct_entity_names
        conn = get_connection(db_path)
        names = get_distinct_entity_names(conn)
        conn.close()

    total = 0
    for name in names:
        try:
            total += search_by_name(name)
        except Exception as e:
            logger.error("eCourts search failed for '%s': %s", name, e)
        time.sleep(REQUEST_DELAY)

    logger.info("eCourts sync complete. %d new records.", total)
    return total


# ── Self-test ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    sample_html = """
    <div class="court-listing">Ramesh Kumar, S/o Raghunath, Age 45, in FIR
    321/2025 for fraud at Mumbai Sessions Court</div>
    """
    texts = parse_ecourts_html(sample_html)
    print(f"Parse test: {len(texts)} blocks")
    for t in texts:
        print(f"  {t[:80]}")
    print("eCourts connector ready. Run sync_ecourts() for live search.")