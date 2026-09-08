# VAJRA Layer 3 — Design Notes & Roadmap

Design decisions and forward-looking security roadmap for the VAJRA OSINT
aggregator. Keep this updated as the architecture evolves.

## Runtime topology (as of SIH 2026 round)

```
scraper/connectors ──► storage.py (SQLite) ◄── api.py (FastAPI, 127.0.0.1)
       │                        │                        │
       │          scheduler.py  │            dashboard (same origin)
       └─ entity_extraction ────┘
       └─ entity_resolution ────┘
```

- All components talk to SQLite only through `storage.py` — the REST API is
  the only surface exposed to the browser/dashboard.
- `api.py` binds `127.0.0.1` by default. `0.0.0.0` is allowed **only** when
  `VAJRA_ENV=production` **and** a permanent `VAJRA_API_KEY` is set (see the
  guard in the `__main__` block). A throwaway key rotates on restart and
  strands any client that saved it, so production refuses to start without a
  permanent key.
- Rate limiting (slowapi): 60 req/min per API key on authenticated routes,
  10 req/min per IP on the open routes (`/health`, `/docs`). Exceeded
  requests get `429` with a `Retry-After` header computed from the limit
  window (storage-independent; see `rate_limit_handler`).

## API key model — current state

- Key is compared with `secrets.compare_digest` (timing-safe).
- Failed auth is logged at WARNING (ip, endpoint, UTC timestamp) **and**
  persisted to the `auth_failures` table for an admin `GET
  /security/auth-failures` view (brute-force triage).
- The key travels in an `X-API-Key` header (not query string → not logged in
  proxies/access logs).

## Roadmap: key hashing

Why: today a DB dump or debugger memory read leaks the raw key. Hashing kills
that: the server stores only a digest, never the plaintext key.

Steps (in order, each independently shippable):

1. **Binder-lock generated keys (recommended first).** Give generated keys a
   distinguishing prefix, e.g. `vajra_live_<32 random base62 chars>`. This
   lets the server recognise *its own* generated keys vs arbitrary strings
   the operator pasted from memory (which are already unknown bytes to
   anyone but the operator).

2. **Store a digest, not the key.** On first use / on key rotation, store
   `sha256("vajra:" + key)` in a new `api_keys` table instead of keeping the
   raw key env var around. Auth compares HashedKey (HMAC, constant time)
   against the digest. The env var `VAJRA_API_KEY` then only needs to exist
   on the node that *generated* the key (or an encrypted secret store), and
   can be scrubbed from process memory after startup.

3. **Add `+secret` key support for the dashboard.** The browser dashboard
   should not have the master key (it lives on the operator's machine or a
   vault). Issue scoped child keys (`vajra_dash_...`) with
   **read-only + review** privileges. `link_reviews.reviewer` is stamped
   from the child key's owner id so actions are auditable per operator.

4. **Rotation CLI.** `python generate_api_key.py --revoke <id>` / `--list`
   and a `vajra rotate-key` for the operator. Old keys are disabled by
   backdating `active_until` rather than deleting — dashboards get a clean
   `401` with a clear "key expired" hint instead of a confusing auth error.

5. *(If needed)* Hash-KDF relief under brute force: keep the prefix check
   (step 1) as a cheap pre-filter so mass online guessing never reaches the
   KDF. Offline DB dumps still face the KDF work factor.

### API auth-failure policy

- 429 from rate limiting is distinct from 401 (auth) — clients must not
  retry 401s with the same key (that is what the auth_failures view is for).
- Threshold alerting (e.g. >5 failed attempts from one IP in a minute) is
  deliberately NOT in the app: it belongs in a log sink / SIEM. The WARNING
  logs and auth_failures table are the raw material for it.

## Entity resolution — corroboration rule

- `MATCH_THRESHOLD = 85` → candidate pairs below this never become links.
- `REVIEW_THRESHOLD = 95` → auto-confirm **only** when score ≥ 95 AND at
  least one corroborating field (shared FIR number, plate, phone, case id)
  — see `entity_resolution.py`.
- Between 85 and 95 → `needs_review`; operator confirms/rejects through the
  dashboard (`POST /links/{a}/{b}/review`).
- Strict string-subset pairs are pinned just below the auto-confirm line so
  "Rakesh" vs "Rakesh Kumar Verma" can never silently auto-merge.

## Connectors (Part 4)

- `gov_portal_connector.py` defines the per-portal pattern: robots.txt check
  → rate-limited polite fetch (2s delay, browser UA) → CSS/attribute
  extraction → normalized row → storage. New portals go in the same file or
  sibling modules reusing its helpers.
- Static pages that are known-good by judges/mentors are where value is;
  live government portals are flaky — connector failures must be logged and
  never crash the scheduler (see `scheduler.py`).
- Wayback Machine fallback is for *dead links already in the DB* (404
  detection during reconciliation), not for live-crawling.

## Testing approach

- `tests/` is pytest-based; CI (GitHub Actions) runs it on every push with
  the trf model SKIPPED by default (README badge reflects the skip). Full
  suite with the model: `.venv` install + `python -m spacy` model download.
- Every module keeps a `__main__` self-test so `python storage.py` etc. can
  verify in isolation — CI cherry-picks these too via `tests/test_selftests.py`.
- `adversarial_ner.py` holds a 25-sentence Indic-name corpus used by both a
  pytest and the DEMO.