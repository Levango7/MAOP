"""Tests for Phase δ-5 — MCP tool result cache + concurrency + RPM limiting.

Covers:
  * :class:`MCPCacheKey` — hash/eq semantics, ``from_arguments`` hashing.
  * :class:`MCPCache` — get/put/clear, TTL expiry, invalidate (by server
    and by server+tool), cacheability filtering (error / ``_mcp_nocache``),
    LRU eviction, stats.
  * :class:`MCPServerConcurrency` — acquire/release, timeout, per-server
    independence, dynamic ``set_limit``, ``get_active_count``.
  * :class:`MCPServerRateLimiter` — check + record, sliding-window expiry,
    per-server RPM independence, dynamic ``set_rpm``, ``get_remaining``.
  * :class:`MCPHub` integration — backward compatibility when no δ-5
    hooks are injected, cache hit skips transport, concurrency limits
    block, rate limiter raises, cache stores only successful results.
  * The five δ-5 metrics in :mod:`maop.core.monitoring`.
"""
from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from maop.core.mcp_cache import MCPCache, MCPCacheEntry, MCPCacheKey, MCPCacheStats
from maop.core.mcp_concurrency import MCPServerConcurrency, MCPServerRateLimiter
from maop.core.mcp_hub import (
    MCPHub,
    MCPRateLimitedError,
    MCPServerConfig,
    ToolResult,
)
from maop.core.monitoring import (
    MAOP_MCP_CACHE_EVICTION_TOTAL,
    MAOP_MCP_CACHE_HIT_TOTAL,
    MAOP_MCP_CACHE_MISS_TOTAL,
    MAOP_MCP_CONCURRENT_ACTIVE,
    MAOP_MCP_RATE_LIMITED_TOTAL,
)


# ─────────────────────────────────────────────────────────────────
# Shared helpers (mirrors the _FakeTransport pattern from
# test_mcp_observability.py / test_mcp_permission_audit.py)
# ─────────────────────────────────────────────────────────────────


class _FakeTransport:
    """Stand-in transport that bypasses real I/O."""

    def __init__(
        self,
        config: MCPServerConfig,
        *,
        response: dict[str, Any] | None = None,
        raise_on_send: Exception | None = None,
        alive: bool = True,
    ) -> None:
        self._config = config
        self._response = response or {
            "result": {"content": [{"type": "text", "text": "ok"}], "isError": False}
        }
        self._raise = raise_on_send
        self._alive = alive
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def start(self) -> None:
        pass

    async def send_request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self.calls.append((method, params or {}))
        if self._raise is not None:
            raise self._raise
        return self._response

    async def stop(self) -> None:
        self._alive = False

    @property
    def is_alive(self) -> bool:
        return self._alive


def _inject_transport(hub: MCPHub, config: MCPServerConfig, transport: _FakeTransport) -> str:
    """Register a fake transport + config directly in the hub's maps."""
    server_id = "fake-server-id"
    hub._transports[server_id] = transport
    hub._configs[server_id] = config
    return server_id


@pytest.fixture
def hub(tmp_path: Path) -> MCPHub:
    return MCPHub(root_dir=tmp_path)


# ═════════════════════════════════════════════════════════════════
# 1. MCPCacheKey
# ═════════════════════════════════════════════════════════════════


