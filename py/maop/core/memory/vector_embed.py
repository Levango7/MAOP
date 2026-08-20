"""MAOP Vector Embeddings & Similarity — embedding providers and cosine similarity.

This module hosts the embedding-related building blocks used by
:class:`~maop.core.memory.vector_store.VectorStore`:

  * Data models — :class:`VectorEntry`, :class:`VectorSearchResult`
  * Similarity  — :func:`cosine_similarity`
  * Embedding providers:
      - :class:`EmbeddingProvider` (abstract base)
      - :class:`HashEmbedding` (zero-dependency, deterministic hash-based)
      - :class:`SentenceTransformerEmbedding` (local sentence-transformers)

Two embedding strategies are supported:
  - Local: sentence-transformers (22MB, 384-dim, ~5ms/query)
  - API: OpenAI embeddings (1536-dim, requires API key)

Split from ``vector.py`` to keep the embedding/similarity layer independent
of the SQLite-backed storage implementation.
"""

from __future__ import annotations

import logging
import math
import time
import uuid
from abc import ABC, abstractmethod
from typing import Any, cast

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# ── Models ────────────────────────────────────────────────────

class VectorEntry(BaseModel):
    """A vector-indexed entry."""
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:16])
    text: str = ""
    vector: list[float] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: float = Field(default_factory=time.time)


class VectorSearchResult(BaseModel):
    """A vector search result with similarity score."""
    id: str
    text: str
    score: float  # Cosine similarity [0, 1]
    metadata: dict[str, Any] = Field(default_factory=dict)


# ── Cosine similarity ────────────────────────────────────────

def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors.

    Returns value in [-1, 1]. Higher = more similar.
    Returns 0.0 if either vector is zero-length.
    """
    if len(a) != len(b) or len(a) == 0:
        return 0.0

    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for i in range(len(a)):
        dot += a[i] * b[i]
        norm_a += a[i] * a[i]
        norm_b += b[i] * b[i]

    denom = math.sqrt(norm_a) * math.sqrt(norm_b)
    if denom < 1e-10:
        return 0.0
    return dot / denom


# ── Embedding providers ──────────────────────────────────────

class EmbeddingProvider(ABC):
    """Base class for embedding providers."""

    _dim: int = 0

    @abstractmethod
    def embed(self, text: str) -> list[float]:
        """Embed a single text string into a vector."""
        raise NotImplementedError

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed multiple texts."""
        return [self.embed(t) for t in texts]

    @property
    def dimension(self) -> int:
        return self._dim


class HashEmbedding(EmbeddingProvider):
    """Zero-dependency hash-based embedding provider.

    Uses Python's built-in hashlib to produce deterministic 128-dim
    vectors from text. Suitable for testing and lightweight use cases
    where semantic quality is not critical.
    """

    def __init__(self, dim: int = 128) -> None:
        self._dim = dim

    def embed(self, text: str) -> list[float]:
        import hashlib
        h = hashlib.sha256(text.encode("utf-8")).digest()
        vec = []
        for i in range(self._dim):
            byte_val = h[i % len(h)]
            vec.append(float(byte_val) / 255.0)
        norm = math.sqrt(sum(v * v for v in vec))
        if norm < 1e-10:
            return vec
        return [v / norm for v in vec]


class SentenceTransformerEmbedding(EmbeddingProvider):
    """Local sentence-transformers embedding.

    Requires: pip install sentence-transformers
    Model: all-MiniLM-L6-v2 (22MB, 384-dim, ~5ms/query)
    """

    def __init__(self, model_name: str = "all-MiniLM-L6-v2") -> None:
        try:
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer(model_name)
            self._dim: int = int(self._model.get_sentence_embedding_dimension())
        except ImportError:
            raise ImportError(
                "sentence-transformers not installed. "
                "Install with: pip install sentence-transformers"
            )

    def embed(self, text: str) -> list[float]:
        return cast(list[float], self._model.encode(text).tolist())

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return cast(list[list[float]], self._model.encode(texts).tolist())

    @property
    def dimension(self) -> int:
        return self._dim


__all__ = [
    "EmbeddingProvider",
    "HashEmbedding",
    "SentenceTransformerEmbedding",
    "VectorEntry",
    "VectorSearchResult",
    "cosine_similarity",
]