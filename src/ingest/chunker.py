"""T-N02 fallback: RAG-friendly chunker (500–800 chars, 50–100 overlap).

Reads ``data/raw/{book}.json`` (after ``enrich_chapters``), produces
``data/chunks/{book}.json`` per ARCHITECTURE §2.2.

Rules:
- chunks built per chapter (never cross chapters)
- target n_chars in [500, 800]
- adjacent chunks overlap [50, 100] chars
- prefer paragraph (\\n\\n) and sentence (。！？) boundaries
- never cut inside ``$$...$$`` math blocks
- per-chunk metadata: chunk_id, book_id, chapter, section, page, char_start, char_end, n_chars
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

TARGET = 700
MIN_LEN = 500
MAX_LEN = 800
OVERLAP = 80  # within [50, 100]

H2_RE = re.compile(r"^##\s+(.+)$", re.MULTILINE)
MATH_BLOCK_RE = re.compile(r"\$\$.*?\$\$", re.DOTALL)


def _safe_breakpoint(text: str, lo: int, hi: int) -> int:
    """Find a good cut position in text[lo:hi]; return absolute index in text."""
    window = text[lo:hi]
    # Prefer double-newline
    p = window.rfind("\n\n")
    if p >= 0 and p > (hi - lo) * 0.3:
        return lo + p + 2
    # Sentence end
    for c in "。！？!?\n":
        p = window.rfind(c)
        if p >= 0 and p > (hi - lo) * 0.3:
            return lo + p + 1
    # Hard cut
    return hi


def _avoid_math_split(text: str, cut: int) -> int:
    """If cut falls inside $$...$$, push it to the closing $$."""
    for m in MATH_BLOCK_RE.finditer(text):
        if m.start() < cut < m.end():
            return m.end()
    return cut


def chunk_chapter(book_id: str, chapter_idx: int, chapter: dict) -> list[dict]:
    """Slice one chapter into chunks."""
    content = chapter["content"]
    chapter_title = chapter["title"]
    chapter_id = chapter["chapter_id"]
    page_start = chapter.get("page_start", -1)
    page_end = chapter.get("page_end", -1)
    pages_span = max(page_end - page_start, 0)
    total = len(content)

    # Find section (##) boundaries to attach as section labels.
    section_marks = [(m.start(), m.group(1).strip()) for m in H2_RE.finditer(content)]

    def section_of(off: int) -> str:
        cur = ""
        for s, t in section_marks:
            if s > off:
                break
            cur = t
        return cur

    chunks: list[dict] = []
    seq = 0
    pos = 0
    while pos < total:
        end_target = min(pos + MAX_LEN, total)
        if end_target == total:
            cut = total
        else:
            cut = _safe_breakpoint(content, pos + MIN_LEN, end_target)
            cut = _avoid_math_split(content, cut)
            cut = min(cut, total)

        text = content[pos:cut].strip()
        if not text:
            pos = cut
            continue

        # Approximate page within chapter via linear interpolation
        if pages_span > 0:
            page = page_start + int(pages_span * (pos / max(total, 1)))
        else:
            page = page_start

        chunks.append(
            {
                "chunk_id": f"{book_id}::ch{chapter_idx + 1:02d}::{seq:04d}",
                "book_id": book_id,
                "chapter_id": chapter_id,
                "chapter": chapter_title,
                "section": section_of(pos),
                "page": page,
                "char_start": pos,
                "char_end": cut,
                "n_chars": len(text),
                "text": text,
            }
        )
        seq += 1
        if cut >= total:
            break
        # Step with overlap
        pos = max(cut - OVERLAP, pos + 1)

    return chunks


def chunk_book(raw_path: Path, out_path: Path) -> int:
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    book_id = raw["book_id"]
    chapters = raw.get("chapters") or []
    if not chapters:
        raise ValueError(f"{raw_path} has no chapters[]; run enrich_chapters first")

    all_chunks: list[dict] = []
    for i, ch in enumerate(chapters):
        all_chunks.extend(chunk_chapter(book_id, i, ch))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(all_chunks, ensure_ascii=False, indent=2), encoding="utf-8")
    return len(all_chunks)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path", nargs="?", type=Path, help="data/raw/{book}.json")
    ap.add_argument("--all", action="store_true")
    args = ap.parse_args()

    targets = sorted(Path("data/raw").glob("*.json")) if args.all else [args.path]
    for p in targets:
        if p is None:
            continue
        out = Path("data/chunks") / p.name
        n = chunk_book(p, out)
        print(f"OK  {p.name} -> {out}  chunks={n}")


if __name__ == "__main__":
    main()
