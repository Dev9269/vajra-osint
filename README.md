# VAJRA Layer 3 — Self-Updating OSINT Aggregator

SIH 2026 hackathon project. Aggregates publicly available information about wanted/convicted persons from Wikipedia, news RSS feeds, and government portals. Every record carries full source attribution and structured fields (age, gender, crime_type, status, location, FIR number, vehicle plate, phone).

**Build note:** Originally planned as a two-agent build. Claude Code never contributed; Agent A (OpenCode) built the entire tool.

## Data captured per record

Each stored record has structured fields when the source text provides them (missing fields are left `null` — never fabricated):

| Field | Description | Example |
|-------|-------------|---------|
| `entity_name` | Person's name (extracted, not raw headline) | `Iqbal Shahbandri` |
| `age` | Age or computed from DOB | `56` |
| `gender` | male / female (from pronouns/context) | `male` |
| `crime_type` | Normalized crime category | `terrorism`, `cyber fraud`, `murder` |
| `crime_description` | Contextual description text | `FIR 123/2026 — accused in cyber fraud` |
| `status` | wanted / convicted / absconding / arrested etc. | `wanted` |
| `location` | City/state from gazetteer | `Mumbai` |
| `fir_number` | FIR / case reference | `FIR 123/2026` |
| `plate_number` | Vehicle registration | `DL 01 AB 1234` |
| `phone_number` | Contact number | `+919876543210` |
| `source_type` | wikipedia / news / gov_portal | `wikipedia` |
| `source_url` | Exact citation URL | https://en.wikipedia.org/wiki/... |
| `fetched_at` | Fetch timestamp | ISO date |

## Setup

```bash
pip install -r requirements.txt
python -m spacy download en_core_web_sm   # or en_core_web_trf for better NER
```

## Running

### Individual connectors (self-tests)

```bash
python storage.py          # schema tests
python wikipedia_connector.py
python news_connector.py
python gov_portal_connector.py
python entity_extraction.py
python entity_resolution.py
```

### Scheduler (persistent process)

```bash
python scheduler.py
```

Same behavior as v1: initial sync on startup, then news every 4h, Wikipedia daily @02:00, gov daily @03:00, entity resolution nightly @04:00 IST. Logs to `vajra_scheduler.log`.

### API server

```bash
python api.py
# docs at http://localhost:8000/docs
```

Endpoints:
- `GET /entities` — list entities
- `GET /entities/<name>` — records with structured fields + citations
- `GET /entities/<name>/links` — fuzzy-matched related entities
- `GET /search?q=<query>` — partial name search
- `GET /by-status/<status>` — filter by wanted/convicted/etc.
- `GET /by-crime/<crime>` — filter by crime type

## Adding a Government Portal

1. Inspect the live HTML at the portal URL
2. Open `gov_portal_connector.py`, add entry to `PORTALS` with `container` + `text` CSS selectors
3. Verify `robots.txt` allows it (tool checks automatically)
4. Test with `python gov_portal_connector.py`

## Legal Boundary

- **Sources**: only official public records — gov wanted-persons portals, court portals, NCRB open data, press releases, syndicated RSS feeds. No social media, no login/bypass.
- **No fabrication**: never infers DOB/address/etc. not in the source. Missing fields stay `null`. Person names only stored when NER finds an explicit name.
- **Citation required**: every record has `source_url`, `source_type`, `fetched_at`.
- **Scraping etiquette**: robots.txt check, 2s rate limiting, descriptive User-Agent.
- **Copyright**: headlines/short summaries only, always link to original.
- **Entity resolution**: matches < 95% flagged for human review, never auto-merged.

## Tech Stack

Python 3.11+ · SQLite · spaCy (NER) · requests + BeautifulSoup4 · feedparser · rapidfuzz · APScheduler · FastAPI
