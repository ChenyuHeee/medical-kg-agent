"""Patch chunks (and Chroma metadata) so `page` reflects the real PDF page index.

Old chunks store `page` as a synthetic 3000-char slice index (see
src/ingest/pdf_parse.py PAGE_CHARS). This makes it impossible to jump back
to the source PDF. Rebuild the mapping by:

1. For each book, read textbooks/<book_id>.pdf and extract per-page text
   with PyMuPDF (fitz).
2. For each chunk: take a fingerprint (its first ~80 chars, stripped) and
   locate the PDF page whose text contains it. Fall back to longest
   common substring scan if no exact hit.
3. Overwrite chunk["page"] in data/chunks/<book_id>.json.
4. Update the corresponding Chroma metadata (`book_<id>` and `all`).

Idempotent: re-running on already-patched chunks just re-confirms.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

# Make project root importable so `src.rag.store` works regardless of CWD.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import fitz  # PyMuPDF

CHUNKS_DIR = Path("data/chunks")
PDF_DIR = Path("textbooks")


def _norm(s: str) -> str:
    s = re.sub(r"\s+", "", s)
    return s


def _page_texts(pdf_path: Path) -> list[str]:
    doc = fitz.open(pdf_path)
    out = [_norm(p.get_text()) for p in doc]
    doc.close()
    return out


def _find_page(fingerprint: str, pages_norm: list[str], hint: int = 0) -> int:
    """Return 1-based PDF page containing fingerprint, else -1.

    Search forward from hint first (chunks are sequential), then full sweep.
    """
    if not fingerprint:
        return -1
    n = len(pages_norm)
    order = list(range(max(0, hint - 2), n)) + list(range(0, max(0, hint - 2)))
    for i in order:
        if fingerprint in pages_norm[i]:
            return i + 1
    # Looser: shorter prefix
    short = fingerprint[:30]
    if len(short) >= 12:
        for i in order:
            if short in pages_norm[i]:
                return i + 1
    return -1


def patch_book(book_id: str) -> dict:
    chunks_path = CHUNKS_DIR / f"{book_id}.json"
    pdf_path = PDF_DIR / f"{book_id}.pdf"
    if not chunks_path.exists():
        return {"book_id": book_id, "skipped": "no_chunks"}
    if not pdf_path.exists():
        return {"book_id": book_id, "skipped": "no_pdf"}

    chunks = json.loads(chunks_path.read_text(encoding="utf-8"))
    pages_norm = _page_texts(pdf_path)

    hint = 0
    hit = miss = 0
    for c in chunks:
        fp = _norm(c.get("text", ""))[:80]
        pg = _find_page(fp, pages_norm, hint=hint)
        if pg > 0:
            c["page"] = pg
            hint = pg
            hit += 1
        else:
            # keep old, mark
            miss += 1

    chunks_path.write_text(
        json.dumps(chunks, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    return {"book_id": book_id, "chunks": len(chunks), "hit": hit, "miss": miss,
            "pdf_pages": len(pages_norm)}


def reindex_chroma(book_ids: list[str]) -> None:
    """Update `page` metadata in Chroma without re-embedding."""
    try:
        from src.rag.store import _client, _coll, _safe_name
    except Exception as e:
        print("WARN cannot import store:", e)
        return
    cli = _client()
    coll_all = _coll(cli, "all")
    for bid in book_ids:
        chunks = json.loads((CHUNKS_DIR / f"{bid}.json").read_text(encoding="utf-8"))
        ids = [c["chunk_id"] for c in chunks]
        metas = [
            {
                "book_id": c["book_id"],
                "chapter": (c.get("chapter", "") or "")[:200],
                "section": (c.get("section", "") or "")[:200],
                "page": int(c.get("page", -1)),
                "chunk_id": c["chunk_id"],
            }
            for c in chunks
        ]
        # update in batches
        bs = 200
        for i in range(0, len(ids), bs):
            sl = slice(i, i + bs)
            try:
                coll_all.update(ids=ids[sl], metadatas=metas[sl])
            except Exception as e:
                print(f"WARN all coll update {bid} batch {i}: {e}")
            sbn = _safe_name(f"book_{bid}")
            try:
                cb = _coll(cli, sbn)
                cb.update(ids=ids[sl], metadatas=metas[sl])
            except Exception as e:
                print(f"WARN book coll update {bid} batch {i}: {e}")
        print(f"  reindexed metadata for {bid}: {len(ids)} chunks")


def main():
    books = sys.argv[1:] or [p.stem for p in sorted(CHUNKS_DIR.glob("*.json"))]
    summary = []
    for b in books:
        r = patch_book(b)
        print(r)
        if "hit" in r:
            summary.append(b)
    if summary:
        print("Reindexing Chroma metadata for:", summary)
        reindex_chroma(summary)
    print("DONE")


if __name__ == "__main__":
    main()