class TestMCPCacheKey:
    def test_from_arguments_builds_sha256_hash(self):
        key = MCPCacheKey.from_arguments("srv", "read", {"path": "/x"})
        assert key.server_id == "srv"
        assert key.tool_name == "read"
        # SHA-256 hex digest is 64 chars.
        assert len(key.arguments_hash) == 64
        assert all(c in "0123456789abcdef" for c in key.arguments_hash)

    def test_same_arguments_produce_same_key(self):
        k1 = MCPCacheKey.from_arguments("srv", "read", {"path": "/x", "n": 1})
        k2 = MCPCacheKey.from_arguments("srv", "read", {"n": 1, "path": "/x"})
        # sort_keys=True makes key order irrelevant.
        assert k1 == k2
        assert hash(k1) == hash(k2)

    def test_different_arguments_produce_different_keys(self):
        k1 = MCPCacheKey.from_arguments("srv", "read", {"path": "/x"})
        k2 = MCPCacheKey.from_arguments("srv", "read", {"path": "/y"})
        assert k1 != k2

    def test_different_server_produces_different_key(self):
        k1 = MCPCacheKey.from_arguments("srv1", "read", {"path": "/x"})
        k2 = MCPCacheKey.from_arguments("srv2", "read", {"path": "/x"})
        assert k1 != k2

    def test_different_tool_produces_different_key(self):
        k1 = MCPCacheKey.from_arguments("srv", "read", {"path": "/x"})
        k2 = MCPCacheKey.from_arguments("srv", "write", {"path": "/x"})
        assert k1 != k2

    def test_key_usable_in_dict_and_set(self):
        k1 = MCPCacheKey.from_arguments("srv", "read", {"path": "/x"})
        k2 = MCPCacheKey.from_arguments("srv", "read", {"path": "/x"})
        d = {k1: "value"}
        assert d[k2] == "value"
        s = {k1, k2}
        assert len(s) == 1

    def test_none_arguments_treated_as_empty(self):
        k1 = MCPCacheKey.from_arguments("srv", "read", None)
        k2 = MCPCacheKey.from_arguments("srv", "read", {})
        assert k1 == k2


# ═════════════════════════════════════════════════════════════════
# 2. MCPCache
# ═════════════════════════════════════════════════════════════════


