"""ModelScope LLM client. T-03.

Thin wrapper over the OpenAI SDK pointed at ModelScope's API-Inference
endpoint. Provides:
- chat(messages, **kw) -> str
- chat_json(messages, schema_hint=None, **kw) -> dict | list
- automatic retries with exponential backoff
- best-effort JSON extraction from fenced or raw responses

Environment variables:
- MODELSCOPE_API_KEY  (required)
- MODELSCOPE_MODEL    (optional, default: Qwen/Qwen2.5-72B-Instruct)
- MODELSCOPE_BASE_URL (optional, default: https://api-inference.modelscope.cn/v1/)
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Iterable

try:
    from openai import OpenAI
    from openai import APIError, APITimeoutError, RateLimitError
except ImportError as e:  # pragma: no cover
    raise ImportError(
        "openai SDK not installed. Run: pip install openai"
    ) from e


DEFAULT_BASE_URL = "https://api-inference.modelscope.cn/v1/"
DEFAULT_MODEL = "Qwen/Qwen2.5-72B-Instruct"
DEFAULT_FALLBACK_MODEL = None  # disable: only Qwen3-235B-A22B-Instruct-2507 has provider on MS free tier


class LLMError(RuntimeError):
    pass


class LLMJSONParseError(LLMError):
    pass


@dataclass
class LLMConfig:
    api_key: str
    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL
    fallback_model: str | None = DEFAULT_FALLBACK_MODEL
    max_retries: int = 3
    backoff_base: float = 1.5
    request_timeout: float = 120.0

    @classmethod
    def from_env(cls) -> "LLMConfig":
        api_key = os.environ.get("MODELSCOPE_API_KEY", "").strip()
        if not api_key:
            raise LLMError(
                "MODELSCOPE_API_KEY env var is required. "
                "Get token at https://modelscope.cn/my/myaccesstoken"
            )
        return cls(
            api_key=api_key,
            base_url=os.environ.get("MODELSCOPE_BASE_URL", DEFAULT_BASE_URL),
            model=os.environ.get("MODELSCOPE_MODEL", DEFAULT_MODEL),
        )


class LLMClient:
    def __init__(self, config: LLMConfig | None = None):
        self.config = config or LLMConfig.from_env()
        self._client = OpenAI(
            api_key=self.config.api_key,
            base_url=self.config.base_url,
            timeout=self.config.request_timeout,
        )

    # ----- core -----

    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        temperature: float = 0.2,
        max_tokens: int = 2048,
        **kwargs: Any,
    ) -> str:
        """Single non-streaming chat completion. Returns assistant content."""
        model = model or self.config.model
        last_err: Exception | None = None
        for attempt in range(self.config.max_retries):
            try:
                resp = self._client.chat.completions.create(
                    model=model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    **kwargs,
                )
                content = resp.choices[0].message.content
                if content is None:
                    raise LLMError("Empty content from LLM")
                return content
            except (APITimeoutError, RateLimitError, APIError) as e:
                last_err = e
                wait = self.config.backoff_base ** attempt
                time.sleep(wait)
            except Exception as e:  # network etc.
                last_err = e
                wait = self.config.backoff_base ** attempt
                time.sleep(wait)
        # try fallback model once
        if model != self.config.fallback_model and self.config.fallback_model:
            try:
                resp = self._client.chat.completions.create(
                    model=self.config.fallback_model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    **kwargs,
                )
                content = resp.choices[0].message.content
                if content:
                    return content
            except Exception as e:
                last_err = e
        raise LLMError(f"chat failed after retries: {last_err}") from last_err

    def chat_json(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        temperature: float = 0.1,
        max_tokens: int = 2048,
    ) -> Any:
        """Chat and parse JSON from the response.

        The caller is responsible for instructing the model to output JSON.
        We accept either a raw JSON document or a ```json fenced block.
        """
        raw = self.chat(
            messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return parse_json_loose(raw)


# ----- helpers -----

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def parse_json_loose(text: str) -> Any:
    """Extract a JSON value from arbitrary LLM output.

    Tries: fenced ```json``` block -> first {..} or [..] span -> raw json.loads.
    """
    if not text or not text.strip():
        raise LLMJSONParseError("empty response")

    # 1. fenced block
    m = _FENCE_RE.search(text)
    if m:
        candidate = m.group(1).strip()
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass

    # 2. first balanced {..} or [..] span (greedy)
    for opener, closer in (("{", "}"), ("[", "]")):
        i = text.find(opener)
        j = text.rfind(closer)
        if i != -1 and j != -1 and j > i:
            candidate = text[i : j + 1]
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                continue

    # 3. raw
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError as e:
        raise LLMJSONParseError(f"could not parse JSON: {e}; head={text[:200]!r}")


def build_messages(
    *,
    system: str | None = None,
    user: str,
    examples: Iterable[tuple[str, str]] = (),
) -> list[dict[str, str]]:
    """Convenience builder for chat messages."""
    msgs: list[dict[str, str]] = []
    if system:
        msgs.append({"role": "system", "content": system})
    for u, a in examples:
        msgs.append({"role": "user", "content": u})
        msgs.append({"role": "assistant", "content": a})
    msgs.append({"role": "user", "content": user})
    return msgs


# Module-level singleton convenience
_default: LLMClient | None = None


def get_default_client() -> LLMClient:
    global _default
    if _default is None:
        _default = LLMClient()
    return _default


__all__ = [
    "LLMClient",
    "LLMConfig",
    "LLMError",
    "LLMJSONParseError",
    "build_messages",
    "get_default_client",
    "parse_json_loose",
]
