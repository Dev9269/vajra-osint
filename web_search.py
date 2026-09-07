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

CHANGELOG 2026-09-08 (attribution bugfix, mirrors Amit Shah incident):
  - NO MORE blind " crime" query suffix. Appending a keyword to force
    relevance guaranteed the word appeared somewhere in results and got
    innocent people (e.g. a minister discussing anti-narcotics policy)
    attributed a crime_type downstream.
  - Each result now runs through _attribution_for_article: the SAME
    sentence-scoped + reported-speech-excluded attribution that
    entity_extraction.py applies (#1 from the review round). A person
    only receives crime/status context if the keyword is in the same
    sentence/clause as their name, and they are not a reported-speech
    speaker or policy context. The plain keyword-co-occurrence crime
    types are NOT removed from entity_extraction, but web_search no
    longer fabricates them from query terms.
  - Non-matching articles remain storable as case_references (a general
    lead), but their stored meta declares attribution=None so no
    downstream code may populate crime_type/status from them.
"""

import json
import logging
import re
import sys
import time
from typing import Optional

import feedparser
import requests

from entity_extraction import (
    CRIME_TYPES,
    STATUS_KEYWORDS,
    extract_persons,
)

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


# Reported-speech frame words (mirrors entity_extraction.REPORTING_VERBS):
# if an entity name is IMMEDIATELY followed by one of these in the same
# sentence, the entity is a SPEAKER or policy context ("Amit Shah said...",
# "Mehta on drug-free India"), NOT an accused person.
_REPORTING_AFTER_NAME = re.compile(
    r"(?:said|says|say|told|tells|stated?|states?|announced?|announces?|"
    r"claimed?|claims?|warned?|warns?|revealed?|reveals?|added?|adds?|"
    r"noted?|notes?|confirmed?|confirms?|declared?|declares?|"
    r"mentioned?|mentions?|acknowledged?|acknowledges?|on)\b",
    re.IGNORECASE,
)


def _sentence_scoped_attribution(entity_name: str, title: str, snippet: str) -> dict:
    """Return {"crime_type":..,"status":..} for an entity ONLY if a crime/status
    keyword appears in the SAME sentence as the entity name in the title/snippet.

    This mirrors entity_extraction.py's #1 fix (ATTRIBUTION_WINDOW=0 + reported-
    speech exclusion). Two safety nets:

      1. sentence scoping — the keyword must be in the sentence that CONTAINS
         the entity's name (never diffused across the whole article/result);
      2. reported-speech frame — if the name is the subject of a reporting verb
         ("Amit Shah SAID police arrested..."), or is followed by "on" in a
         policy-phrase ("Amit Shah ON drug-free India"), attribution is refused.

    Attribution is refused (returns {"crime_type":None,"status":None}) rather
    than guessed, so an innocent public figure never inherits a crime_type.
    """
    if not entity_name or len(entity_name.strip()) < 2:
        return {"crime_type": None, "status": None}

    # Reuse the full NER pipeline so PERSON detection and speaker exclusion are
    # identical to the entity_extraction fix. Empty on NER failure → safe "no".
    try:
        haystack = f"{title}. {snippet}"
        persons = extract_persons(haystack)
    except Exception:
        persons = []

    for p in persons:
        if entity_name.lower() not in p.name.lower():
            continue
        # Person's OWN sentence scope already enforced by extract_persons
        # (ATTRIBUTION_WINDOW=0, speakers excluded). If the extractor
        # attributed nothing in that sentence, we attribute nothing here.
        p_att = {"crime_type": p.crime_type, "status": p.status}
        return _guard_reported_speech(entity_name, title, snippet, p_att)

    # NER may fail to spot the entity (truncated model) → fall back to pure
    # sentence-scoped keyword matching, plus the reported-speech guard.
    return _guard_reported_speech(
        entity_name,
        title,
        snippet,
        _sentence_scope_fallback(entity_name, title, snippet),
    )


def _sentence_scope_fallback(entity_name: str, title: str, snippet: str) -> dict:
    """Pure regex sentence-scope: crime/status keyword must be in the same
    sentence as the entity name. Used only when NER doesn't return the person
    (en_core_web_sm fallback or OOV named entity)."""
    for text in (title, snippet):
        for sentence in re.split(r"(?<=[.!?])\s+", text):
            if entity_name.lower() not in sentence.lower():
                continue
            crime = _extract_crime_type(sentence)
            status = _extract_status(sentence)
            if crime or status:
                return {"crime_type": crime, "status": status}
    return {"crime_type": None, "status": None}


def _guard_reported_speech(entity_name: str, title: str, snippet: str, att: dict) -> dict:
    """Refuse attribution when the entity is the subject of a reported-speech
    frame in ITS OWN sentence ("Amit Shah SAID ...", "Mehta ON drug-free India").

    Scoped to the exact entity name within one sentence — "Police said Singh
    ran a racket" does not void the attribution derived from the accused's
    OWN sentence ("Vikram Singh arrested over fraud...").
    """
    if not att or (not att.get("crime_type") and not att.get("status")):
        return att
    name = re.escape(entity_name.strip())
    frame = re.compile(
        rf"\b{name}\b\s+(?:{_REPORTING_AFTER_NAME.pattern})",
        re.IGNORECASE,
    )
    for text in (title, snippet):
        for sentence in re.split(r"(?<=[.!?])\s+", text):
            if frame.search(sentence):
                return {"crime_type": None, "status": None}
    return att


def _extract_crime_type(text: str) -> Optional[str]:
    text_lower = text.lower()
    best, best_len = None, 0
    for keyword, crime_type in CRIME_TYPES.items():
        if keyword in text_lower and len(keyword) > best_len:
            best, best_len = crime_type, len(keyword)
    return best


def _extract_status(text: str) -> Optional[str]:
    text_lower = text.lower()
    best, best_len = None, 0
    for keyword, status in STATUS_KEYWORDS.items():
        if keyword in text_lower and len(keyword) > best_len:
            best, best_len = status, len(keyword)
    return best


def _attribution_for_article(entity_names, title: str, snippet: str) -> dict:
    """Aggregate sentence-scoped attribution across candidate entity names.

    Returns {"crime_type":..., "status":...} — NEVER assigns a crime to a
    person merely because an article happened to contain the word crime.
    """
    merged = {"crime_type": None, "status": None}
    for name in entity_names or []:
        att = _sentence_scoped_attribution(name, title, snippet)
        if att.get("crime_type") or att.get("status"):
            merged = att
            break
    return merged


def search_case(
    case_id: str,
    queries: Optional[list[str]] = None,
    db_path: Optional[str] = None,
    entity_names: Optional[list[str]] = None,
) -> int:
    """Run legal web searches for a case and store references.

    queries defaults to [case_id] plus the (FIR/drug/loan-app etc.)
    identity when a single query is supplied — pass a list for richer
    coverage. entity_names (optional) are the people this case is ABOUT;
    when supplied, each stored reference records whether a sentence-scoped
    crime/status attribution was justified (meta). Returns the number of
    new references stored.
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
            results = fetcher(query)
            for r in results:
                attribution = _attribution_for_article(entity_names, r["title"], r["snippet"])
                ok = insert_case_reference(
                    conn,
                    case_id=case_id,
                    source_type=source_type,
                    title=r["title"],
                    url=r["url"],
                    snippet=r["snippet"],
                    meta=json.dumps(attribution),
                )
                if ok:
                    inserted += 1
                    logger.info(
                        "  [%s] %s (crime=%s, status=%s)",
                        source_type,
                        r["title"][:60],
                        attribution.get("crime_type"),
                        attribution.get("status"),
                    )
            time.sleep(REQUEST_DELAY)

    conn.close()
    logger.info("Case %s search complete. %d new references stored.", case_id, inserted)
    return inserted


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    # ── Attribution regression tests (offline, no network) ──────────────────
    print("Running web_search attribution self-test (regression: policy speaker is NOT an accused)...")
    failures = 0

    def check(cond: bool, label: str):
        global failures
        print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
        if not cond:
            failures += 1

    # Test: a real public figure discussing policy must NOT get a crime attribution.
    # Mirrors the live incident: "Amit Shah on drug-free India by 2029" got tagged
    # crime_type=narcotics because the query forced the word "crime"/"narcotics".
    policy_title = (
        "'We eradicated Naxalism, narcotics next': Home Minister Arjun Mehta "
        "on drug-free India by 2029"
    )
    policy_snippet = (
        "Mehta addressed the conference on the new anti-narcotics policy and "
        "emphasized rehabilitation over prosecution for drug users."
    )
    att_policy = _sentence_scoped_attribution("Arjun Mehta", policy_title, policy_snippet)
    check(
        att_policy["crime_type"] is None and att_policy["status"] is None,
        f"policy speaker Arjun Mehta: no crime/status attribution (got {att_policy})",
    )

    # Second variant: "said" reporting frame on the SAME sentence as a crime word.
    att_said = _sentence_scoped_attribution(
        "Arjun Mehta",
        "Home Minister Arjun Mehta said police arrested three men for fraud",
        "The accused were produced before a Noida court.",
    )
    check(
        att_said["crime_type"] is None and att_said["status"] is None,
        f"'Arjun Mehta SAID police arrested...' speaker is not accused (got {att_said})",
    )

    # Positive control: the accused him/herself in the same sentence still gets attributed.
    att_accused = _sentence_scoped_attribution(
        "Vikram Singh",
        "Vikram Singh arrested over fraud FIR 123/2026 filed in Noida",
        "Police said Singh ran a fake loan app racket.",
    )
    check(
        att_accused["crime_type"] == "fraud" and att_accused["status"] == "arrested",
        f"accused Vikram Singh still attributed fraud/arrested (got {att_accused})",
    )

    # A person merely mentioned (no crime word in their sentence) gets nothing.
    att_innocent = _sentence_scoped_attribution(
        "Rakesh Gupta",
        "Rakesh Gupta chaired a meeting in Noida today",
        "Meanwhile, police arrested three men for fraud in Lucknow.",
    )
    check(
        att_innocent["crime_type"] is None and att_innocent["status"] is None,
        f"innocent Rakesh Gupta in neutral sentence: no attribution (got {att_innocent})",
    )

    # The old bug: fetcher was called as fetcher(f"{query} crime"). Verify the
    # query pipeline no longer appends a keyword — grep source for the pattern.
    import inspect
    search_src = inspect.getsource(search_case)
    check(
        'fetcher(f"{query} crime")' not in search_src
        and '" crime"' not in search_src,
        "no blind ' crime' suffix appended to queries",
    )

    if failures:
        print(f"{failures} ATTRIBUTION TEST(S) FAILED")
        sys.exit(1)

    print("\nRunning web_search self-test (CASE-001, live)...")
    n = search_case(
        "CASE-001",
        queries=[
            "Chinese loan app money laundering ED Kerala arrests",
            "ED Kerala cluster case mule bank accounts",
        ],
    )
    print(f"CASE-001 references stored: {n}")