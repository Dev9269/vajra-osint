# VAJRA OSINT — Demo Guide

This document is the step-by-step walkthrough for the judge/mentor demo of the
2026-09-08 round (Build & Fix Master Prompt). Every step is **testable** and
most can be run live in front of an audience. Requires the venv + spaCy
`en_core_web_trf` (see README "Setup").

All commands run from the repo root: `C:\Users\vboxuser\projects\vajra-osint`.

## 0. One-time setup

```powershell
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
pip install -r requirements-models.txt     # en_core_web_trf (large; required for NER)
Copy-Item .env.example .env                # then set VAJRA_API_KEY
python generate_api_key.py                 # paste output as VAJRA_API_KEY in .env
```

Set a permanent key so the dashboard stays authenticated across restarts:

```
set VAJRA_API_KEY=<generated-key>
```

## 1. Part 1 — API security (rate limiting, auth-failure logging, binding)

**Run the API** (default binds `127.0.0.1:8000`):

```powershell
python api.py
```

### 1a. Auth is enforced

```bash
curl http://localhost:8000/entities                       # -> 401 (no key)
curl -H "X-API-Key: wrong" http://localhost:8000/entities # -> 401
curl -H "X-API-Key: $key" http://localhost:8000/entities  # -> 200 JSON
```

### 1b. Failed auth is logged and persisted for the admin

Send a few wrong keys, then:

```bash
curl -H "X-API-Key: $key" http://localhost:8000/security/auth-failures
```

Returns `{"count": N, "failures": [...]}` — each with `client_ip`, `endpoint`,
`attempted_at`. The WARNING lines appear in the server console.

### 1c. Rate limit + Retry-After

`/health` allows 10/min per IP. Loop 11 times:

```bash
for i in $(seq 1 11); do curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8000/health; done
```

The 11th returns `429` with a `Retry-After:` header. Authenticated routes
(`/entities`, `/search`, …) allow 60/min per API key.

### 1d. Bind to 0.0.0.0 is gated

```powershell
set VAJRA_HOST=0.0.0.0
python api.py   # without VAJRA_ENV=production -> falls back to 127.0.0.1 with a warning
```

Set `VAJRA_ENV=production` **and** a `VAJRA_API_KEY` to allow `0.0.0.0`.
With `VAJRA_ENV=production` and no key, the server refuses to start.

## 2. Part 2 — entity resolution (corroboration + transliteration + initials)

```powershell
python entity_resolution.py
```

Expect all checks PASS and one auto-confirm (Mohammed↔Muhammad, shared FIR),
the rest `needs_review`. Key correctness rules verified:

- `Pradeep Singh` vs `Pradeep Singh Rajput` → **needs_review** (strict subset,
  never auto-merged) even though `token_set_ratio` is 100.
- `Suresh Prasad` vs `Suresh Prasaad` → **needs_review** (high string score but
  no corroborating FIR/plate/phone/location).
- `Mohammed Khan` vs `Muhammad Khan` **with the same FIR** → **auto_confirmed**
  (transliteration + corroboration).
- `Rakesh V.` vs `Rakesh Verma` now **reaches the review queue** (initials
  expansion) instead of being silently dropped below 85.

## 3. Part 3 — pytest suite + CI

```powershell
.\.venv\Scripts\python.exe -m pytest
```

34 tests pass. Notable suites:

- `tests/test_api_security.py` — 401s, rate limit (429 + Retry-After),
  SQL-injection payloads, `/entities` pagination + `total_count`,
  auth-failure persistence.
- `tests/test_entity_resolution.py` — corroboration rule, transliteration,
  initials, subset pinning.
- `tests/test_ner_adversarial.py` — 25-sentence Indic-name corpus (runs when
  `en_core_web_trf` is present; skipped in CI).
- `tests/test_dedup.py`, `tests/test_storage_review.py`, `tests/test_scheduler.py`.

CI is defined in `.github/workflows/ci.yml` (fast suite on 3.11/3.12 + gitleaks
secret scan). The README badge reflects it.

## 4. Part 4 — new connectors

Each has an offline self-test:

```powershell
python pib_connector.py
python ecourts_connector.py       # parse test
python agency_connector.py        # keyword + NER smoke
python regional_news_connector.py # Devanagari headline -> lead
python state_police_wanted.py     # lists configured portals + robots status
python ncrb_connector.py          # NCRB parse smoke
python wayback_connector.py       # alive-check + CDX smoke
```

And the scheduler now wires all of them in (`scheduler.py`).

## 5. Part 5 — dependencies + secret scanning

```powershell
pip install -r requirements.txt    # adds slowapi, trafilatura, dateparser,
                                   # httpx, pytest/pytest-cov/requests-mock
git diff -- requirements.txt
gitleaks git --pre-commit          # or .pre-commit-config.yaml via pre-commit
```

`.env` and `vajra.db` remain gitignored.

## 6. Part 6 — dashboard with needs_review queue

```powershell
python api.py                      # 127.0.0.1:8000
# open http://localhost:8000/      -> prompted for the API key (stored in localStorage)
```

Dashboard tabs:
- **Records** — search + paginated entity table with `total_count`.
- **Needs Review** — every `needs_review` link with **Confirm / Reject**
  buttons (backed by `POST /links/<a>/<b>/review`; stamped in `link_reviews`).
- **Links** — search a name and see its matched links + `match_status`.

Confirming/rejecting in the UI is immediately reflected in the row.

## 7. Part 7 — deliverables

- `README.md` updated (badge, security, connectors, testing, dashboard).
- `DESIGN.md` — architecture + key-hashing roadmap.
- `DEMO.md` — this file.
- All changes committed with conventional messages and pushed to `main`;
  CI badge reflects the last push's result.

---

### Quick self-check of the whole repo

```powershell
.\.venv\Scripts\python.exe storage.py
.\.venv\Scripts\python.exe entity_extraction.py
.\.venv\Scripts\python.exe entity_resolution.py
.\.venv\Scripts\python.exe -m pytest
```

All must exit 0.
