from __future__ import annotations
import json, re
from pathlib import Path
from typing import Dict, List, Any

STATUS_PATTERNS = [
    ("prosecution_allegation", r"\b(alleged|allegedly|prosecution case|prosecution alleged|according to prosecution)\b"),
    ("court_record", r"\b(judgment records|court record|the judgment states|the order states)\b"),
    ("official_case_record", r"\b(NIA said|NIA reported|official release|press release)\b"),
    ("media_report", r"\b(report says|reported|news report)\b"),
]

def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()

def load_jsonl(path: Path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

def load_entities(case_dir: Path):
    return load_jsonl(case_dir / "entities.jsonl")

def classify_status(text: str, default="inferred") -> str:
    low = text.lower()
    for status, pat in STATUS_PATTERNS:
        if re.search(pat, low):
            return status
    return default

def extract_entities(text: str, entities) -> List[Dict[str, Any]]:
    out, seen = [], set()
    low = text.lower()
    for e in entities:
        variants = [e["name"]] + e.get("aliases", [])
        for v in variants:
            if v and v.lower() in low:
                key = e["entity_id"]
                if key not in seen:
                    out.append({
                        "entity_id": key,
                        "name": e["name"],
                        "entity_type": e["entity_type"],
                        "mention": v
                    })
                    seen.add(key)
                break
    return out

def extract_quantities(text: str) -> List[Dict[str, Any]]:
    hits = []
    patterns = [
        (r"([\d,]+(?:\.\d+)?)\s*kg\b", "kg"),
        (r"([\d,]+(?:\.\d+)?)\s*crore", "crore_inr")
    ]
    for pat, unit in patterns:
        for m in re.finditer(pat, text, flags=re.I):
            hits.append({"value": float(m.group(1).replace(",", "")), "unit": unit, "text": m.group(0)})
    return hits

def extract_routes(text: str):
    routes=[]
    if re.search(r"Afghanistan.*Bandar Abbas.*Mundra", text, re.I):
        routes.append(["Afghanistan","Bandar Abbas","Mundra Port"])
    return routes

def run(case_dir: Path, output_dir: Path):
    output_dir.mkdir(parents=True, exist_ok=True)
    entities = load_entities(case_dir)
    docs = load_jsonl(case_dir / "raw_documents.jsonl")
    preds=[]
    for d in docs:
        ents = extract_entities(d["text"], entities)
        quantities = extract_quantities(d["text"])
        routes = extract_routes(d["text"])
        preds.append({
            "doc_id": d["doc_id"],
            "entities": ents,
            "relationships": [],
            "financial_observations": quantities,
            "events": [],
            "routes": routes,
            "document_status": classify_status(d["text"])
        })
    (output_dir / "predictions.jsonl").write_text(
        "\n".join(json.dumps(x, ensure_ascii=False) for x in preds) + "\n", encoding="utf-8"
    )
    return preds

if __name__ == "__main__":
    import argparse
    p=argparse.ArgumentParser()
    p.add_argument("--case-dir", default=".")
    p.add_argument("--output-dir", default="run_output")
    a=p.parse_args()
    run(Path(a.case_dir), Path(a.output_dir))
