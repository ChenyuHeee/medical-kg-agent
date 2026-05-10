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
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s — %(message)s")
log = logging.getLogger("api")

app = FastAPI(title="医学教材知识图谱整合 Agent", version="1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"],
)


# --------------- Security: write-token + rate limit ---------------
# When the API is exposed publicly (e.g. localtunnel), restrict mutations.
# - All non-GET/HEAD/OPTIONS requests require header `X-Demo-Token` matching $DEMO_WRITE_TOKEN.
# - A small allowlist of POSTs (read-like RPC: chat, rag query) is open to visitors.
# - Per-IP token bucket on the open POSTs to prevent abuse.
import os as _os
import collections as _coll
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request as _Req
from starlette.responses import Response as _Resp, JSONResponse as _JR

_WRITE_TOKEN = _os.environ.get("DEMO_WRITE_TOKEN", "").strip()
# POSTs that visitors may call without a token (read-like)
_OPEN_POSTS = {"/api/chat", "/api/chat/undo", "/api/rag/query"}
# Per-IP rate limit: max N requests per WINDOW seconds for open POSTs
_RL_MAX = int(_os.environ.get("DEMO_RL_MAX", "30"))
_RL_WINDOW = int(_os.environ.get("DEMO_RL_WINDOW", "60"))
_rl_hits: dict[str, _coll.deque] = {}
_rl_lock = threading.Lock()

# Cap upload size (bytes). Default 50 MB.
MAX_UPLOAD_BYTES = int(_os.environ.get("DEMO_MAX_UPLOAD", str(50 * 1024 * 1024)))


def _client_ip(req: _Req) -> str:
    xff = req.headers.get("x-forwarded-for", "").split(",")[0].strip()
    return xff or (req.client.host if req.client else "?")


def _rate_check(ip: str) -> bool:
    now = time.time()
    with _rl_lock:
        dq = _rl_hits.setdefault(ip, _coll.deque())
        while dq and now - dq[0] > _RL_WINDOW:
            dq.popleft()
        if len(dq) >= _RL_MAX:
            return False
        dq.append(now)
        return True


class GuardMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: _Req, call_next):
        m = request.method.upper()
        path = request.url.path
        # Always allow safe methods and static / SPA assets
        if m in ("GET", "HEAD", "OPTIONS"):
            return await call_next(request)
        ip = _client_ip(request)
        is_local = ip in ("127.0.0.1", "::1", "localhost")
        # Open POSTs: rate-limit only
        if path in _OPEN_POSTS and not is_local:
            if not _rate_check(ip):
                return _JR({"error": "rate_limited", "retry_after": _RL_WINDOW}, status_code=429)
        else:
            # Require token for all other mutating endpoints
            if not is_local:
                tok = request.headers.get("x-demo-token", "")
                if not _WRITE_TOKEN or tok != _WRITE_TOKEN:
                    return _JR({"error": "forbidden", "detail": "write operations disabled in public demo"}, status_code=403)
        return await call_next(request)


app.add_middleware(GuardMiddleware)


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
            st["total_chars"] = d.get("total_chars") or d.get("n_chars") or 0
            chapters = d.get("chapters") or d.get("toc") or []
            st["chapters"] = len(chapters) if isinstance(chapters, list) else 0
        except Exception:
            pass
        # Fallback: derive size from chunks file if raw didn't expose it
        if not st.get("total_chars") and st.get("chunks_exists"):
            try:
                cs = json.loads((CHUNKS_DIR / f"{bid}.json").read_text(encoding="utf-8"))
                st["total_chars"] = sum(int(c.get("n_chars") or len(c.get("text", ""))) for c in cs)
                if not st.get("chapters"):
                    st["chapters"] = len({c.get("chapter") for c in cs if c.get("chapter")})
            except Exception:
                pass
        # Derive pipeline stage
        if st.get("kg_exists"):
            st["stage"] = "kg_built"
        elif st.get("triples_exists"):
            st["stage"] = "extracted"
        elif st.get("chunks_exists"):
            st["stage"] = "chunked"
        elif st.get("raw_exists"):
            st["stage"] = "parsed"
        else:
            st["stage"] = "unknown"
        # Merge with progress tracker (may override stage if a job is running)
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


# --------------- PDF source serving (read-only, for citation jump) ---------------

PDF_DIR = Path("textbooks")


