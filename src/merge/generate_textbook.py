"""Generate consolidated integrated textbook from original MD sources + KG.

Reads merged KG to identify core concepts, then extracts relevant
original-text sections from data/md/*.md, deduplicates, and organizes
into a standard medical curriculum chapter order.

Usage:
    python -m src.merge.generate_textbook -o data/report/consolidated_textbook.md
"""

import argparse
import json
import logging
import re
from collections import defaultdict
from pathlib import Path

log = logging.getLogger(__name__)

# Standard medical curriculum chapter order
CURRICULUM = [
    ("细胞与组织", ["细胞", "组织", "上皮", "结缔组织", "肌组织", "神经组织", "胚胎"]),
    ("血液与免疫", ["血液", "免疫", "白细胞", "红细胞", "血小板", "抗体", "抗原", "T细胞", "B细胞", "补体", "淋巴"]),
    ("循环系统", ["循环", "心脏", "血管", "血压", "心肌", "动脉", "静脉", "心电图", "心输出量"]),
    ("呼吸系统", ["呼吸", "肺", "肺泡", "通气", "换气", "氧", "二氧化碳"]),
    ("消化与代谢", ["消化", "胃", "肠", "肝", "胰", "代谢", "能量", "体温", "营养", "吸收"]),
    ("泌尿系统", ["肾", "尿", "滤过", "重吸收", "排泄", "水平衡", "电解质"]),
    ("神经系统", ["神经", "突触", "反射", "感觉", "运动", "大脑", "脊髓", "自主神经", "递质"]),
    ("内分泌与生殖", ["内分泌", "激素", "甲状腺", "肾上腺", "胰岛", "生殖", "性腺", "妊娠"]),
    ("病理基础", ["病理", "损伤", "坏死", "凋亡", "萎缩", "肥大", "化生", "肿瘤", "炎症", "修复"]),
    ("病理生理与疾病", ["休克", "缺氧", "发热", "应激", "水肿", "酸碱", "缺血", "再灌注", "衰竭", "凝血"]),
    ("微生物与传染病", ["微生物", "细菌", "病毒", "真菌", "寄生虫", "传染", "感染", "流行病", "疫苗", "消毒"]),
    ("解剖学基础", ["解剖", "骨骼", "肌肉", "关节", "体表", "筋膜", "神经血管束", "器官位置"]),
]


def load_merged(kg_path: Path = Path("data/kg/merged.json")) -> dict:
    with open(kg_path) as f:
        return json.load(f)


def load_md(book_id: str) -> str:
    """Load original markdown for a book."""
    path = Path(f"data/md/{book_id}.md")
    if path.exists():
        return path.read_text(encoding="utf-8")
    return ""


def assign_node_to_chapter(node: dict) -> str:
    """Assign a KG node to a curriculum chapter based on name and definition."""
    name = node.get("name", "")
    definition = node.get("definition", "")
    book_ids = node.get("book_ids", [])
    category = node.get("category", "")

    # Direct book-to-chapter mapping for strong signals
    book_map = {
        "局部解剖学": "解剖学基础",
        "组织学与胚胎学": "细胞与组织",
        "生理学": "循环系统",  # fallback, individual concepts override
        "医学微生物学": "微生物与传染病",
        "病理学": "病理基础",
        "传染病学": "微生物与传染病",
        "病理生理学": "病理生理与疾病",
    }

    scores = defaultdict(int)

    # Keyword matching in name + definition (most specific)
    combined = name + definition
    for ch_name, keywords in CURRICULUM:
        for kw in keywords:
            if kw in name:
                scores[ch_name] += 10  # name match is strongest
            elif kw in definition:
                scores[ch_name] += 5

    # Book-based bonus
    for bid in book_ids:
        bid_short = bid.split("_", 1)[-1] if "_" in bid else bid
        if bid_short in book_map:
            scores[book_map[bid_short]] += 3

    # Category hints
    cat_map = {
        "疾病": "病理生理与疾病",
        "病原体": "微生物与传染病",
        "结构": "解剖学基础",
    }
    if category in cat_map:
        scores[cat_map[category]] += 2

    if scores:
        best = max(scores, key=scores.get)
        if scores[best] >= 3:
            return best

    # Fallback: assign based on source book
    for bid in book_ids:
        bid_short = bid.split("_", 1)[-1] if "_" in bid else bid
        if bid_short in book_map:
            return book_map[bid_short]

    return "病理生理与疾病"


