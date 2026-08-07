"""Tests for MAOP.core.cache — LRU + TTL cache."""

from __future__ import annotations

import time

from maop.core.reliability.cache import LRUCache, get_cache


class TestLRUCache:
    def test_put_and_get(self):
        cache = LRUCache(max_size=10)
        cache.put("key1", "value1")
        assert cache.get("key1") == "value1"

    def test_get_missing_returns_none(self):
        cache = LRUCache(max_size=10)
        assert cache.get("nonexistent") is None

    def test_overwrite_key(self):
        cache = LRUCache(max_size=10)
        cache.put("key1", "v1")
        cache.put("key1", "v2")
        assert cache.get("key1") == "v2"

    def test_lru_eviction(self):
        cache = LRUCache(max_size=3)
        cache.put("a", 1)
        cache.put("b", 2)
        cache.put("c", 3)
        cache.put("d", 4)  # evicts "a"

        assert cache.get("a") is None
        assert cache.get("b") == 2
        assert cache.get("c") == 3
        assert cache.get("d") == 4

    def test_lru_access_updates_order(self):
        cache = LRUCache(max_size=3)
        cache.put("a", 1)
        cache.put("b", 2)
        cache.put("c", 3)

        # Access "a" → moves to end (most recent)
        cache.get("a")

        # Insert "d" → evicts "b" (oldest unused)
        cache.put("d", 4)
        assert cache.get("a") == 1
        assert cache.get("b") is None

    def test_delete(self):
        cache = LRUCache(max_size=10)
        cache.put("key1", "value1")
        assert cache.delete("key1") is True
        assert cache.get("key1") is None
        assert cache.delete("key1") is False

    def test_clear(self):
        cache = LRUCache(max_size=10)
        cache.put("a", 1)
        cache.put("b", 2)
        cache.clear()
        assert cache.size() == 0


class TestTTL:
    def test_ttl_expiration(self):
        cache = LRUCache(max_size=10, default_ttl_s=0.2)
        cache.put("key1", "value1")
        assert cache.get("key1") == "value1"

        time.sleep(0.3)
        assert cache.get("key1") is None

    def test_custom_ttl(self):
        cache = LRUCache(max_size=10)
        cache.put("short", "data", ttl_s=0.1)
        cache.put("long", "data", ttl_s=10.0)

        time.sleep(0.2)
        assert cache.get("short") is None
        assert cache.get("long") == "data"

    def test_zero_ttl_never_expires(self):
        cache = LRUCache(max_size=10, default_ttl_s=0.0)
        cache.put("forever", "data")
        time.sleep(0.1)
        assert cache.get("forever") == "data"

    def test_cleanup_expired(self):
        cache = LRUCache(max_size=10)
        cache.put("exp1", "data", ttl_s=0.1)
        cache.put("exp2", "data", ttl_s=0.1)
        cache.put("keep", "data", ttl_s=100.0)

        time.sleep(0.2)
        removed = cache.cleanup_expired()
        assert removed == 2
        assert cache.size() == 1


class TestGetOrCompute:
    def test_cache_hit(self):
        cache = LRUCache(max_size=10)
        cache.put("key1", 42)
        result = cache.get_or_compute("key1", lambda: 99)
        assert result == 42

    def test_cache_miss_computes(self):
        cache = LRUCache(max_size=10)
        compute_count = 0

        def compute():
            nonlocal compute_count
            compute_count += 1
            return 42

        result = cache.get_or_compute("key1", compute)
        assert result == 42
        assert compute_count == 1

        # Second call should hit cache
        result2 = cache.get_or_compute("key1", compute)
        assert result2 == 42
        assert compute_count == 1  # Not recomputed


class TestInvalidatePrefix:
    def test_invalidate_prefix(self):
        cache = LRUCache(max_size=100)
        cache.put("config:agents", "a")
        cache.put("config:rules", "r")
        cache.put("memory:entries", "m")

        removed = cache.invalidate_prefix("config:")
        assert removed == 2
        assert cache.get("config:agents") is None
        assert cache.get("memory:entries") == "m"


class TestStats:
    def test_hit_miss_stats(self):
        cache = LRUCache(max_size=10)
        cache.put("key1", "value1")

        cache.get("key1")  # hit
        cache.get("key1")  # hit
        cache.get("missing")  # miss

        stats = cache.stats()
        assert stats.hits == 2
        assert stats.misses == 1
        assert stats.hit_rate == 2 / 3

    def test_eviction_stats(self):
        cache = LRUCache(max_size=2)
        cache.put("a", 1)
        cache.put("b", 2)
        cache.put("c", 3)  # evicts "a"

        stats = cache.stats()
        assert stats.evictions == 1
        assert stats.size == 2


class TestNamedCache:
    def test_get_cache_singleton(self):
        c1 = get_cache("test_cache", max_size=50)
        c2 = get_cache("test_cache", max_size=50)
        assert c1 is c2

    def test_different_names_different_caches(self):
        c1 = get_cache("cache_a", max_size=10)
        c2 = get_cache("cache_b", max_size=10)
        assert c1 is not c2


