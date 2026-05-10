"""T-A02: KnowledgeAgent — single-agent function-calling loop.

Per ARCHITECTURE §7.2.  Sessions kept in-memory (single-process demo).
"""

from __future__ import annotations

import json
import logging
import time
from collections import defaultdict
from typing import Any

from ..llm import get_default_client
from .tools import TOOLS_SCHEMA, TOOL_FUNCS, undo as tool_undo, get_state

log = logging.getLogger(__name__)

SYSTEM = """你是医学教材整合知识图谱的 AI 助教。当前已经有一份"整合知识图谱"。

【你能做什么】
- 用户问知识点，你用 rag_query 检索带引用的回答
- 用户问"X 和 Y 是不是一回事/有什么不同"，你用 compare_books
- 用户说"它们应该是同一个/不应该合并"，你用 propose_merge / propose_split
- 用户说"X 是 Y 的前提"等，你用 update_relation
- 用户说"重新压缩到 25%" 等，你用 recompress

【硬规矩】
1. 每次你修改图谱，必须先用 search_kg 或 compare_books 给出依据再写。
2. 改图后用一句中文向用户确认你做了什么、依据是什么。
3. 不确定的时候反问用户，不要乱写。
4. 关系类型只能是 prerequisite / parallel / contains / applies_to。
"""


_SESSIONS: dict[str, list[dict]] = defaultdict(list)
_TOOL_CARDS: dict[str, list[dict]] = defaultdict(list)


def get_history(session_id: str) -> list[dict]:
    return list(_SESSIONS[session_id])


def reset_session(session_id: str):
    _SESSIONS.pop(session_id, None)
    _TOOL_CARDS.pop(session_id, None)


def chat(session_id: str, message: str, max_tool_loops: int = 4) -> dict[str, Any]:
    """Run one user turn: returns {reply, tool_calls, diff_cards}."""
    msgs = _SESSIONS[session_id]
    if not msgs:
        msgs.append({"role": "system", "content": SYSTEM})
    msgs.append({"role": "user", "content": message})

    client = get_default_client()
    diff_cards: list[dict] = []
    tool_calls_log: list[dict] = []

    for _ in range(max_tool_loops):
        try:
            resp = client._client.chat.completions.create(
                model=client.config.model,
                messages=msgs,
                tools=TOOLS_SCHEMA,
                tool_choice="auto",
                temperature=0.2,
                max_tokens=1024,
            )
        except Exception as ex:
            log.exception("chat openai call failed")
            reply = f"抱歉，模型调用失败：{ex}"
            msgs.append({"role": "assistant", "content": reply})
            return {"reply": reply, "tool_calls": tool_calls_log, "diff_cards": diff_cards}

        choice = resp.choices[0].message
        tool_calls = choice.tool_calls or []

        if not tool_calls:
            reply = choice.content or ""
            msgs.append({"role": "assistant", "content": reply})
            return {"reply": reply, "tool_calls": tool_calls_log, "diff_cards": diff_cards}

        # Append assistant message with tool_calls (required by spec)
        msgs.append({
            "role": "assistant",
            "content": choice.content or "",
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                }
                for tc in tool_calls
            ],
        })

        for tc in tool_calls:
            name = tc.function.name
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            fn = TOOL_FUNCS.get(name)
            if not fn:
                result = {"error": f"unknown tool {name}"}
            else:
                try:
                    result = fn(**args)
                except TypeError as ex:
                    result = {"error": f"bad args: {ex}"}
                except Exception as ex:
                    log.exception("tool %s failed", name)
                    result = {"error": str(ex)}

            tool_calls_log.append({"name": name, "args": args, "result_summary": _summarize(result)})

            # Hoist diff_card if present
            if isinstance(result, dict) and "diff_card" in result:
                diff_cards.append(result["diff_card"])

            msgs.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": json.dumps(_compact(result), ensure_ascii=False),
            })

    # Loop exhausted
    reply = "（达到工具调用上限，请重述需求）"
    msgs.append({"role": "assistant", "content": reply})
    return {"reply": reply, "tool_calls": tool_calls_log, "diff_cards": diff_cards}


def _summarize(result: Any) -> str:
    if not isinstance(result, dict):
        return str(result)[:120]
    if "error" in result:
        return f"error: {result['error']}"
    keys = list(result.keys())
    return ", ".join(keys[:6])


def _compact(result: Any) -> Any:
    """Strip subgraphs from tool replies before sending back to LLM (token saver)."""
    if isinstance(result, dict):
        out = {}
        for k, v in result.items():
            if k == "diff_card":
                out[k] = {"edit_id": v.get("edit", {}).get("edit_id"), "op": v.get("edit", {}).get("op")}
            elif k == "subgraph" and isinstance(v, dict):
                nodes = v.get("nodes", [])
                edges = v.get("edges", [])
                out[k] = {"nodes_count": len(nodes), "edges_count": len(edges),
                          "nodes_sample": [n.get("name") for n in nodes[:8]],
                          "edges_sample": [f"{e.get('source','')[-8:]}-[{e.get('relation_type','?')}]->{e.get('target','')[-8:]}" for e in edges[:8]]}
            else:
                out[k] = v
        return out
    return result


__all__ = ["chat", "get_history", "reset_session", "tool_undo"]
