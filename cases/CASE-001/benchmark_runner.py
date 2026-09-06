#!/usr/bin/env python3
"""
CASE-001 OSINT Extraction Benchmark Runner

Usage:
    python benchmark_runner.py --case-dir ./CASE-001 --predictions predictions.jsonl

Prediction format (one JSON object per line):
{
  "doc_id": "DOC001",
  "entities": [
      {"entity_id": "P001"},
      {"name": "Sayid Muhammed"}
  ],
  "relationships": [
      {"relationship_id": "R001"},
      {"source_entity": "P001", "relation": "allegedly_arranged", "target_entity": "CL001",
       "status": "agency_claim"}
  ],
  "financial_observations": [{"observation_id": "FO001"}],
  "events": [{"event_id": "E005"}]
}

The evaluator is intentionally conservative: it does not infer guilt, and it
penalizes relationship predictions that omit a gold provenance/legal-status
label when the gold relation has one.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple


def load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def load_jsonl(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSONL at {path}:{line_no}: {exc}") from exc
    return rows


def norm_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).casefold()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def f1(precision: float, recall: float) -> float:
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def prf(tp: int, fp: int, fn: int) -> Dict[str, float]:
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    return {
        "precision": precision,
        "recall": recall,
        "f1": f1(precision, recall),
        "tp": tp,
        "fp": fp,
        "fn": fn,
    }


def build_entity_maps(entities: Iterable[Dict[str, Any]]) -> Tuple[Dict[str, str], Dict[str, str]]:
    alias_to_id: Dict[str, str] = {}
    id_to_canonical: Dict[str, str] = {}
    for e in entities:
        eid = e["entity_id"]
        canonical = e.get("canonical_name", eid)
        id_to_canonical[eid] = canonical
        for candidate in [canonical, *e.get("aliases", [])]:
            key = norm_text(candidate)
            if key:
                alias_to_id[key] = eid
    return alias_to_id, id_to_canonical


def resolve_entity(value: Any, alias_to_id: Dict[str, str]) -> Optional[str]:
    if value is None:
        return None
    text = str(value)
    # Stable IDs win immediately.
    if re.fullmatch(r"(?:P|O|PL|L|CL)\d+", text):
        return text
    return alias_to_id.get(norm_text(text))


def relation_key(row: Dict[str, Any], alias_to_id: Dict[str, str]) -> Tuple[str, str, str]:
    source = row.get("source_entity") or row.get("source")
    target = row.get("target_entity") or row.get("target")
    relation = row.get("relation") or row.get("relationship")
    source_id = resolve_entity(source, alias_to_id) or str(source)
    target_id = resolve_entity(target, alias_to_id) or str(target)
    return source_id, str(relation), target_id


def build_gold_relationships(rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    return {r["relationship_id"]: r for r in rows}


def relation_status_correct(pred: Dict[str, Any], gold: Dict[str, Any]) -> bool:
    gold_status = gold.get("status")
    if not gold_status:
        return True
    return pred.get("status") == gold_status


def load_predictions(path: Path) -> Dict[str, Dict[str, Any]]:
    """Merge duplicate doc_id rows while keeping first-seen fields."""
    predictions: Dict[str, Dict[str, Any]] = {}
    for row in load_jsonl(path):
        doc_id = row.get("doc_id")
        if not doc_id:
            raise ValueError("Every prediction row must contain doc_id")
        existing = predictions.setdefault(
            doc_id,
            {"doc_id": doc_id, "entities": [], "relationships": [],
             "financial_observations": [], "events": []},
        )
        for field in ("entities", "relationships", "financial_observations", "events"):
            values = row.get(field, [])
            if not isinstance(values, list):
                raise ValueError(f"{doc_id}.{field} must be a list")
            existing[field].extend(values)
    return predictions


def as_id_set(items: List[Any], key: str) -> Set[str]:
    out: Set[str] = set()
    for item in items:
        if isinstance(item, str):
            out.add(item)
        elif isinstance(item, dict) and item.get(key):
            out.add(str(item[key]))
    return out


def dedupe_preserve(values: Iterable[str]) -> List[str]:
    return list(dict.fromkeys(values))


def evaluate_entities(
    gold_annotations: Dict[str, List[str]],
    predictions: Dict[str, Dict[str, Any]],
    alias_to_id: Dict[str, str],
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    tp = fp = fn = 0
    per_doc: Dict[str, Any] = {}
    for doc_id, gold_ids in gold_annotations.items():
        gold = set(gold_ids)
        pred_ids: Set[str] = set()
        for item in predictions.get(doc_id, {}).get("entities", []):
            if isinstance(item, str):
                resolved = resolve_entity(item, alias_to_id)
            else:
                resolved = item.get("entity_id") or resolve_entity(item.get("name") or item.get("text"), alias_to_id)
            if resolved:
                pred_ids.add(resolved)
        doc_tp = len(gold & pred_ids)
        doc_fp = len(pred_ids - gold)
        doc_fn = len(gold - pred_ids)
        tp += doc_tp; fp += doc_fp; fn += doc_fn
        per_doc[doc_id] = prf(doc_tp, doc_fp, doc_fn)
    return prf(tp, fp, fn), per_doc


def evaluate_relationships(
    gold_annotations: Dict[str, List[str]],
    gold_relations: Dict[str, Dict[str, Any]],
    predictions: Dict[str, Dict[str, Any]],
    alias_to_id: Dict[str, str],
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    # Gold is keyed by relationship_id, but predictions may provide IDs or triples.
    gold_key_by_doc: Dict[str, Set[str]] = {k: set(v) for k, v in gold_annotations.items()}
    tp = fp = fn = 0
    provenance_correct = 0
    provenance_total = 0
    per_doc: Dict[str, Any] = {}

    gold_lookup_by_key: Dict[Tuple[str, str, str], str] = {}
    for rid, rel in gold_relations.items():
        gold_lookup_by_key[relation_key(rel, alias_to_id)] = rid

    for doc_id, gold_ids in gold_key_by_doc.items():
        gold_set = set(gold_ids)
        pred_ids: Set[str] = set()
        pred_rows: List[Dict[str, Any]] = []
        for item in predictions.get(doc_id, {}).get("relationships", []):
            if isinstance(item, str):
                rid = item
                row = {"relationship_id": rid}
            else:
                row = item
                rid = item.get("relationship_id")
                if not rid and item.get("source_entity") and (item.get("relation") or item.get("relationship")) and item.get("target_entity"):
                    rid = gold_lookup_by_key.get(relation_key(item, alias_to_id))
            if rid:
                pred_ids.add(str(rid))
                pred_rows.append(row)

        doc_tp = len(gold_set & pred_ids)
        doc_fp = len(pred_ids - gold_set)
        doc_fn = len(gold_set - pred_ids)
        tp += doc_tp; fp += doc_fp; fn += doc_fn

        # Provenance is evaluated only for matched relationships where a status is present in gold.
        matched = gold_set & pred_ids
        for rid in matched:
            gold = gold_relations.get(rid, {})
            if not gold.get("status"):
                continue
            pred_candidates = [r for r in pred_rows if str(r.get("relationship_id", "")) == rid]
            provenance_total += 1
            if pred_candidates and relation_status_correct(pred_candidates[0], gold):
                provenance_correct += 1

        per_doc[doc_id] = {
            **prf(doc_tp, doc_fp, doc_fn),
            "provenance_accuracy": (
                sum(
                    1 for rid in matched
                    if gold_relations.get(rid, {}).get("status") and any(
                        relation_status_correct(r, gold_relations[rid])
                        for r in pred_rows if str(r.get("relationship_id", "")) == rid
                    )
                ) / max(
                    sum(1 for rid in matched if gold_relations.get(rid, {}).get("status")), 1
                )
            ) if matched else None,
        }

    metrics = prf(tp, fp, fn)
    metrics["provenance_accuracy"] = provenance_correct / provenance_total if provenance_total else None
    metrics["provenance_evaluated"] = provenance_total
    return metrics, per_doc


def evaluate_id_lists(
    gold_annotations: Dict[str, List[str]],
    predictions: Dict[str, Dict[str, Any]],
    field: str,
    id_key: str,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    tp = fp = fn = 0
    per_doc: Dict[str, Any] = {}
    for doc_id, gold_ids in gold_annotations.items():
        gold = set(gold_ids)
        pred = as_id_set(predictions.get(doc_id, {}).get(field, []), id_key)
        doc_tp = len(gold & pred)
        doc_fp = len(pred - gold)
        doc_fn = len(gold - pred)
        tp += doc_tp; fp += doc_fp; fn += doc_fn
        per_doc[doc_id] = prf(doc_tp, doc_fp, doc_fn)
    return prf(tp, fp, fn), per_doc


def evaluate_temporal_fields(
    gold_events: List[Dict[str, Any]],
    predictions: Dict[str, Dict[str, Any]],
    gold_annotations: Dict[str, List[str]],
) -> Dict[str, Any]:
    gold_by_id = {e["event_id"]: e for e in gold_events}
    total = correct = 0
    for doc_id, gold_event_ids in gold_annotations.items():
        pred_rows = predictions.get(doc_id, {}).get("events", [])
        pred_map = {str(r.get("event_id")): r for r in pred_rows if isinstance(r, dict) and r.get("event_id")}
        for eid in gold_event_ids:
            if eid not in gold_by_id:
                continue
            gold_date = gold_by_id[eid].get("event_date")
            pred_date = pred_map.get(eid, {}).get("event_date")
            total += 1
            if pred_date == gold_date:
                correct += 1
    return {"accuracy": correct / total if total else None, "correct": correct, "evaluated": total}


def evaluate_financial_fields(
    gold_financials: List[Dict[str, Any]],
    predictions: Dict[str, Dict[str, Any]],
    gold_annotations: Dict[str, List[str]],
) -> Dict[str, Any]:
    gold_by_id = {f["observation_id"]: f for f in gold_financials}
    total = correct = 0
    fields = ("metric", "value", "currency", "scope", "date")
    field_correct = defaultdict(int)
    field_total = defaultdict(int)

    for doc_id, gold_obs_ids in gold_annotations.items():
        pred_rows = predictions.get(doc_id, {}).get("financial_observations", [])
        pred_map = {str(r.get("observation_id")): r for r in pred_rows if isinstance(r, dict) and r.get("observation_id")}
        for oid in gold_obs_ids:
            gold = gold_by_id.get(oid)
            if not gold:
                continue
            pred = pred_map.get(oid)
            total += 1
            if not pred:
                continue
            all_ok = True
            for field in fields:
                field_total[field] += 1
                if pred.get(field) == gold.get(field):
                    field_correct[field] += 1
                else:
                    all_ok = False
            if all_ok:
                correct += 1

    field_acc = {k: field_correct[k] / field_total[k] if field_total[k] else None for k in fields}
    return {
        "accuracy": correct / total if total else None,
        "exact_matches": correct,
        "evaluated": total,
        "field_accuracy": field_acc,
        "warning": "Financial observations are matched as source-scoped observations; stage-dependent aggregates must not be summed.",
    }


def evaluate_false_positive_suppression(predictions: Dict[str, Dict[str, Any]], gold_entities: Set[str]) -> Dict[str, Any]:
    """Simple guardrail metric: company/platform IDs are not marked as criminal by role/status text.

    This is a static sanity check over predicted entity metadata. It does not attempt to judge guilt.
    """
    risky_markers = {"criminal", "convicted", "criminal organization", "guilty", "offender"}
    violations: List[Dict[str, Any]] = []
    checked = 0
    for doc_id, pred in predictions.items():
        for item in pred.get("entities", []):
            if not isinstance(item, dict):
                continue
            eid = item.get("entity_id")
            if eid not in gold_entities:
                continue
            checked += 1
            text = norm_text(item.get("legal_status") or item.get("role") or item.get("status"))
            if any(marker in text for marker in risky_markers):
                violations.append({"doc_id": doc_id, "entity_id": eid, "text": text})
    return {
        "checked_entity_predictions": checked,
        "violations": len(violations),
        "details": violations,
        "accuracy": 1.0 if not violations else max(0.0, 1 - len(violations) / max(checked, 1)),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate CASE-001 OSINT extraction predictions.")
    parser.add_argument("--case-dir", required=True, help="Path containing the CASE-001 benchmark files")
    parser.add_argument("--predictions", required=True, help="Prediction JSONL generated by your OSINT engine")
    parser.add_argument("--output", default="evaluation_report.json", help="Path for machine-readable report")
    args = parser.parse_args()

    case_dir = Path(args.case_dir)
    pred_path = Path(args.predictions)
    out_path = Path(args.output)

    required = [
        "entities.jsonl", "relationships.jsonl", "events.jsonl",
        "financial_observations.jsonl", "benchmark_annotations.jsonl",
    ]
    missing = [name for name in required if not (case_dir / name).exists()]
    if missing:
        print(f"Missing benchmark files: {', '.join(missing)}", file=sys.stderr)
        return 2

    entities = load_jsonl(case_dir / "entities.jsonl")
    relationships = load_jsonl(case_dir / "relationships.jsonl")
    events = load_jsonl(case_dir / "events.jsonl")
    financials = load_jsonl(case_dir / "financial_observations.jsonl")
    annotations = load_jsonl(case_dir / "benchmark_annotations.jsonl")
    predictions = load_predictions(pred_path)

    alias_to_id, _ = build_entity_maps(entities)
    gold_relations = build_gold_relationships(relationships)

    ann_entities = {r["doc_id"]: r.get("gold_entities", []) for r in annotations}
    ann_relations = {r["doc_id"]: r.get("gold_relationships", []) for r in annotations}
    ann_financials = {r["doc_id"]: r.get("gold_financial_observations", []) for r in annotations}
    ann_events = {r["doc_id"]: r.get("gold_events", []) for r in annotations}

    entity_metrics, entity_by_doc = evaluate_entities(ann_entities, predictions, alias_to_id)
    relation_metrics, relation_by_doc = evaluate_relationships(ann_relations, gold_relations, predictions, alias_to_id)
    event_metrics, event_by_doc = evaluate_id_lists(ann_events, predictions, "events", "event_id")
    fin_id_metrics, fin_id_by_doc = evaluate_id_lists(ann_financials, predictions, "financial_observations", "observation_id")
    temporal_metrics = evaluate_temporal_fields(events, predictions, ann_events)
    financial_metrics = evaluate_financial_fields(financials, predictions, ann_financials)

    gold_entity_ids = {e["entity_id"] for e in entities}
    guardrail_metrics = evaluate_false_positive_suppression(predictions, gold_entity_ids)

    report = {
        "benchmark": "CASE-001 OSINT Extraction Benchmark v1",
        "not_a_finding_of_guilt": True,
        "metrics": {
            "entity_extraction": entity_metrics,
            "relationship_extraction": relation_metrics,
            "event_extraction": event_metrics,
            "financial_observation_id_match": fin_id_metrics,
            "temporal_accuracy": temporal_metrics,
            "financial_field_accuracy": financial_metrics,
            "false_positive_suppression": guardrail_metrics,
        },
        "per_document": {
            "entities": entity_by_doc,
            "relationships": relation_by_doc,
            "events": event_by_doc,
            "financial_observation_ids": fin_id_by_doc,
        },
        "prediction_coverage": {
            "gold_documents": len(annotations),
            "predicted_documents": len(predictions),
            "missing_prediction_documents": sorted(set(ann_entities) - set(predictions)),
        },
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    # Human-readable console summary
    print("\n=== CASE-001 OSINT BENCHMARK ===")
    print(f"Entities      P={entity_metrics['precision']:.3f} R={entity_metrics['recall']:.3f} F1={entity_metrics['f1']:.3f}")
    print(f"Relationships  P={relation_metrics['precision']:.3f} R={relation_metrics['recall']:.3f} F1={relation_metrics['f1']:.3f}")
    print(f"Provenance     Acc={relation_metrics['provenance_accuracy'] if relation_metrics['provenance_accuracy'] is not None else float('nan'):.3f}")
    print(f"Events         P={event_metrics['precision']:.3f} R={event_metrics['recall']:.3f} F1={event_metrics['f1']:.3f}")
    print(f"Finance IDs    P={fin_id_metrics['precision']:.3f} R={fin_id_metrics['recall']:.3f} F1={fin_id_metrics['f1']:.3f}")
    print(f"Temporal       Acc={temporal_metrics['accuracy'] if temporal_metrics['accuracy'] is not None else float('nan'):.3f}")
    print(f"False-positive Acc={guardrail_metrics['accuracy']:.3f} | violations={guardrail_metrics['violations']}")
    print(f"\nReport written to: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
