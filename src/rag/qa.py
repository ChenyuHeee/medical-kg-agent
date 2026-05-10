"""T-R03: Cited RAG Q&A — vanilla flavor.

Returns:
{
  "answer": "...",
  "citations": [{textbook, chapter, page, relevance_score}],
  "source_chunks": ["..."]
}
"""

from __future__ import annotations

import json
import logging
from typing import Any

from ..llm import LLMClient, build_messages, get_default_client
from .store import query

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """你是医学教材问答助手。严格按下面规则回答问题。

【规则】
1. 只能使用下面提供的"上下文"作答。
2. 不允许使用上下文之外的知识发挥；不允许编造引用。
3. 如果上下文不足以回答，必须回复："当前知识库中未找到相关信息"，不要硬答。
4. 回答正文末尾给出 1~5 条引用，每条形如 [教材名, 第X章, 第X页]。
5. 输出严格 JSON 对象：
{
  "answer": "<纯文本回答正文，含 [教材, 章节, 页码] 标注>",
  "citation_indices": [<引用了的 chunk 序号，从 1 开始>]
}
不要 markdown 包裹。"""


def _ctx_block(chunks: list[dict[str, Any]]) -> str:
    parts = []
    for i, c in enumerate(chunks, 1):
        m = c["metadata"]
        parts.append(
            f"[{i}] 教材={m.get('book_id','')}  章节={m.get('chapter','')}  页={m.get('page',-1)}\n{c['text']}"
        )
    return "\n\n---\n\n".join(parts)


def answer(question: str, k: int = 5, book_ids: list[str] | None = None,
           client: LLMClient | None = None) -> dict[str, Any]:
    chunks = query(question, k=k, book_ids=book_ids)
    if not chunks:
        return {
            "answer": "当前知识库中未找到相关信息",
            "citations": [],
            "source_chunks": [],
        }

    user = f"【问题】{question}\n\n【上下文】\n{_ctx_block(chunks)}\n\n请输出 JSON。"
    msgs = build_messages(system=SYSTEM_PROMPT, user=user)
    client = client or get_default_client()

    try:
        data = client.chat_json(msgs)
        ans = data.get("answer", "").strip()
        idxs = data.get("citation_indices", []) or []
    except Exception as ex:
        log.warning("llm answer failed: %s", ex)
        ans = "当前知识库中未找到相关信息"
        idxs = []

    if not ans:
        ans = "当前知识库中未找到相关信息"
        idxs = []

    citations = []
    src_texts = []
    for i in idxs:
        try:
            i = int(i)
        except Exception:
            continue
        if 1 <= i <= len(chunks):
            c = chunks[i - 1]
            m = c["metadata"]
            citations.append({
                "textbook": m.get("book_id", ""),
                "chapter": m.get("chapter", ""),
                "page": m.get("page", -1),
                "relevance_score": c["score"],
                "chunk_id": c["chunk_id"],
            })
            src_texts.append(c["text"])

    # Fallback: if model gave no indices but produced an answer, attribute to top-3.
    if not citations and ans != "当前知识库中未找到相关信息":
        for c in chunks[:3]:
            m = c["metadata"]
            citations.append({
                "textbook": m.get("book_id", ""),
                "chapter": m.get("chapter", ""),
                "page": m.get("page", -1),
                "relevance_score": c["score"],
                "chunk_id": c["chunk_id"],
            })
            src_texts.append(c["text"])

    return {"answer": ans, "citations": citations, "source_chunks": src_texts}


__all__ = ["answer"]


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("question")
    ap.add_argument("-k", type=int, default=5)
    args = ap.parse_args()
    print(json.dumps(answer(args.question, k=args.k), ensure_ascii=False, indent=2))
