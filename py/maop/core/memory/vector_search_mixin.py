"""Vector search mixin for :class:`maop.core.memory.vector_store.VectorStore`.

Provides :class:`VectorSearchMixin`, which collects the search-tier
methods of :class:`VectorStore` into a separate mixin class:

    * :meth:`search` — text query (auto-embeds)
    * :meth:`search_vector` — pre-computed vector query with tier fallback
    * :meth:`_hnsw_add` — feed new vectors into the HNSW index (called by
      the host's ``index`` / ``index_batch``)
    * :meth:`_search_vector_hnsw` — HNSW ANN tier (best)
    * :meth:`_search_vector_sqlite_vec` — sqlite-vec ANN tier
    * :meth:`_search_vector_numpy` — NumPy-accelerated tier
    * :meth:`_search_vector_python` — pure-Python fallback tier

The mixin relies on ``self`` attributes/methods provided by the host
:class:`VectorStore` (``_cache``, ``_cache_lock``, ``_load_cache``,
``_get_entry_info``, ``_enable_hnsw``, ``hnsw_threshold``,
``_hnsw_index``, ``_path``, ``_embedding``). It performs no
initialisation of its own.

Split out of :mod:`maop.core.memory.vector_store` to keep the search
tier chain independent of the indexing/storage logic. This is a pure
physical split — no method signatures or logic were changed.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from maop.core.backends.db_utils import sqlite_connect

# HNSW index wrapper (split out to hnsw_index.py).
from maop.core.memory.hnsw_index import _HnswIndex

# Embedding/similarity building blocks (split out to vector_embed.py).
from maop.core.memory.vector_embed import (
    VectorSearchResult,
    cosine_similarity,
)

logger = logging.getLogger(__name__)


class VectorSearchMixin:
    """Search-tier methods for :class:`VectorStore`.

    Mixed into :class:`VectorStore` so that ``VectorStore`` instances
    gain :meth:`search` / :meth:`search_vector` and the four private
    tier backends. All methods access state via ``self`` and therefore
    share the host store's caches, locks, and HNSW index handle.

    """

    # 宿主属性（由组合类 VectorStore.__init__ 赋值，均为可空）：HNSW 索引在
    # 未启用/不可用时为 None，维度在首次写入前为 None。显式声明为可空，
    # 否则 mypy 会按 Mixin 内的赋值推断成非空类型，与组合类的 | None 冲突。
    _hnsw_index: _HnswIndex | None
    _hnsw_dim: int | None

    # ── Search ────────────────────────────────────────────────

    def search(
        self,
        query: str,
        *,
        top: int = 10,
        threshold: float = 0.0,
    ) -> list[VectorSearchResult]:
        """Search by text query (auto-embeds).

        Parameters
        ----------
        query : str
            Search query text.
        top : int
            Maximum results.
        threshold : float
            Minimum cosine similarity threshold.

        Returns
        -------
        list[VectorSearchResult]
            Results sorted by similarity descending.
        """
        query_vec = self._embedding.embed(query)
        return self.search_vector(query_vec, top=top, threshold=threshold)

    def search_vector(
        self,
        query_vector: list[float],
        *,
        top: int = 10,
        threshold: float = 0.0,
    ) -> list[VectorSearchResult]:
        """Search by pre-computed vector.

        Parameters
        ----------
        query_vector : list[float]
            Query embedding vector.
        top : int
            Maximum results.
        threshold : float
            Minimum cosine similarity.

        Returns
        -------
        list[VectorSearchResult]
            Results sorted by similarity descending.
        """
        # Load all vectors (with cache)
        # B22: 加锁保护缓存读取
        with self._cache_lock:
            cache_empty = not self._cache
        if cache_empty:
            self._load_cache()

        with self._cache_lock:
            cache_empty = not self._cache
            cache_len = len(self._cache)
        if cache_empty:
            return []

        # P1-5: Tier 0 — HNSW (best, optional). Only attempted when
        # the index size exceeds the configured threshold and hnswlib
        # is available. Falls back silently on any failure.
        if self._enable_hnsw and cache_len >= self.hnsw_threshold:
            try:
                return self._search_vector_hnsw(query_vector, top, threshold)
            except Exception as e:
                logger.debug("[vector] HNSW tier failed: %s", e, exc_info=True)

        # Tier 1 — sqlite-vec ANN (default dep, ~100x faster than brute-force)
        try:
            return self._search_vector_sqlite_vec(query_vector, top, threshold)
        except Exception as e:
            logger.debug("ignored: %s", e, exc_info=True)

        # Tier 2 — numpy-accelerated batch similarity
        try:
            import numpy as np
            return self._search_vector_numpy(query_vector, top, threshold, np)
        except ImportError:
            pass

        # Fallback: pure Python
        return self._search_vector_python(query_vector, top, threshold)

    # ── HNSW tier (P1-5) ──────────────────────────────────────

    def _hnsw_add(self, items: list[tuple[str, list[float]]]) -> None:
        """Feed new ``(id, vector)`` pairs into the HNSW index.

        No-op when HNSW is disabled, when no items are supplied, or
        when the vectors are zero-length (we cannot infer a dimension).
        The index is built lazily on the first call that supplies a
        non-empty vector; subsequent calls incrementally append.
        """
        if not self._enable_hnsw or not items:
            return
        # Infer dim from first non-empty vector
        for _, vec in items:
            if vec:
                if self._hnsw_dim is None:
                    self._hnsw_dim = len(vec)
                elif len(vec) != self._hnsw_dim:
                    # Mixed dims — skip HNSW entirely for safety
                    logger.debug(
                        "[vector] HNSW disabled: dim mismatch %d != %d",
                        len(vec), self._hnsw_dim,
                    )
                    self._enable_hnsw = False
                    return
                break
        if self._hnsw_dim is None:
            return  # all vectors empty
        if self._hnsw_index is None:
            self._hnsw_index = _HnswIndex(
                path=self._path.with_suffix(self._path.suffix + ".hnsw"),
                dim=self._hnsw_dim,
            )
        try:
            self._hnsw_index.build(items)
        except Exception as exc:
            logger.debug("[vector] HNSW build failed: %s", exc, exc_info=True)
            # Disable HNSW for the rest of this store's lifetime so we
            # don't retry the build on every search.
            self._enable_hnsw = False

    def _search_vector_hnsw(
        self,
        query_vector: list[float],
        top: int,
        threshold: float,
    ) -> list[VectorSearchResult]:
        """HNSW ANN search (tier 0, best).

        Raises if HNSW is not built or dim mismatches; the caller
        catches and falls back to sqlite-vec.
        """
        if self._hnsw_index is None:
            raise RuntimeError("HNSW index not initialized")
        # Rebuild if dirty (e.g. after delete/clear)
        # B22: 加锁保护缓存读取
        with self._cache_lock:
            cache_empty = not self._cache
        if cache_empty:
            self._load_cache()
        # Sync index with current cache when sizes diverge (post-delete)
        with self._cache_lock:
            cache_len = len(self._cache)
            cache_items = list(self._cache.items()) if self._hnsw_index.size != cache_len else None
        if cache_items is not None:
            self._hnsw_index.invalidate()
            self._hnsw_index.build(cache_items)
        hits = self._hnsw_index.search(query_vector, top)
        results: list[VectorSearchResult] = []
        for eid, score in hits:
            if score < threshold:
                continue
            text, meta = self._get_entry_info(eid)
            results.append(VectorSearchResult(id=eid, text=text, score=score, metadata=meta))
        return results

    def _search_vector_sqlite_vec(
        self,
        query_vector: list[float],
        top: int,
        threshold: float,
    ) -> list[VectorSearchResult]:
        """sqlite-vec ANN search (optional, ~100x faster than brute-force).

        Requires the ``sqlite-vec`` package. If not installed, raises
        ImportError which is caught by the caller to fall back to NumPy.
        """
        import sqlite_vec
        with sqlite_connect(self._path, timeout=10, wal=True) as conn:
            conn.enable_load_extension(True)
            sqlite_vec.load(conn)
            # Use virtual table if it exists, else raise to fall back
            cursor = conn.execute(
                "SELECT id, text, metadata, distance FROM vec_vectors "
                "WHERE embedding MATCH ? AND k = ? ORDER BY distance",
                (json.dumps(query_vector), top),
            )
            rows = cursor.fetchall()
        results: list[VectorSearchResult] = []
        for row in rows:
            eid, text, meta_json, dist = row
            score = max(0.0, 1.0 - float(dist))
            if score < threshold:
                continue
            meta = json.loads(meta_json) if meta_json else {}
            results.append(VectorSearchResult(id=eid, text=text, score=score, metadata=meta))
        return results

    def _search_vector_numpy(
        self,
        query_vector: list[float],
        top: int,
        threshold: float,
        np: Any,
    ) -> list[VectorSearchResult]:
        """NumPy-accelerated batch cosine similarity search."""
        # B22: 加锁保护缓存读取，获取快照后在锁外计算。
        with self._cache_lock:
            ids = list(self._cache.keys())
            vecs = list(self._cache.values())
        mat = np.array(vecs, dtype=np.float64)
        q = np.array(query_vector, dtype=np.float64)

        norms = np.linalg.norm(mat, axis=1)
        q_norm = np.linalg.norm(q)
        denom = norms * q_norm
        denom = np.where(denom < 1e-10, 1.0, denom)
        sims = (mat @ q) / denom

        mask = sims >= threshold
        scored = [(float(sims[i]), ids[i]) for i in range(len(ids)) if mask[i]]
        scored.sort(key=lambda x: x[0], reverse=True)
        scored = scored[:top]

        results = []
        for score, eid in scored:
            text, meta = self._get_entry_info(eid)
            results.append(VectorSearchResult(id=eid, text=text, score=score, metadata=meta))
        return results

    def _search_vector_python(
        self,
        query_vector: list[float],
        top: int,
        threshold: float,
    ) -> list[VectorSearchResult]:
        """Pure Python cosine similarity search (fallback)."""
        scored: list[tuple[float, str, str, dict]] = []
        # B22: 加锁保护缓存读取，获取快照后在锁外计算。
        with self._cache_lock:
            cache_items = list(self._cache.items())
        for entry_id, vec in cache_items:
            sim = cosine_similarity(query_vector, vec)
            if sim >= threshold:
                text, meta = self._get_entry_info(entry_id)
                scored.append((sim, entry_id, text, meta))

        scored.sort(key=lambda x: x[0], reverse=True)

        return [VectorSearchResult(
            id=eid, text=text, score=score, metadata=meta,
        ) for score, eid, text, meta in scored[:top]]


__all__ = ["VectorSearchMixin"]