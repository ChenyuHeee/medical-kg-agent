"""T-01.1: PDF -> Markdown via PDF2MD.

Usage:
    python src/ingest/pdf_to_md.py textbooks/03_生理学.pdf -o data/md/03_生理学.md
"""

import argparse
import sys
from pathlib import Path

from pdf2md import ConvertOptions, convert


def main():
    parser = argparse.ArgumentParser(description="Convert PDF to Markdown via PDF2MD")
    parser.add_argument("pdf", type=Path, help="Path to PDF file")
    parser.add_argument("-o", "--output", type=Path, help="Output .md path")
    args = parser.parse_args()

    if not args.pdf.exists():
        print(f"ERROR: {args.pdf} not found", file=sys.stderr)
        sys.exit(1)

    out = args.output or Path(f"data/md/{args.pdf.stem}.md")
    out.parent.mkdir(parents=True, exist_ok=True)

    opts = ConvertOptions(
        extract_images=False,
        extract_tables=True,
        table_format="gfm",
    )

    result = convert(str(args.pdf), str(out), options=opts)
    size = result.stat().st_size if result.exists() else 0
    print(f"OK  {args.pdf.name} -> {out}  ({size:,} bytes)")


if __name__ == "__main__":
    main()
