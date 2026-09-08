# VAJRA Layer 3 — Self-Updating OSINT Aggregator

![CI](https://github.com/Dev9269/vajra-osint/actions/workflows/ci.yml/badge.svg)

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
| `image_url` | Photo/image URL straight from the source (Wikipedia page photo, RSS thumbnail) — **never downloaded or stored locally**, only the link | `https://upload.wikimedia.org/.../Dawood_ibrahim.png` |
| `source_type` | wikipedia / news / gov_portal | `wikipedia` |
| `source_url` | Exact citation URL | https://en.wikipedia.org/wiki/... |
| `fetched_at` | Fetch timestamp | ISO date |

**Photos** (`image_url`): the connector stores the image link from the original
source, not the image itself — Wikipedia uses the official `prop=pageimages`
(endpoint: `extracts|pageimages`, `piprop=original|thumbnail`) and news uses
RSS `media:content` / `media:thumbnail` / image enclosures. Missing photos are
left `null`.

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

## Entity resolution (`#3`/`#4`, Part 2)

Fast block-based fuzzy matching via rapidfuzz with the stored decision
persistent:

- Links ≥ 85% go into `entity_links` with a `match_status` column.
- **`auto_confirmed`** — near-identical names (≥ 95%) that are *not* strict
  token subsets, **and** that share at least one verifiable identifier
  (FIR number, plate, phone, or location). A high string score alone is never
  enough — reviewers confirm the rest.
- **`needs_review`** — everything below 95% (including no-corroboration), and
  **all strict subset matches.** `"Pradeep Singh"` vs `"Pradeep Singh Rajput"`
  returns `token_set_ratio` 100 but is pinned to 94.5 / `needs_review`,
  because a common short name + rare full name is usually two *different*
  people. Matches in this band surface in the dashboard's review queue.
- **Transliteration** normalises common Indian-name variant spellings
  (Mohammed/Muhammad/Mohd, Chaudhary/Chowdhury, Sharma/Sarma, …) before
  matching.
- **Initials** (e.g. `"Rakesh V."`) are expanded against the other name's full
  tokens so abbreviated records still reach the review queue instead of being
  dropped below the threshold.
- The `GET /entities/<name>/links` endpoint returns `match_status` for every
  link; `POST /links/<a>/<b>/review` confirms/rejects review-queue items.

## Setup

> **MANDATORY**: the spaCy transformer model (`en_core_web_trf`) must be
> installed or entity extraction silently degrades — name truncation and missed
> detections with **no error and only a startup WARNING**. Install it FIRST:

```bash
pip install -r requirements-models.txt    # REQUIRED — en_core_web_trf (transformers NER), a large download
# or: python -m spacy download en_core_web_trf

pip install -r requirements.txt           # base app dependencies
# optional lightweight fallback: uncomment en_core_web_sm in requirements-models.txt
```

The transformer model is preferred (`_get_nlp()` auto-detects it, and
`report_spacy_model()` logs a visible WARNING at scheduler/API startup if the
pipeline will fall back to `en_core_web_sm`). It fixes name truncation (e.g.
`en_core_web_sm` labeled "Rakesh Kumar Verma" as "Kumar Verma") — a real
accuracy difference for Indic names.

## Before first run: set a permanent API key

The API rejects requests without a valid `X-API-Key` header (only `/health` is
open). The key is **permanent** — set it once, and it never changes across
restarts, redeploys, or crash-recovery:

```bash
python generate_api_key.py          # prints a fresh 43-char key
# copy the output into a .env file (copied from .env.example) as:
#   VAJRA_API_KEY=<the-generated-key>
```

1. `copy .env.example .env` (PowerShell: `Copy-Item .env.example .env`)
2. `python generate_api_key.py` → copy the printed value into `.env` as `VAJRA_API_KEY`
3. Start any process (`python api.py`, `python scheduler.py`) from the repo root —
   it auto-loads `.env`. Or set the variable in your shell.

This key is what the dashboard/client sends as the `X-API-Key` header. It will
**not** change unless you edit `.env` yourself.

> **Safety**: `.env` is gitignored and never committed. Only `.env.example`
> (placeholders) is in the repo — never put a real key in it.
> **Production**: set `VAJRA_ENV=production` in `.env`. If `VAJRA_API_KEY` is
> then missing, `api.py` refuses to start rather than generating a throwaway
> key that would rotate on every restart.

## Running

### Individual connectors (self-tests)

```bash
python storage.py          # schema + dedup + match_status + auth/review tests
python wikipedia_connector.py
python news_connector.py
python gov_portal_connector.py
python pib_connector.py        # #PIB press releases
python agency_connector.py     # #SEBI / ED / CBI
python ecourts_connector.py    # #eCourts case status
python regional_news_connector.py  # #Hindi + regional (leads)
python state_police_wanted.py  # #state-police wanted pages
python ncrb_connector.py       # #NCRB open-data pages
python wayback_connector.py    # #dead-link recovery via Wayback CDX
python entity_extraction.py   # exits 0 only if ALL attribution tests pass
python entity_resolution.py   # includes subset/review + corroboration regression
```

### Public-source connectors (`2026-09-08`, Part 4)

| Connector | Source | What it adds |
|-----------|--------|--------------|
| `pib_connector.py` | PIB RSS (EN + HI) | Home-ministry / national press releases with crime/terror/legal keywords |
| `agency_connector.py` | SEBI RSS + ED/CBI/SEBI pages | Financial & agency prosecution press releases (money laundering, fraud, SEBI orders) |
| `ecourts_connector.py` | eCourts public API | Case-status records for known entity names; offline parse test |
| `state_police_wanted.py` | Delhi/UP/Mah, NIA/CBI/TN/Karnataka wanted pages | Wanted-persons pages (robots-checked, per-portal isolation) |
| `regional_news_connector.py` | Hindi/Bengali/Marathi/Tamil/Telugu/Kannada feeds | Regional crime leads (Devanagari stored as `unresolved_lead`) |
| `ncrb_connector.py` | ncrb.gov.in publications + wanted/absconder notices | NCRB open-data crime/notice pages (robots-checked) |
| `wayback_connector.py` | Wayback Machine CDX API | Recover 404'd `source_url`s as `web.archive.org` snapshots |

All follow the same etiquette: robots.txt check (where applicable), 2 s polite
delay, descriptive User-Agent, per-source error isolation (one failure never
kills the queue). Connector failures are logged and swallowed so the scheduler
keeps running.

### Test suite (pytest)

```bash
pip install -r requirements.txt     # includes pytest + pytest-cov
python -m pytest                    # all tests (NER model tests run if installed)
python -m pytest -m "not model and not network"   # CI run (small, fast)
```

CI (GitHub Actions, `.github/workflows/ci.yml`) runs the fast suite on
Python 3.11/3.12 and a gitleaks secret scan on every push/PR. Test coverage:
API security (auth, rate limit + Retry-After, SQL injection, pagination),
auth-failure logging, link-review flow, entity-resolution corroboration/
transliteration/initials, dedup idempotency, scheduler wiring, plus a
25-sentence adversarial Indic-name NER corpus (`tests/adversarial_ner.py`).

### Scheduler (persistent process)

```bash
python scheduler.py
```

Initial sync on startup, then: news every 4h, Wikipedia daily @02:00, gov daily
@03:00, PIB @03:30, agency (SEBI/ED/CBI) @04:00, regional news every 6h,
state-police wanted @04:30, NCRB @04:45, entity resolution @05:00, case research
@05:30, dead-link recovery / Wayback @06:00, eCourts every 12h (IST). Logs to
`vajra_scheduler.log`.

### API server (`#7` — auth, CORS, rate limiting)

```bash
python api.py        # reads VAJRA_API_KEY from .env (see "Before first run")
# dashboard at   http://localhost:8000/
# docs at        http://localhost:8000/docs
```

- Binds `127.0.0.1` by default. Set `VAJRA_HOST`/`VAJRA_PORT` to change the
  bind address; `0.0.0.0` is honoured **only** when `VAJRA_ENV=production`
  **and** a permanent `VAJRA_API_KEY` is set (see `__main__` guard).
- All data endpoints require the `X-API-Key` header. Missing/invalid → `401`.
  `/health` is open for load balancers.
- **Rate limiting** (slowapi): 60 req/min per API key on authenticated routes;
  10 req/min per IP on `GET /` and `/docs` (a middleware rate-limits the
  framework-owned Swagger/redoc routes). Exceeded → `429` with a `Retry-After`
  header.
- **Failed auth is logged at WARNING** (IP, endpoint, UTC timestamp) *and*
  persisted to the `auth_failures` table — `GET /security/auth-failures`
  (admin) surfaces brute-force attempts.
- The key comes from `VAJRA_API_KEY` in `.env` (auto-loaded) or the real
  environment. With no key set, a **temporary** random key is generated and a
  prominent warning is printed — it changes on every restart, so this is only
  for five-minute local tests.
- With `VAJRA_ENV=production` and no `VAJRA_API_KEY`, the server **refuses to
  start** instead of using a throwaway key.
- CORS is locked to `http://localhost:3000,http://localhost:8080` by default;
  override with `VAJRA_CORS_ORIGINS` (comma-separated). `allow_credentials` is
  off. No wildcard origins.

```bash
curl -H "X-API-Key: your-secret-key" http://localhost:8000/entities
```

Endpoints:
- `GET /` — dashboard (records, needs-review queue, links), served from `static/`
- `GET /entities` — list entities; `?limit=` / `?offset=` for pagination, returns `total_count`
- `GET /entities/<name>` — records with structured fields + citations, plus `related_cases`, `evidence`, `references` (photos/videos/FIR links, auto web research)
- `GET /entities/<name>/links` — matched entities with `match_status`
- `POST /links/<a>/<b>/review?action=confirm|reject` — confirm/reject a `needs_review` pair (auditable in `link_reviews`)
- `GET /cases/<case_id>` — case dossier: evidence + references
- `GET /search?q=<query>` — partial name search
- `GET /by-status/<status>` — filter by wanted/convicted/etc.
- `GET /by-crime/<crime>` — filter by crime type
- `GET /security/auth-failures` — admin brute-force view

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
python cases/ingest_cases.py          # both cases; writes to the local DB directly
python cases/ingest_cases.py --cases CASE-002
python api.py                          # then query /entities, /search?q=...
```

### Case evidence, FIR copies, photos & videos (`2026-09-07`)

Each case ships an `evidence/evidence_metadata/media_manifest.json`
(`CASE-001-all-evidence-one-folder.zip`, `CASE-002-all-evidence-one-folder.zip`)
that is ingested link-only into the `case_evidence` table:

```powershell
python cases/ingest_evidence.py      # loads CASE-001 + CASE-002 manifests
```

Evidence items cover official documents (including the **actual NIA FIR PDF**
`RC-26/2021/NIA/DLI`), court judgments, gov press releases, photo references
(CCTV stills police release inside articles) and video references (news/YouTube
coverage). Links only — media is **never downloaded or stored**, and private
victim/bank/phone details are excluded.

**FIR availability check** (`fir_scraper.py`) verifies each official-document
URL is live (HTTP 200 + `application/pdf`) and records `available` status +
first-page text attribution — without saving the document:

```powershell
python fir_scraper.py               # checks CASE-001 + CASE-002 official docs
```

**Automatic case research** (`web_search.py`) — the legal "Google the case"
step. Uses Google News RSS + Wikipedia's search API (never scrapes
google.com; RSS/API only) and stores matched articles in `case_references`:

```powershell
python web_search.py                # self-test for CASE-001
```

The **scheduler** now re-runs all of this daily at 05:30 IST
(`job_case_research`).

### Dossier endpoints

- `GET /entities/<name>` — now includes `related_cases`, `evidence`
  (photos/videos/FIR-official-doc links) and `references` (auto web research)
  right next to the person's records. **Photos/videos/FIR links show in the
  same entity view via `image_url` + evidence — nothing opens elsewhere.**
- `GET /cases/<case_id>` — full case dossier: all evidence + web references.
- `GET /entities/<name>/links` — matched entities with `match_status`.

## Adding a Government Portal

1. Inspect the live HTML at the portal URL
2. Open `gov_portal_connector.py`, add entry to `PORTALS` with `container` + `text` CSS selectors
3. Verify `robots.txt` allows it (tool checks automatically)
4. Test with `python gov_portal_connector.py`

## Legal Boundary

- **Sources**: only official public records — gov wanted-persons portals, court portals, NCRB open data, press releases, syndicated RSS feeds. No social media, no login/bypass.
- **No fabrication**: never infers DOB/address/etc. not in the source. Missing fields stay `null`. Person names only stored when NER finds an explicit name. No crime attribution to anyone who merely *reports* or speaks about a crime.
- **Citation required**: every record has `source_url`, `source_type`, `fetched_at`.
- **Scraping etiquette**: robots.txt check, 4s rate limiting with automatic retry/backoff on 429 (Wikipedia), descriptive User-Agent.
- **Copyright**: headlines/short summaries only, always link to original. Case evidence is **link-only** — FIR/CCTV/video/media URLs are stored and attributed, never downloaded or re-hosted; private victim data (bank/phone/address) is excluded.
- **No Google scraping**: automatic case research uses Google News RSS + Wikipedia search API only (never automated `google.com` results). Official FIR lookup links are constructed for eCourts/FIR-status portals; private/login FIR portals are capability-checked without bypass attempts (login/OTP/CAPTCHA never automated).
- **Entity resolution**: subset/ambiguous matches stored as `needs_review`, never auto-merged (max one-way auto-merge for near-identical names only).
- **Access control**: API is key-protected; not suitable for public exposure without further rate limiting + HTTPS.

## Tech Stack

Python 3.11+ · SQLite · spaCy en_core_web_trf (NER) · requests + BeautifulSoup4 · feedparser · rapidfuzz · APScheduler · FastAPI + slowapi | tests: pytest + pytest-cov · GitHub Actions CI
