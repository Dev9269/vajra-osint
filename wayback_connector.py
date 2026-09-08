"""Wayback Machine dead-link recovery connector for VAJRA OSINT.

Scans entity links (source_url) stored in the DB for HTTP 404s. When a link
is dead, it queries the Wayback Machine CDX API for the closest archived copy
and inserts a `source_url` record pointing to the web.archive.org snapshot.

This is a reconciliation pass — not a live crawler. Run nightly via the
scheduler's case_research job or standalone.

Offline-testable: resolve_dead_links() is pure logic + storage; CDX query
is the only network call.
"""

import logging
import time
from typing import Optional

import requests

from storage import get_connection

logger = logging.getLogger(__name__)

REQUEST_DELAY = 2
USER_AGENT = "VAJRA-OSINT/1.0 (SIH 2026 hackathon project; educational use)"
CDX_API = "https://web.archive.org/cdx/search/cdx"


def check_url_alive(url: str, timeout: int = 10) -> bool:
    """Quick HEAD check; returns True if the URL responds with 2xx/3xx."""
    try:
        resp = requests.head(
            url,
            headers={"User-Agent": USER_AGENT},
            timeout=timeout,
            allow_redirects=True,
        )
        return resp.status_code < 400
    except requests.RequestException:
        return False


def query_wayback(url: str, limit: int = 1) -> Optional[str]:
    """Query the Wayback Machine CDX API for the most recent archive of `url`.

    Returns the web.archive.org URL of the snapshot, or None if not archived.
    """
    try:
        resp = requests.get(
            CDX_API,
            params={"url": url, "output": "json", "limit": limit, "fl": "timestamp,original"},
            headers={"User-Agent": USER_AGENT},
            timeout=15,
        )
        resp.raise_for_status()
        rows = resp.json()
        if len(rows) < 2:
            return None  # first row is headers
        timestamp, original = rows[1][:2]
        return f"https://web.archive.org/web/{timestamp}/{original}"
    except Exception as e:
        logger.error("Wayback CDX query failed for %s: %s", url, e)
        return None


def resolve_dead_links(
    db_path: Optional[str] = None,
    max_checks: int = 50,
) -> dict:
    """Find dead source URLs in entity_links / records and recover via Wayback.

    Returns summary dict with counts. Inserts Wayback snapshots as new records.
    """
    conn = get_connection(db_path)
    # Gather distinct source URLs from records (the authoritative source_url list).
    rows = conn.execute(
        "SELECT DISTINCT source_url FROM records WHERE source_url IS NOT NULL "
        "AND source_url != '' LIMIT ?",
        (max_checks,),
    ).fetchall()

    total = len(rows)
    dead = 0
    recovered = 0

    for row in rows:
        url = row["source_url"]
        # Skip archive.org links already present.
        if "web.archive.org" in url:
            continue
        alive = check_url_alive(url)
        if alive:
            continue

        dead += 1
        archive_url = query_wayback(url)
        if archive_url is None:
            logger.info("No Wayback archive for: %s", url[:80])
            continue

        # Insert a record linking to the archived copy, preserving the original
        # source_url as source_url so the provenance chain stays intact.
        from storage import insert_record
        ok = insert_record(
            conn,
            entity_name="[Wayback Recovery]",
            record_type="unresolved_lead",
            detail=f"Dead link recovered via Wayback Machine. Original: {url}",
            source_type="wayback",
            source_url=archive_url,
            fetched_at=__import__("datetime").datetime.now(
                __import__("datetime").timezone.utc
            ).isoformat(),
        )
        if ok:
            recovered += 1
            logger.info("Recovered dead link: %s -> %s", url[:60], archive_url[:60])
        time.sleep(REQUEST_DELAY)

    conn.close()
    summary = {"checked": total, "dead": dead, "recovered": recovered}
    logger.info("Wayback recovery complete: %s", summary)
    return summary


# ── Self-test ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    # Smoke test: check a known-good and a known-bad URL.
    good = check_url_alive("https://example.com")
    dead = check_url_alive("https://definitely-not-a-real-domain-xyz.com")
    print(f"alive=example.com: {good}")
    print(f"alive=garbage: {dead}")
    cdx = query_wayback("https://example.com")
    print(f"wayback example.com: {cdx}")
    print("Wayback connector ready.")