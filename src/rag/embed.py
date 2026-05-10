"""T-R01: Sentence embedding wrapper (BGE-small-zh-v1.5).

Lazy singleton; first call downloads ~100MB model to ~/.cache/huggingface.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Iterable

import numpy as np

log = logging.getLogger(__name__)

DEFAULT_MODEL = "BAAI/bge-small-zh-v1.5"


@lru_cache(maxsize=1)
def get_model(name: str = DEFAULT_MODEL):
    from sentence_transformers import SentenceTransformer
    log.info("loading embedding model: %s", name)
    return SentenceTransformer(name)


def embed(texts: list[str], model_name: str = DEFAULT_MODEL, batch_size: int = 64) -> np.ndarray:
    """Return L2-normalized embeddings (so dot product == cosine)."""
    model = get_model(model_name)
    vecs = model.encode(
        list(texts),
        batch_size=batch_size,
        normalize_embeddings=True,
        show_progress_bar=False,
        convert_to_numpy=True,
    )
    return vecs.astype("float32")


def embed_query(text: str, model_name: str = DEFAULT_MODEL) -> np.ndarray:
    # BGE recommends the query prefix for better retrieval
    prefixed = f"为这个句子生成表示以用于检索相关文章：{text}"
    return embed([prefixed], model_name=model_name)[0]


__all__ = ["embed", "embed_query", "get_model", "DEFAULT_MODEL"]
