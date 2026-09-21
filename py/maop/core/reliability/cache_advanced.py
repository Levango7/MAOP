"""LRUCache advanced operations mixin — extracted from cache.py.

Physical split, no logic change. Provides LRUCacheAdvancedMixin containing
higher-level cache operations:

  - get_or_compute (SingleFlight stampede protection)
  - invalidate_prefix (bulk invalidation by key prefix)
  - contains / size / keys / stats (query operations)
  - cleanup_expired / warmup (maintenance operations)

The mixin expects the host class (LRUCache) to provide:
  - self._lock, self._store, self._hits, self._misses, self._evictions
  - self._max_size, self._pinned, self._on_evict
  - self._flight_lock, self._flights
  - self.get(key), self.put(key, value, *, ttl_s=...), self.put_null(key, *, ttl_s=...)

Symbols SENTINEL_NULL, CacheStats, _SINGLEFLIGHT_WAIT_TIMEOUT_S are imported
lazily from .cache at call time to avoid a circular import at module load
(cache.py imports this mixin at top level).

Backward compatibility: LRUCache in cache.py inherits this mixin, so all
methods remain accessible on LRUCache instances unchanged.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

# Use the same logger name as cache.py so log records are attributed
# consistently regardless of which module the method physically lives in.
logger = logging.getLogger("maop.core.reliability.cache")


class LRUCacheAdvancedMixin:
    """Advanced operations mixin for LRUCache.

    All methods here are extracted verbatim from the original LRUCache class
    in cache.py. They rely on attributes/methods provided by the host class.
    """

    # ── Bulk operations ───────────────────────────────────────

    def get_or_compute(
        self,
        key: str,
        compute_fn: Any,  # Callable[[], V]
        *,
        ttl_s: float | None = None,
        null_ttl_s: float = 10.0,
    ) -> Any:
        """Get from cache, or compute and cache if missing/expired.

        Implements SingleFlight (stampede protection): if multiple threads
        call get_or_compute with the same key simultaneously, only one
        executes compute_fn; the others wait and reuse the result.

        If compute_fn returns None, the key is null-cached with null_ttl_s
        to prevent penetration on subsequent lookups.

        Parameters
        ----------
        key : str
            Cache key.
        compute_fn : Callable
            Zero-arg function to compute the value on cache miss.
        ttl_s : float | None
            TTL override for the computed value.
        null_ttl_s : float
            TTL for null-cached entries (penetration protection).
        """
        # Lazy import to avoid circular dependency at module load time.
        from .cache import _SINGLEFLIGHT_WAIT_TIMEOUT_S

        # High fix (C-3): loop instead of recursion. Under sustained
        # contention the old code recursed on every wait timeout and could
        # hit RecursionError (~1000 frames). The loop is semantically
        # identical: retry until we either observe a cached value or win
        # the flight registration and compute ourselves.
        while True:
            # First check: fast path (no lock contention)
            value = self.get(key)
            if value is not None:
                return value

            # SingleFlight: ensure only one thread computes for this key
            with self._flight_lock:
                if key in self._flights:
                    # Another thread is computing — wait for it
                    flight_event = self._flights[key]
                else:
                    # We are the first — register our flight
                    flight_event = threading.Event()
                    self._flights[key] = flight_event
                    flight_event = None  # Signal that WE should compute

            if flight_event is None:
                break  # we are the computing thread

            # Wait for the computing thread
            flight_event.wait(timeout=_SINGLEFLIGHT_WAIT_TIMEOUT_S)
            # Now the value should be in cache
            result = self.get(key)
            if result is not None:
                return result
            # Wait timed out or compute failed — loop and try again
            # (may become the computing thread on the next iteration).

        # We are the computing thread
        try:
            value = compute_fn()

            if value is None:
                # Null caching: prevent penetration
                self.put_null(key, ttl_s=null_ttl_s)
                return None

            self.put(key, value, ttl_s=ttl_s)
            return value
        finally:
            # Signal other waiters
            with self._flight_lock:
                event = self._flights.pop(key, None)
            if event is not None:
                event.set()

    def invalidate_prefix(self, prefix: str) -> int:
        """Remove all keys starting with prefix. Returns count removed.

        Complexity: O(n) where n = len(self._store). Scans every key under
        the lock to test ``k.startswith(prefix)``.

        Rationale: LRUCache is sized by ``max_size`` (default 256, typically
        < 1000 entries). At this scale the linear scan is cheap and the
        overhead of maintaining a prefix → keys index (extra memory plus
        per-put/delete bookkeeping) is not justified. If the cache is ever
        resized to tens of thousands of entries or ``invalidate_prefix`` is
        called in a hot path, consider adding an auxiliary trie/prefix index
        and re-evaluating.

        Thread-safety: holds ``self._lock`` for the whole scan+delete, so
        concurrent puts/gets block until invalidation completes. Snapshot
        the keys first to avoid mutating the OrderedDict during iteration
        (which would raise ``RuntimeError``).
        """
        with self._lock:
            # Snapshot keys first: deleting from self._store while iterating
            # it directly raises RuntimeError. list(...) forces a copy.
            keys_to_remove = [k for k in self._store if k.startswith(prefix)]
            for k in keys_to_remove:
                del self._store[k]
            return len(keys_to_remove)

    # ── Query ─────────────────────────────────────────────────

    def contains(self, key: str) -> bool:
        """Check if key exists and is not expired (without updating LRU)."""
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return False
            if entry.expires_at > 0 and time.time() > entry.expires_at:
                del self._store[key]
                return False
            return True

    def size(self) -> int:
        """Current number of entries (including potentially expired)."""
        with self._lock:
            return len(self._store)

    def keys(self) -> list[str]:
        """Return all cache keys (most recent last)."""
        with self._lock:
            return list(self._store.keys())

    def stats(self) -> Any:  # CacheStats
        """Return cache statistics."""
        # Lazy import to avoid circular dependency at module load time.
        from .cache import SENTINEL_NULL, CacheStats

        with self._lock:
            # 原写法 sum(1 for ... if ...) 被 mypy 判为
            # "Generator has incompatible item type int; expected bool"（对
            # sum 重载的误判）。改用等价的计数写法，语义不变。
            null_count = len(
                [e for e in self._store.values() if e.value is SENTINEL_NULL]
            )
            return CacheStats(
                hits=self._hits,
                misses=self._misses,
                evictions=self._evictions,
                size=len(self._store),
                max_size=self._max_size,
                null_entries=null_count,
            )

    # ── Maintenance ───────────────────────────────────────────

    def cleanup_expired(self) -> int:
        """Remove all expired entries. Returns count removed."""
        now = time.time()
        removed = 0
        with self._lock:
            keys_to_remove = [
                k for k, v in self._store.items()
                if v.expires_at > 0 and now > v.expires_at
            ]
            for k in keys_to_remove:
                del self._store[k]
                removed += 1
        return removed

    def warmup(self, entries: dict[str, Any], *, ttl_s: float | None = None) -> int:
        """Pre-populate cache to prevent cold-start avalanche.

        Parameters
        ----------
        entries : dict
            Key-value pairs to pre-load.
        ttl_s : float | None
            TTL for warmup entries (uses jittered TTL).

        Returns
        -------
        int
            Number of entries loaded.
        """
        loaded = 0
        for key, value in entries.items():
            self.put(key, value, ttl_s=ttl_s)
            loaded += 1
        logger.info("[cache] Warmup: %d entries loaded", loaded)
        return loaded