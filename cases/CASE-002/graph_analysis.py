from __future__ import annotations
import json
from pathlib import Path
from collections import defaultdict, deque

def load_jsonl(path: Path):
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()] if path.exists() else []

def build_graph(case_dir: Path):
    entities=load_jsonl(case_dir/"entities.jsonl")
    rels=load_jsonl(case_dir/"relationships.jsonl")
    nodes={e["entity_id"]:e for e in entities}
    edges=[]
    adj=defaultdict(set)
    for r in rels:
        s,t=r.get("source_entity"),r.get("target_entity")
        if s in nodes and t in nodes:
            edges.append(r)
            adj[s].add(t); adj[t].add(s)
    return nodes, edges, adj

def connected_components(adj):
    seen=set(); comps=[]
    for n in adj:
        if n in seen: continue
        q=[n]; seen.add(n); comp=[]
        while q:
            x=q.pop(); comp.append(x)
            for y in adj[x]:
                if y not in seen:
                    seen.add(y); q.append(y)
        comps.append(sorted(comp))
    return comps

def degree_centrality(adj):
    deg={n:len(v) for n,v in adj.items()}
    return sorted(deg.items(), key=lambda kv:(-kv[1], kv[0]))

def shortest_path(adj, start, target):
    if start==target: return [start]
    q=deque([start]); prev={start:None}
    while q:
        x=q.popleft()
        for y in adj[x]:
            if y not in prev:
                prev[y]=x; q.append(y)
                if y==target:
                    path=[]; cur=y
                    while cur is not None:
                        path.append(cur); cur=prev[cur]
                    return path[::-1]
    return []

def analyze(case_dir: Path, out: Path):
    nodes, edges, adj = build_graph(case_dir)
    result={
        "node_count":len(nodes),
        "edge_count":len(edges),
        "components":connected_components(adj),
        "degree_centrality":[{"entity_id":n,"degree":d} for n,d in degree_centrality(adj)],
        "key_routes":[shortest_path(adj,"P001","O002"), shortest_path(adj,"P001","L001")],
        "legal_status_counts":{}
    }
    for r in edges:
        s=r.get("status","unknown")
        result["legal_status_counts"][s]=result["legal_status_counts"].get(s,0)+1
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result

if __name__=="__main__":
    import argparse
    p=argparse.ArgumentParser()
    p.add_argument("--case-dir", default=".")
    p.add_argument("--output", default="graph_analysis.json")
    a=p.parse_args()
    analyze(Path(a.case_dir), Path(a.output))