class TestMCPCache:
    def test_put_and_get(self):
        cache = MCPCache(max_entries=10, default_ttl_s=60)
        key = MCPCacheKey.from_arguments("srv", "read", {"path": "/x"})
        cache.put(key, {"content": [{"text": "hello"}], "is_error": False})
        result = cache.get(key)
        assert result is not None
        assert result["content"] == [{"text": "hello"}]
        assert result["is_error"] is False

    def test_get_missing_returns_none(self):
        cache = MCPCache(max_entries=10)
        key = MCPCacheKey.from_arguments("srv", "read", {"path": "/x"})
        assert cache.get(key) is None

    def test_clear(self):
        cache = MCPCache(max_entries=10)
        k = MCPCacheKey.from_arguments("srv", "read", {"path": "/x"})
        cache.put(k, {"content": [], "is_error": False})
        assert cache.get(k) is not None
        cache.clear()
        assert cache.get(k) is None

    def test_ttl_expiration(self):
        cache = MCPCache(max_entries=10, default_ttl_s=0.1)
        k = MCPCacheKey.from_arguments("srv", "read", {"path": "/x"})
        cache.put(k, {"content": [], "is_error": False})
        assert cache.get(k) is not None
        time.sleep(0.15)
        assert cache.get(k) is None

    def test_custom_ttl_overrides_default(self):
        cache = MCPCache(max_entries=10, default_ttl_s=100)
        k_short = MCPCacheKey.from_arguments("srv", "read", {"path": "/short"})
        k_long = MCPCacheKey.from_arguments("srv", "read", {"path": "/long"})
        cache.put(k_short, {"content": [], "is_error": False}, ttl_s=0.1)
        cache.put(k_long, {"content": [], "is_error": False}, ttl_s=10)
        time.sleep(0.2)
        assert cache.get(k_short) is None
        assert cache.get(k_long) is not None

    def test_invalidate_by_server(self):
        cache = MCPCache(max_entries=10)
        k1 = MCPCacheKey.from_arguments("srv1", "read", {"path": "/a"})
        k2 = MCPCacheKey.from_arguments("srv1", "write", {"path": "/b"})
        k3 = MCPCacheKey.from_arguments("srv2", "read", {"path": "/c"})
        for k in (k1, k2, k3):
            cache.put(k, {"content": [], "is_error": False})

        removed = cache.invalidate("srv1")
        assert removed == 2
        assert cache.get(k1) is None
        assert cache.get(k2) is None
        assert cache.get(k3) is not None

    def test_invalidate_by_server_and_tool(self):
        cache = MCPCache(max_entries=10)
        k1 = MCPCacheKey.from_arguments("srv", "read", {"path": "/a"})
        k2 = MCPCacheKey.from_arguments("srv", "write", {"path": "/b"})
        cache.put(k1, {"content": [], "is_error": False})
        cache.put(k2, {"content": [], "is_error": False})

        removed = cache.invalidate("srv", "read")
        assert removed == 1
        assert cache.get(k1) is None
        assert cache.get(k2) is not None

    def test_invalidate_nonexistent_returns_zero(self):
        cache = MCPCache(max_entries=10)
        assert cache.invalidate("no-such-server") == 0

    def test_does_not_cache_error_result(self):
        cache = MCPCache(max_entries=10)
        k = MCPCacheKey.from_arguments("srv", "read", {"path": "/x"})
        cache.put(k, {"content": [], "is_error": True, "error_message": "boom"})
        assert cache.get(k) is None

    def test_does_not_cache_response_with_error_key(self):
        cache = MCPCache(max_entries=10)
        k = MCPCacheKey.from_arguments("srv", "read", {"path": "/x"})
        cache.put(k, {"error": {"message": "fail"}})
        assert cache.get(k) is None

    def test_does_not_cache_mcp_nocache_marker(self):
        cache = MCPCache(max_entries=10)
        k = MCPCacheKey.from_arguments("srv", "read", {"path": "/x"})
        cache.put(k, {"content": [], "is_error": False, "_mcp_nocache": True})
        assert cache.get(k) is None

    def test_is_cacheable_static_method(self):
        assert MCPCache.is_cacheable({"content": [], "is_error": False}) is True
        assert MCPCache.is_cacheable({"is_error": True}) is False
        assert MCPCache.is_cacheable({"isError": True}) is False
        assert MCPCache.is_cacheable({"error": {"message": "x"}}) is False
        assert MCPCache.is_cacheable({"_mcp_nocache": True}) is False
        assert MCPCache.is_cacheable({"content": [], "is_error": False, "_mcp_nocache": False}) is True

    def test_lru_eviction(self):
        cache = MCPCache(max_entries=2, default_ttl_s=0)
        k1 = MCPCacheKey.from_arguments("s", "t", {"i": 1})
        k2 = MCPCacheKey.from_arguments("s", "t", {"i": 2})
        k3 = MCPCacheKey.from_arguments("s", "t", {"i": 3})
        cache.put(k1, {"content": [], "is_error": False})
        cache.put(k2, {"content": [], "is_error": False})
        # Access k1 to make k2 the LRU candidate.
        cache.get(k1)
        cache.put(k3, {"content": [], "is_error": False})
        # k2 should have been evicted (oldest unused).
        assert cache.get(k2) is None
        assert cache.get(k1) is not None
        assert cache.get(k3) is not None

    def test_stats_tracks_hits_misses_size_evictions(self):
        cache = MCPCache(max_entries=2, default_ttl_s=0)
        k1 = MCPCacheKey.from_arguments("s", "t", {"i": 1})
        k2 = MCPCacheKey.from_arguments("s", "t", {"i": 2})
        k3 = MCPCacheKey.from_arguments("s", "t", {"i": 3})

        cache.put(k1, {"content": [], "is_error": False})
        cache.put(k2, {"content": [], "is_error": False})
        cache.get(k1)  # hit
        cache.get(k2)  # hit
        cache.get(MCPCacheKey.from_arguments("s", "t", {"i": 99}))  # miss
        cache.put(k3, {"content": [], "is_error": False})  # evicts k1 or k2

        stats = cache.stats()
        assert isinstance(stats, MCPCacheStats)
        assert stats.hits == 2
        assert stats.misses == 1
        assert stats.size == 2
        assert stats.evictions == 1

    def test_access_count_increments_on_get(self):
        cache = MCPCache(max_entries=10)
        k = MCPCacheKey.from_arguments("s", "t", {"i": 1})
        cache.put(k, {"content": [], "is_error": False})
        # Peek the internal entry to verify access_count.
        with cache._lock:
            entry = cache._store[k]
            assert entry.access_count == 0
        cache.get(k)
        cache.get(k)
        with cache._lock:
            assert cache._store[k].access_count == 2

    def test_entry_is_expired_method(self):
        e1 = MCPCacheEntry(result={"x": 1}, created_at=time.time() - 100, ttl_s=10)
        assert e1.is_expired() is True
        e2 = MCPCacheEntry(result={"x": 1}, created_at=time.time(), ttl_s=100)
        assert e2.is_expired() is False
        e3 = MCPCacheEntry(result={"x": 1}, created_at=time.time() - 9999, ttl_s=0)
        assert e3.is_expired() is False  # ttl_s=0 means never expires


