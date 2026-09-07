"""Legal web-reference search for VAJRA cases.

Searches public, bot-allowed endpoints only — NEVER scrapes google.com
(Goolge ToS forbids automated scraping of the results page):

  - Google News RSS (news.google.com/rss/search?q=...) — a public RSS
    feed designed for syndication; returns matching news articles.
  - Wikipedia Search API (action=query&list=search) — official API.

Results are stored in the case_references table with full source
attribution and served through the API. Search links only; no article
content is fetched or stored.

CHANGELOG 2026-09-07: new feature — automatic case research ("Google
the case") that stays inside the API instead of opening other sites.
"""

import logging
import re
import time
from typing import Optional

import feedparser
import requests

logger = logging.getLogger(__name__)

GOOGLE_NEWS_RSS = "https://news.google.com/rss/search"
WIKI_API = "https://en.wikipedia.org/w/api.php"
USER_AGENT = "VAJRA-OSINT/1.0 (SIH 2026 hackathon project; educational use)"
REQUEST_DELAY = 3
MAX_PER_SOURCE = 20

STOPWORDS = {
    "the", "and", "for", "with", "case", "cases", "india", "indian",
    "news", "from", "into", "was", "are", "kerala", "this", "that",
}

_session = requests.Session()
_session.headers.update({"User-Agent": USER_AGENT})


def _google_news_search(query: str, limit: int = MAX_PER_SOURCE) -> list[dict]:
    """Query Google News RSS. Returns [{title, url, snippet}]."""
    params = {"q": query, "hl": "en-IN", "gl": "IN", "ceid": "IN:en"}
    try:
        resp = _session.get(GOOGLE_NEWS_RSS, params=params, timeout=30)
        resp.raise_for_status()
        feed = feedparser.parse(resp.content)
        results = []
        for entry in feed.entries[:limit]:
            title = entry.get("title", "").strip()
            link = entry.get("link", "").strip()
            if not title or not link:
                continue
            summary = entry.get("summary", "") or ""
            results.append({"title": title, "url": link, "snippet": _clean(summary)})
        return results
    except requests.RequestException as e:
        logger.error("Google News RSS search failed for %r: %s", query, e)
        return []


def _wikipedia_search(query: str, limit: int = MAX_PER_SOURCE) -> list[dict]:
    """Query Wikipedia's official search API. Returns [{title, url, snippet}]."""
    significant = {w.lower() for w in re.split(r"[^a-z0-9]+", query.lower())
                   if len(w) > 3 and w.lower() not in STOPWORDS}
    params = {
        "action": "query",
        "list": "search",
        "srsearch": query,
        "srlimit": str(min(limit, 20)),
        "format": "json",
    }
    try:
        resp = _session.get(WIKI_API, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        results = []
        for item in data.get("query", {}).get("search", []):
            title = item.get("title", "").strip()
            if not title:
                continue
            url = f"https://en.wikipedia.org/wiki/{title.replace(' ', '_')}"
            snippet = _clean(item.get("snippet", ""))
            haystack = f"{title} {snippet}".lower()
            # Keep only results sharing a significant query term in title or
            # snippet, else the funnel fills with unrelated Wikipedia pages.
            if not significant or any(term in haystack for term in significant):
                results.append({"title": title, "url": url, "snippet": snippet})
        return results
    except requests.RequestException as e:
        logger.error("Wikipedia search failed for %r: %s", query, e)
        return []


def _clean(text: str) -> str:
    import re

    clean = re.sub(r"<[^>]+>", "", text or "")
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean[:500]


def search_case(
    case_id: str,
    queries: Optional[list[str]] = None,
    db_path: Optional[str] = None,
) -> int:
    """Run legal web searches for a case and store references.

    queries defaults to [case_id] plus the (FIR/drug/loan-app etc.)
    identity when a single query is supplied — pass a list for richer
    coverage. Returns the number of new references stored.
    """
    from storage import get_connection, insert_case_reference

    if queries is None:
        queries = [case_id]

    conn = get_connection(db_path)
    inserted = 0

    for query in queries:
        logger.info("Searching case %s: %r", case_id, query)
        for source_type, fetcher in (
            ("google_news", _google_news_search),
            ("wikipedia", _wikipedia_search),
        ):
            results = fetcher(f"{query} crime") if source_type == "google_news" else fetcher(query)
            for r in results:
                ok = insert_case_reference(
                    conn,
                    case_id=case_id,
                    source_type=source_type,
                    title=r["title"],
                    url=r["url"],
                    snippet=r["snippet"],
                )
                if ok:
                    inserted += 1
                    logger.info("  [%s] %s", source_type, r["title"][:70])
            time.sleep(REQUEST_DELAY)

    conn.close()
    logger.info("Case %s search complete. %d new references stored.", case_id, inserted)
    return inserted


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    print("Running web_search self-test (CASE-001)...")
    n = search_case(
        "CASE-001",
        queries=[
            "Chinese loan app money laundering ED Kerala arrests",
            "ED Kerala cluster case mule bank accounts",
        ],
    )
    print(f"CASE-001 references stored: {n}")