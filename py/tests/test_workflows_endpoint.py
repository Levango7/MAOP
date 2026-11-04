"""M-X2 E2-b 端点契约：``/api/workflows`` 列表 + 提交 + 状态回写。

- 提交走生产入口 ``wiring.run_workflow_sync``（测试 monkeypatch 模块级
  ``_runner`` 缝，零真实 LLM/网络）；状态回写 = ``run_events`` 的
  ``run.workflow.started/completed/failed``，用 sqlite 直查断言；
- admin 门禁沿 test_cost_attribution_endpoint 的注入形态；无注入 app 钉 403。
"""

from __future__ import annotations

import sqlite3
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from maop.core.backends.db_utils import get_db_path
from maop.dashboard.routers import workflows as workflows_router
from maop.maop_plan import PlanStepResult, WorkflowResult


def _fake_config() -> SimpleNamespace:
    return SimpleNamespace(workflows={
        "demo": SimpleNamespace(
            steps=[
                SimpleNamespace(agent="alpha"),
                SimpleNamespace(agent="beta"),
            ],
            description="demo workflow",
        ),
    })


def _make_client(*, admin: bool) -> TestClient:
    app = FastAPI()

    if admin:
        @app.middleware("http")
        async def _inject_admin(request, call_next):
            request.state.auth_roles = ["admin"]
            request.state.auth_identity = "admin"
            return await call_next(request)

    app.include_router(workflows_router.router)
    return TestClient(app)


def _workflow_events(trace_id: str) -> list[str]:
    db = get_db_path("run_events", legacy_fallback="maop")
    with sqlite3.connect(str(db)) as conn:
        rows = conn.execute(
            "SELECT kind FROM run_events WHERE trace_id = ? ORDER BY seq",
            (trace_id,),
        ).fetchall()
    return [r[0] for r in rows]


def test_list_returns_configured_workflows(monkeypatch) -> None:
    monkeypatch.setattr(workflows_router, "_load_config", _fake_config)
    resp = _make_client(admin=True).get("/api/workflows")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["workflows"] == [{
        "name": "demo", "steps": 2, "agents": ["alpha", "beta"],
        "description": "demo workflow",
    }]


def test_list_requires_admin() -> None:
    resp = _make_client(admin=False).get("/api/workflows")
    assert resp.status_code == 403


def test_run_ok_writes_started_and_completed_events(monkeypatch) -> None:
    monkeypatch.setattr(workflows_router, "_load_config", _fake_config)

    def _fake_runner(name, cfg, initial_vars, **kw):
        assert name == "demo"
        assert kw.get("backend") == "temporal"
        return WorkflowResult(
            workflow_name=name, variables=dict(initial_vars),
            steps_total=2, steps_planned=2, steps_completed=1,
            step_results=[
                PlanStepResult(step_index=0, agent="alpha",
                               status="success", output="status=success"),
                PlanStepResult(step_index=1, agent="beta",
                               status="failed", output="status=failed"),
            ],
        )

    monkeypatch.setattr(workflows_router, "_runner", _fake_runner)
    resp = _make_client(admin=True).post(
        "/api/workflows/demo/run",
        json={"initial_vars": {"ticket": "1042"}, "backend": "temporal"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["trace_id"]
    assert body["result"]["steps_total"] == 2
    assert body["result"]["steps_completed"] == 1
    assert body["result"]["step_results"][1]["status"] == "failed"
    # 状态回写闭环：started → completed 两事件落 run_events
    assert _workflow_events(body["trace_id"]) == [
        "run.workflow.started", "run.workflow.completed",
    ]


def test_run_unknown_workflow_errors_without_trace(monkeypatch) -> None:
    monkeypatch.setattr(workflows_router, "_load_config", _fake_config)
    resp = _make_client(admin=True).post(
        "/api/workflows/nope/run", json={}
    )
    assert resp.status_code == 200  # 信封内 error，非 500
    body = resp.json()
    assert body["status"] == "error"
    assert "not found" in body["error"]
    assert body["trace_id"] == ""


def test_run_failure_records_failed_event(monkeypatch) -> None:
    monkeypatch.setattr(workflows_router, "_load_config", _fake_config)

    def _boom(name, cfg, initial_vars, **kw):
        raise RuntimeError("temporal unreachable")

    monkeypatch.setattr(workflows_router, "_runner", _boom)
    resp = _make_client(admin=True).post(
        "/api/workflows/demo/run", json={"backend": "temporal"}
    )
    body = resp.json()
    assert body["status"] == "error"
    assert "temporal unreachable" in body["error"]
    assert body["trace_id"]
    assert _workflow_events(body["trace_id"]) == [
        "run.workflow.started", "run.workflow.failed",
    ]


def test_run_requires_admin() -> None:
    resp = _make_client(admin=False).post(
        "/api/workflows/demo/run", json={}
    )
    assert resp.status_code == 403
