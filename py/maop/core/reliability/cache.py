"""MAOP Cache — LRU + TTL in-memory cache with statistics and three-protection.

Provides a thread-safe, generic cache for:
  - ConfigLoader: avoid re-parsing YAML on every access
  - MemoryStore: hot entry caching to skip SQLite queries
  - Dashboard: TTL-based state caching

Three-protection against cache failures:
  - Penetration (穿透): Null/sentinel caching for non-existent keys
  - Stampede/Breakdown (击穿): SingleFlight — only one compute per key
  - Avalanche (雪崩): Jittered TTL — random spread prevents mass expiry

Usage::

    cache = LRUCache(max_size=100, default_ttl_s=60.0)

    cache.put("config:agents", config_obj)
    cfg = cache.get("config:agents")  # returns config_obj or None

    # With custom TTL
    cache.put("temp:result", data, ttl_s=5.0)

    # SingleFlight: concurrent get_or_compute only runs fn once
    value = cache.get_or_compute("key", expensive_fn, ttl_s=30)

    # Null caching: mark a key as "known absent"
    cache.put_null("user:404", ttl_s=10)
    cache.get("user:404")  # returns SENTINEL_NULL, not None

Module split (physical, no logic change):
  - cache_advanced.py: LRUCacheAdvancedMixin (get_or_compute, invalidate_prefix,
    contains, size, keys, stats, cleanup_expired, warmup)
  - cache_guard.py: CacheGuardConfig, CacheGuardStats, SingleFlight, CacheGuard
  - cache.py (this file): core LRU + pin + dict-like dunder + get_cache,
    re-exports guard symbols for backward compatibility.
"""

from __future__ import annotations

import logging
import random
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, TypeVar

logger = logging.getLogger(__name__)

# M7 fix (Phase R7): SingleFlight 等待超时提取为命名常量
_SINGLEFLIGHT_WAIT_TIMEOUT_S = 30.0

K = TypeVar("K")
V = TypeVar("V")

# ── Sentinel for null caching (penetration protection) ────────

class _SentinelNull:
    """Sentinel value indicating 'key exists but value is null'.

    This prevents cache penetration: repeated queries for non-existent
    keys bypass the cache and hit the backing store every time.
    By caching _SentinelNull, we remember that the key has no value.
    """
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "SENTINEL_NULL"

    def __bool__(self) -> bool:
        return False


SENTINEL_NULL = _SentinelNull()
"""Global sentinel — cache.get() returns this for null-cached keys."""


def is_sentinel_null(value: Any) -> bool:
    """Check if a value is the null sentinel."""
    return value is SENTINEL_NULL


@dataclass
class CacheEntry:
    """Internal cache entry with TTL tracking."""
    value: Any
    expires_at: float  # 0 = never expires
    created_at: float = field(default_factory=time.time)
    access_count: int = 0


@dataclass
class CacheStats:
    """Cache hit/miss statistics."""
    hits: int = 0
    misses: int = 0
    evictions: int = 0
    size: int = 0
    max_size: int = 0
    null_entries: int = 0  # Count of sentinel-null entries

    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return self.hits / total if total > 0 else 0.0


# Import advanced operations mixin. cache_advanced.py does NOT import any
# symbols from this module at top level (it uses lazy imports inside methods),
# so there is no circular import at module load time.
from .cache_advanced import LRUCacheAdvancedMixin


