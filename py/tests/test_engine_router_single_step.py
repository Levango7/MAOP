"""Tests for ``POST /api/engine/run`` 单步端点的字段透传。

回归缺陷：该端点此前把请求里的 ``type``/``retry``/``timeout``/``on_failure`` 全丢掉
（写死 ``StepType.AGENT``），也接受 ``depends_on`` 却不处理 —— 单节点 DAG 里任何依赖
都不可能满足，静默丢弃会跑出一个"看起来执行了、其实没等依赖"的结果。
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from maop.dashboard.routers import engine_router


class _Result:
    def __init__(self) -> None:
        self.success = True
        self.trace_id = "tr-test"

    def model_dump(self) -> dict[str, Any]:
        return {"success": True}


class _FakeEngine:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def run(self, **kwargs: Any) -> _Result:
        self.calls.append(kwargs)
        return _Result()


@pytest.fixture
def fake_engine(monkeypatch: pytest.MonkeyPatch) -> _FakeEngine:
    engine = _FakeEngine()
    monkeypatch.setattr(engine_router, "_get_engine", lambda *a, **k: engine)
    return engine


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()

    @app.middleware("http")
    async def _inject_admin(request, call_next):
        request.state.auth_roles = ["admin"]
        request.state.auth_identity = "admin"
        return await call_next(request)

    app.include_router(engine_router.router)
    return TestClient(app)


class TestEngineSingleStepPassthrough:
    def test_step_config_reaches_workflow_step(self, client: TestClient, fake_engine: _FakeEngine):
        resp = client.post("/api/engine/run", json={
            "id": "s1",
            "type": "verify",
            "agent": "claude",
            "task": "check",
            "retry": 3,
            "timeout": 45,
            "on_failure": "skip",
        })
        assert resp.status_code == 200
        step = fake_engine.calls[0]["steps"][0]
        assert str(step.type) in {"verify", "StepType.VERIFY"}
        assert (step.retry, step.timeout, step.on_failure) == (3, 45, "skip")
        assert step.agent == "claude"

    def test_unknown_type_falls_back_to_agent(self, client: TestClient, fake_engine: _FakeEngine):
        client.post("/api/engine/run", json={"id": "s2", "type": "nonsense", "task": "t"})
        step = fake_engine.calls[0]["steps"][0]
        assert "agent" in str(step.type).lower()

    def test_depends_on_is_rejected_not_dropped(self, client: TestClient, fake_engine: _FakeEngine):
        resp = client.post("/api/engine/run", json={
            "id": "s3", "task": "t", "depends_on": ["other"],
        })
        assert resp.status_code == 400
        assert "run-distributed" in resp.json()["error"]
        assert fake_engine.calls == []
