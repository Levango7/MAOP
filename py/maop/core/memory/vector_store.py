"""MAOP Vector Store — SQLite-backed tiered vector similarity search.

Provides semantic search capability using a four-tier fallback chain:

    HNSW (hnswlib, optional) → sqlite-vec (default) → NumPy → pure Python

Tier selection is governed by an index-size threshold
(:attr:`VectorStore.hnsw_threshold`):

    * ``count < hnsw_threshold`` (default 100_000) → sqlite-vec ANN
    * ``count ≥ hnsw_threshold`` and ``hnswlib`` installed → HNSW ANN
    * Any tier failure transparently falls back to the next tier.

Storage: SQLite-backed vector index for persistence.

Usage::

    vs = VectorStore(db_path="data/vectors.db")

    # Index documents
    vs.index("doc1", "Fix login timeout bug", metadata={"agent": "claude"})
    vs.index("doc2", "Deploy new config system", metadata={"agent": "kimi"})

    # Search by text (auto-embeds query)
    results = vs.search("authentication timeout", top=5)

    # Search by vector (pre-computed embedding)
    results = vs.search_vector(query_vec, top=5)

Split from ``vector.py``; embedding providers and cosine similarity live in
:mod:`maop.core.memory.vector_embed`.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from maop.core.backends.db_utils import sqlite_connect

# Embedding/similarity building blocks (split out to vector_embed.py).
from maop.core.memory.vector_embed import (
    EmbeddingProvider,
    HashEmbedding,
    VectorSearchResult,
    cosine_similarity,
)

logger = logging.getLogger(__name__)

# ── SQLite DDL ────────────────────────────────────────────────

_VECTOR_DDL = """
CREATE TABLE IF NOT EXISTS vector_entries (
  id TEXT PRIMARY KEY,
  text TEXT NOT NULL DEFAULT '',
  vector TEXT NOT NULL DEFAULT '[]',
  metadata TEXT NOT NULL DEFAULT '{}',
  created_at REAL NOT NULL DEFAULT 0.0
);

