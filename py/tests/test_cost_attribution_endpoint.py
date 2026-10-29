"""M-X3 端点切片契约：``/api/cost-attribution`` 聚合与越阈检查。

数据隔离：CostTracker 与 alert db 都走 ``get_db_path``，由 conftest 的
``MAOP_DATA_DIR`` 每测试隔离。admin 注入沿 test_coordination_m1 的
``_make_app`` 形态；无注入的 app 用来钉 403（成本数据 admin-only）。
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from maop.core.cost_tracker import CostTracker, ModelPricing
from maop.dashboard.routers import cost_attribution as cost_router

_PRICING = {"m-test": ModelPricing(prompt_per_1m=2.0, completion_per_1m=4.0)}


def _make_client(*, admin: bool) -> TestClient:
    app = FastAPI()

    if admin:
        @app.middleware("http")
        async def _inject_admin(request, call_next):
            request.state.auth_roles = ["admin"]
            request.state.auth_identity = "admin"
            return await call_next(request)

    app.include_router(cost_router.router)
    return TestClient(app)


def _seed() -> None:
    tracker = CostTracker(pricing=dict(_PRICING))
    tracker.record(
        model="m-test", agent="alpha", prompt_tokens=1000, completion_tokens=500,
        metadata={"phase": "execute"},
    )
    tracker.record(
        model="m-test", agent="beta", prompt_tokens=2000, completion_tokens=1000,
    )


def test_get_attribution_returns_typed_buckets():
    _seed()
    client = _make_client(admin=True)
    resp = client.get("/api/cost-attribution")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    summary = body["summary"]
    assert summary["total"]["cost_usd"] == 0.012
    assert summary["total"]["calls"] == 2
    assert summary["by_agent"]["alpha"]["calls"] == 1
    assert summary["by_phase"]["execute"]["cost_usd"] == 0.004
    # 未带 phase 的那条进 unattributed 桶（与单元口径一致）
    assert summary["by_phase"]["unattributed"]["calls"] == 1


def test_get_attribution_requires_admin():
    _seed()
    client = _make_client(admin=False)
    resp = client.get("/api/cost-attribution")
    assert resp.status_code == 403


def test_check_budget_returns_alerts_without_ingest():
    _seed()
    client = _make_client(admin=True)
    resp = client.post(
        "/api/cost-attribution/check-budget",
        params={"total_budget_usd": 0.011},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert len(body["alerts"]) == 1
    assert body["alerts"][0]["labels"]["alertname"] == "CostBudgetThreshold"
    assert body["alerts"][0]["labels"]["severity"] == "warning"

    # check-budget 只查不落库：alert 通道不应出现新行
    from maop.core.alert_ingest import count_alerts

    before = count_alerts()
    client.post("/api/cost-attribution/check-budget", params={"total_budget_usd": 0.011})
    assert count_alerts() == before


def test_check_budget_requires_admin():
    _seed()
    client = _make_client(admin=False)
    resp = client.post("/api/cost-attribution/check-budget", params={"total_budget_usd": 0.012})
    assert resp.status_code == 403


def test_attribution_window_filters():
    """start_date/end_date 窗口直通聚合：窗口外条目不计入。"""
    tracker = CostTracker(pricing=dict(_PRICING))
    tracker.record(model="m-test", agent="alpha", prompt_tokens=1000, completion_tokens=0)
    client = _make_client(admin=True)
    # 未来窗口：库里没有匹配条目 → 空聚合
    resp = client.get(
        "/api/cost-attribution",
        params={"start_date": "2999-01-01", "end_date": "2999-12-31"},
    )
    assert resp.status_code == 200
    assert resp.json()["summary"]["total"]["calls"] == 0
    # 全量窗口：命中
    resp = client.get("/api/cost-attribution")
    assert resp.json()["summary"]["total"]["calls"] == 1
