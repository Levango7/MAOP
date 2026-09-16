"""Cache guard — penetration/breakdown/avalanche protection.

Extracted from cache.py (physical split, no logic change) to keep each
module focused and under 500 lines.

Provides:
  - CacheGuardConfig / CacheGuardStats: pydantic configuration & stats models
  - SingleFlight: deduplicate concurrent calls per key (breakdown protection)
  - CacheGuard: wraps a cache-like object with three-protection

Backward compatibility: cache.py re-exports all public symbols from this
module, so ``from maop.core.reliability.cache import CacheGuard`` continues
to work.
"""

from __future__ import annotations

import random
import threading
import time
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel


class CacheGuardConfig(BaseModel):
    """Configuration for cache guard."""
    null_ttl: float = 30.0           # TTL for null-value cache entries (penetration)
    null_value_marker: str = "__NULL__"  # Sentinel for cached nulls
    ttl_jitter_ratio: float = 0.1    # +/- 10% jitter on TTL (avalanche)
    singleflight_timeout: float = 30.0  # Max wait for SingleFlight (breakdown)
    enable_null_cache: bool = True   # Enable null-value caching
    enable_jitter: bool = True       # Enable TTL jitter
    enable_singleflight: bool = True  # Enable SingleFlight


class CacheGuardStats(BaseModel):
    """Statistics for cache guard."""
    hits: int = 0
    misses: int = 0
    null_hits: int = 0        # Null-value cache hits (penetration prevented)
    singleflight_waits: int = 0  # Times a request waited for SingleFlight
    singleflight_dedups: int = 0  # Times a duplicate request was deduplicated
    ttl_jitters: int = 0      # Times TTL jitter was applied


class SingleFlight:
    """Ensure only one caller executes a function for a given key at a time.

    Other callers wait and receive the same result.
    """

    def __init__(self, timeout: float = 30.0):
        self._timeout = timeout
        self._locks: dict[str, threading.Event] = {}
        self._results: dict[str, Any] = {}
        self._errors: dict[str, Exception] = {}
        self._mutex = threading.Lock()

    def execute(
        self,
        key: str,
        fn: Callable[[], Any],
    ) -> tuple[Any, bool]:
        """Execute fn for key, deduplicating concurrent calls.

        Returns (result, was_dedup) where was_dedup=True if this call
        waited for another caller's result.
        """
        with self._mutex:
            if key in self._locks:
                event = self._locks[key]
                need_wait = True
            else:
                self._results.pop(key, None)
                self._errors.pop(key, None)
                event = threading.Event()
                self._locks[key] = event
                need_wait = False

        if need_wait:
            return self._wait(key, event)

        try:
            result = fn()
            with self._mutex:
                self._results[key] = result
                event.set()
            return result, False
        except Exception as e:
            with self._mutex:
                self._errors[key] = e
                event.set()
            raise
        finally:
            with self._mutex:
                self._locks.pop(key, None)

    def _wait(self, key: str, event: threading.Event) -> tuple[Any, bool]:
        """Wait for the executing caller to finish.

        Raises
        ------
        TimeoutError
            If the wait exceeds ``self._timeout`` and the executing caller
            has not yet set the event. Previously returned ``(None, True)``
            which was ambiguous — the caller could not distinguish "the
            computed result is genuinely None" from "wait timed out".
        """
        if not event.wait(timeout=self._timeout):
            raise TimeoutError(
                f"SingleFlight wait timed out after {self._timeout}s for key={key!r}"
            )

        with self._mutex:
            if key in self._errors:
                raise self._errors[key]
            result = self._results.get(key)

        return result, True