# ═════════════════════════════════════════════════════════════════
# 3. MCPServerConcurrency
# ═════════════════════════════════════════════════════════════════


class TestMCPServerConcurrency:
    def test_acquire_and_release(self):
        cc = MCPServerConcurrency(default_max_concurrent=2)
        assert cc.acquire("srv", timeout_s=1.0) is True
        assert cc.acquire("srv", timeout_s=1.0) is True
        assert cc.get_active_count("srv") == 2
        cc.release("srv")
        assert cc.get_active_count("srv") == 1
        cc.release("srv")
        assert cc.get_active_count("srv") == 0

    def test_acquire_returns_false_on_timeout(self):
        cc = MCPServerConcurrency(default_max_concurrent=1)
        assert cc.acquire("srv", timeout_s=0.1) is True
        # Second acquire should time out since the single slot is held.
        result = cc.acquire("srv", timeout_s=0.1)
        assert result is False
        cc.release("srv")

    def test_per_server_limits_independent(self):
        cc = MCPServerConcurrency(
            default_max_concurrent=1,
            per_server_limits={"a": 1, "b": 2},
        )
        assert cc.acquire("a", timeout_s=0.1) is True
        # Server a is full, but server b still has capacity.
        assert cc.acquire("b", timeout_s=0.1) is True
        assert cc.acquire("b", timeout_s=0.1) is True
        assert cc.acquire("a", timeout_s=0.1) is False
        assert cc.acquire("b", timeout_s=0.1) is False
        cc.release("a")
        cc.release("b")
        cc.release("b")

    def test_get_limit_returns_default(self):
        cc = MCPServerConcurrency(default_max_concurrent=7)
        assert cc.get_limit("unknown") == 7

    def test_get_limit_returns_per_server_override(self):
        cc = MCPServerConcurrency(
            default_max_concurrent=5,
            per_server_limits={"special": 10},
        )
        assert cc.get_limit("special") == 10
        assert cc.get_limit("other") == 5

    def test_set_limit_takes_effect_for_new_acquires(self):
        cc = MCPServerConcurrency(default_max_concurrent=1)
        assert cc.get_limit("srv") == 1
        cc.set_limit("srv", 3)
        assert cc.get_limit("srv") == 3
        # Now we can acquire 3 slots.
        assert cc.acquire("srv", timeout_s=0.1) is True
        assert cc.acquire("srv", timeout_s=0.1) is True
        assert cc.acquire("srv", timeout_s=0.1) is True
        assert cc.get_active_count("srv") == 3
        cc.release("srv")
        cc.release("srv")
        cc.release("srv")

    def test_set_limit_lower_blocks_new_acquires(self):
        cc = MCPServerConcurrency(default_max_concurrent=3)
        # Hold two slots.
        cc.acquire("srv", timeout_s=0.1)
        cc.acquire("srv", timeout_s=0.1)
        # Lower the limit to 1 — active(2) >= limit(1), so new acquires block.
        cc.set_limit("srv", 1)
        assert cc.acquire("srv", timeout_s=0.1) is False
        # Releasing one slot leaves active=1 = limit=1, still at capacity.
        cc.release("srv")
        assert cc.acquire("srv", timeout_s=0.1) is False
        # Releasing the second slot brings active to 0 < limit=1.
        cc.release("srv")
        assert cc.acquire("srv", timeout_s=0.5) is True
        cc.release("srv")

    def test_get_active_count_for_unknown_server_is_zero(self):
        cc = MCPServerConcurrency()
        assert cc.get_active_count("nope") == 0

    def test_release_without_acquire_is_noop(self):
        cc = MCPServerConcurrency(default_max_concurrent=2)
        # Should not raise or go negative.
        cc.release("srv")
        assert cc.get_active_count("srv") == 0

    def test_concurrent_acquire_from_multiple_threads(self):
        """Two threads acquire concurrently; only one gets the single slot,
        the other must wait for the release."""
        cc = MCPServerConcurrency(default_max_concurrent=1)
        results: list[bool] = []
        barrier = threading.Barrier(2)

        def worker():
            barrier.wait()
            got = cc.acquire("srv", timeout_s=2.0)
            results.append(got)
            if got:
                time.sleep(0.1)
                cc.release("srv")

        t1 = threading.Thread(target=worker)
        t2 = threading.Thread(target=worker)
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)
        # Both threads should have eventually acquired (sequentially).
        assert results == [True, True]


