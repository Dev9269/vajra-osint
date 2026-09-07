"""FIR / official-document availability checker for VAJRA.

For each case's evidence marked official_document (FIR, NIA/ED/DRI/PIB
press releases, court PDFs), tries to fetch the public URL:

  - verifies HTTP 200 + application/pdf (or HTML page for non-PDF links)
  - reads only a capped header chunk (never stores the file)
  - extracts first-page text for attribution (FIR title/station when the
    PDF text is uncompressed enough to read)
  - records `available` + a short `meta` summary in case_evidence

Legend of "scraping": we only check availability and attribution of the
official public link. The tool keeps links, never the document bytes, and
never stores private victim details. Court/prosecution status is not
converted into convictions.

CHANGELOG 2026-09-07: new feature — FIR copies surfaced as verified,
official-source links inside the case/entity view.
"""

import logging
import re
import time
from typing import Optional
from urllib.parse import urlparse

import requests

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from storage import get_connection, get_case_evidence  # noqa: E402

logger = logging.getLogger(__name__)

USER_AGENT = "VAJRA-OSINT/1.0 (SIH 2026 hackathon project; educational use)"
REQUEST_DELAY = 3
CAP_BYTES = 200_000

_session = requests.Session()
_session.headers.update({"User-Agent": USER_AGENT})

_OFFICIAL_STATUSES = {"official_source", "official_media", "court_record"}


def _looks_private(url: str) -> bool:
    """Reject URLs to obviously private portals we must not bypass."""
    host = (urlparse(url).netloc or "").lower()
    if any(marker in host for marker in ("erfir", "efir", "citizenservices")):
        # Government FIR citizen portals require login/OTP — capability check only,
        # we never attempt authentication or CAPTCHA bypass.
        return True
    return False


def _fetch_meta(url: str) -> tuple[int, Optional[str], Optional[str]]:
    """Return (available, content_type, meta). Reads a capped chunk only."""
    if _looks_private(url):
        return 0, None, "restricted portal — requires login/OTP (bypass not attempted)"

    try:
        with _session.get(url, timeout=45, stream=True) as resp:
            if resp.status_code == 429:
                return 0, None, "rate-limited by source (not retried)"
            if resp.status_code >= 400:
                return 0, None, f"HTTP {resp.status_code}"
            content_type = (resp.headers.get("Content-Type") or "").split(";")[0].strip()
            chunk = b""
            for block in resp.iter_content(chunk_size=4096):
                chunk += block
                if len(chunk) >= CAP_BYTES:
                    break
            return _summarize(url, content_type, chunk)
    except requests.RequestException as e:
        return 0, None, f"fetch error: {e.__class__.__name__}"


def _summarize(url: str, content_type: str, chunk: bytes) -> tuple[int, str, str]:
    is_pdf = content_type == "application/pdf" or chunk[:5] == b"%PDF-"
    if not is_pdf:
        return 1, content_type or "html", "link live (non-PDF page)"

    title = _pdf_title(chunk)
    first_text = _pdf_first_text(chunk)
    meta_parts = ["PDF verified"]
    if title:
        meta_parts.append(f"title: {title[:90]}")
    if first_text:
        meta_parts.append(f"first-page: {_clean(first_text)[:220]}")
    return 1, "application/pdf", " | ".join(meta_parts)


def _pdf_title(chunk: bytes) -> Optional[str]:
    m = re.search(rb"/Title\s*\(\s*([^)]{3,180})\s*\)", chunk)
    return m.group(1).decode("latin-1", "replace") if m else None


def _pdf_first_text(chunk: bytes) -> Optional[str]:
    # Only works when text runs uncompressed in the stream (common for
    # OCR-less official PDFs); best effort only.
    text = chunk.decode("latin-1", "ignore")
    matches = re.findall(r"\(([^)]{4,})\) Tj", text)
    return " ".join(matches)[:400] if matches else None


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def check_case_documents(case_id: str, db_path: Optional[str] = None) -> int:
    """Check all official-document evidence URLs for one case. Returns checked count."""
    conn = get_connection(db_path)
    items = get_case_evidence(conn, case_id)
    checked = 0
    for item in items:
        url = item.get("url", "")
        media_type = item.get("media_type", "")
        status = item.get("status", "")
        if not url or media_type != "official_document":
            continue
        if status not in _OFFICIAL_STATUSES:
            continue
        available, content_type, meta = _fetch_meta(url)
        conn.execute(
            "UPDATE case_evidence SET available = ?, meta = ? WHERE case_id = ? AND evidence_id = ?",
            (available, (content_type or "") + " :: " + (meta or ""), case_id, item["evidence_id"]),
        )
        conn.commit()
        checked += 1
        logger.info("[%s] %s -> available=%s | %s", case_id, item["evidence_id"],
                    bool(available), (meta or "")[:80])
        time.sleep(REQUEST_DELAY)
    conn.close()
    return checked


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    cases = sys.argv[1:] or ["CASE-001", "CASE-002"]
    total = 0
    for cid in cases:
        total += check_case_documents(cid)
    print(f"Checked {total} official documents across {cases}.")