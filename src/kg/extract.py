"""T-N04: Extract knowledge points (nodes) and relations (edges) per official spec.

For each chunk, ask the LLM to emit:
- nodes: {id, name, definition, category, chapter, page}
- edges: {source, target, relation_type, description}
       relation_type ∈ {prerequisite, parallel, contains, applies_to}

Per-chunk output is merged into per-book ``data/triples/{book_id}.json``.

Usage:
    python -m src.kg.extract data/chunks/03_生理学.json
    python -m src.kg.extract --all
    python -m src.kg.extract data/chunks/03_生理学.json --limit 5  # smoke test
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
from pathlib import Path
from typing import Any

from ..llm import LLMClient, LLMJSONParseError, build_messages, get_default_client
from ..config.domain import get_domain

log = logging.getLogger(__name__)

# Domain schema (defaults to medical when $DOMAIN is unset; legacy behaviour preserved).
_DOMAIN = get_domain()
ALLOWED_RELATIONS = set(_DOMAIN.relations)
ALLOWED_CATEGORIES = set(_DOMAIN.categories)
DEFAULT_CATEGORY = _DOMAIN.default_category
SYSTEM_PROMPT = _DOMAIN.system_prompt

# --- legacy literal kept for reference / diff readability (unused) -----------
_LEGACY_MEDICAL_PROMPT = """你是医学知识图谱构建助手。任务：从教材片段中提取知识点（节点）与知识点间关系（边）。

【输出格式】严格 JSON 对象（不要 markdown 包裹），结构：
{
  "nodes": [
    {"name": "动作电位", "definition": "细胞受到刺激后膜电位发生的一次快速可逆的倒转", "category": "核心概念"}
  ],
  "edges": [
    {"source": "动作电位", "target": "静息电位", "relation_type": "prerequisite", "description": "理解动作电位需先掌握静息电位"}
  ]
}

【硬约束】
1. 只抽取该片段中明确出现的知识点；name 必须在原文里出现或为原文同义术语。
2. definition 控制在 30~120 字，必须基于原文，不要发挥。
3. category ∈ {核心概念, 现象, 过程, 结构, 物质, 疾病, 方法}（必须从这7个里选；拿不准选"核心概念"）
4. relation_type 必须是这 4 种之一：
   - prerequisite：B 学习需先掌握 A（A 是 B 的前置）
   - parallel：同层级平行概念
   - contains：A 包含 B（上位包含下位）
   - applies_to：A 是 B 的应用场景
5. edges 中的 source / target 必须是 nodes 数组里出现过的 name。
6. description 30 字以内，说明关系成立的依据。
7. 只抽事实，不推测；同片段内同名实体只出现一次。
8. 单次输出 nodes ≤ 12 条，edges ≤ 15 条；优先信息量大的核心概念。
9. 若片段是目录页/版权页/纯图注，输出 {"nodes":[],"edges":[]}。"""

USER_TEMPLATE = """【教材】{book_id}
【章节】{chapter} / {section}
【页码】{page}
【正文】
{text}

