from __future__ import annotations
import argparse, subprocess, sys, json
from pathlib import Path

def run(cmd, cwd):
    print(">", " ".join(map(str,cmd)))
    subprocess.run(cmd, cwd=cwd, check=True)

if __name__=="__main__":
    p=argparse.ArgumentParser(description="Run CASE-002 end-to-end offline baseline.")
    p.add_argument("--case-dir", default=".")
    p.add_argument("--output-dir", default="run_output")
    a=p.parse_args()
    case=Path(a.case_dir).resolve()
    out=case/ a.output_dir
    out.mkdir(parents=True, exist_ok=True)

    run([sys.executable,"osint_engine.py","--case-dir",str(case),"--output-dir",str(out)], case)
    run([sys.executable,"graph_analysis.py","--case-dir",str(case),"--output",str(out/"graph_analysis.json")], case)
    run([sys.executable,"benchmark_runner.py","--case-dir",str(case),"--predictions",str(out/"predictions.jsonl"),"--output",str(out/"evaluation_report.json")], case)

    print("\nCASE-002 complete. Outputs:")
    for pth in [out/"predictions.jsonl",out/"graph_analysis.json",out/"evaluation_report.json"]:
        print(" -", pth)
