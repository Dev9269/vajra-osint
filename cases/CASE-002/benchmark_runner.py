from __future__ import annotations
import argparse, json, re
from pathlib import Path
from collections import defaultdict

def load_jsonl(path):
    return [json.loads(x) for x in Path(path).read_text(encoding="utf-8").splitlines() if x.strip()] if Path(path).exists() else []

def p_r_f1(tp, fp, fn):
    p = tp/(tp+fp) if tp+fp else 0.0
    r = tp/(tp+fn) if tp+fn else 0.0
    f = 2*p*r/(p+r) if p+r else 0.0
    return p,r,f

def normalize(s):
    return re.sub(r"[^a-z0-9]+"," ", str(s).lower()).strip()

def entity_key(x):
    return x.get("entity_id") or normalize(x.get("name",""))

def score_entities(pred, gold):
    p=set(entity_key(x) for x in pred)
    g=set(entity_key(x) for x in gold)
    return p,g

def main(case_dir, pred_path, out_path):
    case_dir=Path(case_dir)
    preds={x["doc_id"]:x for x in load_jsonl(pred_path)}
    anns=load_jsonl(case_dir/"benchmark_annotations.jsonl")
    totals=defaultdict(int)
    status_total=status_ok=0

    for a in anns:
        pr=preds.get(a["doc_id"], {})
        pp,gg=score_entities(pr.get("entities",[]), a.get("expected_entities",[]))
        tp=len(pp & gg); fp=len(pp-gg); fn=len(gg-pp)
        totals["entity_tp"]+=tp; totals["entity_fp"]+=fp; totals["entity_fn"]+=fn
        # Relationships in this baseline are only scored when ids are emitted.
        p_rel=set(x.get("relationship_id") for x in pr.get("relationships",[]) if x.get("relationship_id"))
        g_rel=set(x.get("relationship_id") for x in a.get("expected_relationships",[]) if x.get("relationship_id"))
        totals["rel_tp"]+=len(p_rel&g_rel); totals["rel_fp"]+=len(p_rel-g_rel); totals["rel_fn"]+=len(g_rel-p_rel)
        p_fin=set(x.get("observation_id") for x in pr.get("financial_observations",[]) if x.get("observation_id"))
        g_fin=set(x.get("observation_id") for x in a.get("expected_financials",[]) if x.get("observation_id"))
        totals["fin_tp"]+=len(p_fin&g_fin); totals["fin_fp"]+=len(p_fin-g_fin); totals["fin_fn"]+=len(g_fin-p_fin)

    ep,er,ef=p_r_f1(totals["entity_tp"],totals["entity_fp"],totals["entity_fn"])
    rp,rr,rf=p_r_f1(totals["rel_tp"],totals["rel_fp"],totals["rel_fn"])
    fp,fr,ff=p_r_f1(totals["fin_tp"],totals["fin_fp"],totals["fin_fn"])

    report={
        "case_id":"CASE-002",
        "entities":{"precision":ep,"recall":er,"f1":ef},
        "relationships":{"precision":rp,"recall":rr,"f1":rf},
        "financial_observations":{"precision":fp,"recall":fr,"f1":ff},
        "note":"This baseline engine intentionally does not guess unsupported relationships; replacing it with an NER/LLM extractor should be evaluated against the same gold annotations."
    }
    Path(out_path).write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))

if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--case-dir", default=".")
    p.add_argument("--predictions", required=True)
    p.add_argument("--output", default="evaluation_report.json")
    a=p.parse_args()
    main(a.case_dir,a.predictions,a.output)