请输出符合格式的 JSON 对象。"""

# (ALLOWED_CATEGORIES is set at module top from the active Domain.)


def _node_id(book_id: str, chunk_id: str, name: str, idx: int) -> str:
    h = hashlib.md5(f"{book_id}|{name}".encode("utf-8")).hexdigest()[:8]
    return f"{book_id}::node_{h}"


def _validate_node(n: dict[str, Any]) -> dict[str, Any] | None:
    try:
        name = str(n["name"]).strip()
        definition = str(n.get("definition", "")).strip()
    except (KeyError, TypeError):
        return None
    if not name or len(name) > 40:
        return None
    if not definition:
        definition = name  # tolerate empty
    category = str(n.get("category", DEFAULT_CATEGORY)).strip()
    if category not in ALLOWED_CATEGORIES:
        category = DEFAULT_CATEGORY
    return {"name": name, "definition": definition[:200], "category": category}


def _validate_edge(e: dict[str, Any], names: set[str]) -> dict[str, Any] | None:
    try:
        source = str(e["source"]).strip()
        target = str(e["target"]).strip()
        rt = str(e.get("relation_type", "")).strip()
    except (KeyError, TypeError):
        return None
    if not source or not target or source == target:
        return None
    if source not in names or target not in names:
        return None
    if rt not in ALLOWED_RELATIONS:
        return None
    desc = str(e.get("description", "")).strip()[:80]
    return {"source": source, "target": target, "relation_type": rt, "description": desc}


def _is_low_value(chunk: dict) -> bool:
    """Skip chunks that are clearly noise (front matter / TOC / copyright)."""
    text = chunk.get("text", "")
    if len(text) < 80:
        return True
    chapter = (chunk.get("chapter") or "").strip()
    title_blacklist = ("修订说明", "前言", "编委", "目录", "版权", "出版", "致谢", "序言", "总序", "审稿", "主编", "副主编")
    for kw in title_blacklist:
        if kw in chapter or kw in text[:60]:
            return True
    # Pure list of names (≥6 中文姓名 separated by spaces) → editorial board
    import re
    names = re.findall(r"[\u4e00-\u9fff]{2,4}\s{2,}[\u4e00-\u9fff]{2,4}", text[:400])
    if len(names) >= 4:
        return True
    return False


def extract_chunk(chunk: dict[str, Any], client: LLMClient | None = None) -> dict[str, Any]:
    """Extract {nodes:[], edges:[]} from a single chunk. Never raises."""
    if _is_low_value(chunk):
        return {"nodes": [], "edges": []}
    client = client or get_default_client()
    user = USER_TEMPLATE.format(
        book_id=chunk.get("book_id", ""),
        chapter=chunk.get("chapter", ""),
        section=chunk.get("section", ""),
        page=chunk.get("page", -1),
        text=chunk.get("text", "").strip()[:3500],
    )
    messages = build_messages(system=SYSTEM_PROMPT, user=user)

    try:
        data = client.chat_json(messages)
    except (LLMJSONParseError, Exception) as ex:
        log.warning("extract_chunk failed for %s: %s", chunk.get("chunk_id"), ex)
        return {"nodes": [], "edges": []}

    raw_nodes = data.get("nodes", []) if isinstance(data, dict) else []
    raw_edges = data.get("edges", []) if isinstance(data, dict) else []

    nodes: list[dict] = []
    seen: set[str] = set()
    for i, n in enumerate(raw_nodes):
        v = _validate_node(n) if isinstance(n, dict) else None
        if not v:
            continue
        if v["name"] in seen:
            continue
        seen.add(v["name"])
        v["id"] = _node_id(chunk["book_id"], chunk["chunk_id"], v["name"], i)
        v["book_id"] = chunk["book_id"]
        v["chapter"] = chunk.get("chapter", "")
        v["page"] = chunk.get("page", -1)
        v["chunk_id"] = chunk["chunk_id"]
        nodes.append(v)

    name_to_id = {n["name"]: n["id"] for n in nodes}
    edges: list[dict] = []
    for e in raw_edges:
        if not isinstance(e, dict):
            continue
        v = _validate_edge(e, set(name_to_id.keys()))
        if not v:
            continue
        edges.append(
            {
                "source": name_to_id[v["source"]],
                "target": name_to_id[v["target"]],
                "relation_type": v["relation_type"],
                "description": v["description"],
                "book_id": chunk["book_id"],
                "chunk_id": chunk["chunk_id"],
            }
        )

    return {"nodes": nodes, "edges": edges}


def extract_book(chunks_path: Path, out_path: Path, limit: int | None = None,
                 workers: int = 8, resume: bool = True) -> dict[str, int]:
    chunks = json.loads(chunks_path.read_text(encoding="utf-8"))
    if limit:
        chunks = chunks[:limit]

    client = get_default_client()
    err_dir = Path("data/errors"); err_dir.mkdir(parents=True, exist_ok=True)

    # Resume: skip already-processed chunk_ids
    done_ids: set[str] = set()
    all_nodes: list[dict] = []
    all_edges: list[dict] = []
    if resume and out_path.exists():
        try:
            prev = json.loads(out_path.read_text(encoding="utf-8"))
            all_nodes = prev.get("nodes", [])
            all_edges = prev.get("edges", [])
            done_ids = {n["chunk_id"] for n in all_nodes if "chunk_id" in n}
            # Also count chunks that produced 0 results — track via marker file
            marker = out_path.with_suffix(".done.txt")
            if marker.exists():
                done_ids |= set(marker.read_text().split())
            if done_ids:
                log.info("resume: skipping %d already-processed chunks", len(done_ids))
        except Exception:
            pass

    pending = [c for c in chunks if c["chunk_id"] not in done_ids]

    from concurrent.futures import ThreadPoolExecutor, as_completed
    from tqdm import tqdm

    marker = out_path.with_suffix(".done.txt")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    def _save():
        out = {"book_id": chunks_path.stem, "nodes": all_nodes, "edges": all_edges}
        out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    bar = tqdm(total=len(pending), desc=chunks_path.stem, unit="chunk")
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(extract_chunk, ch, client): ch for ch in pending}
        n_done_since_save = 0
        for fut in as_completed(futs):
            ch = futs[fut]
            try:
                res = fut.result()
            except Exception as ex:
                log.exception("hard fail on %s", ch.get("chunk_id"))
                (err_dir / f"{ch['chunk_id'].replace('::','_')}.txt").write_text(str(ex), encoding="utf-8")
                res = {"nodes": [], "edges": []}
            all_nodes.extend(res["nodes"])
            all_edges.extend(res["edges"])
            with marker.open("a", encoding="utf-8") as f:
                f.write(ch["chunk_id"] + "\n")
            bar.update(1)
            n_done_since_save += 1
            if n_done_since_save >= 50:
                _save()
                n_done_since_save = 0
    bar.close()
    _save()
    return {"chunks": len(chunks), "processed": len(pending), "nodes": len(all_nodes), "edges": len(all_edges)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path", nargs="?", type=Path)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--no-resume", action="store_true")
    args = ap.parse_args()

    targets = sorted(Path("data/chunks").glob("*.json")) if args.all else [args.path]
    for p in targets:
        if p is None:
            continue
        out = Path("data/triples") / p.name
        stats = extract_book(p, out, limit=args.limit, workers=args.workers, resume=not args.no_resume)
        print(f"OK  {p.name} -> {out}  {stats}")


if __name__ == "__main__":
    main()
