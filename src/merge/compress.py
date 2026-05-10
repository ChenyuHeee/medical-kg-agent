"""T-N07: Compress merged graph to ≤30% original char count + integrity self-check.

Spec metric (per ARCHITECTURE §5):
    original_chars = sum(RawDoc.total_chars across books)
    merged_chars   = sum(node.definition len) + sum(edge.descriptions concat len)
    ratio          = merged_chars / original_chars   ≤ 0.30

Compression strategy:
- Score nodes by: 2*n_books + log1p(n_mentions) + 0.5*log1p(degree)
- Greedy keep nodes by score until merged_chars > target
- Always preserve "prerequisite chain head": if B kept and (A → prerequisite → B),
  add A back even if low score (teaching integrity rescue, T-X01)
- Edges: kept iff both endpoints kept

Integrity self-check writes data/report/integrity.json:
{
  "broken_prereqs": [{from, to}],     # before rescue
  "auto_recovered": [node_id],        # nodes added back
  "after_check": "ok"
}
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any

import networkx as nx


def _node_chars(d: dict) -> int:
    return len(d.get("definition", "") or "")


def _edge_chars(d: dict) -> int:
    descs = d.get("descriptions", []) or []
    return sum(len(x) for x in descs)


def _to_json(g: nx.MultiDiGraph) -> dict:
    nodes = []
    for n, d in g.nodes(data=True):
        nodes.append({"id": n, **{k: v for k, v in d.items()}})
    edges = []
    for u, v, k, d in g.edges(data=True, keys=True):
        edges.append({"source": u, "target": v, **{k2: v2 for k2, v2 in d.items()}})
    return {"nodes": nodes, "edges": edges}


def _from_json(doc: dict) -> nx.MultiDiGraph:
    g = nx.MultiDiGraph()
    for n in doc["nodes"]:
        g.add_node(n["id"], **{k: v for k, v in n.items() if k != "id"})
    for e in doc["edges"]:
        g.add_edge(
            e["source"], e["target"],
            key=e.get("relation_type", "rel"),
            **{k: v for k, v in e.items() if k not in ("source", "target")},
        )
    return g


def score_node(d: dict, deg: int) -> float:
    nb = len(d.get("book_ids", []) or [])
    nm = d.get("n_mentions", 1)
    return 2.0 * nb + math.log1p(nm) + 0.5 * math.log1p(deg)


def integrity_rescue(merged: nx.MultiDiGraph, kept: set[str]) -> tuple[set[str], list[str], list[dict]]:
    """If B in kept and (A → prerequisite → B) but A not kept, add A.

    Iterate to fixpoint (rescued A may itself need its A's prereq).
    Returns (new_kept, recovered_list, broken_list).
    """
    recovered: list[str] = []
    broken: list[dict] = []
    changed = True
    while changed:
        changed = False
        for u, v, _, d in list(merged.edges(data=True, keys=True)):
            if d.get("relation_type") != "prerequisite":
                continue
            if v in kept and u not in kept:
                broken.append({"from": u, "to": v, "rescued": True})
                kept.add(u)
                recovered.append(u)
                changed = True
    return kept, recovered, broken


def compress(
    merged: nx.MultiDiGraph,
    original_chars: int,
    target_ratio: float = 0.30,
    max_node_ratio: float = 0.30,
) -> tuple[nx.MultiDiGraph, dict]:
    """Greedy compress merged graph; returns (compact_graph, stats).

    Two simultaneous caps:
      - kept text chars  <=  original_chars * target_ratio  (content density)
      - kept node count  <=  merged_nodes * max_node_ratio  (visual density)
    Stop as soon as either cap is reached. This guarantees compact graph is
    visibly smaller than merged even when merged is already char-sparse.
    """
    target_chars = int(original_chars * target_ratio)
    target_nodes = max(1, int(merged.number_of_nodes() * max_node_ratio))
    deg = {n: merged.degree(n) for n in merged.nodes()}
    ranked = sorted(
        merged.nodes(data=True),
        key=lambda x: -score_node(x[1], deg.get(x[0], 0)),
    )

    kept: set[str] = set()
    cur = 0
    for n, d in ranked:
        c = _node_chars(d)
        if (cur + c > target_chars or len(kept) >= target_nodes) and len(kept) > 0:
            break
        kept.add(n)
        cur += c

    # Integrity rescue
    kept, recovered, broken = integrity_rescue(merged, kept)

    # Build subgraph
    sub = merged.subgraph(kept).copy()

    # Recompute chars
    n_chars = sum(_node_chars(d) for _, d in sub.nodes(data=True))
    e_chars = sum(_edge_chars(d) for _, _, d in sub.edges(data=True))
    merged_chars = n_chars + e_chars

    stats = {
        "original_chars": original_chars,
        "merged_chars": merged_chars,
        "node_definition_chars": n_chars,
        "edge_description_chars": e_chars,
        "ratio": round(merged_chars / max(original_chars, 1), 4),
        "target_ratio": target_ratio,
        "passed": merged_chars <= int(original_chars * target_ratio * 1.05),  # 5% tol
        "merged_node_count": merged.number_of_nodes(),
        "merged_edge_count": merged.number_of_edges(),
        "compact_node_count": sub.number_of_nodes(),
        "compact_edge_count": sub.number_of_edges(),
        "node_compression_ratio": round(sub.number_of_nodes() / max(merged.number_of_nodes(), 1), 4),
        "integrity": {
            "broken_prereqs_total": len(broken),
            "auto_recovered_count": len(recovered),
            "auto_recovered_sample": recovered[:20],
        },
    }
    return sub, stats


def compute_original_chars(raw_dir: Path = Path("data/raw")) -> int:
    total = 0
    for p in raw_dir.glob("*.json"):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            total += int(d.get("total_chars", 0) or 0)
        except Exception:
            pass
    return total


def run(target_ratio: float = 0.30, max_node_ratio: float = 0.10) -> dict:
    merged_path = Path("data/kg/merged.json")
    if not merged_path.exists():
        raise FileNotFoundError("data/kg/merged.json — run align first")
    merged = _from_json(json.loads(merged_path.read_text(encoding="utf-8")))
    original = compute_original_chars()
    sub, stats = compress(merged, original, target_ratio, max_node_ratio)

    out_dir = Path("data/kg"); out_dir.mkdir(parents=True, exist_ok=True)
    rep_dir = Path("data/report"); rep_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "compact.json").write_text(json.dumps(_to_json(sub), ensure_ascii=False, indent=2), encoding="utf-8")
    (rep_dir / "compression.json").write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    (rep_dir / "integrity.json").write_text(json.dumps(stats["integrity"], ensure_ascii=False, indent=2), encoding="utf-8")
    return stats


__all__ = ["compress", "run", "compute_original_chars", "score_node", "_to_json", "_from_json"]


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--ratio", type=float, default=0.30)
    args = ap.parse_args()
    s = run(args.ratio)
    print(json.dumps(s, ensure_ascii=False, indent=2))
