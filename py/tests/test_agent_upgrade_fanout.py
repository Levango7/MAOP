"""批 8 F1 钉测：GET /api/agent/upgrade 有界并发 + TTL 缓存 + 超时收紧。

背景（2026-11-16）：原实现 N agents × (cli --version 5s + pip show 10s) 串行，
CLI 挂起时端点分钟级不返回（前端 30s 超时后版本区永远空态），且客户端断开后
服务端扫描仍跑完挤兑全站。修复＝asyncio.gather + 信号量限流 + TTL 缓存 +
超时收紧。本测试钉住：并发确有发生、并发上界＝信号量、缓存命中零新调用、
超时参数 ≤ 设定值。
"""
from __future__ import annotations

import asyncio
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from maop.dashboard.routers.system import agent_admin as aa

N_AGENTS = 12
CALL_LATENCY_S = 0.15


class _FakeAgentDef:
    def __init__(self, cli: str) -> None:
        self.cli = cli


class _FakeConfig:
    def __init__(self) -> None:
        self.agents = {f"agent{i}": _FakeAgentDef(f"cli{i}") for i in range(N_AGENTS)}
        self.routing = {}


class _FakeLoader:
    def __init__(self, project_root=None) -> None:
        pass

    def load(self) -> _FakeConfig:
        return _FakeConfig()


class _Spy:
    """替代 _deps._run_subprocess：记录并发峰值、次数与超时参数。"""

    def __init__(self) -> None:
        self.active = 0
        self.max_active = 0
        self.calls = 0
        self.timeout_by_kind: dict[str, float] = {}

    async def __call__(self, cmd, timeout=10):
        self.calls += 1
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        kind = "version" if "--version" in cmd else "pip"
        self.timeout_by_kind[kind] = timeout
        try:
            await asyncio.sleep(CALL_LATENCY_S)
        finally:
            self.active -= 1
        if "--version" in cmd:
            return 0, "1.0.0", ""
        return 0, "Version: 2.0.0\n", ""


@pytest.fixture
def ctl(monkeypatch):
    spy = _Spy()
    monkeypatch.setattr(aa, "_agent_list_cache", {})
    monkeypatch.setattr("maop.dashboard.routers.system._deps._run_subprocess", spy)
    monkeypatch.setattr(
        "maop.dashboard.routers.system.agent_admin.shutil.which",
        lambda c: f"/fake/{c}",
    )
    import maop.config.loader as loader_mod

    monkeypatch.setattr(loader_mod, "ConfigLoader", _FakeLoader)
    app = FastAPI()

    @app.middleware("http")
    async def _inject_admin(request, call_next):
        request.state.auth_roles = ["admin"]
        return await call_next(request)

    app.include_router(aa.router)
    return TestClient(app), spy


def test_bounded_parallel_and_speed(ctl):
    client, spy = ctl
    t0 = time.monotonic()
    data = client.get("/api/agent/upgrade").json()
    elapsed = time.monotonic() - t0
    assert data["status"] == "ok"
    assert len(data["agents"]) == N_AGENTS
    # 并行确实发生（>1），且不超过信号量上界
    assert spy.max_active > 1
    assert spy.max_active <= aa._AGENT_SCAN_CONCURRENCY
    # 串行下限＝24 calls × 0.15s ＝ 3.6s；并行版须显著更快
    assert elapsed < N_AGENTS * 2 * CALL_LATENCY_S * 0.75
    # 超时收紧：--version ≤3s，pip show ≤5s（原 5s/10s）
    assert spy.timeout_by_kind.get("version", 99.0) <= 3.0
    assert spy.timeout_by_kind.get("pip", 99.0) <= 5.0


def test_ttl_cache_second_call_no_scan(ctl):
    client, spy = ctl
    client.get("/api/agent/upgrade")
    calls_after_first = spy.calls
    assert calls_after_first > 0
    client.get("/api/agent/upgrade")
    assert spy.calls == calls_after_first  # 命中缓存：零新子进程
