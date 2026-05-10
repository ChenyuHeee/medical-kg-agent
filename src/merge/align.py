"""T-N06: Cross-textbook entity alignment + integration decisions.

Two-stage alignment:
1. Lexical: NFKC + punctuation/whitespace strip → normalized name; same key merges.
2. Embedding (optional): for nodes within same category, compute cosine on
   "name||definition" using BGE-small-zh; pairs above threshold proposed for merge.

Outputs:
- merged graph (NetworkX MultiDiGraph) with nodes carrying multi-source provenance
- decisions.json per ARCHITECTURE §2.6 (action: merge|keep|remove + reason + confidence)
- alias_table mapping original_id -> merged_id
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

import networkx as nx

from ..kg.build import build_graph as build_one  # for symmetry; not used here

log = logging.getLogger(__name__)

PUNCT_RE = re.compile(r"[\s\u3000\-_·.,，。、；;:：（）()\[\]【】「」『』《》<>\"'`!！?？/\\]+")


def normalize_name(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "").lower()
    s = PUNCT_RE.sub("", s)
    return s.strip()


def _merged_id(canonical_name: str) -> str:
    h = hashlib.md5(canonical_name.encode("utf-8")).hexdigest()[:10]
    return f"merged::{h}"


def _embed_optional(texts: list[str]):
    """Try BGE; on failure return None so we silently degrade to lexical-only."""
    try:
        from sentence_transformers import SentenceTransformer
        import numpy as np
        model = SentenceTransformer("BAAI/bge-small-zh-v1.5")
        vecs = model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
        return np.asarray(vecs, dtype="float32")
    except Exception as ex:
        log.warning("embedding unavailable, lexical-only alignment: %s", ex)
        return None


def align_and_merge(
    per_book_graphs: dict[str, nx.MultiDiGraph],
    use_embedding: bool = True,
    sim_threshold: float = 0.92,
) -> tuple[nx.MultiDiGraph, list[dict], dict[str, str]]:
    """Merge multiple per-book graphs into one.

    Returns: (merged_graph, decisions, alias_table)
    """
    # Stage 1: lexical buckets keyed by (normalized_name, category)
    buckets: dict[tuple[str, str], list[tuple[str, str, dict]]] = defaultdict(list)
    for book_id, g in per_book_graphs.items():
        for nid, d in g.nodes(data=True):
            key = (normalize_name(d.get("name", nid)), d.get("category", "概念"))
            buckets[key].append((book_id, nid, d))

    # alias_table: original_id -> canonical merged_id
    alias_table: dict[str, str] = {}
    canonical: dict[str, dict] = {}  # merged_id -> aggregated record
    decisions: list[dict] = []

    for (norm_name, cat), members in buckets.items():
        # Pick canonical name = longest definition's name (richer)
        members_sorted = sorted(members, key=lambda x: -len(x[2].get("definition", "")))
        canonical_name = members_sorted[0][2].get("name", norm_name)
        mid = _merged_id(f"{norm_name}|{cat}")

        # Aggregate
        rec = {
            "id": mid,
            "name": canonical_name,
            "category": cat,
            "definition": "",
            "alt_definitions": [],
            "book_ids": set(),
            "chapters": set(),
            "pages": set(),
            "chunk_ids": set(),
            "n_mentions": 0,
            "source_node_ids": [],
        }
        for book_id, nid, d in members:
            alias_table[nid] = mid
            rec["source_node_ids"].append(nid)
            rec["book_ids"].add(book_id)
            for c in d.get("chapters", []):
                rec["chapters"].add(c)
            for p in d.get("pages", []):
                rec["pages"].add(p)
            for cid in d.get("chunk_ids", []):
                rec["chunk_ids"].add(cid)
            rec["n_mentions"] += d.get("n_mentions", 1)
            df = d.get("definition", "")
            if len(df) > len(rec["definition"]):
                if rec["definition"] and rec["definition"] not in rec["alt_definitions"]:
                    rec["alt_definitions"].append(rec["definition"])
                rec["definition"] = df
            elif df and df != rec["definition"] and df not in rec["alt_definitions"]:
                rec["alt_definitions"].append(df)
        canonical[mid] = rec

        # Decision record
        if len({b for b, _, _ in members}) >= 2:
            decisions.append({
                "decision_id": f"merge_{mid}",
                "action": "merge",
                "affected_nodes": [nid for _, nid, _ in members],
                "result_node": mid,
                "reason": f"{len(members)} 本教材均提及'{canonical_name}'（归一名一致），合并为统一节点；保留定义最完整版本",
                "confidence": 0.95,
                "stage": "lexical",
            })
        elif len(members) >= 2:
            # Same book multiple chunks → also merged but lower-stakes
            decisions.append({
                "decision_id": f"merge_{mid}",
                "action": "merge",
                "affected_nodes": [nid for _, nid, _ in members],
                "result_node": mid,
                "reason": f"同一教材内 {len(members)} 个 chunk 提到'{canonical_name}'，合并",
                "confidence": 0.99,
                "stage": "intra-book",
            })
        else:
            # Singleton kept
            decisions.append({
                "decision_id": f"keep_{mid}",
                "action": "keep",
                "affected_nodes": [members[0][1]],
                "result_node": mid,
                "reason": "唯一来源，保留",
                "confidence": 1.0,
                "stage": "singleton",
            })

    # Stage 2: embedding-based merge across remaining canonical nodes
    if use_embedding and len(canonical) > 1:
        ids = list(canonical.keys())
        texts = [f"{canonical[i]['name']}。{canonical[i]['definition']}" for i in ids]
        vecs = _embed_optional(texts)
        if vecs is not None:
            import numpy as np
            # Group by category to limit comparisons
            by_cat: dict[str, list[int]] = defaultdict(list)
            for idx, mid in enumerate(ids):
                by_cat[canonical[mid]["category"]].append(idx)

            redirects: dict[str, str] = {}  # mid_to_remove -> mid_to_keep
            for cat, idx_list in by_cat.items():
                if len(idx_list) < 2:
                    continue
                sub = vecs[idx_list]
                sim = sub @ sub.T  # cosine since normalized
                for i in range(len(idx_list)):
                    for j in range(i + 1, len(idx_list)):
                        if sim[i, j] < sim_threshold:
                            continue
                        mid_i, mid_j = ids[idx_list[i]], ids[idx_list[j]]
                        if mid_i in redirects or mid_j in redirects:
                            continue
                        # Keep the one with more book_ids (more authoritative)
                        ri, rj = canonical[mid_i], canonical[mid_j]
                        if len(rj["book_ids"]) > len(ri["book_ids"]):
                            keep, drop = mid_j, mid_i
                        else:
                            keep, drop = mid_i, mid_j
                        redirects[drop] = keep
                        decisions.append({
                            "decision_id": f"emerge_{drop}_{keep}",
                            "action": "merge",
                            "affected_nodes": [drop, keep],
                            "result_node": keep,
                            "reason": f"语义相似度 {float(sim[i,j]):.3f} ≥ {sim_threshold}（'{canonical[drop]['name']}' ≈ '{canonical[keep]['name']}'），合并",
                            "confidence": float(sim[i, j]),
                            "stage": "embedding",
                        })

            # Apply redirects
            for drop, keep in redirects.items():
                rec_d = canonical.pop(drop)
                rec_k = canonical[keep]
                rec_k["book_ids"] |= rec_d["book_ids"]
                rec_k["chapters"] |= rec_d["chapters"]
                rec_k["pages"] |= rec_d["pages"]
                rec_k["chunk_ids"] |= rec_d["chunk_ids"]
                rec_k["n_mentions"] += rec_d["n_mentions"]
                rec_k["source_node_ids"].extend(rec_d["source_node_ids"])
                if rec_d["definition"] and rec_d["definition"] != rec_k["definition"]:
                    rec_k["alt_definitions"].append(rec_d["definition"])
                rec_k["alt_definitions"].extend(rec_d["alt_definitions"])
                # Update alias table
                for orig, mid in list(alias_table.items()):
                    if mid == drop:
                        alias_table[orig] = keep

    # Build merged graph
    merged = nx.MultiDiGraph()
    for mid, rec in canonical.items():
        merged.add_node(
            mid,
            name=rec["name"],
            definition=rec["definition"],
            alt_definitions=list(dict.fromkeys(rec["alt_definitions"])),
            category=rec["category"],
            book_ids=sorted(rec["book_ids"]),
            chapters=sorted(rec["chapters"]),
            pages=sorted(rec["pages"]),
            chunk_ids=sorted(rec["chunk_ids"]),
            n_mentions=rec["n_mentions"],
            source_node_ids=rec["source_node_ids"],
        )

    # Edges: re-route via alias_table; aggregate
    em: dict[tuple[str, str, str], dict[str, Any]] = {}
    for book_id, g in per_book_graphs.items():
        for u, v, k, d in g.edges(data=True, keys=True):
            mu, mv = alias_table.get(u), alias_table.get(v)
            if not mu or not mv or mu == mv:
                continue
            rt = d.get("relation_type", k)
            key = (mu, mv, rt)
            if key not in em:
                em[key] = {
                    "relation_type": rt,
                    "descriptions": [],
                    "book_ids": set(),
                    "chunk_ids": set(),
                    "weight": 0,
                }
            a = em[key]
            a["weight"] += int(d.get("weight", 1))
            for desc in d.get("descriptions", []):
                if desc and desc not in a["descriptions"]:
                    a["descriptions"].append(desc)
            for b in d.get("book_ids", []):
                a["book_ids"].add(b)
            for c in d.get("chunk_ids", []):
                a["chunk_ids"].add(c)

    for (s, t, rt), a in em.items():
        merged.add_edge(s, t, key=rt,
            relation_type=rt,
            descriptions=a["descriptions"],
            book_ids=sorted(a["book_ids"]),
            chunk_ids=sorted(a["chunk_ids"]),
            weight=a["weight"],
        )

    return merged, decisions, alias_table


def merge_from_files(graph_json_paths: list[Path]) -> tuple[nx.MultiDiGraph, list[dict], dict[str, str]]:
    """Load per-book graphs from data/kg/{book}.json, run alignment."""
    per_book: dict[str, nx.MultiDiGraph] = {}
    for p in graph_json_paths:
        doc = json.loads(p.read_text(encoding="utf-8"))
        g = nx.MultiDiGraph()
        for n in doc["nodes"]:
            g.add_node(n["id"], **{k: v for k, v in n.items() if k != "id"})
        for e in doc["edges"]:
            g.add_edge(e["source"], e["target"], key=e["relation_type"], **{k: v for k, v in e.items() if k not in ("source","target")})
        # book_id from filename stem
        per_book[p.stem] = g
    return align_and_merge(per_book)


__all__ = ["normalize_name", "align_and_merge", "merge_from_files"]


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-embed", action="store_true")
    args = ap.parse_args()

    files = sorted(Path("data/kg").glob("*.json"))
    files = [f for f in files if not f.name.endswith(".merged.json")]
    if not files:
        print("no per-book graph json found in data/kg/")
        raise SystemExit(1)

    per_book: dict[str, nx.MultiDiGraph] = {}
    for p in files:
        doc = json.loads(p.read_text(encoding="utf-8"))
        g = nx.MultiDiGraph()
        for n in doc["nodes"]:
            g.add_node(n["id"], **{k: v for k, v in n.items() if k != "id"})
        for e in doc["edges"]:
            g.add_edge(e["source"], e["target"], key=e["relation_type"], **{k: v for k, v in e.items() if k not in ("source","target")})
        per_book[p.stem] = g

    merged, decisions, alias = align_and_merge(per_book, use_embedding=not args.no_embed)
    from .compress import _to_json  # type: ignore  # cycle-safe at runtime

    out_dir = Path("data/kg"); out_dir.mkdir(parents=True, exist_ok=True)
    rep_dir = Path("data/report"); rep_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "merged.json").write_text(json.dumps(_to_json(merged), ensure_ascii=False, indent=2), encoding="utf-8")
    (rep_dir / "decisions.json").write_text(json.dumps(decisions, ensure_ascii=False, indent=2), encoding="utf-8")
    (rep_dir / "alias_table.json").write_text(json.dumps(alias, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"OK  merged: {merged.number_of_nodes()} nodes, {merged.number_of_edges()} edges; decisions={len(decisions)}; aliases={len(alias)}")
