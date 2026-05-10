"""T-R04 ⭐: GraphRAG — augment vanilla RAG with KG subgraph context.

Pipeline:
1. Vector retrieve top-k chunks (same as qa.py)
2. From top chunks, find entity nodes (KG node names that appear in chunks)
3. Pull 1-hop neighborhood for each matched entity from the merged graph
4. Format subgraph as triples list and feed into LLM alongside chunks
5. Return enriched answer + citations + a "knowledge_paths" field

Falls back to vanilla RAG if merged graph is missing.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from ..llm import build_messages, get_default_client, LLMClient
from .store import query
from .qa import _ctx_block, SYSTEM_PROMPT as VANILLA_SYS

log = logging.getLogger(__name__)

GRAPH_SYS = VANILLA_SYS + """

【知识图谱补充上下文】
你还会收到从教材整合知识图谱中检索到的"知识脉络"三元组（A -[关系]-> B）。这些是教材作者认证的概念依赖网。
- 优先利用知识脉络解释概念间关系（如前置依赖、包含、并列、应用）。
- 答案中如果引用了知识脉络，在 JSON 输出加 "graph_paths_used": ["A -[prerequisite]-> B", ...]。
"""


def _load_merged() -> dict | None:
    p = Path("data/kg/merged.json")
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def _build_index(merged: dict) -> tuple[dict, dict]:
    """name → list of node ids; id → node dict."""
    by_name: dict[str, list[str]] = {}
    by_id: dict[str, dict] = {}
    for n in merged["nodes"]:
        by_id[n["id"]] = n
        by_name.setdefault(n["name"], []).append(n["id"])
    return by_name, by_id


def _match_entities(text: str, by_name: dict[str, list[str]], cap: int = 8) -> list[str]:
    """Cheap entity match: exact substring on node names, longest-first.

    Avoids O(N*M) by only scanning names already in the chunks.
    """
    hits: list[str] = []
    for name in sorted(by_name.keys(), key=len, reverse=True):
        if len(name) < 2:
            continue
        if name in text:
            for nid in by_name[name]:
                if nid not in hits:
                    hits.append(nid)
                    if len(hits) >= cap:
                        return hits
    return hits


def _neighbors(merged: dict, by_id: dict, node_id: str, hop: int = 1) -> list[dict]:
    """Return outgoing & incoming edges for node (1-hop)."""
    out = []
    for e in merged["edges"]:
        if e["source"] == node_id or e["target"] == node_id:
            out.append(e)
    return out


def _format_paths(merged: dict, by_id: dict, entity_ids: list[str]) -> list[str]:
    seen: set[tuple] = set()
    paths: list[str] = []
    eid_set = set(entity_ids)
    for e in merged["edges"]:
        if e["source"] not in eid_set and e["target"] not in eid_set:
            continue
        s = by_id.get(e["source"], {}).get("name", e["source"])
        t = by_id.get(e["target"], {}).get("name", e["target"])
        rt = e["relation_type"]
        key = (s, rt, t)
        if key in seen:
            continue
        seen.add(key)
        paths.append(f"{s} -[{rt}]-> {t}")
        if len(paths) >= 30:
            break
    return paths


def answer(question: str, k: int = 5, book_ids: list[str] | None = None,
           client: LLMClient | None = None) -> dict[str, Any]:
    chunks = query(question, k=k, book_ids=book_ids)
    if not chunks:
        return {"answer": "当前知识库中未找到相关信息", "citations": [], "source_chunks": [], "knowledge_paths": []}

    merged = _load_merged()
    paths: list[str] = []
    matched_entities: list[str] = []
    if merged:
        by_name, by_id = _build_index(merged)
        ent_ids: list[str] = []
        # Also use the question itself
        ent_ids.extend(_match_entities(question, by_name, cap=4))
        for c in chunks:
            for nid in _match_entities(c["text"], by_name, cap=4):
                if nid not in ent_ids:
                    ent_ids.append(nid)
                if len(ent_ids) >= 12:
                    break
        matched_entities = [by_id[i]["name"] for i in ent_ids if i in by_id]
        paths = _format_paths(merged, by_id, ent_ids)

    user_blocks = [
        f"【问题】{question}",
        f"【上下文 chunks】\n{_ctx_block(chunks)}",
    ]
    if paths:
        user_blocks.append("【知识脉络（教材整合图谱）】\n" + "\n".join(f"- {p}" for p in paths))
    user_blocks.append("请输出 JSON。")
    user = "\n\n".join(user_blocks)

    msgs = build_messages(system=GRAPH_SYS, user=user)
    client = client or get_default_client()
    try:
        data = client.chat_json(msgs)
        ans = data.get("answer", "").strip()
        idxs = data.get("citation_indices", []) or []
        used_paths = data.get("graph_paths_used", []) or []
    except Exception as ex:
        log.warning("graphrag llm failed: %s", ex)
        ans = "当前知识库中未找到相关信息"
        idxs = []
        used_paths = []

    if not ans:
        ans = "当前知识库中未找到相关信息"
        idxs = []

    citations: list[dict] = []
    src_texts: list[str] = []
    for i in idxs:
        try:
            i = int(i)
        except Exception:
            continue
        if 1 <= i <= len(chunks):
            c = chunks[i - 1]
            m = c["metadata"]
            citations.append({
                "textbook": m.get("book_id", ""),
                "chapter": m.get("chapter", ""),
                "page": m.get("page", -1),
                "relevance_score": c["score"],
                "chunk_id": c["chunk_id"],
            })
            src_texts.append(c["text"])

    if not citations and ans != "当前知识库中未找到相关信息":
        for c in chunks[:3]:
            m = c["metadata"]
            citations.append({
                "textbook": m.get("book_id", ""),
                "chapter": m.get("chapter", ""),
                "page": m.get("page", -1),
                "relevance_score": c["score"],
                "chunk_id": c["chunk_id"],
            })
            src_texts.append(c["text"])

    return {
        "answer": ans,
        "citations": citations,
        "source_chunks": src_texts,
        "knowledge_paths": used_paths or paths[:10],
        "matched_entities": matched_entities,
    }


__all__ = ["answer"]
