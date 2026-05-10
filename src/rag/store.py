"""T-R02: ChromaDB-backed vector store for chunks.

- One persistent client at ``data/vector/``
- Per-book collections + a unified ``all`` collection
- Metadata kept lean (book_id/chapter/section/page/chunk_id) for filtering
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Iterable

import numpy as np

from .embed import embed, embed_query

log = logging.getLogger(__name__)

VECTOR_DIR = Path("data/vector")


def _safe_name(name: str) -> str:
    """Sanitize a name for ChromaDB (only [a-zA-Z0-9._-] allowed)."""
    import hashlib
    safe = "".join(c if c.isascii() and c.isalnum() or c in "._-" else "_" for c in name)
    # If the name was heavily modified, append a short hash to keep uniqueness
    if safe != name:
        h = hashlib.md5(name.encode()).hexdigest()[:8]
        safe = safe.strip("_") + "_" + h
    return safe


def _client():
    import chromadb
    from chromadb.config import Settings
    return chromadb.PersistentClient(
        path=str(VECTOR_DIR),
        settings=Settings(anonymized_telemetry=False, allow_reset=True),
    )


def _coll(client, name: str):
    return client.get_or_create_collection(name=name, metadata={"hnsw:space": "cosine"})


def index_book(book_id: str, chunks_path: Path, batch: int = 64) -> dict[str, Any]:
    """Embed and upsert all chunks of one book."""
    chunks = json.loads(chunks_path.read_text(encoding="utf-8"))
    if not chunks:
        return {"book_id": book_id, "n_chunks": 0}

    client = _client()
    sbn = _safe_name(f"book_{book_id}")
    coll_book = _coll(client, sbn)
    coll_all = _coll(client, "all")

    # Reset book collection to keep idempotent
    try:
        client.delete_collection(sbn)
    except Exception:
        pass
    coll_book = _coll(client, sbn)

    ids = [c["chunk_id"] for c in chunks]
    docs = [c["text"] for c in chunks]
    metas = [
        {
            "book_id": c["book_id"],
            "chapter": c.get("chapter", "")[:200],
            "section": c.get("section", "")[:200],
            "page": int(c.get("page", -1)),
            "chunk_id": c["chunk_id"],
        }
        for c in chunks
    ]

    # Drop previous all-collection records for this book (id-prefix match)
    try:
        coll_all.delete(where={"book_id": book_id})
    except Exception:
        pass

    n = len(ids)
    for i in range(0, n, batch):
        sl = slice(i, i + batch)
        vecs = embed(docs[sl])
        coll_book.upsert(ids=ids[sl], documents=docs[sl], metadatas=metas[sl], embeddings=vecs.tolist())
        coll_all.upsert(ids=ids[sl], documents=docs[sl], metadatas=metas[sl], embeddings=vecs.tolist())
    return {"book_id": book_id, "n_chunks": n}


def index_all(chunks_dir: Path = Path("data/chunks")) -> list[dict[str, Any]]:
    out = []
    for p in sorted(chunks_dir.glob("*.json")):
        out.append(index_book(p.stem, p))
        log.info("indexed %s", p.stem)
    return out


def query(question: str, k: int = 5, book_ids: list[str] | None = None) -> list[dict[str, Any]]:
    """Return top-k chunks with score in [0,1] (1=identical)."""
    client = _client()
    coll = _coll(client, "all")
    qvec = embed_query(question)
    where = {"book_id": {"$in": book_ids}} if book_ids else None
    res = coll.query(
        query_embeddings=[qvec.tolist()],
        n_results=k,
        where=where,
    )
    out = []
    for i in range(len(res["ids"][0])):
        dist = res["distances"][0][i]  # cosine distance in [0,2]
        score = max(0.0, 1.0 - dist / 2.0) if dist is not None else 0.0
        out.append({
            "chunk_id": res["ids"][0][i],
            "text": res["documents"][0][i],
            "metadata": res["metadatas"][0][i],
            "score": round(float(score), 4),
        })
    return out


def status() -> dict[str, Any]:
    try:
        client = _client()
        cols = client.list_collections()
        names = [c.name for c in cols]
        all_coll = _coll(client, "all")
        n_total = all_coll.count()
        n_books = sum(1 for n in names if n.startswith("book_"))
        return {"ready": n_total > 0, "n_books": n_books, "n_chunks": n_total, "collections": names}
    except Exception as e:
        return {"ready": False, "error": str(e)}


__all__ = ["index_book", "index_all", "query", "status"]


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--index", action="store_true")
    ap.add_argument("--query", type=str)
    ap.add_argument("-k", type=int, default=5)
    args = ap.parse_args()
    if args.index:
        out = index_all()
        print(json.dumps(out, ensure_ascii=False, indent=2))
    if args.query:
        res = query(args.query, k=args.k)
        for r in res:
            print(f"[{r['score']:.3f}] {r['metadata']['book_id']} / {r['metadata']['chapter']}  p{r['metadata']['page']}")
            print(r["text"][:200].replace("\n", " "))
            print("---")
    if not args.index and not args.query:
        print(json.dumps(status(), ensure_ascii=False, indent=2))
