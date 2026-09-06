# CASE-001 Dataset

Real public-source OSINT benchmark for SIH26189.

## Core files
- `case.json` — case metadata and legal/data caveats
- `sources.jsonl` — source registry and provenance
- `entities.jsonl` — normalized people, organizations, platforms, clusters and locations
- `relationships.jsonl` — source-backed and qualified graph edges
- `financial_observations.jsonl` — aggregate and person-specific financial observations
- `events.jsonl` — investigation timeline
- `ground_truth.json` — evaluation policy and gold edges

## Design rules
1. Every extracted relationship should retain `source_ids`, `status`, and `confidence`.
2. `agency_claim`, `prosecution_allegation`, and `media_report` are not equivalent to criminal conviction.
3. Aggregate figures are observations tied to a publication date and scope. Do not sum figures that may overlap.
4. Name variants are retained rather than silently normalized.
5. Platforms, payment companies, locations, and investigators are not automatically criminal actors.

## Suggested ingestion path
`raw source -> document chunk -> entity extraction -> entity resolution -> claim/relationship extraction -> provenance -> temporal graph -> analytics`

## Next benchmark layer
Add raw text snippets from each source and ask the pipeline to regenerate entities/claims without seeing `ground_truth.json`. Score entity-resolution F1, relation precision/recall, temporal consistency, and false-positive suppression.

## Benchmark layer
- `raw_documents.jsonl` — six bounded source excerpts/paraphrases for blind NLP/OSINT extraction.
- `benchmark_annotations.jsonl` — gold mappings from each document to entity, relationship, financial-observation, and event IDs. Keep this file hidden from the extraction pipeline during testing.
- `benchmark_spec.json` — scoring protocol and test modes.

### Benchmark rule
Run the pipeline on `raw_documents.jsonl` without loading `benchmark_annotations.jsonl` or `ground_truth.json`. Compare outputs afterward. The benchmark intentionally contains source-stage variations and name variants so the pipeline must preserve provenance and uncertainty.
