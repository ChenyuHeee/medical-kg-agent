"""T-N02: 章节切分 + RAG 友好 chunker。

Reads RawDoc JSON, splits into Chunk[] per Lead's new schema.
- 500-800 chars per chunk
- 50-100 char overlap (sliding window)
- Splits at paragraph boundaries (\\n\\n), never inside $$...$$ blocks
- chunk_id format: {book_id}::ch{NN}::s{NNN}::{NNNNN}

Usage:
    python src/ingest/chunk.py data/raw/03_生理学.json -o data/chunks/03_生理学.json
"""

import argparse
import json
import re
import sys
from pathlib import Path

CHUNK_MIN = 500
CHUNK_MAX = 800
OVERLAP = 80

# Match $$...$$ blocks (may span lines)
FORMULA_RE = re.compile(r"\$\$[^$]*\$\$", re.DOTALL)
HEADING_RE = re.compile(r"^(#{1,3})\s+(.+)$", re.MULTILINE)


def find_page_for_offset(rawdoc: dict, char_offset: int) -> int:
    """Find which synthetic page contains the given char offset."""
    pos = 0
    for p in rawdoc["pages"]:
        next_pos = pos + len(p["text"])
        if char_offset < next_pos:
            return p["page_no"]
        pos = next_pos
    return rawdoc["pages"][-1]["page_no"] if rawdoc["pages"] else -1


def protect_formulas(text: str) -> tuple[str, dict]:
    """Replace $$...$$ blocks with placeholders so they aren't split.

    Returns (protected_text, placeholder_map).
    """
    placeholders = {}

    def _repl(m):
        idx = len(placeholders)
        key = f"__FORMULA_{idx}__"
        placeholders[key] = m.group(0)
        return key

    return FORMULA_RE.sub(_repl, text), placeholders


def restore_formulas(text: str, placeholders: dict) -> str:
    for k, v in placeholders.items():
        text = text.replace(k, v)
    return text


def _is_real_chapter(title: str) -> bool:
    """Filter out frontmatter noise headings."""
    noise = {
        "Physiology",
        "读者信息反馈方式",
        "版权所有，侵权必究！",
        "序言",
        "前言",
        "推荐阅读",
        "中英文名词对照索引",
    }
    if title in noise:
        return False
    if re.match(r"^第\d+版$", title):  # "第10版"
        return False
    if "全国高等学校" in title:
        return False
    if "规划教材修订说明" in title:
        return False
    if re.match(r"^[A-Za-z\s]+$", title):  # pure English title like "Physiology"
        return False
    # Must contain Chinese or be a recognized chapter marker
    if not re.search(r"[一-鿿]", title):
        return False
    return True


def extract_heading_sections(md_text: str) -> list[dict]:
    """Split markdown text into sections at #/##/### headings.

    Returns list of {level, title, chapter, section, start, end, text}.
    Frontmatter noise is filtered out.
    """
    sections = []
    headings = list(HEADING_RE.finditer(md_text))

    if not headings:
        sections.append(
            {
                "level": 0,
                "title": "",
                "chapter": "",
                "section": "",
                "start": 0,
                "end": len(md_text),
                "text": md_text,
            }
        )
        return sections

    current_chapter = ""
    current_section = ""

    for i, m in enumerate(headings):
        level = len(m.group(1))
        title = m.group(2).strip()
        start = m.start()
        end = headings[i + 1].start() if i + 1 < len(headings) else len(md_text)
        text = md_text[start:end]

        # Only real chapters become section boundaries; frontmatter is grouped
        # under an empty chapter
        if level == 1:
            if _is_real_chapter(title):
                current_chapter = title
                current_section = ""
            else:
                # Merge frontmatter into previous section or skip
                if sections:
                    sections[-1]["text"] += text
                    sections[-1]["end"] = end
                continue
        elif level == 2:
            current_section = title

        sections.append(
            {
                "level": level,
                "title": title,
                "chapter": current_chapter,
                "section": current_section if level >= 2 else "",
                "start": start,
                "end": end,
                "text": text,
            }
        )

    return sections


