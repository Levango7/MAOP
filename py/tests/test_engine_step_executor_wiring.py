"""钉测：engine 端点的 step_executor 注入与 Dispatcher 配置加载。

回归缺陷（swarm 编排产品化实测发现）：
1. ``/api/engine/run`` 与 ``/api/engine/run-distributed`` 此前 ``_get_engine()``
   不传 ``step_executor``，engine 无执行器，agent step 全部 failed
   （「No step executor configured for agent step」）。
2. ``_default_step_executor`` 此前 ``Dispatcher()`` 空配置，所有 agent 报
   「not found in config or registry」；必须 ``Dispatcher(ConfigLoader().load())``。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from maop.dashboard.routers import engine_router
from maop.dashboard.services import execution_service


class _Result:
    def __init__(self) -> None:
        self.success = True
        self.trace_id = "tr-wiring"

    def model_dump(self) -> dict[str, Any]:
        return {"success": True}


class _FakeEngine:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def run(self, **kwargs: Any) -> _Result:
        self.calls.append(kwargs)
        return _Result()


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()

    @app.middleware("http")
    async def _inject_admin(request, call_next):
        request.state.auth_roles = ["admin"]
        return await call_next(request)

    app.include_router(engine_router.router)
    return TestClient(app)


@pytest.fixture
def engine_spy(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """记录 _get_engine 收到的 kwargs，返回假 engine。"""
    seen: dict[str, Any] = {}
    fake = _FakeEngine()

    def _spy(*args: Any, **kwargs: Any) -> _FakeEngine:
        seen.update(kwargs)
        return fake

    monkeypatch.setattr(engine_router, "_get_engine", _spy)
    seen["engine"] = fake
    return seen


def test_run_endpoint_injects_step_executor(client: TestClient, engine_spy: dict) -> None:
    resp = client.post("/api/engine/run", json={"id": "s1", "agent": "kilo", "task": "t"})
    assert resp.status_code == 200
    assert engine_spy.get("step_executor") is execution_service._default_step_executor


def test_run_distributed_injects_step_executor(client: TestClient, engine_spy: dict) -> None:
    resp = client.post(
        "/api/engine/run-distributed",
        json={"steps": [{"id": "s1", "agent": "kilo", "task": "t"}]},
    )
    assert resp.status_code == 200
    assert engine_spy.get("step_executor") is execution_service._default_step_executor


class _FakeDispatcher:
    """记录构造参数；dispatch 返回固定成功结果。"""

    seen_config: Any = None

    def __init__(self, config: Any = None) -> None:
        _FakeDispatcher.seen_config = config

    async def dispatch(self, **kwargs: Any) -> Any:
        return SimpleNamespace(
            result=SimpleNamespace(stdout="ok-out", exit_code=0, error="", stderr="", ok=True)
        )


def test_default_step_executor_loads_config(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    from maop.config import loader as loader_mod
    from maop.delegate import dispatch_core as dc_mod

    sentinel_config = {"agents": {"kilo": {"cli": "kilo"}}}
    monkeypatch.setattr(loader_mod.ConfigLoader, "load", lambda self: sentinel_config)
    monkeypatch.setattr(dc_mod, "Dispatcher", _FakeDispatcher)

    step = SimpleNamespace(agent="kilo", task="hello")
    ns = asyncio.run(
        execution_service._default_step_executor(step, context={}, workdir="", trace_id="t1")
    )
    assert _FakeDispatcher.seen_config is sentinel_config, (
        "Dispatcher 必须收到 ConfigLoader().load() 的配置（空配置 = 所有 agent not found）"
    )
    assert ns.output == "ok-out"
    assert ns.exit_code == 0
    assert ns.error == ""


def test_default_step_executor_failure_returns_error_ns(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    from maop.config import loader as loader_mod
    from maop.delegate import dispatch_core as dc_mod

    class _Boom:
        def __init__(self, config: Any = None) -> None:
            pass

        async def dispatch(self, **kwargs: Any) -> Any:
            raise RuntimeError("cli missing")

    monkeypatch.setattr(loader_mod.ConfigLoader, "load", lambda self: {})
    monkeypatch.setattr(dc_mod, "Dispatcher", _Boom)

    step = SimpleNamespace(agent="ghost", task="x")
    ns = asyncio.run(
        execution_service._default_step_executor(step, context={}, workdir="", trace_id="t2")
    )
    assert ns.exit_code == 1
    assert ns.output == ""
    assert ns.error  # 有错误描述但不泄漏内部异常细节
