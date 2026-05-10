"""T-A01: Tools for KnowledgeAgent.

All tools operate on a shared GraphState (in-memory MultiDiGraph + diff_log).
Write tools append a GraphEdit to diff_log and emit a "diff_card" with
before/after subgraphs for the frontend to render (T-X02).

Tools (per ARCHITECTURE §7.3):
- search_kg
- show_subgraph
- compare_books
- propose_merge
- propose_split
- update_relation
- add_evidence
- recompress
- export_report
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable

import networkx as nx

from ..merge.compress import _from_json, _to_json
from ..rag import qa as rag_qa, graph_rag

log = logging.getLogger(__name__)

DIFF_LOG_PATH = Path("data/chat/diff_log.jsonl")
MERGED_PATH = Path("data/kg/merged.json")


class GraphState:
    """In-memory mutable graph state shared by all tools across one chat session."""

    def __init__(self):
        self.graph: nx.MultiDiGraph | None = None
        self.diff_log: list[dict] = []
        self.load()

    def load(self):
        if MERGED_PATH.exists():
            self.graph = _from_json(json.loads(MERGED_PATH.read_text(encoding="utf-8")))
        else:
            self.graph = nx.MultiDiGraph()

    def to_json(self) -> dict:
        return _to_json(self.graph)

    def persist(self):
        MERGED_PATH.parent.mkdir(parents=True, exist_ok=True)
        MERGED_PATH.write_text(json.dumps(self.to_json(), ensure_ascii=False, indent=2), encoding="utf-8")

    def append_edit(self, edit: dict):
        self.diff_log.append(edit)
        DIFF_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with DIFF_LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(edit, ensure_ascii=False) + "\n")


# Singleton (per-process) — fine for single-user demo
_state: GraphState | None = None


def get_state() -> GraphState:
    global _state
    if _state is None:
        _state = GraphState()
    return _state


def reset_state():
    global _state
    _state = None


# ----------------- helpers -----------------

def _find_node(g: nx.MultiDiGraph, query_str: str) -> list[str]:
    q = query_str.strip().lower()
    out = []
    for nid, d in g.nodes(data=True):
        name = (d.get("name") or "").lower()
        if q == name:
            out.insert(0, nid)
        elif q in name:
            out.append(nid)
    return out


def _subgraph_dict(g: nx.MultiDiGraph, node_ids: list[str], hop: int = 1) -> dict:
    seen = set(node_ids)
    for nid in list(node_ids):
        if nid not in g:
            continue
        for _, nb in g.out_edges(nid):
            seen.add(nb)
        for nb, _ in g.in_edges(nid):
            seen.add(nb)
    sub = g.subgraph(seen).copy()
    return _to_json(sub)


def _emit_diff_card(state: GraphState, edit: dict, before_ids: list[str], after_ids: list[str]) -> dict:
    return {
        "type": "diff_card",
        "edit": edit,
        "before_subgraph": _subgraph_dict(state.graph, before_ids) if before_ids else {"nodes": [], "edges": []},
        "after_subgraph": _subgraph_dict(state.graph, after_ids) if after_ids else {"nodes": [], "edges": []},
    }


def _new_edit(op: str, args: dict, rationale: str, actor: str = "agent") -> dict:
    return {
        "edit_id": str(uuid.uuid4()),
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "op": op,
        "actor": actor,
        "args": args,
        "rationale": rationale,
    }


# ----------------- tools -----------------

def search_kg(query: str, k: int = 10) -> dict:
    g = get_state().graph
    ids = _find_node(g, query)[:k]
    nodes = []
    for nid in ids:
        d = g.nodes[nid]
        nodes.append({
            "id": nid, "name": d.get("name"), "category": d.get("category"),
            "book_ids": d.get("book_ids", []),
            "definition": (d.get("definition") or "")[:200],
            "n_mentions": d.get("n_mentions", 1),
        })
    return {"nodes": nodes, "n": len(nodes)}


def show_subgraph(node_id_or_name: str, hop: int = 1) -> dict:
    g = get_state().graph
    if node_id_or_name in g:
        ids = [node_id_or_name]
    else:
        ids = _find_node(g, node_id_or_name)[:1]
    if not ids:
        return {"error": f"node not found: {node_id_or_name}"}
    return {"subgraph": _subgraph_dict(g, ids, hop=hop), "center_ids": ids}


def compare_books(node_id_or_name: str) -> dict:
    g = get_state().graph
    ids = [node_id_or_name] if node_id_or_name in g else _find_node(g, node_id_or_name)[:5]
    out = []
    for nid in ids:
        d = g.nodes[nid]
        out.append({
            "id": nid, "name": d.get("name"),
            "book_ids": d.get("book_ids", []),
            "definition": d.get("definition", ""),
            "alt_definitions": d.get("alt_definitions", []),
            "chapters": d.get("chapters", []),
            "pages": d.get("pages", []),
        })
    return {"comparisons": out}


def propose_merge(node_a: str, node_b: str, reason: str) -> dict:
    state = get_state()
    g = state.graph
    a = node_a if node_a in g else (_find_node(g, node_a)[:1] or [None])[0]
    b = node_b if node_b in g else (_find_node(g, node_b)[:1] or [None])[0]
    if not a or not b or a == b:
        return {"error": "node not found or same node", "a": a, "b": b}

    # Capture before
    before_ids = [a, b]
    a_before = dict(g.nodes[a])
    b_before = dict(g.nodes[b])

    # Merge b into a
    da, db = g.nodes[a], g.nodes[b]
    da["book_ids"] = sorted(set(da.get("book_ids", [])) | set(db.get("book_ids", [])))
    da["chapters"] = sorted(set(da.get("chapters", [])) | set(db.get("chapters", [])))
    da["pages"] = sorted(set(da.get("pages", [])) | set(db.get("pages", [])))
    da["chunk_ids"] = sorted(set(da.get("chunk_ids", [])) | set(db.get("chunk_ids", [])))
    da["n_mentions"] = (da.get("n_mentions", 1) + db.get("n_mentions", 1))
    if len(db.get("definition", "")) > len(da.get("definition", "")):
        da["alt_definitions"] = list(dict.fromkeys((da.get("alt_definitions", []) + [da.get("definition", "")])))
        da["definition"] = db.get("definition", "")
    else:
        da["alt_definitions"] = list(dict.fromkeys(da.get("alt_definitions", []) + [db.get("definition", "")]))

    # Re-route edges
    for u, v, k, d in list(g.in_edges(b, keys=True, data=True)):
        if u != a:
            g.add_edge(u, a, key=k, **d)
    for u, v, k, d in list(g.out_edges(b, keys=True, data=True)):
        if v != a:
            g.add_edge(a, v, key=k, **d)
    g.remove_node(b)

    edit = _new_edit("merge", {"node_a": a, "node_b": b, "before_b": b_before}, reason)
    state.append_edit(edit)
    state.persist()
    card = _emit_diff_card(state, edit, before_ids, [a])
    return {"ok": True, "merged_into": a, "diff_card": card}


def propose_split(node_id: str, by_book: bool = True) -> dict:
    state = get_state()
    g = state.graph
    if node_id not in g:
        ids = _find_node(g, node_id)[:1]
        if not ids:
            return {"error": "not found"}
        node_id = ids[0]
    d = g.nodes[node_id]
    books = d.get("book_ids", [])
    if len(books) < 2:
        return {"error": "node has <2 source books, nothing to split"}

    new_ids = []
    for b in books:
        nid = f"{node_id}::split::{b}"
        g.add_node(nid, **{**d, "book_ids": [b]})
        new_ids.append(nid)
        # Re-route edges that originally came via this book
        for u, v, k, ed in list(g.in_edges(node_id, keys=True, data=True)):
            if b in (ed.get("book_ids") or []):
                g.add_edge(u, nid, key=k, **ed)
        for u, v, k, ed in list(g.out_edges(node_id, keys=True, data=True)):
            if b in (ed.get("book_ids") or []):
                g.add_edge(nid, v, key=k, **ed)
    g.remove_node(node_id)

    edit = _new_edit("split", {"node_id": node_id, "by_book": by_book, "new_ids": new_ids}, f"按教材拆分为 {len(new_ids)} 个节点")
    state.append_edit(edit)
    state.persist()
    return {"ok": True, "new_ids": new_ids, "diff_card": _emit_diff_card(state, edit, [], new_ids)}


def update_relation(source: str, target: str, new_relation_type: str, description: str = "") -> dict:
    """Add or replace a relation edge."""
    ALLOWED = {"prerequisite", "parallel", "contains", "applies_to"}
    if new_relation_type not in ALLOWED:
        return {"error": f"relation_type must be in {ALLOWED}"}
    state = get_state()
    g = state.graph
    s = source if source in g else (_find_node(g, source)[:1] or [None])[0]
    t = target if target in g else (_find_node(g, target)[:1] or [None])[0]
    if not s or not t:
        return {"error": "node not found"}
    g.add_edge(s, t, key=new_relation_type,
               relation_type=new_relation_type,
               descriptions=[description] if description else [],
               book_ids=["__user__"], chunk_ids=[], weight=1)
    edit = _new_edit("update_relation", {"source": s, "target": t, "relation_type": new_relation_type, "description": description}, "用户反馈新增/修改关系")
    state.append_edit(edit)
    state.persist()
    return {"ok": True, "diff_card": _emit_diff_card(state, edit, [s, t], [s, t])}


def add_evidence(source: str, target: str, evidence: str, book_id: str) -> dict:
    state = get_state()
    g = state.graph
    s = source if source in g else (_find_node(g, source)[:1] or [None])[0]
    t = target if target in g else (_find_node(g, target)[:1] or [None])[0]
    if not s or not t:
        return {"error": "node not found"}
    found = False
    for u, v, k, d in g.edges(s, keys=True, data=True):
        if v == t:
            descs = list(d.get("descriptions", []))
            if evidence not in descs:
                descs.append(evidence)
            d["descriptions"] = descs
            books = list(set(list(d.get("book_ids", [])) + [book_id]))
            d["book_ids"] = books
            found = True
            break
    if not found:
        return {"error": "no edge between nodes"}
    edit = _new_edit("add_evidence", {"source": s, "target": t, "evidence": evidence, "book_id": book_id}, "追加证据片段")
    state.append_edit(edit)
    state.persist()
    return {"ok": True, "diff_card": _emit_diff_card(state, edit, [s, t], [s, t])}


def recompress(target_ratio: float = 0.30) -> dict:
    from ..merge import compress as cm
    # Use current in-memory graph
    state = get_state()
    state.persist()  # ensure file matches in-memory
    stats = cm.run(target_ratio=target_ratio)
    return {"ok": True, "stats": stats}


def export_report(path: str = "report/整合报告.md") -> dict:
    from ..merge import report as rm
    out = rm.generate_report(Path(path))
    return {"ok": True, "path": str(out)}


def rag_query(question: str, mode: str = "graph", k: int = 5) -> dict:
    """Bonus: agent can also do citation-grounded Q&A."""
    fn = graph_rag.answer if mode == "graph" else rag_qa.answer
    return fn(question, k=k)


def undo(edit_id: str) -> dict:
    """T-X02: rollback a single edit by edit_id (best-effort)."""
    state = get_state()
    target = next((e for e in state.diff_log if e["edit_id"] == edit_id), None)
    if not target:
        return {"error": "edit_id not found"}
    op = target["op"]
    args = target.get("args", {})
    g = state.graph
    if op == "merge":
        # Restore b as standalone node with its prior attrs
        b = args["node_b"]
        prior = args.get("before_b") or {}
        if b in g:
            return {"error": "target id already present"}
        g.add_node(b, **prior)
    elif op == "split":
        # Re-merge new_ids back into one
        new_ids = args.get("new_ids", [])
        if not new_ids:
            return {"error": "no new_ids on record"}
        keep = new_ids[0]
        for nid in new_ids[1:]:
            if nid in g:
                propose_merge(keep, nid, "undo split")
    elif op == "update_relation":
        s, t, rt = args["source"], args["target"], args["relation_type"]
        if g.has_edge(s, t, key=rt):
            g.remove_edge(s, t, key=rt)
    else:
        return {"error": f"undo not implemented for op={op}"}
    edit = _new_edit("undo", {"undone_edit_id": edit_id}, f"撤销 {op}", actor="user")
    state.append_edit(edit)
    state.persist()
    return {"ok": True, "undone_op": op}


# ----------------- OpenAI tools schema -----------------

TOOLS_SCHEMA: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "search_kg",
            "description": "在知识图谱中按名称模糊搜索节点。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索关键词"},
                    "k": {"type": "integer", "default": 10},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "show_subgraph",
            "description": "返回某节点的 1 跳邻居子图。",
            "parameters": {
                "type": "object",
                "properties": {
                    "node_id_or_name": {"type": "string"},
                    "hop": {"type": "integer", "default": 1},
                },
                "required": ["node_id_or_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "compare_books",
            "description": "对同一概念在多本教材中的定义/章节/页码做对比。",
            "parameters": {
                "type": "object",
                "properties": {"node_id_or_name": {"type": "string"}},
                "required": ["node_id_or_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "propose_merge",
            "description": "将 node_b 合并入 node_a；用于老师确认两个节点是同一概念。",
            "parameters": {
                "type": "object",
                "properties": {
                    "node_a": {"type": "string"},
                    "node_b": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["node_a", "node_b", "reason"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "propose_split",
            "description": "按教材来源把一个被错误合并的节点拆开。",
            "parameters": {
                "type": "object",
                "properties": {
                    "node_id": {"type": "string"},
                    "by_book": {"type": "boolean", "default": True},
                },
                "required": ["node_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_relation",
            "description": "新增或修改两个节点之间的关系（4 种枚举）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "source": {"type": "string"},
                    "target": {"type": "string"},
                    "new_relation_type": {"type": "string", "enum": ["prerequisite", "parallel", "contains", "applies_to"]},
                    "description": {"type": "string"},
                },
                "required": ["source", "target", "new_relation_type"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "recompress",
            "description": "在当前图上重新执行压缩（默认 30%），并跑教学完整性自检。",
            "parameters": {
                "type": "object",
                "properties": {"target_ratio": {"type": "number", "default": 0.30}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "export_report",
            "description": "导出整合报告 markdown。",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "default": "report/整合报告.md"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "rag_query",
            "description": "基于教材内容做带引用的检索增强问答（可选 graph 或 vanilla 模式）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {"type": "string"},
                    "mode": {"type": "string", "enum": ["graph", "vanilla"], "default": "graph"},
                    "k": {"type": "integer", "default": 5},
                },
                "required": ["question"],
            },
        },
    },
]

TOOL_FUNCS: dict[str, Callable[..., dict]] = {
    "search_kg": search_kg,
    "show_subgraph": show_subgraph,
    "compare_books": compare_books,
    "propose_merge": propose_merge,
    "propose_split": propose_split,
    "update_relation": update_relation,
    "add_evidence": add_evidence,
    "recompress": recompress,
    "export_report": export_report,
    "rag_query": rag_query,
}