def split_section_text(
    text: str,
    chapter: str,
    section: str,
    book_id: str,
    rawdoc: dict,
    global_offset: int,
    chunk_idx_start: int = 0,
) -> tuple[list[dict], int]:
    """Split a section's text into overlapping chunks of 500-800 chars.
    Returns (chunks, next_chunk_idx).
    """
    chunks = []
    chunk_idx = chunk_idx_start
    protected, formulas = protect_formulas(text)

    # Split by paragraphs
    paragraphs = re.split(r"\n\n+", protected)
    paragraphs = [p.strip() for p in paragraphs if p.strip()]

    # Merge short paragraphs; split long ones
    merged: list[str] = []
    buf = ""
    for p in paragraphs:
        if len(p) > CHUNK_MAX:
            # Flush buffer first
            if buf:
                merged.append(buf)
                buf = ""
            # Split long paragraph
            for piece in _split_long_para(p, formulas):
                merged.append(piece)
            continue

        candidate = (buf + "\n\n" + p).strip() if buf else p
        if len(candidate) <= CHUNK_MAX:
            buf = candidate
        else:
            if buf:
                merged.append(buf)
            buf = p
    if buf:
        merged.append(buf)

    if not merged:
        return []

    # Build a position map: for each merged block, track its offset in text
    block_offsets: list[int] = []
    search_start = 0
    for mg in merged:
        # Use first 40 chars as unique anchor
        anchor = mg[:40]
        idx = text.find(anchor, search_start)
        if idx >= 0:
            block_offsets.append(idx)
            search_start = idx + len(mg)
        else:
            block_offsets.append(search_start)

    # Build overlapping chunks
    i = 0
    while i < len(merged):
        chunk_text = merged[i]
        chunk_start = block_offsets[i]
        j = i + 1
        while j < len(merged) and len(chunk_text) + len(merged[j]) + 2 <= CHUNK_MAX:
            chunk_text += "\n\n" + merged[j]
            j += 1

        chunk_text = restore_formulas(chunk_text.strip(), formulas)
        n = len(chunk_text)

        # Only skip truly tiny fragments at the very end
        if n >= CHUNK_MIN or (i == len(merged) - 1 and n >= 50):
            char_start = global_offset + chunk_start
            char_end = char_start + n
            page = find_page_for_offset(rawdoc, char_start)

            chunks.append(
                {
                    "chunk_id": f"{book_id}::ch{_chapter_seq(chapter):02d}::s{_section_seq(section):03d}::{chunk_idx:05d}",
                    "book_id": book_id,
                    "chapter": chapter,
                    "section": section,
                    "page": page,
                    "char_start": char_start,
                    "char_end": char_end,
                    "n_chars": n,
                    "text": chunk_text,
                }
            )
            chunk_idx += 1

        # Advance with overlap
        if j - i >= 2:
            # Count chars from end of chunk backward to find overlap point
            overlap_chars = 0
            k = j - 1
            while k > i:
                overlap_chars += len(merged[k]) + 2
                if overlap_chars >= OVERLAP:
                    break
                k -= 1
            i = max(k + 1, i + 1)
        else:
            i = j

    return chunks, chunk_idx


def _split_long_para(text: str, formulas: dict) -> list[str]:
    """Split a single long paragraph (no natural breaks) into pieces."""
    parts = []
    pos = 0
    while pos < len(text):
        end = min(pos + CHUNK_MAX, len(text))
        chunk = text[pos:end]
        # Try to break at sentence end
        if end < len(text):
            for sep in "。！？\n":
                idx = chunk.rfind(sep)
                if idx > CHUNK_MIN:
                    end = pos + idx + 1
                    chunk = text[pos:end]
                    break
        chunk = restore_formulas(chunk.strip(), formulas)
        if chunk:
            parts.append(chunk)
        pos = end - OVERLAP if end < len(text) else end
    return parts


# Simple counters for chunk_id generation
_CH_CTR: dict[str, int] = {}
_SEC_CTR: dict[str, int] = {}


def _chapter_seq(name: str) -> int:
    if name not in _CH_CTR:
        _CH_CTR[name] = len(_CH_CTR) + 1
    return _CH_CTR[name]


def _section_seq(name: str) -> int:
    if name not in _SEC_CTR:
        _SEC_CTR[name] = len(_SEC_CTR) + 1
    return _SEC_CTR[name]


def process_book(raw_path: Path) -> list[dict]:
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    book_id = raw["book_id"]

    # Reconstruct full MD text from pages
    full_text = "\n".join(p["text"] for p in raw["pages"])

    # Reset counters per book
    _CH_CTR.clear()
    _SEC_CTR.clear()

    sections = extract_heading_sections(full_text)
    all_chunks = []
    chunk_idx = 0
    for sec in sections:
        ch = sec.get("chapter", "")
        se = sec.get("section", "")
        chunks, chunk_idx = split_section_text(
            sec["text"], ch, se, book_id, raw, sec["start"], chunk_idx
        )
        all_chunks.extend(chunks)

    return all_chunks


def main():
    parser = argparse.ArgumentParser(description="Split RawDoc JSON into Chunk[]")
    parser.add_argument("raw", type=Path, help="Path to RawDoc JSON")
    parser.add_argument("-o", "--output", type=Path, help="Output JSON path")
    args = parser.parse_args()

    if not args.raw.exists():
        print(f"ERROR: {args.raw} not found", file=sys.stderr)
        sys.exit(1)

    chunks = process_book(args.raw)

    out = args.output or Path(f"data/chunks/{args.raw.stem}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(chunks, ensure_ascii=False, indent=2), encoding="utf-8")

    total_chars = sum(c["n_chars"] for c in chunks)
    max_c = max((c["n_chars"] for c in chunks), default=0)
    min_c = min((c["n_chars"] for c in chunks), default=0)
    avg_c = total_chars / len(chunks) if chunks else 0
    print(
        f"OK  {args.raw.name} -> {out}\n"
        f"    chunks={len(chunks)}  total={total_chars}  "
        f"min={min_c}  max={max_c}  avg={avg_c:.0f}"
    )


if __name__ == "__main__":
    main()
