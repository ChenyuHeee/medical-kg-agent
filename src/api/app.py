"""T-B*: FastAPI app — full backend per ARCHITECTURE §9.

Run:
    uvicorn src.api.app:app --reload --port 8000
Browser:
    http://localhost:8000/  (serves src/web/index.html)
"""

from __future__ import annotations

import json
import logging
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s — %(message)s")
log = logging.getLogger("api")

app = FastAPI(title="医学教材知识图谱整合 Agent", version="1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"],
)


# --------------- Paths & state ---------------

UPLOAD_DIR = Path("data/uploads"); UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
RAW_DIR = Path("data/raw")
MD_DIR = Path("data/md"); MD_DIR.mkdir(parents=True, exist_ok=True)
CHUNKS_DIR = Path("data/chunks")
KG_DIR = Path("data/kg")
REPORT_DIR = Path("data/report"); REPORT_DIR.mkdir(parents=True, exist_ok=True)
WEB_DIR = Path("src/web")

# Per-book status tracker
_BOOK_STATUS: dict[str, dict[str, Any]] = {}


def _scan_books() -> dict[str, dict[str, Any]]:
    """Discover books from data/raw/ and merge with in-memory status."""
    out: dict[str, dict] = {}
    for p in sorted(RAW_DIR.glob("*.json")):
        bid = p.stem
        st = {
            "book_id": bid,
            "raw_exists": True,
            "chunks_exists": (CHUNKS_DIR / f"{bid}.json").exists(),
            "kg_exists": (KG_DIR / f"{bid}.json").exists(),
            "triples_exists": (Path("data/triples") / f"{bid}.json").exists(),
        }
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            st["title"] = d.get("title", bid)
            st["total_chars"] = d.get("total_chars", 0)
            st["chapters"] = len(d.get("chapters", []))
        except Exception:
            pass
        # Merge with progress tracker
        st.update(_BOOK_STATUS.get(bid, {}))
        out[bid] = st
    # Books with progress but no raw yet
    for bid, st in _BOOK_STATUS.items():
        if bid not in out:
            out[bid] = st
    return out


# --------------- Request models ---------------

class ParseReq(BaseModel):
    book_id: str


class IndexReq(BaseModel):
    book_ids: list[str] | None = None


class QueryReq(BaseModel):
    question: str
    k: int = 5
    mode: str = "graph"   # "graph" | "vanilla"
    book_ids: list[str] | None = None


class MergeReq(BaseModel):
    book_ids: list[str] | None = None
    use_embedding: bool = True


class CompressReq(BaseModel):
    target_ratio: float = 0.30


class ChatReq(BaseModel):
    session_id: str
    message: str


class UndoReq(BaseModel):
    session_id: str
    edit_id: str


# --------------- Health & books ---------------

@app.get("/healthz")
def healthz():
    return {"ok": True, "ts": time.time()}


@app.get("/api/books")
def list_books():
    return {"books": list(_scan_books().values())}


# --------------- Upload + Parse ---------------

ALLOWED_EXT = {".pdf", ".md", ".markdown", ".txt", ".docx"}


@app.post("/api/upload")
async def upload(file: UploadFile = File(...)):
    name = file.filename or "untitled"
    ext = Path(name).suffix.lower()
    if ext not in ALLOWED_EXT:
        raise HTTPException(400, f"unsupported format: {ext}; allowed={sorted(ALLOWED_EXT)}")
    bid = Path(name).stem
    target = UPLOAD_DIR / name
    with target.open("wb") as f:
        shutil.copyfileobj(file.file, f)
    _BOOK_STATUS[bid] = {**_BOOK_STATUS.get(bid, {}), "book_id": bid, "uploaded": True, "format": ext, "size": target.stat().st_size, "stage": "uploaded"}
    return {"book_id": bid, "filename": name, "size": target.stat().st_size, "format": ext}