# ═════════════════════════════════════════════════════════════════
# 4. MCPServerRateLimiter
# ═════════════════════════════════════════════════════════════════


class TestMCPServerRateLimiter:
    def test_check_allowed_under_limit(self):
        rl = MCPServerRateLimiter(default_rpm=10)
        assert rl.check("srv") is True

    def test_check_false_after_exhausting_quota(self):
        rl = MCPServerRateLimiter(default_rpm=2)
        rl.record("srv")
        rl.record("srv")
        assert rl.check("srv") is False

    def test_check_does_not_consume(self):
        rl = MCPServerRateLimiter(default_rpm=1)
        # check should not count against the quota.
        rl.check("srv")
        rl.check("srv")
        rl.record("srv")
        # Quota was 1, now exhausted.
        assert rl.check("srv") is False

    def test_sliding_window_expiry(self):
        rl = MCPServerRateLimiter(default_rpm=1)
        rl.record("srv")
        assert rl.check("srv") is False
        # Wait for the window to slide past. The window is 60s in
        # production, but we can monkey-patch it for the test.
        original_window = MCPServerRateLimiter.WINDOW_S
        try:
            MCPServerRateLimiter.WINDOW_S = 0.1
            time.sleep(0.15)
            assert rl.check("srv") is True
        finally:
            MCPServerRateLimiter.WINDOW_S = original_window

    def test_per_server_rpm_independent(self):
        rl = MCPServerRateLimiter(
            default_rpm=1,
            per_server_rpm={"a": 1, "b": 3},
        )
        rl.record("a")
        assert rl.check("a") is False  # a exhausted (RPM=1, 1 record)
        assert rl.check("b") is True   # b still has quota (RPM=3, 0 records)
        rl.record("b")
        rl.record("b")
        assert rl.check("b") is True   # b has 2 records, still under RPM=3
        rl.record("b")  # b records 3 calls, reaching RPM=3
        assert rl.check("b") is False  # b now exhausted too

    def test_get_remaining(self):
        rl = MCPServerRateLimiter(default_rpm=5)
        assert rl.get_remaining("srv") == 5
        rl.record("srv")
        assert rl.get_remaining("srv") == 4
        rl.record("srv")
        rl.record("srv")
        rl.record("srv")
        rl.record("srv")
        assert rl.get_remaining("srv") == 0
        # One more record would exceed, but get_remaining floors at 0.
        rl.record("srv")
        assert rl.get_remaining("srv") == 0

    def test_set_rpm_changes_limit(self):
        rl = MCPServerRateLimiter(default_rpm=2)
        rl.record("srv")
        rl.record("srv")
        assert rl.check("srv") is False
        rl.set_rpm("srv", 10)
        assert rl.check("srv") is True
        assert rl.get_remaining("srv") == 8  # 10 - 2 already recorded

    def test_get_remaining_unknown_server_uses_default(self):
        rl = MCPServerRateLimiter(default_rpm=7)
        assert rl.get_remaining("nope") == 7


# ═════════════════════════════════════════════════════════════════
# 5. MCPHub integration
# ═════════════════════════════════════════════════════════════════


