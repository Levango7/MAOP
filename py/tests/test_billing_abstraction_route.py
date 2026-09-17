"""Tests for maop.dashboard.routers.billing_abstraction — 计费抽象引擎 API 端点.

覆盖 4 个端点的正常路径 + 错误路径，使用隔离的 tmp_path DB 避免污染主库。
每个测试通过自定义中间件注入 admin 角色，绕过 require_admin 鉴权。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from maop.core.agent.billing.billing_abstraction import BillingEngine
from maop.core.agent.billing.quota_bucket import QuotaBucket, QuotaEntry
from maop.core.agent.registry.agent_catalog import (
    AgentCatalog,
    AgentDescriptor,
    BillingModel,
)
from maop.dashboard.routers import billing_abstraction as billing_route


# ── Fixtures ─────────────────────────────────────────────────────


def _make_app() -> FastAPI:
    """创建临时 app + 注入 admin 角色 + 注册 billing 路由。"""
    app = FastAPI()

    @app.middleware("http")
    async def _inject_admin(request, call_next):
        request.state.auth_roles = ["admin"]
        return await call_next(request)

    app.include_router(billing_route.router)
    return app


@pytest.fixture
def isolated_engine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> BillingEngine:
    """提供隔离 DB 的 BillingEngine 并注入到路由模块单例。"""
    db_path = tmp_path / "billing_test.db"
    catalog = AgentCatalog(db_path=tmp_path / "catalog_test.db")
    quota_bucket = QuotaBucket(db_path=db_path)
    engine = BillingEngine(
        catalog=catalog,
        quota_bucket=quota_bucket,
        db_path=db_path,
    )
    # Service 层提取后，单例由 billing_service 持有；router 暴露
    # _set_billing_engine 转发函数供测试注入隔离实例。
    billing_route._set_billing_engine(engine)
    return engine


@pytest.fixture
def client(isolated_engine: BillingEngine) -> TestClient:
    """TestClient with admin role injected."""
    return TestClient(_make_app())


def _register_agent(engine: BillingEngine, name: str, model: BillingModel, **config: Any) -> None:
    """在 engine 的 catalog 中注册一个 Agent。"""
    desc = AgentDescriptor(
        name=name,
        billing_model=model,
        billing_config=config,
    )
    engine._catalog.register(desc)


# ── 1. POST /api/billing/charge ─────────────────────────────────


class TestBillingCharge:
    def test_charge_free_agent(self, client: TestClient, isolated_engine: BillingEngine):
        """FREE 模式 Agent 计费不消耗额度。"""
        _register_agent(isolated_engine, "free_agent", BillingModel.FREE)
        resp = client.post("/api/billing/charge", json={
            "agent_name": "free_agent",
            "tokens": 100,
            "calls": 1,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["success"] is True
        assert data["record"]["cost_usd"] == 0.0

    def test_charge_per_token_agent(self, client: TestClient, isolated_engine: BillingEngine):
        """PER_TOKEN 模式 Agent 按 token 计费。"""
        _register_agent(isolated_engine, "token_agent", BillingModel.PER_TOKEN,
                        price_per_token=0.001)
        # 需要先设置额度
        isolated_engine._quota.set_quota("token_agent", QuotaEntry(
            free_quota=10000, prepaid_quota=0, postpaid_quota=0,
        ))
        resp = client.post("/api/billing/charge", json={
            "agent_name": "token_agent",
            "tokens": 500,
            "calls": 1,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["record"]["cost_usd"] == pytest.approx(0.5)

    def test_charge_nonexistent_agent(self, client: TestClient):
        """不存在的 Agent 计费失败。"""
        resp = client.post("/api/billing/charge", json={
            "agent_name": "nonexistent",
            "tokens": 100,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        assert "not found" in data["error"]

    def test_charge_missing_agent_name(self, client: TestClient):
        """缺少 agent_name 返回 400。"""
        resp = client.post("/api/billing/charge", json={"tokens": 100})
        assert resp.status_code == 400

    def test_charge_insufficient_quota(self, client: TestClient, isolated_engine: BillingEngine):
        """额度不足时计费失败。"""
        _register_agent(isolated_engine, "paid_agent", BillingModel.PER_TOKEN,
                        price_per_token=0.001)
        isolated_engine._quota.set_quota("paid_agent", QuotaEntry(free_quota=10))
        resp = client.post("/api/billing/charge", json={
            "agent_name": "paid_agent",
            "tokens": 100,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False


# ── 2. GET /api/billing/estimate ────────────────────────────────


class TestBillingEstimate:
    def test_estimate_free_agent(self, client: TestClient, isolated_engine: BillingEngine):
        """FREE 模式估算成本为 0。"""
        _register_agent(isolated_engine, "free_agent", BillingModel.FREE)
        resp = client.get("/api/billing/estimate", params={
            "agent_name": "free_agent",
            "tokens": 1000,
            "calls": 1,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["estimated_cost_usd"] == 0.0

    def test_estimate_per_call_agent(self, client: TestClient, isolated_engine: BillingEngine):
        """PER_CALL 模式估算。"""
        _register_agent(isolated_engine, "call_agent", BillingModel.PER_CALL,
                        price_per_call=0.05)
        resp = client.get("/api/billing/estimate", params={
            "agent_name": "call_agent",
            "tokens": 0,
            "calls": 10,
        })
        assert resp.status_code == 200
        assert resp.json()["estimated_cost_usd"] == pytest.approx(0.5)

    def test_estimate_nonexistent_agent(self, client: TestClient):
        """不存在的 Agent 估算为 0。"""
        resp = client.get("/api/billing/estimate", params={
            "agent_name": "nonexistent",
            "tokens": 100,
        })
        assert resp.status_code == 200
        assert resp.json()["estimated_cost_usd"] == 0.0

    def test_estimate_missing_agent_name(self, client: TestClient):
        """缺少 agent_name 返回 400。"""
        resp = client.get("/api/billing/estimate", params={"tokens": 100})
        assert resp.status_code == 400


# ── 3. GET /api/billing/summary/{agent_name} ────────────────────


class TestBillingSummary:
    def test_summary_empty_agent(self, client: TestClient):
        """无计费记录的 Agent 摘要。"""
        resp = client.get("/api/billing/summary/agent1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["summary"]["total_calls"] == 0
        assert data["summary"]["total_cost_usd"] == 0.0

    def test_summary_after_charge(self, client: TestClient, isolated_engine: BillingEngine):
        """计费后摘要应反映记录。"""
        _register_agent(isolated_engine, "agent1", BillingModel.PER_TOKEN,
                        price_per_token=0.01)
        isolated_engine._quota.set_quota("agent1", QuotaEntry(free_quota=100000))
        client.post("/api/billing/charge", json={
            "agent_name": "agent1", "tokens": 100, "calls": 1,
        })
        resp = client.get("/api/billing/summary/agent1")
        assert resp.status_code == 200
        data = resp.json()["summary"]
        assert data["total_calls"] == 1
        assert data["total_tokens"] == 100


# ── 4. GET /api/billing/records ─────────────────────────────────


class TestBillingRecords:
    def test_records_empty(self, client: TestClient):
        """无记录时返回空列表。"""
        resp = client.get("/api/billing/records")
        assert resp.status_code == 200
        data = resp.json()
        assert data["records"] == []
        assert data["count"] == 0

    def test_records_after_charge(self, client: TestClient, isolated_engine: BillingEngine):
        """计费后记录列表应包含记录。"""
        _register_agent(isolated_engine, "agent1", BillingModel.FREE)
        client.post("/api/billing/charge", json={
            "agent_name": "agent1", "tokens": 50, "calls": 1,
        })
        resp = client.get("/api/billing/records")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        assert data["records"][0]["agent_name"] == "agent1"

    def test_records_filter_by_agent(self, client: TestClient, isolated_engine: BillingEngine):
        """按 Agent 过滤记录。"""
        _register_agent(isolated_engine, "agent1", BillingModel.FREE)
        _register_agent(isolated_engine, "agent2", BillingModel.FREE)
        client.post("/api/billing/charge", json={"agent_name": "agent1"})
        client.post("/api/billing/charge", json={"agent_name": "agent2"})
        resp = client.get("/api/billing/records", params={"agent_name": "agent1"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        assert data["records"][0]["agent_name"] == "agent1"

    def test_records_invalid_limit(self, client: TestClient):
        """无效 limit 返回 422（FastAPI Query 参数校验）。"""
        resp = client.get("/api/billing/records", params={"limit": 0})
        assert resp.status_code == 422

    def test_records_limit_too_large(self, client: TestClient):
        """limit 过大返回 422（FastAPI Query 参数校验）。"""
        resp = client.get("/api/billing/records", params={"limit": 10001})
        assert resp.status_code == 422