def extract_section_from_md(md_text: str, concept: str, context_chars: int = 500) -> str:
    """Extract a section of MD text around a given concept."""
    idx = md_text.find(concept)
    if idx < 0:
        return ""

    # Expand to paragraph boundaries
    start = max(0, idx - context_chars)
    end = min(len(md_text), idx + len(concept) + context_chars)

    # Find nearest heading before start
    before = md_text[:start]
    heading_matches = list(re.finditer(r"^#{1,3}\s+.+$", before, re.MULTILINE))
    if heading_matches:
        start = heading_matches[-1].start()

    # Find next heading after end
    after = md_text[end:]
    next_heading = re.search(r"^#{1,3}\s+.+$", after, re.MULTILINE)
    if next_heading:
        end = end + next_heading.start()

    return md_text[start:end].strip()


def build_textbook(merged: dict) -> str:
    nodes = merged.get("nodes", [])
    edges = merged.get("edges", [])

    # Assign nodes to curriculum chapters
    ch_nodes: dict[str, list[dict]] = defaultdict(list)
    for n in nodes:
        ch = assign_node_to_chapter(n)
        ch_nodes[ch].append(n)

    # Load all MD sources
    all_books = set()
    for n in nodes:
        for b in n.get("book_ids", []):
            all_books.add(b)
    md_cache = {b: load_md(b) for b in all_books}

    lines = [
        "# 医学知识整合教材",
        "",
        "> 由 Knowledge Nexus 自动生成，整合 7 本医学教材精华内容，去重提纯为一份系统化教材。",
        "",
        f"**来源教材**：局部解剖学、组织学与胚胎学、生理学、医学微生物学、病理学、传染病学、病理生理学",
        f"**覆盖知识点**：{len(nodes)}　|　**关系边**：{len(edges)}",
        "",
        "---",
        "",
        "## 使用说明",
        "",
        "本文档是 AI 自动生成的医学知识整合教材。每个章节围绕一个学科主题，",
        "从多本教材中提取相关知识段落，去重合并后形成系统化内容。",
        "各知识点之间标注了前置依赖（→ 需先学）和并列关系（≈ 相关概念）。",
        "",
        "---",
        "",
    ]

    # Build prerequisite graph for reference
    prereq_of: dict[str, list[str]] = defaultdict(list)
    for e in edges:
        if e.get("relation_type") == "prerequisite":
            prereq_of[e["target"]].append(e["source"])

    for ch_name, _ in CURRICULUM:
        if ch_name not in ch_nodes:
            continue
        chapter_nodes = ch_nodes[ch_name]

        # Sort by importance
        chapter_nodes.sort(key=lambda n: (n.get("n_mentions", 0), len(n.get("book_ids", []))), reverse=True)

        lines.append(f"# {ch_name}")
        lines.append("")

        # Write chapter intro
        top_concepts = [n["name"] for n in chapter_nodes[:10] if n.get("n_mentions", 0) >= 2]
        if top_concepts:
            lines.append(f"本章涵盖{'、'.join(top_concepts[:6])}等核心知识点，"
                        f"整合自{_count_books(chapter_nodes)}本教材相关内容。")
            lines.append("")

        # Section: Core concepts (well-defined, from KG)
        lines.append("## 核心概念与定义")
        lines.append("")
        for node in chapter_nodes:
            name = node.get("name", "")
            definition = node.get("definition", "")
            if not name or not definition or len(definition) < 10:
                continue
            n_mentions = node.get("n_mentions", 0)
            if n_mentions < 2:
                continue

            book_ids = node.get("book_ids", [])
            book_short = [b.split("_", 1)[-1] if "_" in b else b for b in book_ids[:3]]

            # Show prerequisites if any
            nid = node.get("id", name)
            prereqs = prereq_of.get(nid, [])[:3]
            prereq_text = ""
            if prereqs:
                prereq_names = []
                for pid in prereqs:
                    for n2 in chapter_nodes:
                        if n2.get("id") == pid:
                            prereq_names.append(n2.get("name", pid))
                            break
                if prereq_names:
                    prereq_text = f"  ← 前置：{' → '.join(prereq_names)}"

            lines.append(f"**{name}**：{definition}")
            lines.append(f"*来源：{'、'.join(book_short)}{prereq_text}*")
            lines.append("")

        # Section: Original text excerpts for top concepts
        lines.append("## 原文精读")
        lines.append("")
        covered = set()
        excerpt_count = 0
        for node in chapter_nodes:
            if excerpt_count >= 8:
                break
            name = node.get("name", "")
            if not name or name in covered:
                continue
            n_mentions = node.get("n_mentions", 0)
            if n_mentions < 3:
                continue
            covered.add(name)

            # Try to extract from source MD
            for bid in node.get("book_ids", []):
                md = md_cache.get(bid, "")
                if not md:
                    continue
                excerpt = extract_section_from_md(md, name, 400)
                if excerpt and len(excerpt) > 100:
                    book_name = bid.split("_", 1)[-1] if "_" in bid else bid
                    lines.append(f"### {name} — 选自《{book_name}》")
                    lines.append("")
                    # Clean up the excerpt
                    excerpt = _clean_md_excerpt(excerpt, name)
                    lines.append(excerpt)
                    lines.append("")
                    excerpt_count += 1
                    break

        lines.append("---")
        lines.append("")

    lines.append("---")
    lines.append(f"*本文档由 Knowledge Nexus 自动生成，整合 7 本医学教材。P2 技术报告见飞书文档。*")
    return "\n".join(lines)