class TestMCPHubCacheConcurrencyIntegration:
    async def test_no_injection_preserves_original_behaviour(self, hub: MCPHub):
        """Without δ-5 hooks the call_tool path is unchanged."""
        cfg = MCPServerConfig(name="plain")
        transport = _FakeTransport(cfg)
        sid = _inject_transport(hub, cfg, transport)

        result = await hub.call_tool(sid, "read", {"path": "/x"})
        assert result.is_error is False
        assert len(transport.calls) == 1

        # Second call hits the transport again (no cache).
        await hub.call_tool(sid, "read", {"path": "/x"})
        assert len(transport.calls) == 2

    async def test_cache_hit_skips_transport(self, hub: MCPHub, tmp_path: Path):
        """When a cache is injected, the second identical call is served
        from cache without touching the transport."""
        cache = MCPCache(max_entries=10, default_ttl_s=60)
        hub._cache = cache

        cfg = MCPServerConfig(name="cached")
        transport = _FakeTransport(cfg)
        sid = _inject_transport(hub, cfg, transport)

        # First call: cache miss → transport invoked → result cached.
        r1 = await hub.call_tool(sid, "read", {"path": "/x"})
        assert r1.is_error is False
        assert len(transport.calls) == 1

        # Second call: same args → cache hit → transport NOT invoked.
        r2 = await hub.call_tool(sid, "read", {"path": "/x"})
        assert r2.is_error is False
        assert r2.content == r1.content
        assert len(transport.calls) == 1  # still 1

    async def test_cache_miss_with_different_args_hits_transport(self, hub: MCPHub):
        cache = MCPCache(max_entries=10, default_ttl_s=60)
        hub._cache = cache

        cfg = MCPServerConfig(name="cached")
        transport = _FakeTransport(cfg)
        sid = _inject_transport(hub, cfg, transport)

        await hub.call_tool(sid, "read", {"path": "/a"})
        await hub.call_tool(sid, "read", {"path": "/b"})
        assert len(transport.calls) == 2

    async def test_concurrency_limits_parallel_calls(self, hub: MCPHub):
        """With concurrency=1, a second concurrent call must wait for
        the first to release before reaching the transport."""
        cc = MCPServerConcurrency(default_max_concurrent=1)
        hub._concurrency = cc

        cfg = MCPServerConfig(name="limited")
        # The fake transport's send_request blocks on an event so we
        # can control when the first call finishes.
        release_event = asyncio.Event()

        class _BlockingTransport(_FakeTransport):
            async def send_request(self, method, params=None):
                self.calls.append((method, params or {}))
                await release_event.wait()
                return self._response

        transport = _BlockingTransport(cfg)
        sid = _inject_transport(hub, cfg, transport)

        # Launch two calls concurrently.
        task1 = asyncio.create_task(hub.call_tool(sid, "read", {"i": 1}))
        task2 = asyncio.create_task(hub.call_tool(sid, "read", {"i": 2}))
        # Let the event loop advance so task1 acquires the slot and
        # blocks inside send_request.
        await asyncio.sleep(0.05)
        assert cc.get_active_count(sid) == 1
        # task2 should be blocked on the concurrency acquire.
        assert not task2.done()

        # Release the first call.
        release_event.set()
        r1 = await task1
        assert r1.is_error is False
        # Now task2 can proceed.
        r2 = await task2
        assert r2.is_error is False
        assert len(transport.calls) == 2
        # Slot fully released.
        assert cc.get_active_count(sid) == 0

    async def test_rate_limiter_rejects_call(self, hub: MCPHub):
        """When the rate limiter rejects, MCPRateLimitedError is raised
        and the transport is never invoked."""
        rl = MCPServerRateLimiter(default_rpm=1)
        hub._rate_limiter = rl

        cfg = MCPServerConfig(name="rl-srv")
        transport = _FakeTransport(cfg)
        sid = _inject_transport(hub, cfg, transport)

        # First call: quota=1, allowed → transport invoked, quota recorded.
        r1 = await hub.call_tool(sid, "read", {"i": 1})
        assert r1.is_error is False
        assert len(transport.calls) == 1

        # Second call: quota exhausted → MCPRateLimitedError raised.
        with pytest.raises(MCPRateLimitedError):
            await hub.call_tool(sid, "read", {"i": 2})
        # Transport NOT invoked for the rejected call.
        assert len(transport.calls) == 1

    async def test_cache_stores_only_successful_results(self, hub: MCPHub):
        """Error results from the transport must not be cached."""
        cache = MCPCache(max_entries=10, default_ttl_s=60)
        hub._cache = cache

        cfg = MCPServerConfig(name="err-srv")
        # Transport returns a response-level error.
        transport = _FakeTransport(
            cfg,
            response={"error": {"message": "tool failed"}},
        )
        sid = _inject_transport(hub, cfg, transport)

        r1 = await hub.call_tool(sid, "read", {"path": "/x"})
        assert r1.is_error is True

        # Second call should NOT be served from cache (error not cached).
        r2 = await hub.call_tool(sid, "read", {"path": "/x"})
        assert r2.is_error is True
        assert len(transport.calls) == 2

    async def test_cache_stores_only_isError_false_results(self, hub: MCPHub):
        """Tool-level errors (isError=True) must not be cached."""
        cache = MCPCache(max_entries=10, default_ttl_s=60)
        hub._cache = cache

        cfg = MCPServerConfig(name="iserr-srv")
        transport = _FakeTransport(
            cfg,
            response={"result": {"content": [], "isError": True}},
        )
        sid = _inject_transport(hub, cfg, transport)

        await hub.call_tool(sid, "read", {"path": "/x"})
        await hub.call_tool(sid, "read", {"path": "/x"})
        # Both calls hit the transport since isError=True is not cached.
        assert len(transport.calls) == 2

    async def test_concurrency_released_on_transport_exception(self, hub: MCPHub):
        """If the transport raises, the concurrency slot must still be
        released (try/finally)."""
        cc = MCPServerConcurrency(default_max_concurrent=1)
        hub._concurrency = cc

        cfg = MCPServerConfig(name="boom-srv")
        transport = _FakeTransport(cfg, raise_on_send=RuntimeError("network down"))
        sid = _inject_transport(hub, cfg, transport)

        with pytest.raises(RuntimeError):
            await hub.call_tool(sid, "read", {"i": 1})

        # Slot must have been released.
        assert cc.get_active_count(sid) == 0

        # A subsequent call should be able to acquire the slot.
        transport2 = _FakeTransport(cfg)
        hub._transports[sid] = transport2
        r = await hub.call_tool(sid, "read", {"i": 2})
        assert r.is_error is False
        assert cc.get_active_count(sid) == 0

    async def test_all_three_hooks_combined(self, hub: MCPHub):
        """Smoke test: cache + concurrency + rate limiter together."""
        cache = MCPCache(max_entries=10, default_ttl_s=60)
        cc = MCPServerConcurrency(default_max_concurrent=2)
        rl = MCPServerRateLimiter(default_rpm=10)
        hub._cache = cache
        hub._concurrency = cc
        hub._rate_limiter = rl

        cfg = MCPServerConfig(name="combined")
        transport = _FakeTransport(cfg)
        sid = _inject_transport(hub, cfg, transport)

        # First call: miss → transport → cache store.
        r1 = await hub.call_tool(sid, "read", {"path": "/x"})
        assert r1.is_error is False
        assert len(transport.calls) == 1

        # Second call: cache hit.
        r2 = await hub.call_tool(sid, "read", {"path": "/x"})
        assert r2.is_error is False
        assert len(transport.calls) == 1

        # Different args: miss → transport.
        r3 = await hub.call_tool(sid, "read", {"path": "/y"})
        assert r3.is_error is False
        assert len(transport.calls) == 2

        # Concurrency slot always released.
        assert cc.get_active_count(sid) == 0


