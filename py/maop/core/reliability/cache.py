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

from pydantic import BaseModel

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


class LRUCache:
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
        """Remove all keys starting with prefix. Returns count removed."""
        with self._lock:
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

    def stats(self) -> CacheStats:
        """Return cache statistics."""
        with self._lock:
            null_count = sum(
                1 for e in self._store.values()
                if e.value is SENTINEL_NULL
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


# ── Cache Guard (merged from cache_guard.py) ────────────────


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
        """Wait for the executing caller to finish."""
        event.wait(timeout=self._timeout)

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
        self._cache: dict[str, Any] = cache if cache is not None else {}
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

        self._stats.misses += 1

        if self._sf is not None:
            result, was_dedup = self._sf.execute(key, lambda: self._load_and_store(key, loader, ttl))
            if was_dedup:
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
        """Invalidate all keys matching a prefix. Returns count removed."""
        with self._lock:
            keys_to_remove = [k for k in self._cache if k.startswith(prefix)]
            for k in keys_to_remove:
                del self._cache[k]
            return len(keys_to_remove)

    def stats(self) -> CacheGuardStats:
        """Get cache guard statistics."""
        return self._stats.model_copy()  # type: ignore[no-any-return]

    def clear(self) -> None:
        """Clear all cache entries."""
        with self._lock:
            self._cache.clear()