class LRUCache(LRUCacheAdvancedMixin):
    """Thread-safe LRU cache with TTL expiration and three-protection.

    Parameters
    ----------
    max_size : int
        Maximum number of entries. Oldest evicted when full.
    default_ttl_s : float
        Default time-to-live in seconds. 0 = never expires.
    ttl_jitter : float
        Fraction of TTL to add as random jitter (0.0-0.5).
        Prevents cache avalanche by spreading expiry times.
        E.g. ttl_jitter=0.1 means TTL ±10% random spread.
    """

    def __init__(
        self,
        max_size: int = 256,
        default_ttl_s: float = 0.0,
        ttl_jitter: float = 0.1,
        on_evict: Callable[[str, Any], None] | None = None,
    ) -> None:
        self._max_size = max(1, max_size)
        self._default_ttl = default_ttl_s
        self._ttl_jitter = max(0.0, min(0.5, ttl_jitter))
        self._store: OrderedDict[str, CacheEntry] = OrderedDict()
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0
        self._evictions = 0
        self._pinned: set[str] = set()
        # t13: optional eviction observer. Invoked with (key, value) of the
        # EVICTED entry, OUTSIDE the lock, after eviction. Used by
        # ThreeLayerMemory to overflow evicted entries to Episodic Memory.
        self._on_evict = on_evict

        # SingleFlight: tracks in-flight computations by key
        self._flight_lock = threading.Lock()
        self._flights: dict[str, threading.Event] = {}

    # ── TTL jitter helper ─────────────────────────────────────

    def _jittered_ttl(self, ttl_s: float) -> float:
        """Apply random jitter to TTL for avalanche protection.

        Returns TTL * (1 ± jitter), e.g. 60s ± 6s for jitter=0.1.
        """
        if ttl_s <= 0 or self._ttl_jitter <= 0:
            return ttl_s
        spread = ttl_s * self._ttl_jitter
        return ttl_s + random.uniform(-spread, spread)

    # ── Core operations ───────────────────────────────────────

    def get(self, key: str) -> Any | None:
        """Retrieve a value by key.

        Returns SENTINEL_NULL for null-cached keys (penetration protection).
        Returns None if key is missing or expired.
        """
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                self._misses += 1
                return None

            # Check TTL
            if entry.expires_at > 0 and time.time() > entry.expires_at:
                # Expired — remove and count as miss
                del self._store[key]
                self._misses += 1
                return None

            # Hit — move to end (most recently used)
            entry.access_count += 1
            self._store.move_to_end(key)
            self._hits += 1
            return entry.value

    def put(
        self,
        key: str,
        value: Any,
        *,
        ttl_s: float | None = None,
    ) -> None:
        """Store a value with optional TTL override.

        Parameters
        ----------
        key : str
            Cache key.
        value : Any
            Value to cache.
        ttl_s : float | None
            Time-to-live in seconds. None = use default_ttl_s. 0 = never expires.
            TTL is automatically jittered for avalanche protection.

        Notes
        -----
        If the cache is at capacity, the least-recently-used non-pinned entry
        is evicted. If an ``on_evict`` callback was supplied at construction,
        it is invoked with ``(evicted_key, evicted_value)`` AFTER the cache
        lock is released — this avoids reentrancy if the callback inspects
        the cache. Multiple evictions (theoretically possible if pinned keys
        shrink effective capacity) each produce one callback invocation.
        """
        if ttl_s is None:
            ttl_s = self._default_ttl

        # Apply jitter for avalanche protection
        effective_ttl = self._jittered_ttl(ttl_s)
        expires_at = (time.time() + effective_ttl) if effective_ttl > 0 else 0.0

        # t13: collect evicted (key, value) pairs under the lock, then invoke
        # the on_evict callback AFTER releasing the lock to prevent reentrancy.
        evicted_entries: list[tuple[str, Any]] = []

        with self._lock:
            # If key exists, remove first (to re-insert at end)
            if key in self._store:
                del self._store[key]

            # Evict if at capacity (skip pinned keys)
            while len(self._store) >= self._max_size:
                evicted = False
                for k in list(self._store.keys()):
                    if k not in self._pinned:
                        # Capture value before deletion so on_evict gets it.
                        entry = self._store.pop(k)
                        self._evictions += 1
                        evicted_entries.append((k, entry.value))
                        evicted = True
                        break
                if not evicted:
                    break

            self._store[key] = CacheEntry(
                value=value,
                expires_at=expires_at,
            )

        # Invoke eviction observer outside the lock.
        if evicted_entries and self._on_evict is not None:
            for ev_key, ev_value in evicted_entries:
                try:
                    self._on_evict(ev_key, ev_value)
                except Exception:
                    logger.warning(
                        "[cache] on_evict callback failed for key '%s'",
                        ev_key, exc_info=True,
                    )

    def put_null(self, key: str, *, ttl_s: float | None = None) -> None:
        """Cache a null/sentinel value for penetration protection.

        After this call, cache.get(key) returns SENTINEL_NULL instead of None,
        allowing callers to distinguish "key not in cache" from "key known to be null".
        """
        self.put(key, SENTINEL_NULL, ttl_s=ttl_s)

    def delete(self, key: str) -> bool:
        """Remove a key from the cache. Returns True if key existed."""
        with self._lock:
            self._pinned.discard(key)
            if key in self._store:
                del self._store[key]
                return True
            return False

    def pin(self, key: str) -> bool:
        """Pin a key so it is never evicted by LRU or TTL compression.

        Returns True if the key exists and was pinned.
        Pinned keys survive capacity-based eviction and Transform compression.

        High fix (C-1): the number of pinned keys is capped at ``max_size``.
        Without this cap the cache size is unbounded (eviction skips pinned
        keys, so size can grow to len(pinned) + 1 indefinitely). When the cap
        is reached, pin() refuses and returns False with a warning.
        """
        with self._lock:
            if key in self._store:
                if key not in self._pinned and len(self._pinned) >= self._max_size:
                    logger.warning(
                        "[cache] pin('%s') refused: pinned key count reached "
                        "max_size (%d); unpin keys before pinning more",
                        key, self._max_size,
                    )
                    return False
                self._pinned.add(key)
                return True
            return False

    def unpin(self, key: str) -> None:
        """Remove the pin from a key, allowing normal LRU eviction."""
        with self._lock:
            self._pinned.discard(key)

    def is_pinned(self, key: str) -> bool:
        """Check if a key is pinned."""
        with self._lock:
            return key in self._pinned

    def pinned_keys(self) -> list[str]:
        """Return all pinned keys."""
        with self._lock:
            return list(self._pinned)

    def clear(self) -> None:
        """Remove all entries."""
        with self._lock:
            self._store.clear()
            self._pinned.clear()

    # ── dict-like interface (B8: CacheGuard 兼容) ──────────────

    def __contains__(self, key: str) -> bool:
        """Check if key is present and not TTL-expired.

        R4-low fix: previously returned True for expired entries, causing
        CacheGuard consumers to see stale "present" results. Now checks
        expires_at and lazily evicts expired entries.
        """
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return False
            # TTL 过期检查：expires_at > 0 表示有 TTL，已过期则移除并返回 False
            if entry.expires_at > 0 and time.time() > entry.expires_at:
                del self._store[key]
                return False
            return True

    def __getitem__(self, key: str) -> Any:
        """Get value by key. Raises KeyError if missing or TTL-expired.

        R4-low fix: previously returned expired values without checking TTL.
        Now lazily evicts expired entries and raises KeyError on expiry.
        """
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                raise KeyError(key)
            # TTL 过期检查：expires_at > 0 表示有 TTL，已过期则移除并抛 KeyError
            if entry.expires_at > 0 and time.time() > entry.expires_at:
                del self._store[key]
                raise KeyError(key)
            return entry.value

    def __setitem__(self, key: str, value: Any) -> None:
        """Store value without TTL (永不过期), 用于 CacheGuard 自管 TTL 场景。

        M-1 fix: 与 put() 一致，在锁内收集待淘汰项，锁外批量调用 on_evict 回调，
        避免回调重入缓存导致死锁（来源：coding-pattern/lrucache-dict-like-interface-adapter）。
        """
        # t13/M-1: collect evicted (key, value) pairs under the lock, then invoke
        # the on_evict callback AFTER releasing the lock to prevent reentrancy.
        evicted_entries: list[tuple[str, Any]] = []

        with self._lock:
            if key in self._store:
                self._store[key].value = value
                self._store.move_to_end(key)
            else:
                self._store[key] = CacheEntry(value=value, expires_at=0)
                while len(self._store) > self._max_size:
                    # LRU 淘汰：跳过 pinned，淘汰最旧非 pinned 项
                    evicted = False
                    for k in list(self._store.keys()):
                        if k == key:
                            break
                        if k not in self._pinned:
                            evicted_entry = self._store.pop(k)
                            self._evictions += 1
                            evicted_entries.append((k, evicted_entry.value))
                            evicted = True
                            break
                    if not evicted:
                        break

        # Invoke eviction observer outside the lock.
        if evicted_entries and self._on_evict is not None:
            for ev_key, ev_value in evicted_entries:
                try:
                    self._on_evict(ev_key, ev_value)
                except Exception:
                    logger.warning(
                        "[cache] on_evict callback failed for key '%s'",
                        ev_key, exc_info=True,
                    )

    def __delitem__(self, key: str) -> None:
        """Delete by key. Raises KeyError if missing."""
        with self._lock:
            self._pinned.discard(key)
            if key in self._store:
                del self._store[key]
            else:
                raise KeyError(key)

    def __iter__(self):
        """Iterate over keys (snapshot under lock)."""
        with self._lock:
            return iter(list(self._store.keys()))

    def __len__(self) -> int:
        with self._lock:
            return len(self._store)