# ═════════════════════════════════════════════════════════════════
# 6. Metrics
# ═════════════════════════════════════════════════════════════════


class TestMetrics:
    def test_five_metrics_registered_with_expected_names(self):
        assert MAOP_MCP_CACHE_HIT_TOTAL.name == "MAOP_mcp_cache_hit_total"
        assert MAOP_MCP_CACHE_MISS_TOTAL.name == "MAOP_mcp_cache_miss_total"
        assert MAOP_MCP_CACHE_EVICTION_TOTAL.name == "MAOP_mcp_cache_eviction_total"
        assert MAOP_MCP_CONCURRENT_ACTIVE.name == "MAOP_mcp_concurrent_active"
        assert MAOP_MCP_RATE_LIMITED_TOTAL.name == "MAOP_mcp_rate_limited_total"

    async def test_cache_hit_miss_metrics_increment(self, hub: MCPHub):
        cache = MCPCache(max_entries=10, default_ttl_s=60)
        hub._cache = cache

        cfg = MCPServerConfig(name="metric-srv")
        transport = _FakeTransport(cfg)
        sid = _inject_transport(hub, cfg, transport)

        before_hit = MAOP_MCP_CACHE_HIT_TOTAL.get(labels={"server": "metric-srv"})
        before_miss = MAOP_MCP_CACHE_MISS_TOTAL.get(labels={"server": "metric-srv"})

        # First call: miss.
        await hub.call_tool(sid, "read", {"path": "/x"})
        assert MAOP_MCP_CACHE_MISS_TOTAL.get(labels={"server": "metric-srv"}) == before_miss + 1

        # Second call: hit.
        await hub.call_tool(sid, "read", {"path": "/x"})
        assert MAOP_MCP_CACHE_HIT_TOTAL.get(labels={"server": "metric-srv"}) == before_hit + 1

    def test_cache_eviction_metric_increments_on_lru_eviction(self):
        # Use a fresh cache so eviction count is isolated.
        cache = MCPCache(max_entries=1, default_ttl_s=0)
        before = MAOP_MCP_CACHE_EVICTION_TOTAL.get()
        k1 = MCPCacheKey.from_arguments("s", "t", {"i": 1})
        k2 = MCPCacheKey.from_arguments("s", "t", {"i": 2})
        cache.put(k1, {"content": [], "is_error": False})
        cache.put(k2, {"content": [], "is_error": False})  # evicts k1
        assert MAOP_MCP_CACHE_EVICTION_TOTAL.get() == before + 1

    async def test_concurrent_active_gauge_tracks_acquire_release(self, hub: MCPHub):
        cc = MCPServerConcurrency(default_max_concurrent=2)
        hub._concurrency = cc

        cfg = MCPServerConfig(name="gauge-srv")
        # Transport that blocks until released.
        release = asyncio.Event()

        class _BlockingTransport(_FakeTransport):
            async def send_request(self, method, params=None):
                self.calls.append((method, params or {}))
                await release.wait()
                return self._response

        transport = _BlockingTransport(cfg)
        sid = _inject_transport(hub, cfg, transport)

        task = asyncio.create_task(hub.call_tool(sid, "read", {"i": 1}))
        await asyncio.sleep(0.05)
        # While the call is in-flight, the gauge should be 1.
        assert MAOP_MCP_CONCURRENT_ACTIVE.get(labels={"server": sid}) == 1

        release.set()
        await task
        # After completion, the gauge should be 0.
        assert MAOP_MCP_CONCURRENT_ACTIVE.get(labels={"server": sid}) == 0

    async def test_rate_limited_metric_increments(self, hub: MCPHub):
        rl = MCPServerRateLimiter(default_rpm=1)
        hub._rate_limiter = rl

        cfg = MCPServerConfig(name="rl-metric")
        transport = _FakeTransport(cfg)
        sid = _inject_transport(hub, cfg, transport)

        before = MAOP_MCP_RATE_LIMITED_TOTAL.get(labels={"server": "rl-metric"})
        # First call consumes the quota.
        await hub.call_tool(sid, "read", {"i": 1})
        # Second call is rate-limited.
        with pytest.raises(MCPRateLimitedError):
            await hub.call_tool(sid, "read", {"i": 2})
        assert MAOP_MCP_RATE_LIMITED_TOTAL.get(labels={"server": "rl-metric"}) == before + 1
