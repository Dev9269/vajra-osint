# CASE-002 — Mundra Port Narcotics Trafficking Network

Real public-source OSINT benchmark for an AI-powered criminal-network analysis system.

## Sources
Primary sources are NIA releases and court records. News reports are included as supplementary OSINT and are explicitly labeled.

## Important legal/data rule
This dataset preserves the distinction between:
- officially reported facts
- court-recorded procedural facts
- prosecution allegations
- media reporting
- inferred links

It must not be presented as a list of convictions.

## What this case tests
1. Person alias resolution (e.g. Harpreet Singh Talwar / Kabir Talwar).
2. Organization/person role extraction.
3. International route extraction: Afghanistan -> Bandar Abbas -> Mundra.
4. Shipment identity resolution across documents.
5. Court-vs-news provenance.
6. Source-specific numeric discrepancy handling.
7. Timeline construction.
8. Graph centrality / connectivity.

## Run
    python run_case002.py --case-dir . --output-dir run_output

Outputs:
- run_output/predictions.jsonl
- run_output/graph_analysis.json
- run_output/evaluation_report.json

## Note
The included osint_engine.py is an offline deterministic baseline. It is not a trained intelligence model. Plug your team's real NER/LLM extractor into the same prediction schema and rerun the evaluator.