# ── Global cache registry ────────────────────────────────────

_caches: dict[str, LRUCache] = {}
_caches_lock = threading.Lock()


def get_cache(name: str, *, max_size: int = 256, default_ttl_s: float = 0.0) -> LRUCache:
    """Get or create a named cache singleton.

    Usage::

        config_cache = get_cache("config", max_size=50, default_ttl_s=300)
        memory_cache = get_cache("memory", max_size=1000, default_ttl_s=60)
    """
    with _caches_lock:
        if name not in _caches:
            _caches[name] = LRUCache(max_size=max_size, default_ttl_s=default_ttl_s)
        return _caches[name]


# ── Cache Guard (re-export from cache_guard.py for backward compat) ────
# Physical split: CacheGuardConfig, CacheGuardStats, SingleFlight, CacheGuard
# now live in cache_guard.py. They are re-exported here so that all existing
# ``from maop.core.reliability.cache import CacheGuard`` (and similar) imports
# continue to work without change.
# cache_guard.py imports LRUCache lazily (inside CacheGuard.__init__), so
# there is no circular import at module load time.
from .cache_guard import (  # noqa: E402
    CacheGuard,
    CacheGuardConfig,
    CacheGuardStats,
    SingleFlight,
)

__all__ = [
    "SENTINEL_NULL",
    "CacheEntry",
    "CacheGuard",
    "CacheGuardConfig",
    "CacheGuardStats",
    "CacheStats",
    "LRUCache",
    "SingleFlight",
    "get_cache",
    "is_sentinel_null",
]
