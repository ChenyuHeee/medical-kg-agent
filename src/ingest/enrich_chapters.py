"""T-N02b: Enrich RawDoc with structured ``chapters[]`` per official spec.

Reads existing ``data/raw/{book}.json``, parses the markdown text in
``pages[*].text`` to build chapter slices keyed by top-level (#) headings,
and writes back the same file with two new fields:

- ``total_chars`` — sum of chapter content lengths
- ``chapters`` — list of {chapter_id, title, page_start, page_end, content, char_count}

Idempotent: re-running rebuilds chapters[] from current pages.

Usage:
    python -m src.ingest.enrich_chapters data/raw/03_生理学.json
    python -m src.ingest.enrich_chapters --all
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

H1_RE = re.compile(r"^#\s+(.+)$", re.MULTILINE)


def _is_chapter_title(t: str) -> bool:
    """Heuristic: titles like '第一章 …', '第1章 …', '绪论' kept; noise filtered."""
    t = t.strip()
    if not t:
        return False
    if re.match(r"^第[一二三四五六七八九十百千0-9]+章", t):
        return True
    if t in {"绪论", "前言", "序言", "总论"}:
        return True
    # Allow other H1 headings if no 第X章 markers were found at all (fallback)
    return True


def build_chapters(rawdoc: dict) -> list[dict]:
    """Build chapters[] by stitching all pages into one stream and slicing on # headings."""
    book_id = rawdoc["book_id"]
    pages = rawdoc.get("pages", [])

    # Stitch all pages with markers so we can map char offsets back to page_no.
    parts: list[tuple[int, str]] = []  # (page_no, text)
    for p in pages:
        parts.append((p["page_no"], p.get("text", "")))

    full = "\n\n".join(t for _, t in parts)

    # Build offset->page_no map (cumulative)
    offset_to_page: list[tuple[int, int]] = []  # (start_offset, page_no)
    cursor = 0
    for pn, t in parts:
        offset_to_page.append((cursor, pn))
        cursor += len(t) + 2  # +2 for the joining "\n\n"

    def page_of(off: int) -> int:
        # Linear scan is fine; pages are small.
        last = 1
        for start, pn in offset_to_page:
            if off < start:
                return last
            last = pn
        return last

    # Find H1 boundaries.
    matches = list(H1_RE.finditer(full))
    # Filter to chapter-like titles.
    chapter_marks: list[tuple[int, str]] = []
    has_explicit = any(re.match(r"^第[一二三四五六七八九十百千0-9]+章", m.group(1).strip()) for m in matches)
    for m in matches:
        title = m.group(1).strip()
        if has_explicit:
            if not re.match(r"^第[一二三四五六七八九十百千0-9]+章", title) and title not in {"绪论", "总论"}:
                continue
        chapter_marks.append((m.start(), title))

    if not chapter_marks:
        # Whole-book single chapter fallback
        chapter_marks = [(0, rawdoc.get("title", book_id))]

    chapters: list[dict] = []
    for i, (start, title) in enumerate(chapter_marks):
        end = chapter_marks[i + 1][0] if i + 1 < len(chapter_marks) else len(full)
        content = full[start:end].strip()
        page_start = page_of(start)
        page_end = page_of(max(end - 1, start))
        chapters.append(
            {
                "chapter_id": f"{book_id}::ch{i + 1:02d}",
                "title": title,
                "page_start": page_start,
                "page_end": page_end,
                "content": content,
                "char_count": len(content),
            }
        )
    return chapters


def enrich_file(path: Path) -> dict:
    raw = json.loads(path.read_text(encoding="utf-8"))
    chapters = build_chapters(raw)
    raw["chapters"] = chapters
    raw["total_chars"] = sum(c["char_count"] for c in chapters)
    path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    return raw


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path", nargs="?", type=Path)
    ap.add_argument("--all", action="store_true", help="Process all data/raw/*.json")
    args = ap.parse_args()

    targets: list[Path] = []
    if args.all:
        targets = sorted(Path("data/raw").glob("*.json"))
    elif args.path:
        targets = [args.path]
    else:
        ap.error("provide a path or --all")

    for p in targets:
        raw = enrich_file(p)
        print(f"OK  {p.name}: {len(raw['chapters'])} chapters, total_chars={raw['total_chars']}")


if __name__ == "__main__":
    main()
