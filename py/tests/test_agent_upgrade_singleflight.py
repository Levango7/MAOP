"""批 10 钉测：GET /api/agent/upgrade 请求级 single-flight。

背景（2026-11-19）：批 8 修复引入 gather+信号量后，冷缓存并发仍会各自起一组
gather（semaphore 限总量但放大 N 倍）。批 10 加请求级 single-flight：共享
in-flight，冷缓存并发只算一次。本测试用 6 线程并发打同一 TestClient，钉住
「计算只发生一次、所有请求拿到同一份结果」。
"""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor

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
    def __init__(self) -> None:
        self.calls = 0
        self.active = 0
        self.max_active = 0

    async def __call__(self, cmd, timeout=10):
        self.calls += 1
        self.active += 1
        self.max_active = max(self.max_active, self.active)
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
    monkeypatch.setattr(aa, "_agent_list_inflight", {})
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


def test_cold_cache_concurrent_single_flight(ctl):
    client, spy = ctl
    with ThreadPoolExecutor(max_workers=6) as pool:
        payloads = list(pool.map(lambda _: client.get("/api/agent/upgrade").json(), range(6)))
    # 单飞：6 并发只算一次（12 agents × 2 calls），而非 6 倍
    assert spy.calls == N_AGENTS * 2
    for d in payloads:
        assert d["status"] == "ok"
        assert len(d["agents"]) == N_AGENTS
    # 全部请求拿到同一份结果
    sigs = {
        (d["status"], tuple((r["name"], r["current"], r["latest"]) for r in d["agents"]))
        for d in payloads
    }
    assert len(sigs) == 1
    # 任务结束后 in-flight 已清理
    assert "future" not in aa._agent_list_inflight


def test_second_call_within_ttl_no_scan(ctl):
    client, spy = ctl
    client.get("/api/agent/upgrade")
    calls_after_first = spy.calls
    assert calls_after_first > 0
    client.get("/api/agent/upgrade")
    assert spy.calls == calls_after_first
