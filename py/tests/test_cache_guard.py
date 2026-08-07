"""Tests for MAOP.core.cache_guard — penetration/breakdown/avalanche protection."""

from __future__ import annotations

import threading
import time

import pytest

from maop.core.reliability.cache import (
    CacheGuard,
    CacheGuardConfig,
    CacheGuardStats,
    SingleFlight,
)


class TestCacheGuardConfig:
    def test_defaults(self):
        c = CacheGuardConfig()
        assert c.null_ttl == 30.0
        assert c.null_value_marker == "__NULL__"
        assert c.ttl_jitter_ratio == 0.1
        assert c.enable_null_cache is True
        assert c.enable_jitter is True
        assert c.enable_singleflight is True


class TestCacheGuardStats:
    def test_defaults(self):
        s = CacheGuardStats()
        assert s.hits == 0
        assert s.misses == 0
        assert s.null_hits == 0
        assert s.singleflight_waits == 0
        assert s.singleflight_dedups == 0
        assert s.ttl_jitters == 0


class TestSingleFlight:
    def test_first_call_executes(self):
        sf = SingleFlight()
        result, was_dedup = sf.execute("k", lambda: 42)
        assert result == 42
        assert was_dedup is False

    def test_exception_propagates(self):
        sf = SingleFlight()

        def boom():
            raise ValueError("boom")

        with pytest.raises(ValueError, match="boom"):
            sf.execute("k", boom)

    def test_concurrent_dedup(self):
        sf = SingleFlight()
        call_count = 0
        count_lock = threading.Lock()

        def slow_fn():
            nonlocal call_count
            time.sleep(0.05)
            with count_lock:
                call_count += 1
            return "result"

        results = []
        threads = []

        def worker():
            r, _ = sf.execute("shared", slow_fn)
            results.append(r)

        for _ in range(5):
            t = threading.Thread(target=worker)
            threads.append(t)
            t.start()
        for t in threads:
            t.join()

        # Only one thread should have actually executed
        assert call_count == 1
        assert all(r == "result" for r in results)

    def test_different_keys_independent(self):
        sf = SingleFlight()
        r1, d1 = sf.execute("k1", lambda: 1)
        r2, d2 = sf.execute("k2", lambda: 2)
        assert r1 == 1 and d1 is False
        assert r2 == 2 and d2 is False


class TestCacheGuardBasic:
    def test_get_loads_on_miss(self):
        cg = CacheGuard()
        result = cg.get("k", lambda: "value")
        assert result == "value"
        assert cg.stats().misses == 1

    def test_get_hits_on_second_call(self):
        cg = CacheGuard()
        cg.get("k", lambda: "value")
        result = cg.get("k", lambda: "should-not-load")
        assert result == "value"
        assert cg.stats().hits == 1

    def test_get_with_ttl_caches(self):
        cg = CacheGuard()
        cg.get("k", lambda: "v", ttl=100)
        result = cg.get("k", lambda: "other")
        assert result == "v"

    def test_get_expired_entry_reloads(self):
        cg = CacheGuard(config=CacheGuardConfig(enable_jitter=False))
        cg.get("k", lambda: "v1", ttl=0.01)
        time.sleep(0.05)
        result = cg.get("k", lambda: "v2")
        assert result == "v2"

    def test_null_value_caching(self):
        cg = CacheGuard()
        call_count = 0

        def loader():
            nonlocal call_count
            call_count += 1

        cg.get("k", loader, ttl=100)
        cg.get("k", loader, ttl=100)
        # Second call should hit null cache, not call loader
        assert call_count == 1
        assert cg.stats().null_hits == 1

    def test_null_cache_disabled(self):
        cfg = CacheGuardConfig(enable_null_cache=False, enable_jitter=False, enable_singleflight=False)
        cg = CacheGuard(config=cfg)
        call_count = 0

        def loader():
            nonlocal call_count
            call_count += 1

        cg.get("k", loader, ttl=100)
        cg.get("k", loader, ttl=100)
        # Without null cache, None is still cached as a regular value
        assert call_count == 1


class TestCacheGuardInvalidate:
    def test_invalidate_existing(self):
        cg = CacheGuard()
        cg.get("k", lambda: "v")
        assert cg.invalidate("k") is True
        # Next get should miss
        result = cg.get("k", lambda: "new")
        assert result == "new"

    def test_invalidate_nonexistent(self):
        cg = CacheGuard()
        assert cg.invalidate("nope") is False

    def test_invalidate_pattern(self):
        cg = CacheGuard()
        cg.get("user:1", lambda: 1)
        cg.get("user:2", lambda: 2)
        cg.get("post:1", lambda: 3)
        count = cg.invalidate_pattern("user:")
        assert count == 2
        # user keys gone, post key remains
        assert cg.get("post:1", lambda: 99) == 3

    def test_invalidate_pattern_no_match(self):
        cg = CacheGuard()
        cg.get("a", lambda: 1)
        assert cg.invalidate_pattern("z") == 0


class TestCacheGuardStatsMethods:
    def test_stats_returns_copy(self):
        cg = CacheGuard()
        cg.get("k", lambda: "v")  # miss
        cg.get("k", lambda: "v")  # hit
        s1 = cg.stats()
        assert s1.hits == 1
        cg.get("k", lambda: "v")  # hit
        s2 = cg.stats()
        assert s2.hits == 2

    def test_clear(self):
        cg = CacheGuard()
        cg.get("k", lambda: "v")
        cg.clear()
        result = cg.get("k", lambda: "new")
        assert result == "new"


class TestCacheGuardTTLJitter:
    def test_jitter_applied(self):
        cfg = CacheGuardConfig(enable_jitter=True, enable_singleflight=False)
        cg = CacheGuard(config=cfg)
        cg.get("k", lambda: "v", ttl=100)
        assert cg.stats().ttl_jitters == 1

    def test_jitter_disabled(self):
        cfg = CacheGuardConfig(enable_jitter=False, enable_singleflight=False)
        cg = CacheGuard(config=cfg)
        cg.get("k", lambda: "v", ttl=100)
        assert cg.stats().ttl_jitters == 0


class TestCacheGuardSingleFlight:
    def test_singleflight_disabled(self):
        cfg = CacheGuardConfig(enable_singleflight=False)
        cg = CacheGuard(config=cfg)
        assert cg._sf is None
        result = cg.get("k", lambda: "v")
        assert result == "v"

    def test_singleflight_dedup_counted(self):
        cg = CacheGuard()
        call_count = 0
        threading.Event()

        def slow_loader():
            nonlocal call_count
            call_count += 1
            time.sleep(0.05)
            return "loaded"

        results = []
        threads = []

        def worker():
            r = cg.get("shared", slow_loader, ttl=100)
            results.append(r)

        for _ in range(3):
            t = threading.Thread(target=worker)
            threads.append(t)
            t.start()
        for t in threads:
            t.join()

        assert all(r == "loaded" for r in results)
        assert cg.stats().singleflight_dedups >= 1


class TestCacheGuardLoaderError:
    def test_loader_exception_propagates(self):
        cg = CacheGuard()

        def boom():
            raise RuntimeError("loader failed")

        with pytest.raises(RuntimeError, match="loader failed"):
            cg.get("k", boom)

    def test_loader_error_not_cached(self):
        cg = CacheGuard()
        call_count = 0

        def loader():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ValueError("first fails")
            return "success"

        with pytest.raises(ValueError):
            cg.get("k", loader)
        result = cg.get("k", loader)
        assert result == "success"