def _count_books(nodes: list[dict]) -> int:
    books = set()
    for n in nodes:
        for b in n.get("book_ids", []):
            books.add(b)
    return len(books)


def _clean_md_excerpt(text: str, concept: str) -> str:
    """Clean up a markdown excerpt for readability."""
    # Strip original book title headings and metadata lines
    noise_patterns = [
        r"^#{1,3}\s*(?:生理学|病理生理学|病理学|局部解剖学|组织学与胚胎学|医学微生物学|传染病学|Regional Anatomy|Physiology|第\s*版\s*[\d\s]+)\s*$",
        r"^#{1,3}\s*(?:前言|序言|推荐阅读|中英文名词对照索引|读者信息反馈方式|编委名单|全国高等学校|版权所有|侵权必究|本章数字资源).*$",
        r"^#{1,3}\s*(?:第\d+\s*章\s*\S+|第[一二三四五六七八九十]+\s*章\s*\S+|第十轮|规划教材修订说明).*$",
        r"^#{1,3}\s*(?:郭晓奎|彭宜红|罗自强|管又飞).*$",
        r"^#{1,3}\s*\d+\s*$",
        r"^\s*\.{3,}\s*\d+\s*$",
        r"^\s*\d+[\.\s]*$",
    ]
    for pat in noise_patterns:
        text = re.sub(pat, "", text, flags=re.MULTILINE)

    # Limit to ~1200 chars, centered on concept
    if len(text) > 1200:
        idx = text.find(concept)
        if idx > 400:
            start = max(0, idx - 400)
            text = "..." + text[start:idx + 800] + "..."

    # Collapse excessive whitespace
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


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
