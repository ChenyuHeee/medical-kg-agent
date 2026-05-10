"""T-09: Generate a human-readable summary report (markdown).

Inputs: per-book graph JSONs + merged graph JSON + compression metadata.
Output: data/report/summary.md
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any


def _topk_by(items: list[dict], key: str, k: int = 15) -> list[dict]:
    return sorted(items, key=lambda x: x.get(key, 0), reverse=True)[:k]


def build_report(
    book_graphs: dict[str, dict[str, Any]],   # book_id -> graph json
    merged: dict[str, Any],
    compressed: dict[str, Any] | None = None,
) -> str:
    lines: list[str] = []
    lines.append("# 多教材知识整合报告\n")
    lines.append("> 由 `src/merge` 自动生成。包含单本概况、跨教材重叠/互补/缺失分析、压缩指标。\n")

    # ---- 单本概况 ----
    lines.append("## 1. 单本教材统计\n")
    lines.append("| 教材 | 节点数 | 边数 | 高频实体类型 (Top3) |")
    lines.append("|---|---:|---:|---|")
    for book_id, g in book_graphs.items():
        type_cnt = Counter(n.get("type", "Concept") for n in g["nodes"])
        top_types = ", ".join(f"{t}×{c}" for t, c in type_cnt.most_common(3))
        lines.append(f"| {book_id} | {len(g['nodes'])} | {len(g['edges'])} | {top_types} |")
    lines.append("")

    # ---- 跨教材分析 ----
    lines.append("## 2. 跨教材整合分析\n")
    n_overlap = sum(1 for n in merged["nodes"] if n.get("n_books", 0) >= 2)
    lines.append(f"- 合并后节点总数：**{len(merged['nodes'])}**")
    lines.append(f"- 合并后边总数：**{len(merged['edges'])}**")
    lines.append(f"- 跨教材重叠节点（≥2 本提及）：**{n_overlap}**")
    lines.append(f"- 同名/别名分组数：**{len(merged.get('alias_table', {}))}**\n")

    # 重叠 Top
    overlap = sorted(
        (n for n in merged["nodes"] if n.get("n_books", 0) >= 2),
        key=lambda x: (x["n_books"], x["n_mentions"]),
        reverse=True,
    )[:20]
    if overlap:
        lines.append("### 2.1 重叠知识点 Top 20（跨教材公共核心）\n")
        lines.append("| 实体 | 类型 | 跨书数 | 总提及 | 出现教材 |")
        lines.append("|---|---|---:|---:|---|")
        for n in overlap:
            lines.append(
                f"| {n['name']} | {n['type']} | {n['n_books']} | {n['n_mentions']} | {', '.join(n['book_ids'])} |"
            )
        lines.append("")

    # 互补 / 独有
    lines.append("### 2.2 各教材独有知识点（仅出现在 1 本）\n")
    for book_id in book_graphs:
        unique = [
            n for n in merged["nodes"]
            if n["book_ids"] == [book_id]
        ]
        unique.sort(key=lambda x: x["n_mentions"], reverse=True)
        lines.append(f"**{book_id}**：独有 {len(unique)} 个，前 10：{', '.join(n['name'] for n in unique[:10])}\n")

    # 别名表
    aliases = merged.get("alias_table", {})
    if aliases:
        lines.append("### 2.3 别名/同义合并示例（Top 15）\n")
        sample = list(aliases.items())[:15]
        lines.append("| 规范名 | 来源（书:别名） |")
        lines.append("|---|---|")
        for ck, items in sample:
            display = ", ".join(f"{a['book_id']}:{a['alias']}" for a in items)
            lines.append(f"| {ck} | {display} |")
        lines.append("")

    # ---- 压缩 ----
    if compressed:
        c = compressed.get("compression", compressed)
        lines.append("## 3. 内容压缩指标\n")
        if "input_total_nodes" in c:
            # legacy schema
            lines.append(f"- 单本节点数总和（原体量基准）：**{c['input_total_nodes']}**")
            lines.append(f"- 合并后节点数：**{c['merged_nodes']}**")
            lines.append(f"- 压缩后保留节点数：**{c['kept_nodes']}**（边 {c['kept_edges']}）")
            lines.append(f"- **压缩率 (vs 原体量)：{c['node_ratio_vs_input']*100:.2f}%** "
                         f"（目标 ≤ {c['target_ratio']*100:.0f}%；{'✅ 达标' if c['meets_target'] else '❌ 未达标'}）\n")
        else:
            # current char-based schema (compress.run)
            ratio = c.get("ratio", 0.0)
            target = c.get("target_ratio", 0.30)
            passed = c.get("passed", ratio <= target)
            lines.append(f"- 原教材字符总量：**{c.get('original_chars', 0):,}**")
            lines.append(f"- 整合后图谱字符量：**{c.get('merged_chars', 0):,}**"
                         f"（节点定义 {c.get('node_definition_chars', 0):,} / 关系描述 {c.get('edge_description_chars', 0):,}）")
            lines.append(f"- 合并图节点：**{c.get('merged_node_count', 0)}** / 边：**{c.get('merged_edge_count', 0)}**")
            lines.append(f"- 精华图节点：**{c.get('compact_node_count', 0)}** / 边：**{c.get('compact_edge_count', 0)}**")
            lines.append(f"- **压缩率 (vs 原体量)：{ratio*100:.2f}%** "
                         f"（目标 ≤ {target*100:.0f}%；{'✅ 达标' if passed else '❌ 未达标'}）\n")
        lines.append("> 口径：以**字符数**为体量度量。"
                     "压缩策略 = 跨书优先 + 提及频次 + 度中心性加权 Top-K。\n")

    lines.append("---\n")
    lines.append("_报告生成器：`src/merge/report.py`_\n")
    return "\n".join(lines)


def build_report_files(
    book_graph_paths: dict[str, Path],
    merged_path: Path,
    compressed_path: Path | None,
    out_path: Path,
) -> Path:
    book_graphs = {b: json.loads(Path(p).read_text("utf-8")) for b, p in book_graph_paths.items()}
    merged = json.loads(Path(merged_path).read_text("utf-8"))
    compressed = (
        json.loads(Path(compressed_path).read_text("utf-8")) if compressed_path else None
    )
    md = build_report(book_graphs, merged, compressed)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(md, encoding="utf-8")
    return out_path


__all__ = ["build_report", "build_report_files", "generate_report"]


# ---------------- New-schema report (T-D04) ----------------

def _safe_load(p: Path, default):
    if not Path(p).exists():
        return default
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:
        return default


def generate_report(out: Path = Path("report/整合报告.md")) -> Path:
    """Markdown report compatible with the rewritten KGNode/KGEdge schema."""
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    rep = Path("data/report")
    kg = Path("data/kg")

    comp = _safe_load(rep / "compression.json", {})
    integ = _safe_load(rep / "integrity.json", {})
    decisions = _safe_load(rep / "decisions.json", [])
    merged = _safe_load(kg / "merged.json", {"nodes": [], "edges": []})
    compact = _safe_load(kg / "compact.json", {"nodes": [], "edges": []})

    n_merge = sum(1 for d in decisions if d.get("action") == "merge")
    n_keep = sum(1 for d in decisions if d.get("action") == "keep")
    by_stage = Counter(d.get("stage", "?") for d in decisions if d.get("action") == "merge")
    cat_count = Counter(n.get("category", "?") for n in compact.get("nodes", []))

    L: list[str] = ["# 医学教材整合知识图谱 — 整合报告\n"]
    L.append("## 1. 概览\n")
    L.append(f"- 整合后节点：**{len(merged.get('nodes', []))}**，边：**{len(merged.get('edges', []))}**")
    L.append(f"- 压缩后节点：**{len(compact.get('nodes', []))}**，边：**{len(compact.get('edges', []))}**")
    if comp:
        L.append(f"- 字符压缩比：**{comp.get('ratio', 0):.2%}**（目标 ≤ {comp.get('target_ratio', 0.30):.0%}）")
        L.append(f"- 字符体量：{comp.get('compact_chars', 0)} / {comp.get('original_chars', 0)}")
    L.append("")

    L.append("## 2. 合并决策统计\n")
    L.append(f"- merge：{n_merge}（lexical {by_stage.get('lexical', 0)}，embedding {by_stage.get('embedding', 0)}）")
    L.append(f"- keep：{n_keep}\n")

    L.append("## 3. 教学完整性自检\n")
    if integ:
        L.append(f"- 状态：**{'✅ 通过' if integ.get('passed') else '⚠️ 触发救援'}**")
        L.append(f"- 救援节点数：{integ.get('rescued', 0)}")
    else:
        L.append("- 暂无完整性数据\n")
    L.append("")

    L.append("## 4. 类别分布（压缩后）\n")
    for cat, cnt in cat_count.most_common():
        L.append(f"- {cat}: {cnt}")
    L.append("")

    L.append("## 5. 典型合并示例（前 20 条）\n")
    for d in [d for d in decisions if d.get("action") == "merge"][:20]:
        affected = ", ".join(d.get("affected_nodes", []))
        L.append(f"- `{d.get('result_node','?')}` ← {affected} ｜ {d.get('reason','')} ｜ conf={d.get('confidence', 0):.2f}")
    L.append("")

    out.write_text("\n".join(L), encoding="utf-8")
    return out
