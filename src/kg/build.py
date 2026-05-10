"""T-N05: Build a NetworkX KG from extract.py output (new schema).

Input file format (from extract.py):
{
  "book_id": "...",
  "nodes": [{id, name, definition, category, chapter, page, book_id, chunk_id}],
  "edges": [{source, target, relation_type, description, book_id, chunk_id}]
}
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from pathlib import Path
from typing import Any

import networkx as nx

log = logging.getLogger(__name__)


def build_graph(extract_doc: dict[str, Any]) -> nx.MultiDiGraph:
    g = nx.MultiDiGraph()
    raw_nodes = extract_doc.get("nodes", [])
    raw_edges = extract_doc.get("edges", [])

    nm: dict[str, dict[str, Any]] = {}
    for n in raw_nodes:
        nid = n["id"]
        if nid not in nm:
            nm[nid] = {
                "id": nid,
                "name": n["name"],
                "definition": n.get("definition", ""),
                "alt_definitions": [],
                "category": n.get("category", "概念"),
                "book_ids": set(),
                "chapters": set(),
                "pages": set(),
                "chunk_ids": set(),
                "n_mentions": 0,
            }
        rec = nm[nid]
        rec["n_mentions"] += 1
        if n.get("book_id"):
            rec["book_ids"].add(n["book_id"])
        if n.get("chapter"):
            rec["chapters"].add(n["chapter"])
        if n.get("page", -1) >= 0:
            rec["pages"].add(int(n["page"]))
        if n.get("chunk_id"):
            rec["chunk_ids"].add(n["chunk_id"])
        new_def = n.get("definition", "")
        if len(new_def) > len(rec["definition"]):
            if rec["definition"] and rec["definition"] not in rec["alt_definitions"]:
                rec["alt_definitions"].append(rec["definition"])
            rec["definition"] = new_def
        elif new_def and new_def != rec["definition"] and new_def not in rec["alt_definitions"]:
            rec["alt_definitions"].append(new_def)

    for nid, rec in nm.items():
        g.add_node(
            nid,
            name=rec["name"],
            definition=rec["definition"],
            alt_definitions=rec["alt_definitions"],
            category=rec["category"],
            book_ids=sorted(rec["book_ids"]),
            chapters=sorted(rec["chapters"]),
            pages=sorted(rec["pages"]),
            chunk_ids=sorted(rec["chunk_ids"]),
            n_mentions=rec["n_mentions"],
        )

    em: dict[tuple[str, str, str], dict[str, Any]] = {}
    for e in raw_edges:
        s, t, rt = e["source"], e["target"], e["relation_type"]
        if s not in nm or t not in nm:
            continue
        key = (s, t, rt)
        if key not in em:
            em[key] = {
                "relation_type": rt,
                "descriptions": [],
                "book_ids": set(),
                "chunk_ids": set(),
                "weight": 0,
            }
        a = em[key]
        a["weight"] += 1
        if e.get("description") and e["description"] not in a["descriptions"]:
            a["descriptions"].append(e["description"])
        if e.get("book_id"):
            a["book_ids"].add(e["book_id"])
        if e.get("chunk_id"):
            a["chunk_ids"].add(e["chunk_id"])

    for (s, t, rt), a in em.items():
        g.add_edge(
            s, t, key=rt,
            relation_type=rt,
            descriptions=a["descriptions"],
            book_ids=sorted(a["book_ids"]),
            chunk_ids=sorted(a["chunk_ids"]),
            weight=a["weight"],
        )
    return g


def to_json(g: nx.MultiDiGraph) -> dict[str, Any]:
    nodes = []
    for n, d in g.nodes(data=True):
        nodes.append({
            "id": n,
            "name": d.get("name", n),
            "definition": d.get("definition", ""),
            "alt_definitions": d.get("alt_definitions", []),
            "category": d.get("category", "概念"),
            "book_ids": d.get("book_ids", []),
            "chapters": d.get("chapters", []),
            "pages": d.get("pages", []),
            "chunk_ids": d.get("chunk_ids", []),
            "n_mentions": d.get("n_mentions", 1),
        })
    edges = []
    for u, v, k, d in g.edges(data=True, keys=True):
        edges.append({
            "source": u,
            "target": v,
            "relation_type": d.get("relation_type", k),
            "descriptions": d.get("descriptions", []),
            "book_ids": d.get("book_ids", []),
            "chunk_ids": d.get("chunk_ids", []),
            "weight": d.get("weight", 1),
        })
    return {"nodes": nodes, "edges": edges}


def graph_stats(g: nx.MultiDiGraph) -> dict[str, Any]:
    cats: dict[str, int] = defaultdict(int)
    for _, d in g.nodes(data=True):
        cats[d.get("category", "概念")] += 1
    rels: dict[str, int] = defaultdict(int)
    for _, _, d in g.edges(data=True):
        rels[d.get("relation_type", "?")] += 1
    return {
        "nodes": g.number_of_nodes(),
        "edges": g.number_of_edges(),
        "categories": dict(cats),
        "relations": dict(rels),
    }


def save(g: nx.MultiDiGraph, path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    json_path = path.with_suffix(".json")
    json_path.write_text(json.dumps(to_json(g), ensure_ascii=False, indent=2), encoding="utf-8")
    g2 = nx.MultiDiGraph()
    for n, d in g.nodes(data=True):
        g2.add_node(n,
            name=d.get("name", n),
            definition=d.get("definition", "")[:500],
            category=d.get("category", "概念"),
            book_ids="|".join(d.get("book_ids", [])),
            n_mentions=int(d.get("n_mentions", 1)),
        )
    for u, v, k, d in g.edges(data=True, keys=True):
        g2.add_edge(u, v, key=str(k),
            relation_type=d.get("relation_type", str(k)),
            description=(d.get("descriptions") or [""])[0][:200],
            book_ids="|".join(d.get("book_ids", [])),
            weight=int(d.get("weight", 1)),
        )
    nx.write_graphml(g2, path)


def build_from_file(extract_path: Path, out_graphml: Path) -> dict[str, Any]:
    doc = json.loads(Path(extract_path).read_text("utf-8"))
    g = build_graph(doc)
    save(g, Path(out_graphml))
    return graph_stats(g)


__all__ = ["build_graph", "build_from_file", "graph_stats", "to_json", "save"]


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("path", nargs="?", type=Path)
    ap.add_argument("--all", action="store_true")
    args = ap.parse_args()
    targets = sorted(Path("data/triples").glob("*.json")) if args.all else [args.path]
    for p in targets:
        if p is None: continue
        out = Path("data/kg") / p.with_suffix(".graphml").name
        stats = build_from_file(p, out)
        print(f"OK  {p.name} -> {out}  {stats}")