class TestPin:
    def test_pin_existing_key(self):
        cache = LRUCache(max_size=10)
        cache.put("key1", "value1")
        assert cache.pin("key1") is True
        assert cache.is_pinned("key1") is True

    def test_pin_nonexistent_key(self):
        cache = LRUCache(max_size=10)
        assert cache.pin("nope") is False
        assert cache.is_pinned("nope") is False

    def test_unpin(self):
        cache = LRUCache(max_size=10)
        cache.put("key1", "value1")
        cache.pin("key1")
        assert cache.is_pinned("key1") is True
        cache.unpin("key1")
        assert cache.is_pinned("key1") is False

    def test_unpin_nonexistent_is_noop(self):
        cache = LRUCache(max_size=10)
        cache.unpin("nope")

    def test_pinned_keys(self):
        cache = LRUCache(max_size=10)
        cache.put("a", 1)
        cache.put("b", 2)
        cache.pin("a")
        cache.pin("b")
        assert set(cache.pinned_keys()) == {"a", "b"}

    def test_pinned_key_survives_eviction(self):
        cache = LRUCache(max_size=3)
        cache.put("a", 1)
        cache.put("b", 2)
        cache.put("c", 3)
        cache.pin("a")
        cache.put("d", 4)
        assert cache.get("a") == 1
        assert cache.get("b") is None

    def test_all_pinned_no_eviction(self):
        cache = LRUCache(max_size=2)
        cache.put("a", 1)
        cache.put("b", 2)
        cache.pin("a")
        cache.pin("b")
        cache.put("c", 3)
        assert cache.size() == 3

    def test_delete_removes_pin(self):
        cache = LRUCache(max_size=10)
        cache.put("key1", "value1")
        cache.pin("key1")
        cache.delete("key1")
        assert cache.is_pinned("key1") is False

    def test_clear_removes_all_pins(self):
        cache = LRUCache(max_size=10)
        cache.put("a", 1)
        cache.put("b", 2)
        cache.pin("a")
        cache.pin("b")
        cache.clear()
        assert cache.pinned_keys() == []

    def test_unpin_then_evict(self):
        cache = LRUCache(max_size=2)
        cache.put("a", 1)
        cache.put("b", 2)
        cache.pin("a")
        cache.unpin("a")
        cache.put("c", 3)
        assert cache.get("a") is None



# ── t13: on_evict callback ──────────────────────────────────────────


class TestOnEvictCallback:
    """Verify the on_evict callback is invoked with the EVICTED entry."""

    def test_on_evict_invoked_with_evicted_key_and_value(self):
        evicted = []
        cache = LRUCache(max_size=2, on_evict=lambda k, v: evicted.append((k, v)))
        cache.put("a", 1)
        cache.put("b", 2)
        cache.put("c", 3)  # evicts "a" (oldest)

        assert evicted == [("a", 1)]
        assert cache.get("a") is None
        assert cache.get("b") == 2
        assert cache.get("c") == 3

    def test_on_evict_skipped_for_pinned_keys(self):
        evicted = []
        cache = LRUCache(max_size=2, on_evict=lambda k, v: evicted.append((k, v)))
        cache.put("a", 1)
        cache.put("b", 2)
        assert cache.pin("a") is True
        cache.put("c", 3)  # would evict "a" but it's pinned → evicts "b"

        # Pinned "a" survives; "b" was evicted.
        assert ("a", 1) not in evicted
        assert ("b", 2) in evicted
        assert cache.get("a") == 1
        assert cache.get("b") is None

    def test_on_evict_none_default_no_crash(self):
        # Default (no callback) must not crash on eviction.
        cache = LRUCache(max_size=1)  # on_evict=None
        cache.put("a", 1)
        cache.put("b", 2)  # evicts "a"
        assert cache.get("a") is None
        assert cache.get("b") == 2

    def test_on_evict_callback_exception_does_not_break_put(self):
        def bad_callback(k, v):
            raise RuntimeError("callback exploded")

        cache = LRUCache(max_size=1, on_evict=bad_callback)
        cache.put("a", 1)
        # This put evicts "a"; the bad callback raises but is caught.
        cache.put("b", 2)
        # The new entry was still stored despite callback failure.
        assert cache.get("b") == 2
        assert cache.get("a") is None

    def test_on_evict_called_outside_lock(self):
        """Callback must be able to call cache methods without deadlock."""
        observed_size_during_callback = []

        def callback(k, v):
            # Reading size() acquires the cache lock — if on_evict ran
            # inside the lock, this would deadlock. We inspect the SAME
            # cache that's doing the eviction; after eviction the new
            # entry has been inserted, so size == 1.
            observed_size_during_callback.append(cache_with_cb.size())

        cache_with_cb = LRUCache(max_size=1, on_evict=callback)
        cache_with_cb.put("a", 1)
        cache_with_cb.put("b", 2)  # evicts "a" → callback sees size==1

        assert observed_size_during_callback == [1]
