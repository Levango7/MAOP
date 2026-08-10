"""MAOP Vector Backend abstraction layer (F1-02).

Provides a unified :class:`VectorBackend` ABC so that vector similarity search
can be backed by either SQLite (sqlite-vec / pure-Python fallback) or
PostgreSQL (pgvector IVFFLAT / HNSW), selected at runtime via the
``MAOP_VECTOR_BACKEND`` environment variable.

Backends
--------
- :class:`SqliteVectorBackend` — wraps the legacy
  :class:`maop.core.memory.vector.VectorStore` (sqlite-vec → NumPy → pure Python).
- :class:`PgVectorBackend` — uses the pgvector extension with IVFFLAT or HNSW
  ANN indexes on PostgreSQL.

Selection
---------
Use :func:`maop.core.vector.factory.get_vector_backend` to obtain a backend
instance honouring ``MAOP_VECTOR_BACKEND`` (``sqlite`` | ``pg`` | ``postgresql``).

Re-exports
----------
Shared models (:class:`VectorEntry`, :class:`VectorSearchResult`), the
:func:`cosine_similarity` helper, and embedding providers are re-exported from
:mod:`maop.core.memory.vector` so callers can import everything from this
package without reaching into the legacy module.
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any

# Re-export shared types from the legacy implementation so this package is the
# single import surface for new code. Legacy ``maop.core.memory.vector`` stays
# the source of truth to avoid duplicating model definitions.
from maop.core.memory.vector import (
    DEFAULT_HNSW_THRESHOLD,
    EmbeddingProvider,
    HashEmbedding,
    VectorEntry,
    VectorSearchResult,
    cosine_similarity,
)

logger = logging.getLogger(__name__)

__all__ = [
    "VectorBackend",
    "VectorEntry",
    "VectorSearchResult",
    "EmbeddingProvider",
    "HashEmbedding",
    "cosine_similarity",
    "DEFAULT_HNSW_THRESHOLD",
]


# ── Abstract backend ────────────────────────────────────────────


class VectorBackend(ABC):
    """Abstract base class for vector storage backends.

    Every backend must implement five operations:

    - :meth:`search`        — k-NN similarity search by pre-computed vector.
    - :meth:`insert`        — upsert a single ``(id, text, vector, metadata)``
      entry.
    - :meth:`delete`        — remove an entry by id.
    - :meth:`rebuild_index` — (re)build the ANN index. Implementations **must
      not** block concurrent :meth:`search` calls; on PostgreSQL this is
      achieved via ``CREATE INDEX CONCURRENTLY``.
    - :meth:`stats`         — return a dict of backend statistics (count,
      dimension, index type, …).

    The abstract class is intentionally minimal: higher-level concerns such as
    embedding (text → vector) and result post-processing belong in the caller,
    not in the backend. This keeps the ABC trivially mockable for tests.
    """

    name: str = "abstract"
    """Short identifier used in logs and :meth:`stats` (``"sqlite"`` / ``"pg"``)."""

    # ── Search ──────────────────────────────────────────────────

    @abstractmethod
    def search(
        self,
        query_vector: list[float],
        *,
        top: int = 10,
        threshold: float = 0.0,
    ) -> list[VectorSearchResult]:
        """Return the *top* most similar entries to *query_vector*.

        Parameters
        ----------
        query_vector : list[float]
            Pre-computed query embedding.
        top : int
            Maximum number of results.
        threshold : float
            Minimum cosine similarity in ``[0, 1]``; entries below this
            score are filtered out.

        Returns
        -------
        list[VectorSearchResult]
            Results sorted by similarity descending.
        """
        raise NotImplementedError

    # ── Insert ──────────────────────────────────────────────────

    @abstractmethod
    def insert(
        self,
        entry_id: str,
        text: str,
        vector: list[float],
        *,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Upsert a vector entry.

        Returns ``True`` on success, ``False`` on failure.
        """
        raise NotImplementedError

    # ── Delete ──────────────────────────────────────────────────

    @abstractmethod
    def delete(self, entry_id: str) -> bool:
        """Delete the entry with *entry_id*. Returns ``True`` if a row was
        removed, ``False`` otherwise (including not-found and error)."""
        raise NotImplementedError

    # ── Index rebuild ───────────────────────────────────────────

    @abstractmethod
    def rebuild_index(
        self,
        *,
        index_type: str = "ivfflat",
        **opts: Any,
    ) -> bool:
        """Rebuild the ANN index.

        Implementations **must not** block concurrent :meth:`search`
        operations. On PostgreSQL this is achieved via
        ``CREATE INDEX CONCURRENTLY`` (IVFFLAT) or HNSW's online build.

        Parameters
        ----------
        index_type : str
            ``"ivfflat"`` or ``"hnsw"``. Backends that do not support the
            requested type should fall back to their default and log a
            warning rather than raising.
        **opts
            Backend-specific options (e.g. ``lists=100``, ``m=16``,
            ``ef_construction=64``).

        Returns
        -------
        bool
            ``True`` if the rebuild succeeded, ``False`` otherwise.
        """
        raise NotImplementedError

    # ── Stats ───────────────────────────────────────────────────

    @abstractmethod
    def stats(self) -> dict[str, Any]:
        """Return a dict of backend statistics.

        Common keys: ``backend``, ``count``, ``dimension``, ``index_type``.
        Backends may add extra keys (e.g. ``index_size_bytes``).
        """
        raise NotImplementedError

    # ── Convenience helpers (non-abstract) ──────────────────────

    def insert_batch(
        self,
        entries: list[dict[str, Any]],
    ) -> int:
        """Insert multiple entries.

        Each dict must have ``id``, ``text``, ``vector``; optional ``metadata``.
        Default implementation loops :meth:`insert`; backends with a native
        batch path should override for efficiency.

        Returns the number of entries successfully inserted.
        """
        ok = 0
        for e in entries:
            if self.insert(
                e["id"],
                e["text"],
                e["vector"],
                metadata=e.get("metadata"),
            ):
                ok += 1
        return ok

    def count(self) -> int:
        """Return the number of indexed entries (convenience wrapper
        around :meth:`stats`)."""
        return int(self.stats().get("count", 0))

    def close(self) -> None:
        """Release backend resources (connection pools, file handles).

        Default implementation is a no-op; backends holding external
        resources should override.
        """
        return None