def _parse_pipeline(book_id: str, src_path: Path):
    """Run PDF/MD/TXT/DOCX → md → RawDoc → chunks (T-N02) → triples + KG."""
    try:
        _BOOK_STATUS[book_id]["stage"] = "parsing"
        ext = src_path.suffix.lower()
        md_path = MD_DIR / f"{book_id}.md"
        if ext == ".pdf":
            from ..ingest.pdf_parse import pdf_to_rawdoc
            raw = pdf_to_rawdoc(src_path)  # writes md too
        elif ext in (".md", ".markdown"):
            shutil.copy(src_path, md_path)
            from ..ingest.pdf_parse import md_to_rawdoc
            raw = md_to_rawdoc(md_path, book_id=book_id)
        elif ext == ".txt":
            text = src_path.read_text(encoding="utf-8", errors="ignore")
            md_path.write_text(text, encoding="utf-8")
            from ..ingest.pdf_parse import md_to_rawdoc
            raw = md_to_rawdoc(md_path, book_id=book_id)
        elif ext == ".docx":
            try:
                import docx
                doc = docx.Document(str(src_path))
                paras = [p.text for p in doc.paragraphs]
                md_path.write_text("\n\n".join(paras), encoding="utf-8")
            except Exception as ex:
                raise RuntimeError(f"docx parse failed: {ex}")
            from ..ingest.pdf_parse import md_to_rawdoc
            raw = md_to_rawdoc(md_path, book_id=book_id)
        else:
            raise RuntimeError(f"unsupported ext {ext}")

        # Save RawDoc
        raw_path = RAW_DIR / f"{book_id}.json"
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        raw_path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")

        # Enrich chapters[]
        from ..ingest.enrich_chapters import enrich_file
        enrich_file(raw_path)

        # Chunk
        _BOOK_STATUS[book_id]["stage"] = "chunking"
        from ..ingest.chunker import chunk_book
        n = chunk_book(raw_path, CHUNKS_DIR / f"{book_id}.json")
        _BOOK_STATUS[book_id]["n_chunks"] = n

        # Extract triples + build KG (best-effort; LLM may be slow)
        _BOOK_STATUS[book_id]["stage"] = "extracting"
        from ..kg.extract import extract_book
        from ..kg.build import build_from_file
        try:
            tri_path = Path("data/triples") / f"{book_id}.json"
            stats = extract_book(CHUNKS_DIR / f"{book_id}.json", tri_path)
            _BOOK_STATUS[book_id]["extract_stats"] = stats
            kg_stats = build_from_file(tri_path, KG_DIR / f"{book_id}.graphml")
            _BOOK_STATUS[book_id]["kg_stats"] = kg_stats
        except Exception as ex:
            log.warning("extract/build failed for %s: %s", book_id, ex)
            _BOOK_STATUS[book_id]["extract_error"] = str(ex)

        _BOOK_STATUS[book_id]["stage"] = "parsed"
    except Exception as ex:
        log.exception("parse pipeline failed for %s", book_id)
        _BOOK_STATUS[book_id]["stage"] = "failed"
        _BOOK_STATUS[book_id]["error"] = str(ex)


@app.post("/api/parse")
def parse_book(req: ParseReq):
    bid = req.book_id
    cands = list(UPLOAD_DIR.glob(f"{bid}.*")) + list(Path("textbooks").glob(f"{bid}.*"))
    if not cands:
        raise HTTPException(404, f"no source file for book_id={bid}")
    src = cands[0]
    _BOOK_STATUS[bid] = {**_BOOK_STATUS.get(bid, {}), "stage": "queued"}
    threading.Thread(target=_parse_pipeline, args=(bid, src), daemon=True).start()
    return {"task_id": bid, "status": "queued"}


@app.get("/api/parse/status/{book_id}")
def parse_status(book_id: str):
    return _BOOK_STATUS.get(book_id, {"book_id": book_id, "stage": "unknown"})


# --------------- RAG ---------------

@app.post("/api/rag/index")
def rag_index(req: IndexReq):
    from ..rag.store import index_book, index_all
    if not req.book_ids:
        out = index_all()
    else:
        out = []
        for bid in req.book_ids:
            p = CHUNKS_DIR / f"{bid}.json"
            if not p.exists():
                out.append({"book_id": bid, "error": "chunks missing"})
                continue
            out.append(index_book(bid, p))
    return {"indexed": out}


@app.post("/api/rag/query")
def rag_query(req: QueryReq):
    from ..rag import qa as vanilla, graph_rag
    fn = graph_rag.answer if req.mode == "graph" else vanilla.answer
    return fn(req.question, k=req.k, book_ids=req.book_ids)


@app.get("/api/rag/status")
def rag_status():
    from ..rag.store import status
    return status()


# --------------- Graph & merge & compress ---------------

