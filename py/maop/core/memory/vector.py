"""MAOP Vector Search — Tiered vector similarity search.

Provides semantic search capability using a four-tier fallback chain:

    HNSW (hnswlib, optional) → sqlite-vec (default) → NumPy → pure Python

This module is a **compatibility re-export shim**. The implementation has
been split into two focused submodules to keep the embedding/similarity
layer independent of the SQLite-backed storage:

  * :mod:`maop.core.memory.vector_embed` — data models, cosine similarity,
    and embedding providers (:class:`HashEmbedding`,
    :class:`SentenceTransformerEmbedding`, :class:`EmbeddingProvider`).
  * :mod:`maop.core.memory.vector_store` — :class:`VectorStore` and the
    HNSW index wrapper.

All public symbols are re-exported here so existing imports such as::

    from maop.core.memory.vector import VectorStore, HashEmbedding, cosine_similarity

continue to work unchanged.

Usage::

    vs = VectorStore(db_path="data/vectors.db")

    # Index documents
    vs.index("doc1", "Fix login timeout bug", metadata={"agent": "claude"})
    vs.index("doc2", "Deploy new config system", metadata={"agent": "kimi"})

    # Search by text (auto-embeds query)
    results = vs.search("authentication timeout", top=5)

    # Search by vector (pre-computed embedding)
    results = vs.search_vector(query_vec, top=5)
"""

from __future__ import annotations

# Re-export embedding/similarity building blocks.
from maop.core.memory.vector_embed import (  # noqa: F401
    EmbeddingProvider,
    HashEmbedding,
    SentenceTransformerEmbedding,
    VectorEntry,
    VectorSearchResult,
    cosine_similarity,
)

# Re-export vector store and HNSW threshold constant.
from maop.core.memory.vector_store import (  # noqa: F401
    DEFAULT_HNSW_THRESHOLD,
    VectorStore,
)

