#!/usr/bin/env python3
"""Offline graph analytics for CASE-001 network_graph.json.

No third-party packages required. Computes degree, weighted degree, betweenness
centrality (exact Brandes algorithm for an unweighted graph), connected components,
and a simple suspiciousness ranking based on graph position + diversity of links.
It is an analytical aid, not a guilt classifier.
"""
from __future__ import annotations
import argparse, json
from collections import defaultdict, deque
from pathlib import Path
from typing import Dict, List, Any, Set


def load(path: Path):
    return json.loads(path.read_text(encoding='utf-8'))


def adjacency(graph):
    adj=defaultdict(set)
    edge_count=defaultdict(int)
    for e in graph.get('edges',[]):
        s,t=e.get('source'),e.get('target')
        if not s or not t: continue
        adj[s].add(t); adj[t].add(s)
        edge_count[(s,t)] += 1
        edge_count[(t,s)] += 1
    return adj, edge_count


def components(adj):
    seen=set(); out=[]
    for start in adj:
        if start in seen: continue
        q=[start]; seen.add(start); comp=[]
        while q:
            u=q.pop(); comp.append(u)
            for v in adj[u]:
                if v not in seen: seen.add(v); q.append(v)
        out.append(sorted(comp))
    return sorted(out,key=lambda x:(-len(x),x))


def betweenness(adj):
    nodes=list(adj)
    cb={v:0.0 for v in nodes}
    for s in nodes:
        stack=[]; pred={w:[] for w in nodes}; sigma={w:0 for w in nodes}; dist={w:-1 for w in nodes}
        sigma[s]=1; dist[s]=0; q=deque([s])
        while q:
            v=q.popleft(); stack.append(v)
            for w in adj[v]:
                if dist[w]<0:
                    q.append(w); dist[w]=dist[v]+1
                if dist[w]==dist[v]+1:
                    sigma[w]+=sigma[v]; pred[w].append(v)
        delta={w:0.0 for w in nodes}
        while stack:
            w=stack.pop()
            for v in pred[w]:
                delta[v]+= (sigma[v]/sigma[w])*(1+delta[w]) if sigma[w] else 0
            if w!=s: cb[w]+=delta[w]
    if len(nodes)>2:
        scale=1/2 # undirected graph
        for v in cb: cb[v]*=scale
    return cb


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--graph',required=True)
    ap.add_argument('--output',default='graph_analysis.json')
    args=ap.parse_args()
    path=Path(args.graph); graph=load(path); adj,weights=adjacency(graph)
    between=betweenness(adj); comps=components(adj)
    node_meta={n['entity_id']:n for n in graph.get('nodes',[])}
    rows=[]
    for n in adj:
        neighbors=adj[n]
        relation_diversity=set()
        for e in graph.get('edges',[]):
            if e.get('source')==n or e.get('target')==n:
                if e.get('relation'): relation_diversity.add(e['relation'])
        score=(len(neighbors)*1.0)+(between.get(n,0)*2.0)+(len(relation_diversity)*0.5)
        rows.append({
            'entity_id':n,
            'name':node_meta.get(n,{}).get('name',n),
            'entity_type':node_meta.get(n,{}).get('entity_type'),
            'degree':len(neighbors),
            'betweenness_centrality':round(between.get(n,0),6),
            'relation_diversity':len(relation_diversity),
            'analytical_priority_score':round(score,6),
        })
    rows.sort(key=lambda r:(-r['analytical_priority_score'],r['entity_id']))
    result={
        'disclaimer':'Analytical prioritization only; not a finding of guilt or criminality.',
        'component_count':len(comps),
        'components':comps,
        'ranked_nodes':rows,
        'top_priority':rows[:10],
    }
    Path(args.output).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'component_count':len(comps),'top_priority':rows[:5]},ensure_ascii=False,indent=2))

if __name__=='__main__': main()