class CacheGuard:
    """Cache guard with penetration/breakdown/avalanche protection.

    Wraps a cache-like object (dict or MAOP.core.cache) with:
      - Null-value caching (penetration)
      - SingleFlight deduplication (breakdown)
      - TTL jitter (avalanche)
    """

    def __init__(
        self,
        cache: dict[str, Any] | None = None,
        config: CacheGuardConfig | None = None,
    ):
        # B8: 默认使用 LRUCache(max_size=1000) 替代裸 dict，防止无限制增长。
        # 调用方仍可显式传入自定义 cache（dict 或 LRUCache）。
        # Lazy import to avoid circular dependency: cache.py re-exports
        # CacheGuard from this module at import time.
        if cache is not None:
            self._cache: Any = cache
        else:
            from .cache import LRUCache
            self._cache = LRUCache(max_size=1000)
        self._config = config or CacheGuardConfig()
        self._stats = CacheGuardStats()
        self._sf = SingleFlight(
            timeout=self._config.singleflight_timeout,
        ) if self._config.enable_singleflight else None
        self._lock = threading.Lock()

    def get(
        self,
        key: str,
        loader: Callable[[], Any],
        *,
        ttl: float | None = None,
    ) -> Any:
        """Get a value from cache, loading it if missing.

        Applies all three protections:
          1. Check cache (including null-value entries)
          2. SingleFlight to deduplicate concurrent loads
          3. TTL jitter on store

        Parameters
        ----------
        key : str
            Cache key.
        loader : Callable
            Function to load the value if not in cache.
        ttl : float | None
            Base TTL in seconds. Jitter is applied if enabled.

        Returns
        -------
        Any
            The cached or loaded value. Returns None if the value
            doesn't exist (and caches the null).
        """
        with self._lock:
            if key in self._cache:
                entry = self._cache[key]
                if isinstance(entry, dict) and "expires" in entry:
                    if entry["expires"] is not None and time.time() > entry["expires"]:
                        del self._cache[key]
                    else:
                        value = entry["value"]
                        if value == self._config.null_value_marker:
                            self._stats.null_hits += 1
                            return None
                        self._stats.hits += 1
                        return value
                else:
                    self._stats.hits += 1
                    return entry
            # H-5 fix: _stats.misses 移入锁内更新，避免与并发的 hits/null_hits
            # 更新竞态导致统计偏差。
            # （来源：coding-pattern/python-shared-dict-cache-concurrency-audit-fix-playbook）
            self._stats.misses += 1

        if self._sf is not None:
            result, was_dedup = self._sf.execute(key, lambda: self._load_and_store(key, loader, ttl))
            if was_dedup:
                # L-1 fix: singleflight_dedups 统计计数器移入锁内更新
                with self._lock:
                    self._stats.singleflight_dedups += 1
            return result
        else:
            return self._load_and_store(key, loader, ttl)

    def _load_and_store(
        self,
        key: str,
        loader: Callable[[], Any],
        ttl: float | None,
    ) -> Any:
        """Load a value and store it in cache."""
        value = loader()

        effective_ttl = ttl
        if ttl is not None and self._config.enable_jitter:
            jitter = ttl * self._config.ttl_jitter_ratio * (2 * random.random() - 1)
            effective_ttl = max(1.0, ttl + jitter)
            # P2-12 fix: ttl_jitters 统计计数器原先在锁外更新，与并发的
            # hits/misses/null_hits 更新竞态导致统计偏差。移入锁内。
            # （来源：coding-pattern/python-shared-dict-cache-concurrency-audit-fix-playbook）
            with self._lock:
                self._stats.ttl_jitters += 1

        with self._lock:
            if value is None and self._config.enable_null_cache:
                null_ttl = self._config.null_ttl
                if self._config.enable_jitter:
                    null_ttl *= (1 + self._config.ttl_jitter_ratio * (2 * random.random() - 1))
                self._cache[key] = {
                    "value": self._config.null_value_marker,
                    "expires": time.time() + null_ttl,
                }
            else:
                expires = (time.time() + effective_ttl) if effective_ttl is not None else None
                self._cache[key] = {
                    "value": value,
                    "expires": expires,
                }

        return value

    def invalidate(self, key: str) -> bool:
        """Remove a key from cache. Returns True if it existed."""
        with self._lock:
            if key in self._cache:
                del self._cache[key]
                return True
            return False

    def invalidate_pattern(self, prefix: str) -> int:
        """Invalidate all keys matching a prefix. Returns count removed.

        R4-low notice: This method performs an O(n) full scan of all cache
        keys to match the prefix. For large caches (>10k entries) called at
        high frequency, prefer targeted ``invalidate(key)`` calls for each
        known key instead. The O(n) cost is acceptable for infrequent bulk
        invalidation (e.g., config reload, namespace reset) but not for
        per-request hot paths.
        """
        with self._lock:
            keys_to_remove = [k for k in self._cache if k.startswith(prefix)]
            for k in keys_to_remove:
                del self._cache[k]
            return len(keys_to_remove)

    def stats(self) -> CacheGuardStats:
        """Get cache guard statistics."""
        return self._stats.model_copy()  # type: ignore

    def clear(self) -> None:
        """Clear all cache entries."""
        with self._lock:
            self._cache.clear()