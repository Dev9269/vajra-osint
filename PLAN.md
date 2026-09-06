# VAJRA Layer 3 — Agent Split

## Agent A (OpenCode) — Ingestion Lane ✅ CLAIMED

| File | Purpose |
|------|---------|
| `storage.py` | SQLite schema — `records` table, `entity_links` table, insert/query helpers |
| `wikipedia_connector.py` | Wikipedia Action API integration — category members → plain-text extracts |
| `news_connector.py` | RSS feed polling via feedparser — keyword-filtered headline + summary storage |
| `gov_portal_connector.py` | Template-based government portal scraper — robots.txt + rate limiting |

## Agent B (Claude Code) — Intelligence / Serving Lane

| File | Purpose |
|------|---------|
| `entity_extraction.py` | spaCy NER + regex for structured identifiers (phone, plates, FIR numbers) |
| `entity_resolution.py` | Fuzzy name matching via rapidfuzz token_set_ratio |
| `scheduler.py` | APScheduler long-lived process — news 4h, wikipedia/gov daily, resolution nightly |
| `api.py` | FastAPI REST layer — /entities, /entities/<name>, /entities/<name>/links |

## Shared Contract

Agent B must read `storage.py` before writing any code that calls it. If a function signature or table column changes, note it at the top of the edited file (one-line comment: what changed and why).

## Integration Checkpoint

Once both lanes are done, whichever agent finishes second wires `scheduler.py` imports against the actual connector function names from the committed files — not against names in this doc.