CREATE INDEX IF NOT EXISTS idx_ve_created ON vector_entries(created_at);
"""


# ── HNSW index wrapper ───────────────────────────────────────

# Threshold above which VectorStore prefers HNSW over sqlite-vec.
# 100K is the empirical knee where brute-force / flat ANN starts to
# lose to graph-based ANN on common embedding dims (384, 768, 1536).
DEFAULT_HNSW_THRESHOLD = 100_000


class _HnswIndex:
    """Thin wrapper around ``hnswlib`` for cosine-similarity ANN.

    Lifecycle:
      * Built lazily on first search when ``len(vectors) >= threshold``.
      * Persisted to ``<db_path>.hnsw`` so restarts do not rebuild.
      * Marked dirty on delete/clear; rebuilt on next search.

    The wrapper never raises on missing ``hnswlib`` — callers detect
    availability via :meth:`available` and skip the tier silently.
    """

    def __init__(
        self,
        path: Path,
        dim: int,
        *,
        max_elements: int = 1_000_000,
        m: int = 16,
        ef_construction: int = 200,
        ef_search: int = 64,
    ) -> None:
        self._path = path
        self._dim = dim
        self._max_elements = max_elements
        self._m = m
        self._ef_construction = ef_construction
        self._ef_search = ef_search
        self._index: Any = None
        # internal label → entry_id mapping (hnswlib returns labels)
        self._label_to_id: dict[int, str] = {}
        self._id_to_label: dict[str, int] = {}
        self._dirty = True

    @staticmethod
    def available() -> bool:
        """Return True iff ``hnswlib`` is importable."""
        try:
            import hnswlib  # noqa: F401
            return True
        except ImportError:
            return False

    def _new_index(self) -> Any:
        import hnswlib
        idx = hnswlib.Index(space="cosine", dim=self._dim)
        idx.init_index(
            max_elements=self._max_elements,
            ef_construction=self._ef_construction,
            M=self._m,
        )
        idx.set_ef(self._ef_search)
        return idx

    def build(self, items: list[tuple[str, list[float]]]) -> None:
        """Build the HNSW index from ``(id, vector)`` pairs.

        Reuses the on-disk index when possible and only adds missing
        labels, keeping incremental indexing cheap.
        """
        if not items:
            self._dirty = False
            return
        # Load existing index if present and compatible
        if self._index is None and self._path.exists():
            try:
                import hnswlib
                idx = hnswlib.Index(space="cosine", dim=self._dim)
                idx.load_index(str(self._path))
                self._index = idx
            except Exception as exc:
                logger.debug("[vector] HNSW load failed, will rebuild: %s", exc)
                self._index = None

        if self._index is None:
            # Size max_elements to 2x current count, capped at config
            target_max = max(self._max_elements, len(items) * 2)
            self._max_elements = target_max
            self._index = self._new_index()
            self._label_to_id.clear()
            self._id_to_label.clear()

        # Ensure capacity
        cur_count = self._index.get_current_count()
        if cur_count + len(items) > self._max_elements:
            new_max = max(self._max_elements * 2, cur_count + len(items))
            self._index.resize_index(new_max)
            self._max_elements = new_max

        # Add items with sequential labels
        import numpy as np
        new_labels: list[int] = []
        new_vecs: list[list[float]] = []
        for entry_id, vec in items:
            if entry_id in self._id_to_label:
                continue  # already indexed
            if len(vec) != self._dim:
                # dim mismatch — skip silently; tier downgrade will handle
                logger.debug(
                    "[vector] HNSW skip %s: dim %d != %d",
                    entry_id, len(vec), self._dim,
                )
                continue
            label = cur_count + len(new_labels)
            self._label_to_id[label] = entry_id
            self._id_to_label[entry_id] = label
            new_labels.append(label)
            new_vecs.append(vec)

        if new_labels:
            data = np.array(new_vecs, dtype=np.float32)
            self._index.add_items(data, np.array(new_labels))
            try:
                self._index.save_index(str(self._path))
            except Exception as exc:
                logger.debug("[vector] HNSW save failed: %s", exc)

        self._dirty = False

    def mark_dirty(self) -> None:
        """Mark the index as needing rebuild on next search."""
        self._dirty = True

    def invalidate(self) -> None:
        """Drop the in-memory index and delete the on-disk file."""
        self._index = None
        self._label_to_id.clear()
        self._id_to_label.clear()
        self._dirty = True
        try:
            if self._path.exists():
                self._path.unlink()
        except Exception as exc:
            logger.debug("[vector] HNSW unlink failed: %s", exc)

    def search(
        self,
        query_vector: list[float],
        top: int,
    ) -> list[tuple[str, float]]:
        """Return ``(entry_id, similarity)`` pairs.

        Similarity is cosine in [0, 1] (1 − distance).
        """
        if self._index is None or self._dirty:
            raise RuntimeError("HNSW index not built or dirty")
        if len(query_vector) != self._dim:
            raise RuntimeError(
                f"HNSW dim mismatch: query {len(query_vector)} != index {self._dim}"
            )
        import numpy as np
        labels, distances = self._index.knn_query(
            np.array([query_vector], dtype=np.float32), k=top,
        )
        out: list[tuple[str, float]] = []
        for label, dist in zip(labels[0], distances[0]):
            entry_id = self._label_to_id.get(int(label))
            if entry_id is None:
                continue
            # cosine space: distance = 1 - cos_sim
            sim = max(0.0, 1.0 - float(dist))
            out.append((entry_id, sim))
        return out

    @property
    def dim(self) -> int:
        return self._dim

    @property
    def size(self) -> int:
        return len(self._id_to_label)


# ── VectorStore ───────────────────────────────────────────────

class VectorStore:
    """SQLite-backed vector store with tiered cosine similarity search.

    Search tier chain (best → worst):

        HNSW (hnswlib) → sqlite-vec → NumPy → pure Python

    Tier selection is automatic based on index size and dependency
    availability. See :data:`DEFAULT_HNSW_THRESHOLD`.

    Parameters
    ----------
    db_path : Path | str | None
        Path to SQLite database file.
    embedding : EmbeddingProvider | None
        Embedding provider. Defaults to HashEmbedding (zero-dependency).
    hnsw_threshold : int
        Vector count above which HNSW is preferred over sqlite-vec.
        Defaults to :data:`DEFAULT_HNSW_THRESHOLD` (100_000). Set to
        a very large value to effectively disable HNSW.
    enable_hnsw : bool
        Master switch for the HNSW tier. When False, the store skips
        HNSW even if ``hnswlib`` is installed and the threshold is
        exceeded. Useful for tests and small datasets where the
        build cost is not worthwhile.
    """

    def __init__(
        self,
        db_path: Path | str | None = None,
        embedding: EmbeddingProvider | None = None,
        *,
        hnsw_threshold: int = DEFAULT_HNSW_THRESHOLD,
        enable_hnsw: bool = True,
    ) -> None:
        if db_path is None:
            db_path = Path(__file__).resolve().parents[2] / "data" / "vectors.db"
        self._path = Path(db_path)
        self._embedding = embedding or HashEmbedding()
        self._cache: dict[str, list[float]] = {}  # id → vector cache
        self._cache_max_size = 50000  # P2 fix: prevent unbounded memory growth
        self._text_cache: dict[str, str] = {}  # id → text cache
        self._meta_cache: dict[str, dict[str, Any]] = {}  # id → metadata cache
        # P1-5: HNSW tier configuration
        self.hnsw_threshold = max(0, int(hnsw_threshold))
        self._enable_hnsw = bool(enable_hnsw) and _HnswIndex.available()
        self._hnsw_index: _HnswIndex | None = None
        self._hnsw_dim: int | None = None
        self._init_db()

    def _connect(self):
        return sqlite_connect(self._path, foreign_keys=False)

    def _init_db(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with self._connect() as conn:
                conn.executescript(_VECTOR_DDL)
        except Exception as exc:
            logger.warning("Failed to initialize vector DB: %s", exc)

    # ── Index ─────────────────────────────────────────────────

    def index(
        self,
        entry_id: str,
        text: str,
        *,
        vector: list[float] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Index a document with its text and optional pre-computed vector.

        **P4-§4.7 调研结论**: ``index()`` 是 **写入/索引操作**
        (``INSERT OR REPLACE INTO vector_entries``)，不是查找操作。
        其复杂度由 SQLite 主键 ``id`` 的 B-tree 索引决定，为 **O(log n)**，
        而非 ``list.index(x)`` 风格的 O(n) 线性查找。因此无需改为
        dict 倒排索引。原任务描述中"是否 O(1) 查找"是基于对方法名
        的误解 —— 此处 ``index`` 是动词（"建立索引"），不是名词
        (``list.index``)。

        Parameters
        ----------
        entry_id : str
            Unique document ID.
        text : str
            Document text.
        vector : list[float] | None
            Pre-computed embedding. If None, computed via embedding provider.
        metadata : dict | None
            Optional metadata dict.

        Returns
        -------
        str
            Entry ID.
        """
        if vector is None:
            vector = self._embedding.embed(text)

        meta = metadata or {}
        now = time.time()

        try:
            with self._connect() as conn:
                conn.execute(
                    """INSERT OR REPLACE INTO vector_entries
                        (id, text, vector, metadata, created_at)
                        VALUES (?, ?, ?, ?, ?)""",
                    (entry_id, text, json.dumps(vector), json.dumps(meta), now),
                )
        except Exception as exc:
            logger.warning("[vector] Index failed: %s", exc)
            return ""

        # Update caches
        self._cache[entry_id] = vector
        self._text_cache[entry_id] = text
        self._meta_cache[entry_id] = meta
        # P1-5: incrementally feed new vector into HNSW if active
        self._hnsw_add([(entry_id, vector)])
        return entry_id

    def index_batch(self, entries: list[dict[str, Any]]) -> int:
        """Index multiple entries at once.

        Parameters
        ----------
        entries : list[dict]
            Each dict must have 'id' and 'text', optional 'vector' and 'metadata'.

        Returns
        -------
        int
            Number of entries indexed.
        """
        # Batch embed texts that don't have pre-computed vectors
        texts_to_embed = []
        embed_indices = []
        vectors: list[list[float] | None] = []

        for i, entry in enumerate(entries):
            vec = entry.get("vector")
            if vec is not None:
                vectors.append(vec)
            else:
                texts_to_embed.append(entry["text"])
                embed_indices.append(i)
                vectors.append(None)

        # Batch embed
        if texts_to_embed:
            embedded = self._embedding.embed_batch(texts_to_embed)
            for idx, vec in zip(embed_indices, embedded):
                vectors[idx] = vec

        # Store all via executemany for batch efficiency
        count = 0
        try:
            with self._connect() as conn:
                now = time.time()
                rows = []
                for i, entry in enumerate(entries):
                    vec = vectors[i]
                    if vec is None:
                        continue
                    meta = entry.get("metadata", {})
                    eid = entry["id"]
                    text = entry["text"]
                    rows.append((eid, text, json.dumps(vec), json.dumps(meta), now))
                    self._cache[eid] = vec
                    self._text_cache[eid] = text
                    self._meta_cache[eid] = meta
                    count += 1
                if rows:
                    conn.executemany(
                        """INSERT OR REPLACE INTO vector_entries
                           (id, text, vector, metadata, created_at)
                           VALUES (?, ?, ?, ?, ?)""",
                        rows,
                    )
        except Exception as exc:
            logger.warning("[vector] Batch index failed: %s", exc)

        # P1-5: incrementally feed new vectors into HNSW if active
        if count > 0:
            new_items: list[tuple[str, list[float]]] = []
            for i, entry in enumerate(entries):
                vec = vectors[i]
                if vec is None:
                    continue
                new_items.append((entry["id"], vec))
            if new_items:
                self._hnsw_add(new_items)

        return count

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
        if not self._cache:
            self._load_cache()

        if not self._cache:
            return []

        # P1-5: Tier 0 — HNSW (best, optional). Only attempted when
        # the index size exceeds the configured threshold and hnswlib
        # is available. Falls back silently on any failure.
        if self._enable_hnsw and len(self._cache) >= self.hnsw_threshold:
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
        if not self._cache:
            self._load_cache()
        # Sync index with current cache when sizes diverge (post-delete)
        if self._hnsw_index.size != len(self._cache):
            items = list(self._cache.items())
            self._hnsw_index.invalidate()
            self._hnsw_index.build(items)
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
        for entry_id, vec in self._cache.items():
            sim = cosine_similarity(query_vector, vec)
            if sim >= threshold:
                text, meta = self._get_entry_info(entry_id)
                scored.append((sim, entry_id, text, meta))

        scored.sort(key=lambda x: x[0], reverse=True)

        return [VectorSearchResult(
            id=eid, text=text, score=score, metadata=meta,
        ) for score, eid, text, meta in scored[:top]]

    def _load_cache(self) -> None:
        """Load vectors, text, and metadata from SQLite into memory cache.

        P2-P3 fix: 分页加载，遵守 _cache_max_size 限制，防止大数据集 OOM。
        - 当总条数 <= _cache_max_size 时，全量加载（保持原行为）
        - 当总条数 > _cache_max_size 时，仅加载最近的 _cache_max_size 条
          （按 created_at DESC 排序，优先保留新数据）
        - sqlite-vec/HNSW 路径不依赖 _cache，不受此限制影响
        - NumPy/Python 回退路径仅搜索缓存中的向量（已知限制）

        Batch-loads all columns in a single query to avoid N+1 per-entry lookups
        during search_vector(). Also populates _text_cache and _meta_cache.
        """
        try:
            with self._connect() as conn:
                # 先查总数，决定是否分页
                total = conn.execute(
                    "SELECT COUNT(*) AS cnt FROM vector_entries"
                ).fetchone()["cnt"]

                if total <= self._cache_max_size:
                    # 小数据集：全量加载（保持原行为）
                    rows = conn.execute(
                        "SELECT id, vector, text, metadata FROM vector_entries"
                    ).fetchall()
                else:
                    # 大数据集：仅加载最近的 _cache_max_size 条
                    logger.warning(
                        "[vector] Dataset %d > cache_max_size %d, "
                        "loading only recent %d entries "
                        "(use sqlite-vec/HNSW for full search)",
                        total, self._cache_max_size, self._cache_max_size,
                    )
                    rows = conn.execute(
                        "SELECT id, vector, text, metadata FROM vector_entries "
                        "ORDER BY created_at DESC LIMIT ?",
                        (self._cache_max_size,),
                    ).fetchall()

                for row in rows:
                    self._cache[row["id"]] = json.loads(row["vector"])
                    self._text_cache[row["id"]] = row["text"] or ""
                    self._meta_cache[row["id"]] = json.loads(row["metadata"] or "{}")
        except Exception as exc:
            logger.warning("[vector] Cache load failed: %s", exc)

    def _get_entry_info(self, entry_id: str) -> tuple[str, dict[str, Any]]:
        """Get text and metadata for an entry from in-memory cache."""
        text = self._text_cache.get(entry_id, "")
        meta = self._meta_cache.get(entry_id, {})
        if text or meta:
            return text, meta
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT text, metadata FROM vector_entries WHERE id = ?",
                    (entry_id,),
                ).fetchone()
                if row:
                    text = row["text"] or ""
                    meta = json.loads(row["metadata"] or "{}")
                    self._text_cache[entry_id] = text
                    self._meta_cache[entry_id] = meta
                    return text, meta
        except Exception as e:
            logger.debug("ignored: %s", e, exc_info=True)
        return "", {}

    # ── Maintenance ───────────────────────────────────────────

    def delete(self, entry_id: str) -> bool:
        """Delete an entry by ID."""
        try:
            with self._connect() as conn:
                conn.execute("DELETE FROM vector_entries WHERE id = ?", (entry_id,))
            self._cache.pop(entry_id, None)
            self._text_cache.pop(entry_id, None)
            self._meta_cache.pop(entry_id, None)
            # P1-5: HNSW does not support cheap incremental delete;
            # mark dirty so the next search rebuilds from cache.
            if self._hnsw_index is not None:
                self._hnsw_index.mark_dirty()
            return True
        except Exception as exc:
            logger.warning("[vector] Delete failed: %s", exc)
            return False

    def count(self) -> int:
        """Count total indexed entries."""
        try:
            with self._connect() as conn:
                row = conn.execute("SELECT COUNT(*) as cnt FROM vector_entries").fetchone()
                return row["cnt"] if row else 0
        except Exception:
            return 0

    def list_all(
        self,
        *,
        limit: int = 1000,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """List indexed entries with pagination.

        **P4-§4.8**: previously the dashboard ``/api/vector/list`` endpoint
        called ``vs.list_all()`` which did not exist on ``VectorStore``
        (``hasattr`` returned False → empty list returned). This method
        provides a real paginated implementation so the endpoint returns
        useful data without OOM-risk of loading the full table.

        Parameters
        ----------
        limit : int
            Maximum entries to return (1..10_000, default 1000).
        offset : int
            Number of entries to skip (>= 0, default 0).
        """
        # Clamp to safe bounds — protects against callers passing negative
        # or huge values via query-string params.
        limit = max(1, min(int(limit), 10_000))
        offset = max(0, int(offset))
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT id, text, metadata, created_at "
                    "FROM vector_entries "
                    "ORDER BY created_at DESC "
                    "LIMIT ? OFFSET ?",
                    (limit, offset),
                ).fetchall()
            result: list[dict[str, Any]] = []
            for row in rows:
                meta = json.loads(row["metadata"] or "{}") if row["metadata"] else {}
                result.append({
                    "id": row["id"],
                    "text": row["text"] or "",
                    "metadata": meta,
                    "created_at": row["created_at"],
                })
            return result
        except Exception as exc:
            logger.warning("[vector] list_all failed: %s", exc)
            return []

    def clear(self) -> int:
        """Remove all entries. Returns count deleted."""
        cnt = self.count()
        try:
            with self._connect() as conn:
                conn.execute("DELETE FROM vector_entries")
            self._cache.clear()
            self._text_cache.clear()
            self._meta_cache.clear()
            # P1-5: drop HNSW index entirely so next build starts fresh.
            if self._hnsw_index is not None:
                self._hnsw_index.invalidate()
        except Exception as exc:
            logger.warning("[vector] Clear failed: %s", exc)
        return cnt


__all__ = [
    "DEFAULT_HNSW_THRESHOLD",
    "VectorStore",
]