@app.get("/api/pdf/{book_id}")
def serve_pdf(book_id: str):
    # Prevent path traversal: only accept the bare stem matching a real file.
    safe = book_id.replace("/", "").replace("\\", "").replace("..", "")
    p = PDF_DIR / f"{safe}.pdf"
    if not p.exists() or not p.is_file():
        raise HTTPException(404, "pdf not found")
    return FileResponse(
        str(p),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="{safe}.pdf"',
            # Allow browser PDF viewer to honor #page= fragments
            "Cache-Control": "public, max-age=3600",
        },
    )


# --------------- Workspaces (book groupings) ---------------

WORKSPACES_FILE = Path("data/workspaces.json")


def _load_workspaces() -> dict:
    if WORKSPACES_FILE.exists():
        try:
            return json.loads(WORKSPACES_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    # default: one workspace containing all books
    all_bids = sorted(_scan_books().keys())
    doc = {
        "active_id": "default",
        "workspaces": [
            {"id": "default", "name": "全部教材", "book_ids": all_bids,
             "color": "#6366f1", "created_at": time.time()}
        ],
    }
    WORKSPACES_FILE.parent.mkdir(parents=True, exist_ok=True)
    WORKSPACES_FILE.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    return doc


def _save_workspaces(doc: dict) -> None:
    WORKSPACES_FILE.parent.mkdir(parents=True, exist_ok=True)
    WORKSPACES_FILE.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")


class WorkspaceReq(BaseModel):
    name: str
    book_ids: list[str] = []
    color: str | None = None


@app.get("/api/workspaces")
def workspaces_list():
    return _load_workspaces()


@app.post("/api/workspaces")
def workspaces_create(req: WorkspaceReq):
    doc = _load_workspaces()
    wid = f"ws-{int(time.time()*1000)}-{uuid.uuid4().hex[:4]}"
    ws = {"id": wid, "name": req.name.strip() or "未命名", "book_ids": list(req.book_ids),
          "color": req.color or "#6366f1", "created_at": time.time()}
    doc["workspaces"].append(ws)
    _save_workspaces(doc)
    return ws


@app.put("/api/workspaces/{ws_id}")
def workspaces_update(ws_id: str, req: WorkspaceReq):
    doc = _load_workspaces()
    for w in doc["workspaces"]:
        if w["id"] == ws_id:
            w["name"] = req.name.strip() or w["name"]
            w["book_ids"] = list(req.book_ids)
            if req.color: w["color"] = req.color
            _save_workspaces(doc)
            return w
    raise HTTPException(404, "workspace not found")


@app.delete("/api/workspaces/{ws_id}")
def workspaces_delete(ws_id: str):
    doc = _load_workspaces()
    if ws_id == "default":
        raise HTTPException(400, "cannot delete default workspace")
    before = len(doc["workspaces"])
    doc["workspaces"] = [w for w in doc["workspaces"] if w["id"] != ws_id]
    if len(doc["workspaces"]) == before:
        raise HTTPException(404, "workspace not found")
    if doc.get("active_id") == ws_id:
        doc["active_id"] = "default"
    _save_workspaces(doc)
    return {"ok": True, "active_id": doc["active_id"]}


@app.post("/api/workspaces/{ws_id}/activate")
def workspaces_activate(ws_id: str):
    doc = _load_workspaces()
    if not any(w["id"] == ws_id for w in doc["workspaces"]):
        raise HTTPException(404, "workspace not found")
    doc["active_id"] = ws_id
    _save_workspaces(doc)
    return {"ok": True, "active_id": ws_id}


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
    written = 0
    with target.open("wb") as f:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            written += len(chunk)
            if written > MAX_UPLOAD_BYTES:
                f.close()
                try: target.unlink()
                except Exception: pass
                raise HTTPException(413, f"file too large; max {MAX_UPLOAD_BYTES} bytes")
            f.write(chunk)
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


# --------------- Source / PDF preview & node citations ---------------

TEXTBOOK_DIR = Path("textbooks")


def _find_book_pdf(book_id: str) -> Path | None:
    """Try textbooks/ first, then data/uploads/. Returns first matching PDF."""
    for d in (TEXTBOOK_DIR, UPLOAD_DIR):
        p = d / f"{book_id}.pdf"
        if p.exists():
            return p
        # Allow stem prefix match (e.g. user-uploaded files with same stem)
        for cand in d.glob(f"{book_id}*.pdf"):
            return cand
    return None


@app.get("/api/source/{book_id}/pdf")
def get_book_pdf(book_id: str):
    """Stream the raw PDF — browsers can render with #page=N."""
    p = _find_book_pdf(book_id)
    if not p:
        raise HTTPException(404, f"no pdf for book_id={book_id}")
    return FileResponse(str(p), media_type="application/pdf")


@app.get("/api/source/{book_id}/chunks")
def get_book_chunks(book_id: str, q: str = "", limit: int = 5):
    """Return chunks of a book containing query string `q` (node name).

    Returns top-N by occurrence count, each with page, chunk_id, snippet (200 chars).
    If `q` is empty, returns first `limit` chunks.
    """
    p = CHUNKS_DIR / f"{book_id}.json"
    if not p.exists():
        raise HTTPException(404, f"no chunks for book_id={book_id}")
    chunks = json.loads(p.read_text(encoding="utf-8"))
    q = (q or "").strip()
    hits = []
    for c in chunks:
        text = c.get("text", "") or ""
        cnt = text.count(q) if q else 0
        if q and cnt == 0:
            continue
        # Build snippet centered on first match
        if q:
            i = text.find(q)
            start = max(0, i - 80)
            end = min(len(text), i + len(q) + 120)
            snippet = ("..." if start > 0 else "") + text[start:end] + ("..." if end < len(text) else "")
        else:
            snippet = text[:200] + ("..." if len(text) > 200 else "")
        hits.append({
            "chunk_id": c.get("chunk_id"),
            "book_id": c.get("book_id", book_id),
            "chapter": c.get("chapter") or c.get("chapter_id") or "",
            "page": c.get("page", 1),
            "n_hits": cnt,
            "snippet": snippet,
        })
    if q:
        hits.sort(key=lambda x: -x["n_hits"])
    return {"book_id": book_id, "query": q, "total": len(hits), "hits": hits[:limit]}


@app.get("/api/source/node/{node_id}")
def get_node_sources(node_id: str, limit: int = 5):
    """For a merged-graph node, find chunks across its source books containing its name.

    Reads merged.json to get node name + book_ids, then searches each book's chunks.
    """
    mp = KG_DIR / "merged.json"
    src_doc = json.loads(mp.read_text(encoding="utf-8")) if mp.exists() else None
    node = None
    if src_doc:
        for n in src_doc["nodes"]:
            if n["id"] == node_id:
                node = n; break
    if not node:
        # Try per-book graphs
        for kp in KG_DIR.glob("*.json"):
            if kp.stem in ("merged", "compact"):
                continue
            try:
                d = json.loads(kp.read_text(encoding="utf-8"))
                for n in d["nodes"]:
                    if n["id"] == node_id:
                        node = {**n, "book_ids": [kp.stem]}; break
                if node:
                    break
            except Exception:
                continue
    if not node:
        raise HTTPException(404, f"node not found: {node_id}")
    name = node.get("canonical_name") or node.get("name") or node.get("id")
    book_ids = node.get("book_ids") or []
    if not book_ids:
        # Fallback: scan all books
        book_ids = [p.stem for p in CHUNKS_DIR.glob("*.json")]
    all_hits = []
    per_book = max(2, limit // max(1, len(book_ids)) + 1)
    for bid in book_ids:
        try:
            r = get_book_chunks(bid, q=name, limit=per_book)
            for h in r.get("hits", []):
                all_hits.append(h)
        except HTTPException:
            continue
    all_hits.sort(key=lambda x: -x.get("n_hits", 0))
    return {"node_id": node_id, "name": name, "book_ids": book_ids,
            "total": len(all_hits), "hits": all_hits[:limit]}


# --------------- Jobs: pipeline runner with live log tail ---------------

import subprocess
import signal as _signal

LOGS_DIR = Path("data/logs/jobs"); LOGS_DIR.mkdir(parents=True, exist_ok=True)
_JOBS: dict[str, dict[str, Any]] = {}  # job_id -> {pid, status, log_path, cmd, book_id, started_at}


class JobReq(BaseModel):
    book_id: str | None = None         # if given, runs for one book; else for all
    books: list[str] | None = None     # explicit book list for merge/compress/run (e.g. workspace)
    stage: str = "all"                 # "all" | "extract" | "build" | "merge" | "compress"


def _spawn_job(cmd: list[str], book_id: str | None, stage: str) -> str:
    job_id = f"{stage}-{int(time.time()*1000)}-{uuid.uuid4().hex[:6]}"
    log_path = LOGS_DIR / f"{job_id}.log"
    f = log_path.open("ab", buffering=0)
    f.write(f"$ {' '.join(cmd)}\n".encode())
    proc = subprocess.Popen(cmd, stdout=f, stderr=subprocess.STDOUT, cwd=str(Path.cwd()))
    _JOBS[job_id] = {
        "job_id": job_id, "pid": proc.pid, "status": "running",
        "log_path": str(log_path), "cmd": cmd,
        "book_id": book_id, "stage": stage, "started_at": time.time(),
    }

    def _waiter():
        rc = proc.wait()
        _JOBS[job_id]["status"] = "done" if rc == 0 else (
            "cancelled" if _JOBS[job_id].get("cancel_requested") else "failed"
        )
        _JOBS[job_id]["return_code"] = rc
        _JOBS[job_id]["finished_at"] = time.time()
        try: f.close()
        except Exception: pass
    threading.Thread(target=_waiter, daemon=True).start()
    return job_id


@app.post("/api/jobs/run")
def jobs_run(req: JobReq):
    """Start a pipeline job. Returns job_id immediately."""
    py = "python"
    bid = req.book_id
    if req.stage == "extract":
        if not bid:
            raise HTTPException(400, "extract requires book_id")
        cp = CHUNKS_DIR / f"{bid}.json"
        if not cp.exists():
            raise HTTPException(404, f"no chunks for {bid}; parse first")
        cmd = [py, "-u", "-m", "src.kg.extract", str(cp), "--workers", "6"]
    elif req.stage == "build":
        if not bid:
            raise HTTPException(400, "build requires book_id")
        cmd = [py, "-u", "-m", "src.pipeline", "build", "--book", bid]
    elif req.stage == "merge":
        cmd = [py, "-u", "-m", "src.pipeline", "merge"]
        if req.books: cmd += ["--books", *req.books]
    elif req.stage == "compress":
        cmd = [py, "-u", "-m", "src.pipeline", "compress"]
        if req.books: cmd += ["--books", *req.books]
    elif req.stage == "all":
        # Full per-book pipeline: extract+build (merge/compress are global)
        if bid:
            cmd = [py, "-u", "-m", "src.pipeline", "run", "--books", bid]
        elif req.books:
            cmd = [py, "-u", "-m", "src.pipeline", "run", "--books", *req.books]
        else:
            raise HTTPException(400, "all-stage requires book_id or books")
    else:
        raise HTTPException(400, f"unknown stage: {req.stage}")
    job_id = _spawn_job(cmd, bid, req.stage)
    return {"job_id": job_id, "status": "running", "log_url": f"/api/jobs/{job_id}/log"}


@app.get("/api/jobs")
def jobs_list():
    return {"jobs": list(_JOBS.values())}


@app.get("/api/jobs/{job_id}")
def job_get(job_id: str):
    j = _JOBS.get(job_id)
    if not j: raise HTTPException(404, "no such job")
    return j


@app.get("/api/jobs/{job_id}/log", response_class=PlainTextResponse)
def job_log(job_id: str, since: int = 0, tail: int = 0):
    """Return log content (since=byte-offset for incremental polling, or tail=N lines)."""
    j = _JOBS.get(job_id)
    if not j: raise HTTPException(404, "no such job")
    p = Path(j["log_path"])
    if not p.exists():
        return ""
    if tail > 0:
        try:
            lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()
            return "\n".join(lines[-tail:])
        except Exception:
            return ""
    size = p.stat().st_size
    if since >= size:
        return ""
    with p.open("rb") as f:
        f.seek(since)
        data = f.read()
    return data.decode("utf-8", errors="ignore")


@app.post("/api/jobs/{job_id}/cancel")
def job_cancel(job_id: str):
    j = _JOBS.get(job_id)
    if not j: raise HTTPException(404, "no such job")
    if j["status"] != "running":
        return {"ok": False, "status": j["status"], "msg": "not running"}
    j["cancel_requested"] = True
    try:
        import os as _os
        _os.kill(j["pid"], _signal.SIGTERM)
    except Exception as ex:
        return {"ok": False, "msg": str(ex)}
    return {"ok": True, "status": "cancelling"}


# --------------- Textbook ---------------

@app.get("/api/textbook")
def get_textbook():
    p = REPORT_DIR / "consolidated_textbook.md"
    if not p.exists():
        from ..merge.generate_textbook import load_merged, build_textbook
        merged = load_merged()
        text = build_textbook(merged)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return {"content": p.read_text(encoding="utf-8"), "path": str(p)}


# --------------- Static frontend ---------------

if WEB_DIR.exists():
    app.mount("/web", StaticFiles(directory=str(WEB_DIR), html=True), name="web")


@app.get("/textbook")
def textbook_page():
    p = WEB_DIR / "textbook.html"
    if p.exists():
        return FileResponse(str(p))
    return JSONResponse({"error": "textbook.html not found"}, status_code=404)


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
