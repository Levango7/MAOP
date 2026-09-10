"""MAOP Semantic Cache — Similarity-based LLM response caching.

Caches LLM responses keyed by semantic similarity rather than exact match.
When a new query is semantically similar to a cached query (above a
threshold), the cached response is returned — saving tokens, cost, and latency.

Uses the VectorStore's embedding for similarity comparison.

Usage::

    from maop.core.memory.semantic_cache import SemanticCache

    cache = SemanticCache(similarity_threshold=0.92)

    # Store a response
    cache.put("How do I fix a timeout error?", "Set socket timeout before connecting...")

    # Retrieve by similar query (not exact match)
    result = cache.get("How to resolve timeout issues?")
    # Returns the cached response if similarity >= 0.92
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from typing import Any, cast

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class SemanticCacheEntry(BaseModel):
    """A semantic cache entry."""
    query: str = ""
    response: str = ""
    embedding: list[float] = Field(default_factory=list)
    similarity_score: float = 0.0
    created_at: float = Field(default_factory=time.time)
    access_count: int = 0
    ttl_s: float = 0.0


class SemanticCacheStats(BaseModel):
    """Semantic cache statistics."""
    hits: int = 0
    misses: int = 0
    entries: int = 0
    hit_rate: float = 0.0


class SemanticCache:
    """Semantic similarity cache for LLM responses.

    Unlike LRUCache which uses exact key matching, SemanticCache finds
    the most similar cached query by embedding similarity and returns
    its response if above the threshold.

    Parameters
    ----------
    max_entries : int
        Maximum cache entries.
    similarity_threshold : float
        Minimum cosine similarity to consider a match (0.0-1.0).
        Higher = more strict. Recommended: 0.90-0.95.
    default_ttl_s : float
        Default TTL for entries. 0 = never expires.
    """

    def __init__(
        self,
        max_entries: int = 500,
        similarity_threshold: float = 0.92,
        default_ttl_s: float = 0.0,
        max_scan: int = 100,
    ) -> None:
        self._max_entries = max(1, max_entries)
        self._threshold = max(0.0, min(1.0, similarity_threshold))
        self._default_ttl = default_ttl_s
        # B17: 限制单次相似度扫描条目数，避免 O(n) 全量遍历。
        # 扫描最近 max_scan 条（按插入顺序），兼顾命中率与延迟。
        self._max_scan = max(1, max_scan)
        self._entries: dict[str, SemanticCacheEntry] = {}
        self._order: list[str] = []
        self._hits = 0
        self._misses = 0
        self._embedder: Any = None
        # Guard all _entries/_order/_hits/_misses mutations; the cache is
        # frequently accessed from concurrent request handlers.
        self._lock = threading.Lock()
        # B13: 自动触发过期清理——避免过期条目只在显式调用 cleanup_expired 时才移除。
        # 每次 get/put 操作时检查距上次清理的时间，超过阈值则自动触发。
        self._last_cleanup_time: float = time.time()
        self._cleanup_interval: float = 300.0

    def _get_embedder(self):
        if self._embedder is None:
            from maop.core.memory.vector import HashEmbedding
            self._embedder = HashEmbedding(dim=128)
        return self._embedder

    def _embed(self, text: str) -> list[float]:
        try:
            return cast(list[float], self._get_embedder().embed(text))
        except Exception as e:
            logger.debug("ignored: %s", e, exc_info=True)
            return []

    @staticmethod
    def _cosine_sim(a: list[float], b: list[float]) -> float:
        import math
        if len(a) != len(b) or len(a) == 0:
            return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = math.sqrt(sum(x * x for x in a))
        norm_b = math.sqrt(sum(x * x for x in b))
        denom = norm_a * norm_b
        if denom < 1e-10:
            return 0.0
        return dot / denom

    def _find_similar(self, query_embedding: list[float]) -> tuple[str | None, float]:
        best_key = None
        best_score = 0.0
        now = time.time()

        # B17: 仅扫描最近 max_scan 条目，避免 O(n) 全量遍历。
        # _order 按插入顺序排列，尾部为最新。
        scan_keys = self._order[-self._max_scan:]
        for key in scan_keys:
            entry = self._entries.get(key)
            if entry is None:
                continue
            if entry.ttl_s > 0 and now > entry.created_at + entry.ttl_s:
                continue
            if not entry.embedding:
                continue
            score = self._cosine_sim(query_embedding, entry.embedding)
            if score > best_score:
                best_score = score
                best_key = key

        return best_key, best_score

    def get(self, query: str) -> str | None:
        """Look up a response by semantic similarity.

        Returns the cached response if a similar query exists above
        the similarity threshold, otherwise None.
        """
        self._maybe_cleanup()  # B13: 自动触发过期清理
        query_embedding = self._embed(query)
        if not query_embedding:
            with self._lock:
                self._misses += 1
            return None

        with self._lock:
            best_key, best_score = self._find_similar(query_embedding)

            if best_key is not None and best_score >= self._threshold:
                entry = self._entries[best_key]
                entry.access_count += 1
                self._hits += 1
                logger.debug(
                    "[semantic_cache] HIT: score=%.3f for '%s'",
                    best_score, query[:50],
                )
                return entry.response

            self._misses += 1
        return None

    def put(
        self,
        query: str,
        response: str,
        *,
        ttl_s: float | None = None,
    ) -> None:
        """Store a query-response pair in the semantic cache."""
        self._maybe_cleanup()  # B13: 自动触发过期清理
        if ttl_s is None:
            ttl_s = self._default_ttl

        embedding = self._embed(query)

        with self._lock:
            # B7: 用 uuid4 生成 key，避免并发下 len+timestamp 冲突。
            key = f"sc:{uuid.uuid4().hex}"

            if key in self._entries:
                self._order.remove(key)

            self._entries[key] = SemanticCacheEntry(
                query=query,
                response=response,
                embedding=embedding,
                ttl_s=ttl_s,
            )
            self._order.append(key)

            while len(self._entries) > self._max_entries:
                oldest_key = self._order.pop(0)
                self._entries.pop(oldest_key, None)

    def clear(self) -> None:
        """Clear all entries."""
        with self._lock:
            self._entries.clear()
            self._order.clear()

    def stats(self) -> SemanticCacheStats:
        """Get cache statistics."""
        with self._lock:
            total = self._hits + self._misses
            return SemanticCacheStats(
                hits=self._hits,
                misses=self._misses,
                entries=len(self._entries),
                hit_rate=self._hits / total if total > 0 else 0.0,
            )

    def cleanup_expired(self) -> int:
        """Remove expired entries. Returns count removed."""
        now = time.time()
        with self._lock:
            # B13: 记录本次清理时间，供 _maybe_cleanup 节流判断使用。
            self._last_cleanup_time = now
            expired_keys = [
                k for k, v in self._entries.items()
                if v.ttl_s > 0 and now > v.created_at + v.ttl_s
            ]
            for k in expired_keys:
                self._entries.pop(k, None)
                if k in self._order:
                    self._order.remove(k)
            return len(expired_keys)

    def _maybe_cleanup(self) -> None:
        """B13: 自动触发过期清理——距上次清理超过 _cleanup_interval 秒时触发。

        在每次 get/put 操作时调用此方法。CPython 下 float 读取是原子的；
        即使多线程同时通过检查，cleanup_expired 在锁内操作且幂等，
        最多多执行一次，无正确性问题。
        """
        if time.time() - self._last_cleanup_time < self._cleanup_interval:
            return
        removed = self.cleanup_expired()
        if removed > 0:
            logger.debug(
                "[semantic_cache] auto-cleanup removed %d expired entries",
                removed,
            )
