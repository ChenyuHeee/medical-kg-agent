"""End-to-end pipeline: chunks -> triples -> per-book KG -> merged -> compressed -> report.

Usage:
    # full pipeline for the two P0 books
    MODELSCOPE_API_KEY=ms-xxx python -m src.pipeline run

    # individual stages
    python -m src.pipeline extract --book 03_生理学 --limit 50
    python -m src.pipeline build   --book 03_生理学
    python -m src.pipeline merge
    python -m src.pipeline compress
    python -m src.pipeline report
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

P0_BOOKS = ["03_生理学", "07_病理生理学"]
ALL_BOOKS = [
    "01_局部解剖学", "02_组织学与胚胎学", "03_生理学",
    "04_医学微生物学", "05_病理学", "06_传染病学", "07_病理生理学",
]


def _setup_logging():
    logging.basicConfig(
        format="[%(asctime)s] %(levelname)s %(name)s: %(message)s",
        level=logging.INFO,
        datefmt="%H:%M:%S",
    )


def stage_extract(book: str, limit: int | None = None) -> None:
    from .kg.extract import extract_book

    chunks = DATA / "chunks" / f"{book}.json"
    out = DATA / "triples" / f"{book}.json"
    if not chunks.exists():
        sys.exit(f"chunks not found: {chunks}")
    stats = extract_book(chunks, out, limit=limit)
    print(json.dumps({"stage": "extract", "book": book, **stats}, ensure_ascii=False))


def stage_build(book: str) -> None:
    from .kg.build import build_from_file

    triples = DATA / "triples" / f"{book}.json"
    out = DATA / "kg" / f"{book}.graphml"
    if not triples.exists():
        sys.exit(f"triples not found: {triples}")
    stats = build_from_file(triples, out)
    print(json.dumps({"stage": "build", "book": book, **stats}, ensure_ascii=False))


def stage_merge(books: list[str]) -> None:
    from .merge.align import merge_from_files
    from .merge.compress import _to_json  # type: ignore

    paths = [DATA / "kg" / f"{b}.json" for b in books]
    missing = [p for p in paths if not p.exists()]
    if missing:
        sys.exit(f"missing graph json: {missing}")
    merged, decisions, alias = merge_from_files(paths)
    out = DATA / "kg" / "merged.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(_to_json(merged), ensure_ascii=False, indent=2), encoding="utf-8")
    rep = DATA / "report"; rep.mkdir(parents=True, exist_ok=True)
    (rep / "decisions.json").write_text(json.dumps(decisions, ensure_ascii=False, indent=2), encoding="utf-8")
    (rep / "alias_table.json").write_text(json.dumps(alias, ensure_ascii=False, indent=2), encoding="utf-8")
    stats = {"nodes": merged.number_of_nodes(), "edges": merged.number_of_edges(),
             "decisions": len(decisions), "aliases": len(alias)}
    print(json.dumps({"stage": "merge", **stats}, ensure_ascii=False))


def stage_compress(books: list[str], target: float = 0.30) -> None:
    from .merge.compress import run as compress_run

    merged = DATA / "kg" / "merged.json"
    if not merged.exists():
        sys.exit("merged.json missing; run merge first")
    stats = compress_run(target_ratio=target)
    print(json.dumps({"stage": "compress", **stats}, ensure_ascii=False))


def stage_report(books: list[str]) -> None:
    from .merge.report import build_report_files

    book_paths = {b: DATA / "kg" / f"{b}.json" for b in books}
    merged = DATA / "kg" / "merged.json"
    compressed = DATA / "report" / "compression.json"
    out = DATA / "report" / "summary.md"
    build_report_files(
        book_paths,
        merged,
        compressed if compressed.exists() else None,
        out,
    )
    print(json.dumps({"stage": "report", "out": str(out)}, ensure_ascii=False))


def stage_run(books: list[str], limit: int | None = None, target: float = 0.30) -> None:
    for b in books:
        stage_extract(b, limit=limit)
        stage_build(b)
    stage_merge(books)
    stage_compress(books, target=target)
    stage_report(books)


def main(argv: list[str] | None = None) -> int:
    _setup_logging()
    p = argparse.ArgumentParser("pipeline")
    sub = p.add_subparsers(dest="cmd", required=True)

    for cmd in ("extract", "build"):
        sp = sub.add_parser(cmd)
        sp.add_argument("--book", required=True)
        if cmd == "extract":
            sp.add_argument("--limit", type=int, default=None)

    sp = sub.add_parser("merge")
    sp.add_argument("--books", nargs="+", default=ALL_BOOKS)

    sp = sub.add_parser("compress")
    sp.add_argument("--books", nargs="+", default=ALL_BOOKS)
    sp.add_argument("--target", type=float, default=0.30)

    sp = sub.add_parser("report")
    sp.add_argument("--books", nargs="+", default=ALL_BOOKS)

    sp = sub.add_parser("run")
    sp.add_argument("--books", nargs="+", default=P0_BOOKS)
    sp.add_argument("--limit", type=int, default=None)
    sp.add_argument("--target", type=float, default=0.30)

    args = p.parse_args(argv)

    if args.cmd == "extract":
        stage_extract(args.book, limit=args.limit)
    elif args.cmd == "build":
        stage_build(args.book)
    elif args.cmd == "merge":
        stage_merge(args.books)
    elif args.cmd == "compress":
        stage_compress(args.books, target=args.target)
    elif args.cmd == "report":
        stage_report(args.books)
    elif args.cmd == "run":
        stage_run(args.books, limit=args.limit, target=args.target)
    return 0


if __name__ == "__main__":
    sys.exit(main())
