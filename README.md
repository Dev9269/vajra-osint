# VAJRA Layer 3 — Self-Updating OSINT Aggregator

SIH 2026 hackathon project. Aggregates publicly available information about wanted/convicted persons from government portals, Wikipedia, and news RSS feeds. Every stored record carries full source attribution — no unsourced claims.

## Agent Split

| Agent | Lane | Files |
|-------|------|-------|
| **Agent A (OpenCode)** | Ingestion | `storage.py`, `wikipedia_connector.py`, `news_connector.py`, `gov_portal_connector.py` |
| **Agent B (Claude Code)** | Intelligence / Serving | `entity_extraction.py`, `entity_resolution.py`, `scheduler.py`, `api.py` |

## Setup

```bash
pip install -r requirements.txt
python -m spacy download en_core_web_sm  # or en_core_web_trf if compute allows
```

## Running

### Individual connectors (manual test)

Each connector has a self-test block — run directly to verify against real data:

```bash
python wikipedia_connector.py    # pulls 3 pages from Indian fugitives category
python news_connector.py         # polls 1 news feed
python gov_portal_connector.py   # checks robots.txt, scrapes allowed portals
python storage.py                # runs in-memory schema tests
```

### Scheduler (persistent process)

Agent B's `scheduler.py` runs as a long-lived process:

```bash
python scheduler.py
```

This starts the APScheduler loop:
- News sync: every 4 hours
- Wikipedia sync: daily
- Gov portal sync: daily (staggered from Wikipedia)
- Entity resolution: nightly, after all syncs complete

An initial sync runs immediately on startup.

### API server

```bash
python api.py
# or
uvicorn api:app --reload
```

Endpoints:
- `GET /entities` — list all known entities
- `GET /entities/<name>` — all records for one entity with source citations
- `GET /entities/<name>/links` — fuzzy-matched related entities

## Adding a Government Portal

1. Inspect the live HTML at the portal URL (browser DevTools)
2. Open `gov_portal_connector.py` and find the `PORTALS` list
3. Add a new entry with:
   - `name`: human-readable label
   - `url`: the wanted-persons page URL
   - `selectors`: CSS selectors for `container`, `name`, `detail`, `location`
4. Verify `robots.txt` allows scraping (the tool checks this automatically)
5. Run `python gov_portal_connector.py` to test

## Legal Boundary

This tool is designed to stay strictly within legal bounds:

- **Sources**: Only official public records — government wanted-persons portals, court record portals (eCourts), NCRB open data, press releases, and RSS feeds explicitly published for syndication.
- **No private data**: Does NOT scrape social media profiles, private forums, or any source requiring login/bypassing access controls.
- **No fabrication**: Never infers or fabricates personal data (DOB, address, etc.) not explicitly present in the source text. Missing fields are left null.
- **Citation required**: Every stored record carries `source_url`, `source_type`, and `fetched_at`. No record exists without a traceable citation.
- **Scraping etiquette**: Checks `robots.txt` before scraping, rate-limits requests (2s delay between calls to the same host), sets a descriptive User-Agent.
- **Copyright**: Stores only headlines/short summaries from news sources — never fetches or republishes full article text.

## Tech Stack

- Python 3.11+
- SQLite (portable to Postgres)
- spaCy (NER)
- BeautifulSoup4 + requests (scraping)
- feedparser (RSS)
- rapidfuzz (fuzzy entity matching)
- APScheduler (scheduled syncs)
- FastAPI (query API)
