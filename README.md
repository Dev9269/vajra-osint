# VAJRA Layer 3 — Self-Updating OSINT Aggregator

SIH 2026 hackathon project. Aggregates publicly available information about wanted/convicted persons from Wikipedia, news RSS feeds, and government portals. Every record carries full source attribution and structured fields (age, gender, crime_type, status, location, FIR number, vehicle plate, phone).

**Build note:** Originally planned as a two-agent build. Claude Code never contributed; Agent A (OpenCode) built the entire tool.

## Data captured per record

Each stored record has structured fields when the source text provides them (missing fields are left `null` — never fabricated):

| Field | Description | Example |
|-------|-------------|---------|
| `entity_name` | Person's name (extracted, not raw headline) | `Iqbal Shahbandri` |
| `record_type` | `person` when NER found a name, `unresolved_lead` for raw entries | `person` |
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

**Deduplication** (`#8`): each record gets a non-null `dedup_key` = SHA-256 of
`entity_name | source_url | COALESCE(fir_number, '')`, with a `UNIQUE` index on
it. Re-syncs are idempotent even when `fir_number` is NULL (the old
`UNIQUE(entity_name, source_url, fir_number)` silently treated NULL as "always
distinct" and duplicated rows).

## Attribution safety (`#1` — correctness-critical)

Crime/status/FIR/location are attached to a person **only from the sentence
containing their name** (`ATTRIBUTION_WINDOW = 0` in `entity_extraction.py`).
People who appear as speakers of reported speech ("Home Minister Amit Shah
*said* police arrested…") are excluded from crime attribution entirely — a
reporter or official is never tagged as an accused because their name shares a
sentence with crime keywords. Age/gender/phone/plate are descriptive and drawn
from a slightly wider window. The NER self-test (`python entity_extraction.py`)
includes the Amit Shah anti-misattribution case and exits non-zero on any
failure (`#2`).

## Entity resolution (`#3`/`#4`)

Pairwise fuzzy matching via rapidfuzz and the stored decision is persistent:

- Links ≥ 85% go into `entity_links` with a `match_status` column.
- **`auto_confirmed`** — near-identical names (≥ 95%) that are *not* strict
  token subsets of each other.
- **`needs_review`** — everything below 95%, **including strict subset
  matches.** `"Pradeep Singh"` vs `"Pradeep Singh Rajput"` returns
  `token_set_ratio` 100 but is pinned to 94.5 / `needs_review`, because a
  common short name + rare full name is usually two *different* people. Matches
  in this band are surfaced for human review, never auto-merged.
- The `GET /entities/<name>/links` endpoint returns `match_status` for every
  link.

## Setup

```bash
pip install -r requirements.txt
pip install -r requirements-models.txt    # pulls en_core_web_trf (transformers NER)
# or: python -m spacy download en_core_web_trf
# optional lightweight fallback: uncomment en_core_web_sm in requirements-models.txt
```

The transformer model is preferred (`_get_nlp()` auto-detects it) and fixes
name truncation (e.g. `en_core_web_sm` labeled "Rakesh Kumar Verma" as
"Kumar Verma") — a real accuracy difference for Indic names.

## Running

### Individual connectors (self-tests)

```bash
python storage.py          # schema + dedup + match_status tests
python wikipedia_connector.py
python news_connector.py
python gov_portal_connector.py
python entity_extraction.py   # exits 0 only if ALL attribution tests pass
python entity_resolution.py   # includes subset-match needs_review regression
```

### Scheduler (persistent process)

```bash
python scheduler.py
```

Initial sync on startup, then news every 4h, Wikipedia daily @02:00, gov daily
@03:00, entity resolution nightly @04:00 IST. Logs to `vajra_scheduler.log`.

### API server (`#7` — auth + CORS)

```bash
set VAJRA_API_KEY=your-secret-key        # PowerShell; required by clients
python api.py
# docs at http://localhost:8000/docs
```

- All data endpoints require the `X-API-Key` header. Missing/invalid → `401`.
  `/health` is open for load balancers.
- Without `VAJRA_API_KEY` set, a random key is generated and printed at startup.
- CORS is locked to `http://localhost:3000,http://localhost:8080` by default;
  override with `VAJRA_CORS_ORIGINS` (comma-separated). `allow_credentials` is
  off. No wildcard origins.

```bash
curl -H "X-API-Key: your-secret-key" http://localhost:8000/entities
```

Endpoints:
- `GET /entities` — list entities
- `GET /entities/<name>` — records with structured fields + citations
- `GET /entities/<name>/links` — matched entities with `match_status`
- `GET /search?q=<query>` — partial name search
- `GET /by-status/<status>` — filter by wanted/convicted/etc.
- `GET /by-crime/<crime>` — filter by crime type

## Real Case Datasets (SIH26189)

Two real public-source OSINT benchmark cases ship in `cases/`:

| Case | Topic | Run |
|------|-------|-----|
| CASE-001 | Fake Chinese Loan-App / Money-Laundering cluster (ED, Kerala/Haryana) | `cd cases/CASE-001 && python run_case001.py --case-dir . --output-dir run_output` |
| CASE-002 | Mundra Port narcotics trafficking network (NIA, RC-26/2021/NIA/DLI) | `cd cases/CASE-002 && python run_case002.py --case-dir . --output-dir run_output` |

Each pipeline runs extraction → graph analytics → benchmark evaluation, writing
`run_output/predictions.jsonl`, `graph_analysis.json`, `evaluation_report.json`.
These run dependency-free (pure stdlib) and are deliberately conservative —
the included `osint_engine.py` is an offline baseline you can swap for the
VAJRA NER/LLM pipeline via the same prediction schema.

### Ingest cases into the VAJRA API

`cases/ingest_cases.py` maps each case's curated entities, sources and
relationships into the same `records` + `entity_links` tables the API serves,
preserving `status` and provenance (`needs_review`; court/adjudicated facts are
not converted into convictions):

```powershell
set VAJRA_API_KEY=your-secret-key
python cases/ingest_cases.py          # both cases
python cases/ingest_cases.py --cases CASE-002
python api.py                          # then query /entities, /search?q=...
```

## Adding a Government Portal

1. Inspect the live HTML at the portal URL
2. Open `gov_portal_connector.py`, add entry to `PORTALS` with `container` + `text` CSS selectors
3. Verify `robots.txt` allows it (tool checks automatically)
4. Test with `python gov_portal_connector.py`

## Legal Boundary

- **Sources**: only official public records — gov wanted-persons portals, court portals, NCRB open data, press releases, syndicated RSS feeds. No social media, no login/bypass.
- **No fabrication**: never infers DOB/address/etc. not in the source. Missing fields stay `null`. Person names only stored when NER finds an explicit name. No crime attribution to anyone who merely *reports* or speaks about a crime.
- **Citation required**: every record has `source_url`, `source_type`, `fetched_at`.
- **Scraping etiquette**: robots.txt check, 2s rate limiting, descriptive User-Agent.
- **Copyright**: headlines/short summaries only, always link to original.
- **Entity resolution**: subset/ambiguous matches stored as `needs_review`, never auto-merged (max one-way auto-merge for near-identical names only).
- **Access control**: API is key-protected; not suitable for public exposure without further rate limiting + HTTPS.

## Tech Stack

Python 3.11+ · SQLite · spaCy en_core_web_trf (NER) · requests + BeautifulSoup4 · feedparser · rapidfuzz · APScheduler · FastAPI
