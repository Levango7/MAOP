"""MAOP Vector Store — SQLite-backed tiered vector similarity search.

Four-tier fallback chain:

    HNSW (hnswlib, optional) → sqlite-vec (default) → NumPy → pure Python

Tier selection is governed by :attr:`VectorStore.hnsw_threshold`
(default 100_000). Split from ``vector.py``; embeddings live in
:mod:`maop.core.memory.vector_embed`, the HNSW wrapper in
:mod:`maop.core.memory.hnsw_index`, and the search-tier methods in
:mod:`maop.core.memory.vector_search_mixin` (mixed in via
:class:`VectorSearchMixin`). ``VectorStore`` and
``DEFAULT_HNSW_THRESHOLD`` are re-exported from
:mod:`maop.core.memory.vector` for backward compatibility.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from maop.core.backends.db_utils import sqlite_connect

# Embedding/similarity building blocks (split out to vector_embed.py).
from maop.core.memory.vector_embed import (
    EmbeddingProvider,
    HashEmbedding,
)

# HNSW index wrapper (split out to hnsw_index.py).
from maop.core.memory.hnsw_index import _HnswIndex

# Search-tier methods (split out to vector_search_mixin.py).
from maop.core.memory.vector_search_mixin import VectorSearchMixin

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


# ── VectorStore ───────────────────────────────────────────────

class VectorStore(VectorSearchMixin):
    """SQLite-backed vector store with tiered cosine similarity search.

    Search tier chain (best → worst):

        HNSW (hnswlib) → sqlite-vec → NumPy → pure Python

    Tier selection is automatic based on index size and dependency
    availability. See :data:`DEFAULT_HNSW_THRESHOLD`. Search methods
    are inherited from :class:`VectorSearchMixin`.

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
        # B19: text/meta 缓存也设上限，防止无限制增长。
        self._text_cache_max_size = 50000
        self._meta_cache_max_size = 50000
        # B22: 保护 _cache/_text_cache/_meta_cache 的并发读写。
        self._cache_lock = threading.Lock()
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
        except sqlite3.Error as exc:
            # B25: 改为具体异常类型
            logger.warning("[vector] Index failed: %s", exc)
            return ""
        except Exception as exc:
            # B25: 兜底
            logger.warning("[vector] Index failed (non-SQLite error): %s", exc, exc_info=True)
            return ""

        # Update caches
        # B19/B22: 加锁保护缓存写入，并淘汰超限的最旧项。
        with self._cache_lock:
            self._cache[entry_id] = vector
            self._text_cache[entry_id] = text
            self._meta_cache[entry_id] = meta
            # 淘汰超限项（LRU 语义：配合 _get_entry_info 中的 pop+reinsert，
            # next(iter(...)) 返回最久未访问的项）
            if len(self._cache) > self._cache_max_size:
                oldest = next(iter(self._cache))
                self._cache.pop(oldest, None)
            if len(self._text_cache) > self._text_cache_max_size:
                oldest = next(iter(self._text_cache))
                self._text_cache.pop(oldest, None)
            if len(self._meta_cache) > self._meta_cache_max_size:
                oldest = next(iter(self._meta_cache))
                self._meta_cache.pop(oldest, None)
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
                    # B22: 加锁保护缓存写入
                    with self._cache_lock:
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

                # B22: 批量加锁——将锁持有范围从"每行一个锁"改为"一次锁处理所有行"，
                # 显著减少锁获取/释放开销和缓存填充时间。
                with self._cache_lock:
                    for row in rows:
                        self._cache[row["id"]] = json.loads(row["vector"])
                        self._text_cache[row["id"]] = row["text"] or ""
                        self._meta_cache[row["id"]] = json.loads(row["metadata"] or "{}")
        except Exception as exc:
            logger.warning("[vector] Cache load failed: %s", exc)

    def _get_entry_info(self, entry_id: str) -> tuple[str, dict[str, Any]]:
        """Get text and metadata for an entry from in-memory cache."""
        # B22: 加锁保护缓存读取
        with self._cache_lock:
            text = self._text_cache.get(entry_id, "")
            meta = self._meta_cache.get(entry_id, {})
            # LRU: 缓存命中时将访问的项移到字典末尾（pop 后重新插入），
            # 使淘汰时 next(iter(...)) 返回最久未访问的项而非最旧插入的项。
            if (text or meta) and entry_id in self._text_cache:
                self._text_cache[entry_id] = self._text_cache.pop(entry_id)
            if (text or meta) and entry_id in self._meta_cache:
                self._meta_cache[entry_id] = self._meta_cache.pop(entry_id)
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
                    # B22: 加锁保护缓存写入
                    with self._cache_lock:
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
            # B22: 加锁保护缓存删除
            with self._cache_lock:
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
            # B22: 加锁保护缓存清空
            with self._cache_lock:
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
