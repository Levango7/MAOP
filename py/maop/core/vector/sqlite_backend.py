"""SQLite vector backend — adapts :class:`maop.core.memory.vector.VectorStore`
to the :class:`VectorBackend` ABC.

This is the default backend (zero external dependencies beyond sqlite-vec).
It delegates all storage and search to the legacy
:class:`~maop.core.memory.vector.VectorStore`, which already implements a
tiered search chain (HNSW → sqlite-vec → NumPy → pure Python) and an
in-memory cache.

The adapter is a thin shim: it translates the ABC's ``insert``/``delete``/
``search``/``rebuild_index``/``stats`` calls into the legacy API and adds
the metadata needed by :mod:`maop.core.vector.factory`.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from maop.core.memory.vector import (
    DEFAULT_HNSW_THRESHOLD,
    EmbeddingProvider,
    HashEmbedding,
    VectorSearchResult,
    VectorStore,
)

from . import VectorBackend

logger = logging.getLogger(__name__)

__all__ = ["SqliteVectorBackend"]


class SqliteVectorBackend(VectorBackend):
    """SQLite-backed :class:`VectorBackend` (default).

    Wraps :class:`~maop.core.memory.vector.VectorStore`. The underlying store
    handles persistence (SQLite), ANN search (sqlite-vec / HNSW), and an
    in-memory vector cache.

    Parameters
    ----------
    db_path : Path | str | None
        SQLite database path. ``None`` uses the MAOP default
        (``<root>/data/vectors.db``).
    embedding : EmbeddingProvider | None
        Embedding provider for text→vector conversion. Defaults to
        :class:`HashEmbedding`.
    hnsw_threshold, enable_hnsw
        Forwarded to :class:`VectorStore` (see P1-5 HNSW tier).
    """

    name = "sqlite"

    def __init__(
        self,
        db_path: Path | str | None = None,
        embedding: EmbeddingProvider | None = None,
        *,
        hnsw_threshold: int = DEFAULT_HNSW_THRESHOLD,
        enable_hnsw: bool = True,
    ) -> None:
        self._store = VectorStore(
            db_path=db_path,
            embedding=embedding or HashEmbedding(),
            hnsw_threshold=hnsw_threshold,
            enable_hnsw=enable_hnsw,
        )
        self._embedding = self._store._embedding  # noqa: SLF001 — share provider with store

    # ── Search ──────────────────────────────────────────────────

    def search(
        self,
        query_vector: list[float],
        *,
        top: int = 10,
        threshold: float = 0.0,
    ) -> list[VectorSearchResult]:
        """Delegate to :meth:`VectorStore.search_vector`."""
        return self._store.search_vector(query_vector, top=top, threshold=threshold)

    # ── Insert ──────────────────────────────────────────────────

    def insert(
        self,
        entry_id: str,
        text: str,
        vector: list[float],
        *,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Upsert a single entry via :meth:`VectorStore.index`.

        Returns ``True`` on success (non-empty id returned), ``False`` on
        failure (the legacy ``index`` swallows exceptions and returns ``""``).
        """
        result_id = self._store.index(
            entry_id, text, vector=vector, metadata=metadata,
        )
        return result_id == entry_id

    def insert_batch(self, entries: list[dict[str, Any]]) -> int:
        """Override with the legacy ``index_batch`` for batch efficiency."""
        return self._store.index_batch(entries)

    # ── Delete ──────────────────────────────────────────────────

    def delete(self, entry_id: str) -> bool:
        """Delegate to :meth:`VectorStore.delete`."""
        return self._store.delete(entry_id)

    # ── Index rebuild ───────────────────────────────────────────

    def rebuild_index(
        self,
        *,
        index_type: str = "ivfflat",
        **opts: Any,
    ) -> bool:
        """Rebuild the SQLite ANN index.

        For sqlite-vec there is no persistent index to rebuild (the virtual
        table is rebuilt on every search via ``MATCH``), so this is a no-op
        that returns ``True``. For HNSW we invalidate the on-disk ``.hnsw``
        file so the next search lazily rebuilds it — this does not block
        concurrent searches because the rebuild happens on the search thread.
        """
        if index_type not in ("ivfflat", "hnsw", "auto"):
            logger.warning(
                "[sqlite-vector] unknown index_type %r; treating as 'auto'",
                index_type,
            )
        # Invalidate HNSW index if present so it rebuilds on next search.
        hnsw = getattr(self._store, "_hnsw_index", None)
        if hnsw is not None:
            try:
                hnsw.invalidate()
            except Exception as exc:  # noqa: BLE001 — best-effort
                logger.debug("[sqlite-vector] HNSW invalidate failed: %s", exc)
        return True

    # ── Stats ───────────────────────────────────────────────────

    def stats(self) -> dict[str, Any]:
        """Return backend statistics."""
        count = self._store.count()
        dim = self._embedding.dimension
        hnsw_enabled = bool(getattr(self._store, "_enable_hnsw", False))
        return {
            "backend": self.name,
            "count": count,
            "dimension": dim,
            "index_type": "hnsw" if hnsw_enabled else "sqlite-vec",
            "db_path": str(getattr(self._store, "_path", "")),
            "hnsw_threshold": getattr(self._store, "hnsw_threshold", DEFAULT_HNSW_THRESHOLD),
        }

    # ── Lifecycle ───────────────────────────────────────────────

    def close(self) -> None:
        """Clear in-memory caches to release memory.

        The SQLite file itself is closed after every operation (context
        manager), so there is no persistent connection to drop.
        """
        for cache_attr in ("_cache", "_text_cache", "_meta_cache"):
            cache = getattr(self._store, cache_attr, None)
            if cache is not None:
                cache.clear()