@app.get("/api/graph/{book_id}")
def get_graph(book_id: str):
    p = KG_DIR / f"{book_id}.json"
    if not p.exists():
        raise HTTPException(404, "graph not built yet")
    return JSONResponse(json.loads(p.read_text(encoding="utf-8")))


@app.get("/api/graph/merged")
def get_merged():
    p = KG_DIR / "merged.json"
    if not p.exists():
        raise HTTPException(404, "merged not built; call /api/merge/run")
    return JSONResponse(json.loads(p.read_text(encoding="utf-8")))


@app.get("/api/graph/compact")
def get_compact():
    p = KG_DIR / "compact.json"
    if not p.exists():
        raise HTTPException(404, "compact not built; call /api/compress/run")
    return JSONResponse(json.loads(p.read_text(encoding="utf-8")))


@app.post("/api/merge/run")
def merge_run(req: MergeReq):
    import networkx as nx
    from ..merge.align import align_and_merge
    from ..merge.compress import _to_json

    files = []
    if req.book_ids:
        for bid in req.book_ids:
            p = KG_DIR / f"{bid}.json"
            if p.exists():
                files.append(p)
    else:
        files = [p for p in KG_DIR.glob("*.json") if p.stem not in ("merged", "compact")]

    if not files:
        raise HTTPException(404, "no per-book graphs found")

    per_book = {}
    for p in files:
        doc = json.loads(p.read_text(encoding="utf-8"))
        g = nx.MultiDiGraph()
        for n in doc["nodes"]:
            g.add_node(n["id"], **{k: v for k, v in n.items() if k != "id"})
        for e in doc["edges"]:
            g.add_edge(e["source"], e["target"], key=e.get("relation_type", "rel"),
                       **{k: v for k, v in e.items() if k not in ("source","target")})
        per_book[p.stem] = g

    merged, decisions, alias = align_and_merge(per_book, use_embedding=req.use_embedding)
    (KG_DIR / "merged.json").write_text(json.dumps(_to_json(merged), ensure_ascii=False, indent=2), encoding="utf-8")
    (REPORT_DIR / "decisions.json").write_text(json.dumps(decisions, ensure_ascii=False, indent=2), encoding="utf-8")
    (REPORT_DIR / "alias_table.json").write_text(json.dumps(alias, ensure_ascii=False, indent=2), encoding="utf-8")

    # Reset chat state since graph changed
    from ..chat.tools import reset_state
    reset_state()

    return {
        "ok": True,
        "merged_nodes": merged.number_of_nodes(),
        "merged_edges": merged.number_of_edges(),
        "decisions": len(decisions),
        "merge_decisions": sum(1 for d in decisions if d["action"] == "merge"),
        "keep_decisions": sum(1 for d in decisions if d["action"] == "keep"),
    }


@app.post("/api/compress/run")
def compress_run(req: CompressReq):
    from ..merge.compress import run as crun
    from ..chat.tools import reset_state
    stats = crun(req.target_ratio)
    reset_state()
    return stats


@app.get("/api/compress/stats")
def compress_stats():
    p = REPORT_DIR / "compression.json"
    if not p.exists():
        raise HTTPException(404, "no compression stats; call /api/compress/run")
    return JSONResponse(json.loads(p.read_text(encoding="utf-8")))


@app.get("/api/decisions")
def get_decisions():
    p = REPORT_DIR / "decisions.json"
    if not p.exists():
        return []
    return json.loads(p.read_text(encoding="utf-8"))


# --------------- Chat ---------------

@app.post("/api/chat")
def chat_endpoint(req: ChatReq):
    from ..chat.agent import chat
    return chat(req.session_id, req.message)


@app.post("/api/chat/undo")
def chat_undo(req: UndoReq):
    from ..chat.tools import undo
    return undo(req.edit_id)


@app.get("/api/chat/history/{session_id}")
def chat_history(session_id: str):
    from ..chat.agent import get_history
    return {"history": get_history(session_id)}


# --------------- Static frontend ---------------

if WEB_DIR.exists():
    app.mount("/web", StaticFiles(directory=str(WEB_DIR), html=True), name="web")


@app.get("/")
def root():
    idx = WEB_DIR / "index.html"
    if idx.exists():
        return FileResponse(str(idx))
    return JSONResponse({
        "service": "kg-agent",
        "docs": "/docs",
        "frontend": "src/web/index.html not built yet (T-F01 by CLAUDE)",
    })
