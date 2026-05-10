"""Smoke test for the ModelScope LLM client. Run with:

    MODELSCOPE_API_KEY=ms-xxx python -m src.llm.smoke_test

Will skip gracefully if the key is unset.
"""

from __future__ import annotations

import os
import sys


def main() -> int:
    if not os.environ.get("MODELSCOPE_API_KEY"):
        print("[skip] MODELSCOPE_API_KEY not set")
        return 0

    from .client import LLMClient, build_messages

    client = LLMClient()
    print(f"[info] model={client.config.model}")

    # 1. plain chat
    out = client.chat(build_messages(user="只回答两个字：你好"))
    print(f"[chat] {out!r}")

    # 2. json chat
    msgs = build_messages(
        system="你输出严格 JSON，不要任何额外文字。",
        user='请输出 {"ok": true, "n": 3}',
    )
    obj = client.chat_json(msgs)
    print(f"[json] {obj!r}")
    assert isinstance(obj, dict) and obj.get("ok") is True
    print("[ok] smoke test passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
