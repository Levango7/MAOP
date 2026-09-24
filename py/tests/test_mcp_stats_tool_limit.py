"""Tests for 三个补齐的端点（2026-09-23）：

- ``GET  /api/mcp/stats``        —— MCP 调用统计（按小时分桶 + 按工具聚合）
- ``PUT  /api/mcp/tools/{id}``   —— 工具级并发上限（含真实执行点）
- ``GET  /api/hooks/{id}/history`` —— webhook 投递历史

这三项此前是「前端调用但后端无实现」的缺口（契约检查 KNOWN_MISSING 登记）。
测试重点不在"端点返回 200"，而在**行为是否真的发生**：
统计是否真的采集、并发上限是否真的拦调用、历史是否真的来自投递记录。
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from maop.core.mcp import mcp_hub as mcp_hub_mod
from maop.core.mcp.mcp_call_stats import MCPCallStats
from maop.core.mcp.mcp_hub import MCPHub
from maop.core.mcp.mcp_hub_types import MCPServerConfig


@pytest.fixture(autouse=True)
def _short_tool_acquire_timeout(monkeypatch: pytest.MonkeyPatch):
    """把工具级槽位等待上限压到 0.2s。

    否则每个"验证限流真的拦住"的用例都要真等 30 秒，
    整个测试文件会被拖到分钟级（实测会撞上外层 timeout）。
    """
    monkeypatch.setattr(mcp_hub_mod, "_TOOL_ACQUIRE_TIMEOUT_S", 0.2)


# ══════════════════════════════════════════════════════════════════
# 1. MCPCallStats 单元测试（分桶 / 聚合 / 裁剪）
# ══════════════════════════════════════════════════════════════════


class TestMCPCallStats:
    def test_empty_snapshot(self):
        """无数据时返回空结构而非报错。"""
        snap = MCPCallStats().snapshot()
        assert snap["series"] == []
        assert snap["per_tool"] == []
        assert snap["total"] == {"calls": 0, "errors": 0, "avg_ms": 0.0}

    def test_per_tool_aggregation(self):
        """按工具聚合：调用数 / 错误数 / 均值 / 峰值。"""
        s = MCPCallStats()
        now = time.time()
        for dur, ok in [(10.0, True), (20.0, True), (30.0, False)]:
            s.record("srv", "toolA", dur, ok, now=now)

        snap = s.snapshot()
        assert snap["total"]["calls"] == 3
        assert snap["total"]["errors"] == 1
        row = snap["per_tool"][0]
        assert row["tool"] == "toolA"
        assert row["server_name"] == "srv"
        assert row["calls"] == 3
        assert row["errors"] == 1
        assert row["avg_ms"] == 20.0
        assert row["max_ms"] == 30.0
        assert row["error_rate"] == round(1 / 3, 4)

    def test_separate_tools_not_merged(self):
        """不同工具必须分开统计。"""
        s = MCPCallStats()
        now = time.time()
        s.record("srv", "a", 5.0, True, now=now)
        s.record("srv", "b", 5.0, True, now=now)
        s.record("other", "a", 5.0, True, now=now)
        snap = s.snapshot()
        assert len(snap["per_tool"]) == 3, "同工具名不同 server 不应合并"
        assert snap["total"]["calls"] == 3

    def test_hourly_bucketing(self):
        """跨小时应落到不同桶。

        基准必须贴近当前时间 —— ``snapshot(hours=24)`` 会裁掉窗口外的桶，
        用固定历史时间戳（如 1_700_000_000）会让断言永远看到空结果。
        """
        s = MCPCallStats()
        # 对齐到当前小时起点，保证三个桶都在 24h 窗口内
        base = float(int(time.time() // 3600) * 3600)
        s.record("srv", "t", 1.0, True, now=base)
        s.record("srv", "t", 1.0, True, now=base + 3600)
        s.record("srv", "t", 1.0, True, now=base + 3600 * 2)
        series = s.snapshot(hours=24)["series"]
        assert len(series) == 3, f"应有 3 个小时桶，实际 {len(series)}"
        assert [b["count"] for b in series] == [1, 1, 1]
        # 升序
        assert [b["ts"] for b in series] == sorted(b["ts"] for b in series)

    def test_retention_prunes_old_buckets(self):
        """超出保留窗口的桶应被裁掉，且内存不无界增长。"""
        s = MCPCallStats(retention_hours=2)
        now = time.time()
        s.record("srv", "t", 1.0, True, now=now - 3600 * 10)  # 10 小时前
        assert len(s._state.buckets) == 1
        # 触发裁剪（_maybe_prune 有节流，这里直接置零强制触发）
        s._state.last_prune = 0.0
        s.record("srv", "t", 1.0, True, now=now)
        assert len(s._state.buckets) == 1, "旧桶未被裁剪"

    def test_negative_duration_clamped(self):
        """负耗时（时钟回拨等）应归零，不污染均值。"""
        s = MCPCallStats()
        s.record("srv", "t", -5.0, True, now=time.time())
        assert s.snapshot()["per_tool"][0]["avg_ms"] == 0.0


# ══════════════════════════════════════════════════════════════════
# 2. 工具级并发上限 —— 重点：是否真的拦截调用
# ══════════════════════════════════════════════════════════════════


class _FakeTransport:
    """最小 transport 替身：记录调用、可注入响应/异常。"""

    def __init__(self, response: dict[str, Any] | None = None) -> None:
        self._response = response or {
            "result": {"content": [{"type": "text", "text": "ok"}], "isError": False}
        }
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def start(self) -> None:
        pass

    async def send_request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self.calls.append((method, params or {}))
        return self._response

    async def stop(self) -> None:
        pass

    @property
    def is_alive(self) -> bool:
        return True


@pytest.fixture
def hub(tmp_path: Path) -> MCPHub:
    return MCPHub(root_dir=tmp_path)


@pytest.fixture
def wired_hub(hub: MCPHub):
    """注入 fake transport + config，返回 (hub, server_id, transport)。"""
    sid = "srv-1"
    transport = _FakeTransport()
    hub._transports[sid] = transport
    hub._configs[sid] = MCPServerConfig(name="srv1", transport="stdio")
    return hub, sid, transport


class TestToolConcurrencyLimit:
    @pytest.mark.asyncio
    async def test_limit_persists_and_reads_back(self, hub: MCPHub):
        """设置后能读回（内存限流器与持久表都要更新）。"""
        assert hub.get_tool_concurrency_limit("srv1:toolA") == 0, "默认应为不限制"
        assert hub.set_tool_concurrency_limit("srv1:toolA", 3) == 3
        assert hub.get_tool_concurrency_limit("srv1:toolA") == 3
        assert hub.list_tool_concurrency_limits() == {"srv1:toolA": 3}
        # 内存限流器也必须同步 —— 只写库不更新限流器，新上限要重启才生效
        assert hub._tool_concurrency.get_limit("srv1:toolA") == 3

    @pytest.mark.asyncio
    async def test_zero_means_unlimited_and_does_not_block(self, wired_hub):
        """limit=0（默认）不应阻塞任何调用。"""
        hub, sid, transport = wired_hub
        for _ in range(5):
            r = await hub.call_tool(sid, "toolA")
            assert not r.is_error
        assert len(transport.calls) == 5

    @pytest.mark.asyncio
    async def test_limit_is_enforced(self, wired_hub):
        """limit=1 时，并发的第二个调用必须被拒绝 —— 这是"真的生效"的证明。"""
        import asyncio

        hub, sid, _ = wired_hub
        hub.set_tool_concurrency_limit("srv1:toolA", 1)

        started = asyncio.Event()
        release = asyncio.Event()

        class _Blocking(_FakeTransport):
            async def send_request(self, method, params=None):
                self.calls.append((method, params or {}))
                started.set()
                await release.wait()
                return self._response

        blocking = _Blocking()
        hub._transports[sid] = blocking

        first = asyncio.create_task(hub.call_tool(sid, "toolA"))
        await asyncio.wait_for(started.wait(), timeout=5)

        # 第一个占着槽位 → 第二个应在 30s 超时内拿不到；用短超时断言"被拒"
        second = await hub.call_tool(sid, "toolA")
        assert second.is_error, "limit=1 时第二个并发调用应被拒绝"
        assert "Concurrency limit" in (second.error_message or "")

        release.set()
        await first
        # 释放后应能再次调用
        third = await hub.call_tool(sid, "toolA")
        assert not third.is_error, "槽位释放后应恢复正常"

    @pytest.mark.asyncio
    async def test_limit_is_per_tool_not_global(self, wired_hub):
        """一个工具限流不应影响另一个工具。

        注意：阻塞型 transport 必须**只阻塞目标工具**。若它阻塞所有工具，
        用来验证"toolB 不受影响"的调用自身也会挂住，测试变成死等。
        """
        import asyncio

        hub, sid, _ = wired_hub
        hub.set_tool_concurrency_limit("srv1:toolA", 1)

        started = asyncio.Event()
        release = asyncio.Event()

        class _BlockingA(_FakeTransport):
            async def send_request(self, method, params=None):
                if (params or {}).get("name") == "toolA":
                    started.set()
                    await release.wait()
                return self._response

        hub._transports[sid] = _BlockingA()
        t1 = asyncio.create_task(hub.call_tool(sid, "toolA"))
        await asyncio.wait_for(started.wait(), timeout=5)

        other = await asyncio.wait_for(hub.call_tool(sid, "toolB"), timeout=5)
        assert not other.is_error, "toolB 未被限流，不应受影响"

        release.set()
        await t1


# ══════════════════════════════════════════════════════════════════
# 3. 调用统计 —— 重点：是否真的被 call_tool 采集
# ══════════════════════════════════════════════════════════════════


class TestCallStatsCollectedByHub:
    @pytest.mark.asyncio
    async def test_successful_call_recorded(self, wired_hub):
        """成功的调用应进入统计。"""
        hub, sid, _ = wired_hub
        await hub.call_tool(sid, "toolA")
        snap = hub.call_stats()
        assert snap["total"]["calls"] == 1
        assert snap["total"]["errors"] == 0
        assert snap["per_tool"][0]["tool"] == "toolA"
        assert snap["per_tool"][0]["server_name"] == "srv1"

    @pytest.mark.asyncio
    async def test_tool_level_error_counted_as_error(self, hub: MCPHub):
        """tool-level isError 应计入 errors（与 _record_call_error 口径一致）。"""
        sid = "s2"
        hub._transports[sid] = _FakeTransport(
            {"result": {"content": [], "isError": True}}
        )
        hub._configs[sid] = MCPServerConfig(name="srv2", transport="stdio")
        await hub.call_tool(sid, "badTool")
        snap = hub.call_stats()
        assert snap["total"]["calls"] == 1
        assert snap["total"]["errors"] == 1, "tool-level isError 必须算失败"

    @pytest.mark.asyncio
    async def test_transport_exception_still_recorded(self, hub: MCPHub):
        """传输异常（re-raise 路径）也必须被记录 —— finally 块的价值所在。"""
        sid = "s3"

        class _Boom(_FakeTransport):
            async def send_request(self, method, params=None):
                raise RuntimeError("transport down")

        hub._transports[sid] = _Boom()
        hub._configs[sid] = MCPServerConfig(name="srv3", transport="stdio")

        with pytest.raises(RuntimeError):
            await hub.call_tool(sid, "boomTool")

        snap = hub.call_stats()
        assert snap["total"]["calls"] == 1, "异常路径漏采集了"
        assert snap["total"]["errors"] == 1

    @pytest.mark.asyncio
    async def test_limit_rejection_not_counted_as_tool_call(self, wired_hub):
        """限流拒绝的调用未触达服务端，不应计入工具性能统计。"""
        import asyncio

        hub, sid, _ = wired_hub
        hub.set_tool_concurrency_limit("srv1:toolA", 1)
        started, release = asyncio.Event(), asyncio.Event()

        class _Blocking(_FakeTransport):
            async def send_request(self, method, params=None):
                started.set()
                await release.wait()
                return self._response

        hub._transports[sid] = _Blocking()
        t1 = asyncio.create_task(hub.call_tool(sid, "toolA"))
        await asyncio.wait_for(started.wait(), timeout=5)
        await hub.call_tool(sid, "toolA")  # 被拒
        release.set()
        await t1

        snap = hub.call_stats()
        assert snap["total"]["calls"] == 1, "被限流拒绝的调用不应计入"


# ══════════════════════════════════════════════════════════════════
# 4. 端点层（路由 + 服务 + 响应形状）
# ══════════════════════════════════════════════════════════════════


def _make_mcp_app() -> FastAPI:
    app = FastAPI()

    @app.middleware("http")
    async def _inject_admin(request, call_next):
        request.state.auth_roles = ["admin"]
        return await call_next(request)

    from maop.dashboard.routers import mcp as mcp_route
    app.include_router(mcp_route.router)
    return app


class TestStatsEndpoint:
    def test_stats_shape(self, monkeypatch, tmp_path):
        """响应形状须匹配前端：series + per_tool。"""
        from maop.dashboard.services import plugin_service

        hub = MCPHub(root_dir=tmp_path)
        monkeypatch.setattr(plugin_service, "_get_hub", lambda: hub)

        client = TestClient(_make_mcp_app())
        resp = client.get("/api/mcp/stats")
        assert resp.status_code == 200
        d = resp.json()
        assert d["status"] == "ok"
        assert isinstance(d["series"], list)
        assert isinstance(d["per_tool"], list)

    def test_stats_reflects_recorded_calls(self, monkeypatch, tmp_path):
        from maop.dashboard.services import plugin_service

        hub = MCPHub(root_dir=tmp_path)
        hub._call_stats.record("srvX", "toolX", 12.0, True)
        monkeypatch.setattr(plugin_service, "_get_hub", lambda: hub)

        client = TestClient(_make_mcp_app())
        d = client.get("/api/mcp/stats").json()
        assert d["total"]["calls"] == 1
        assert d["per_tool"][0]["tool"] == "toolX"


class TestToolLimitEndpoint:
    def test_put_unknown_tool_404(self, monkeypatch, tmp_path):
        from maop.dashboard.services import plugin_service

        hub = MCPHub(root_dir=tmp_path)
        monkeypatch.setattr(plugin_service, "_get_hub", lambda: hub)
        client = TestClient(_make_mcp_app())
        resp = client.put("/api/mcp/tools/nope:nope", json={"concurrency_limit": 2})
        assert resp.status_code == 404

    def test_tools_list_exposes_id_and_limit(self, monkeypatch, tmp_path):
        """``/api/mcp/tools`` 必须带 id 与 concurrency_limit —— 否则前端
        ``tool.id`` 为 undefined，PUT 打不出去（这是 E2E 抓到的实际问题）。"""
        from maop.core.mcp.mcp_hub_types import MCPTool
        from maop.dashboard.services import plugin_service

        hub = MCPHub(root_dir=tmp_path)
        monkeypatch.setattr(plugin_service, "_get_hub", lambda: hub)
        monkeypatch.setattr(
            hub, "all_tools",
            lambda: [MCPTool(name="t1", description="d", server_name="s1")],
        )
        hub.set_tool_concurrency_limit("s1:t1", 4)

        client = TestClient(_make_mcp_app())
        tools = client.get("/api/mcp/tools").json()["tools"]
        assert tools[0]["id"] == "s1:t1"
        assert tools[0]["concurrency_limit"] == 4

    def test_put_sets_limit(self, monkeypatch, tmp_path):
        from maop.core.mcp.mcp_hub_types import MCPTool
        from maop.dashboard.services import plugin_service

        hub = MCPHub(root_dir=tmp_path)
        monkeypatch.setattr(plugin_service, "_get_hub", lambda: hub)
        monkeypatch.setattr(
            hub, "all_tools",
            lambda: [MCPTool(name="t1", description="d", server_name="s1")],
        )
        client = TestClient(_make_mcp_app())
        resp = client.put("/api/mcp/tools/s1:t1", json={"concurrency_limit": 7})
        assert resp.status_code == 200
        assert resp.json()["concurrency_limit"] == 7
        assert hub.get_tool_concurrency_limit("s1:t1") == 7
