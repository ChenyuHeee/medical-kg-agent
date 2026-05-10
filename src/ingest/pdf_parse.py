"""T-01 / T-01.1: PDF -> RawDoc JSON via PDF2MD pipeline.

Reads a PDF (auto-converts to MD via PDF2MD) or an existing MD file,
builds RawDoc per ARCHITECTURE.md §2.1.  Page text preserves markdown markup
so downstream chunker can leverage `#`/`##` structure.

Usage:
    python src/ingest/pdf_parse.py textbooks/03_生理学.pdf -o data/raw/03_生理学.json
    python src/ingest/pdf_parse.py data/md/03_生理学.md --from-md -o data/raw/03_生理学.json
"""

import argparse
import json
import re
import sys
from pathlib import Path

PAGE_CHARS = 3000  # synthetic page size when md has no form feeds


def extract_toc_from_md(md_text: str) -> list[dict]:
    """Extract heading structure from markdown.

    Returns list of {level, title, page_no} where page_no is the
    synthetic page index (1-based) based on char offset / PAGE_CHARS.
    """
    entries = []
    heading_re = re.compile(r"^(#{1,3})\s+(.+)$", re.MULTILINE)

    for m in heading_re.finditer(md_text):
        level = len(m.group(1))
        title = m.group(2).strip()
        # Skip noise headings
        if not title:
            continue
        if title in ("版权所有，侵权必究！",):
            continue
        if re.match(r"^第\d+版$", title):  # "第10版"
            continue
        pos = m.start()
        page_no = pos // PAGE_CHARS + 1
        entries.append({"level": level, "title": title, "page_no": page_no})

    return entries


def slice_md_to_pages(md_text: str) -> list[dict]:
    """Slice markdown text into synthetic pages by char count.

    Tries to break at paragraph boundaries (double newline) near PAGE_CHARS.
    """
    pages = []
    pos = 0
    page_no = 1
    remaining = md_text

    while remaining:
        if len(remaining) <= PAGE_CHARS:
            pages.append({"page_no": page_no, "text": remaining.strip(), "bbox_blocks": []})
            break

        # Find a good break point near PAGE_CHARS
        chunk = remaining[:PAGE_CHARS]
        # Prefer breaking at double newline
        brk = chunk.rfind("\n\n")
        if brk < PAGE_CHARS // 3:  # too early, try single newline
            brk = chunk.rfind("\n")
        if brk < PAGE_CHARS // 3:
            # Try to break at sentence end
            for c in "。！？":
                pos_c = chunk.rfind(c)
                if pos_c > brk:
                    brk = pos_c + 1
        if brk < PAGE_CHARS // 3:
            brk = PAGE_CHARS  # hard cut

        pages.append({"page_no": page_no, "text": remaining[:brk].strip(), "bbox_blocks": []})
        remaining = remaining[brk:].lstrip()
        page_no += 1

    return pages


def md_to_rawdoc(md_path: Path, book_id: str | None = None) -> dict:
    """Convert a markdown file to RawDoc dict."""
    md_text = md_path.read_text(encoding="utf-8")

    bid = book_id or md_path.stem
    title = bid.split("_", 1)[1] if "_" in bid else bid

    toc = extract_toc_from_md(md_text)
    pages = slice_md_to_pages(md_text)
    n_chars = sum(len(p["text"]) for p in pages)

    return {
        "book_id": bid,
        "title": title,
        "pages": pages,
        "toc": toc,
        "n_chars": n_chars,
    }


def pdf_to_rawdoc(pdf_path: Path) -> dict:
    """Convert PDF via PDF2MD, then build RawDoc."""
    from pdf2md import ConvertOptions, convert

    md_dir = Path("data/md")
    md_dir.mkdir(parents=True, exist_ok=True)
    md_path = md_dir / f"{pdf_path.stem}.md"

    opts = ConvertOptions(extract_images=False, extract_tables=True, table_format="gfm")
    convert(str(pdf_path), str(md_path), options=opts)

    return md_to_rawdoc(md_path, book_id=pdf_path.stem)


def main():
    parser = argparse.ArgumentParser(
        description="Convert textbook PDF (or MD) to RawDoc JSON"
    )
    parser.add_argument("input", type=Path, help="Path to PDF or MD file")
    parser.add_argument("--from-md", action="store_true", help="Input is already MD")
    parser.add_argument("-o", "--output", type=Path, help="Output JSON path")
    args = parser.parse_args()

    if not args.input.exists():
        print(f"ERROR: {args.input} not found", file=sys.stderr)
        sys.exit(1)

    if args.from_md:
        result = md_to_rawdoc(args.input)
    else:
        result = pdf_to_rawdoc(args.input)

    out = args.output or Path(f"data/raw/{args.input.stem}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"OK  {args.input.name} -> {out}")
    print(
        f"    pages={len(result['pages'])}  chars={result['n_chars']}  toc_entries={len(result['toc'])}"
    )


if __name__ == "__main__":
    main()
