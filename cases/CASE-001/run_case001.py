#!/usr/bin/env python3
"""Single-command CASE-001 pipeline: extraction -> graph analytics -> benchmark."""
from __future__ import annotations
import argparse, subprocess, sys
from pathlib import Path

def main():
    ap=argparse.ArgumentParser(description='Run CASE-001 end-to-end baseline')
    ap.add_argument('--case-dir',required=True)
    ap.add_argument('--output-dir',default='predictions')
    args=ap.parse_args()
    case=Path(args.case_dir); out=Path(args.output_dir); out.mkdir(parents=True,exist_ok=True)
    engine=case/'osint_engine.py'; analyzer=case/'graph_analysis.py'; bench=case/'benchmark_runner.py'
    pred=out/'predictions.jsonl'; graph=out/'network_graph.json'; analysis=out/'graph_analysis.json'; report=out/'evaluation_report.json'
    subprocess.run([sys.executable,str(engine),'--case-dir',str(case),'--output-dir',str(out)],check=True)
    subprocess.run([sys.executable,str(analyzer),'--graph',str(graph),'--output',str(analysis)],check=True)
    subprocess.run([sys.executable,str(bench),'--case-dir',str(case),'--predictions',str(pred),'--output',str(report)],check=True)
    print('\nCASE-001 pipeline complete.')
    print(f'Predictions: {pred}')
    print(f'Graph:       {graph}')
    print(f'Analytics:   {analysis}')
    print(f'Evaluation:  {report}')

if __name__=='__main__': main()
