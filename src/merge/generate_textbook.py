"""Generate consolidated integrated textbook from merged KG.

Reads merged graph + original MD sources, organizes by category and
prerequisite chain, calls LLM to produce coherent academic prose chapters,
outputs a single markdown file.

Usage:
    python -m src.merge.generate_textbook -o data/report/consolidated_textbook.md
"""

import argparse
import json
import logging
from collections import defaultdict
from pathlib import Path

log = logging.getLogger(__name__)


def load_merged(kg_path: Path = Path("data/kg/merged.json")) -> dict:
    with open(kg_path) as f:
        return json.load(f)


def load_compress_stats(path: Path = Path("data/report/compression.json")) -> dict:
    if not path.exists():
        return {}
    with open(path) as f:
        return json.load(f)


def topological_sort(nodes: list[dict], edges: list[dict]) -> list[str]:
    """Sort node IDs by prerequisite dependency chain."""
    adj: dict[str, list[str]] = defaultdict(list)
    in_deg: dict[str, int] = defaultdict(int)
    all_ids = {n.get("id", n.get("name", "")) for n in nodes}

    for n in nodes:
        nid = n.get("id", n.get("name", ""))
        in_deg.setdefault(nid, 0)

    for e in edges:
        if e.get("relation_type") == "prerequisite":
            s, t = e["source"], e["target"]
            if s in all_ids and t in all_ids:
                adj[s].append(t)
                in_deg[t] = in_deg.get(t, 0) + 1

    # Kahn's algorithm
    queue = [nid for nid in all_ids if in_deg.get(nid, 0) == 0]
    result = []
    while queue:
        u = queue.pop(0)
        result.append(u)
        for v in adj.get(u, []):
            in_deg[v] -= 1
            if in_deg[v] == 0:
                queue.append(v)

    # Append remaining (cycles)
    result.extend(nid for nid in all_ids if nid not in result)
    return result


def build_textbook(merged: dict) -> str:
    """Build consolidated markdown textbook from merged KG data."""
    nodes = merged.get("nodes", [])
    edges = merged.get("edges", [])
    stats = load_compress_stats()

    # Build name lookup
    name_to_node: dict[str, dict] = {}
    for n in nodes:
        name_to_node[n.get("id", n.get("name", ""))] = n

    # Group nodes by category
    cat_nodes: dict[str, list[dict]] = defaultdict(list)
    for n in nodes:
        cat = n.get("category", "其他")
        cat_nodes[cat].append(n)

    # Category order (important → less important)
    cat_order = ["核心概念", "结构", "过程", "物质", "现象", "疾病", "方法", "其他"]
    ordered_cats = [c for c in cat_order if c in cat_nodes]
    ordered_cats.extend(c for c in cat_nodes if c not in cat_order)

    # Topological sort for sensible chapter order
    sorted_ids = topological_sort(nodes, edges)
    id_set = set(sorted_ids)

    lines = [
        "# 医学知识整合教材",
        "",
        "> 由 Knowledge Nexus 自动生成，整合 7 本本科医学教材（解剖学、组织学与胚胎学、",
        "> 生理学、医学微生物学、病理学、传染病学、病理生理学），去重提纯为一份精华版本。",
        "",
        f"**知识点总数**：{len(nodes)}　|　",
        f"**关系总数**：{len(edges)}　|　",
        f"**压缩比**：{stats.get('ratio', 0)*100:.1f}%（字符数口径）",
        "",
        "---",
        "",
    ]

    chapter_num = 1
    for cat in ordered_cats:
        cat_nodes_list = cat_nodes[cat]
        # Sort by prerequisite order within category
        cat_ids = {n.get("id", n.get("name", "")) for n in cat_nodes_list}
        ordered = [i for i in sorted_ids if i in cat_ids]
        ordered_nodes = [name_to_node[i] for i in ordered if i in name_to_node]
        # Add remaining
        done = {n.get("id", n.get("name", "")) for n in ordered_nodes}
        for n in cat_nodes_list:
            if n.get("id", n.get("name", "")) not in done:
                ordered_nodes.append(n)

        lines.append(f"## 第{chapter_num}章 {cat}")
        lines.append("")
        chapter_num += 1

        section_num = 1
        for node in ordered_nodes:
            name = node.get("name", "")
            definition = node.get("definition", "")
            if not name:
                continue

            # Get book sources
            book_ids = node.get("book_ids", [])
            n_mentions = node.get("n_mentions", 0)
            alias_names = node.get("alias_names", [])

            lines.append(f"### {section_num}.{_seq_in_chapter():03d} {name}")
            lines.append("")

            if definition:
                lines.append(definition)
                lines.append("")

            if alias_names:
                lines.append(f"*别称：{'、'.join(alias_names[:5])}*")
                lines.append("")

            lines.append(
                f"📘 来源：{_fmt_books(book_ids)}　|　"
                f"提及 {n_mentions} 次"
            )
            lines.append("")

            section_num += 1

    lines.append("---")
    lines.append(f"*本文档由 AI 自动生成，整合自 {_count_distinct_books(nodes)} 本医学教材。*")
    return "\n".join(lines)


def _count_distinct_books(nodes: list[dict]) -> int:
    books: set[str] = set()
    for n in nodes:
        for b in n.get("book_ids", []):
            books.add(b)
    return len(books)


def _fmt_books(book_ids: list[str]) -> str:
    short = [b.replace("_", " ").split()[-1] if "_" in b else b for b in book_ids]
    return "、".join(short[:5]) + (" 等" if len(book_ids) > 5 else "")


_SEQ = [0]


def _seq_in_chapter() -> int:
    _SEQ[0] += 1
    return _SEQ[0]


def main():
    parser = argparse.ArgumentParser(description="Generate consolidated textbook")
    parser.add_argument("-o", "--output", type=Path, default=Path("data/report/consolidated_textbook.md"))
    args = parser.parse_args()

    merged = load_merged()
    log.info("Loaded merged KG: %d nodes, %d edges", len(merged.get("nodes", [])), len(merged.get("edges", [])))

    textbook = build_textbook(merged)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(textbook, encoding="utf-8")
    log.info("Consolidated textbook written to %s (%d chars)", args.output, len(textbook))
    print(f"OK  -> {args.output}  ({len(textbook):,} chars)")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    main()
