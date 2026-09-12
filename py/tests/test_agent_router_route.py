"""Agent Router 路由层测试。

覆盖 agent_router.py 的 5 个端点的正常路径 + 错误路径。
使用隔离 tmp_path DB + monkeypatch require_admin 为 no-op。

经验来源：
- 2026-09-12-fastapi-require-admin-guard-test-adaptation-by-call-type：
  conftest 设 MAOP_AUTH=0 授予 read-only 角色，需 monkeypatch require_admin
  为 no-op 绕过 admin 守卫。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from maop.core.agent.registry.agent_catalog import (
    AgentCatalog,
    AgentCapability,
    AgentDescriptor,
    BillingModel,
)
from maop.core.agent.router.agent_router import AgentRouter


# ── Fixtures ─────────────────────────────────────────────────────


@pytest.fixture
def catalog(tmp_path: Path) -> AgentCatalog:
    """使用隔离 tmp_path DB 的全新 AgentCatalog。"""
    return AgentCatalog(db_path=tmp_path / "test_agent_router_route.db")


@pytest.fixture
def agent_router(catalog: AgentCatalog) -> AgentRouter:
    """基于隔离 catalog 的全新 AgentRouter。"""
    return AgentRouter(catalog=catalog)


@pytest.fixture
def app(agent_router: AgentRouter, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    """创建临时 FastAPI app，注入隔离 router + stub require_admin。"""
    import maop.dashboard.routers.agent_router as route_mod

    route_mod._set_router(agent_router)
    monkeypatch.setattr(route_mod, "require_admin", lambda request: None)

    app = FastAPI()
    app.include_router(route_mod.router)
    return app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


def _register_agents(catalog: AgentCatalog) -> None:
    """注册测试用 Agent。"""
    catalog.register(AgentDescriptor(
        name="free-coder",
        capabilities=[AgentCapability.CODE_GENERATION, AgentCapability.CHAT],
        billing_model=BillingModel.FREE,
        max_concurrent=2,
    ))
    catalog.register(AgentDescriptor(
        name="token-coder",
        capabilities=[AgentCapability.CODE_GENERATION],
        billing_model=BillingModel.PER_TOKEN,
        max_concurrent=1,
    ))


# ── 1. POST /api/agent-router/route ──────────────────────────────


class TestAgentRoute:
    """POST /api/agent-router/route 端点测试。"""

    def test_route_success(self, client: TestClient, catalog: AgentCatalog) -> None:
        """成功路由选择。"""
        _register_agents(catalog)
        resp = client.post("/api/agent-router/route", json={
            "required_capabilities": ["code_generation"],
            "max_cost_tier": "any",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["primary"] is not None
        assert data["reason"] != ""

    def test_route_no_candidates(self, client: TestClient) -> None:
        """空 catalog 路由返回 primary=None。"""
        resp = client.post("/api/agent-router/route", json={
            "required_capabilities": ["code_generation"],
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["primary"] is None

    def test_route_with_strategy(self, client: TestClient, catalog: AgentCatalog) -> None:
        """指定策略路由。"""
        _register_agents(catalog)
        resp = client.post("/api/agent-router/route", json={
            "required_capabilities": ["code_generation"],
            "max_cost_tier": "any",
            "strategy": "cost_optimized",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["primary"]["name"] == "free-coder"  # FREE 成本最低

    def test_route_invalid_capability(self, client: TestClient) -> None:
        """无效能力返回 400。"""
        resp = client.post("/api/agent-router/route", json={
            "required_capabilities": ["invalid_cap"],
        })
        assert resp.status_code == 400

    def test_route_invalid_strategy(self, client: TestClient) -> None:
        """无效策略返回 400。"""
        resp = client.post("/api/agent-router/route", json={
            "strategy": "invalid_strategy",
        })
        assert resp.status_code == 400

    def test_route_with_preferred(self, client: TestClient, catalog: AgentCatalog) -> None:
        """preferred_agent 优先选择。"""
        _register_agents(catalog)
        resp = client.post("/api/agent-router/route", json={
            "required_capabilities": ["code_generation"],
            "max_cost_tier": "any",
            "preferred_agent": "token-coder",
        })
        assert resp.status_code == 200
        assert resp.json()["primary"]["name"] == "token-coder"


# ── 2. GET /api/agent-router/strategies ──────────────────────────


class TestListStrategies:
    """GET /api/agent-router/strategies 端点测试。"""

    def test_list_strategies(self, client: TestClient) -> None:
        """列出所有策略。"""
        resp = client.get("/api/agent-router/strategies")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["count"] == 5
        values = [s["value"] for s in data["strategies"]]
        assert "capability_match" in values
        assert "cost_optimized" in values
        assert "load_balanced" in values
        assert "priority" in values
        assert "round_robin" in values


# ── 3. POST /api/agent-router/acquire ────────────────────────────


class TestAcquireSlot:
    """POST /api/agent-router/acquire 端点测试。"""

    def test_acquire_success(self, client: TestClient, catalog: AgentCatalog) -> None:
        """成功占用槽位。"""
        _register_agents(catalog)
        resp = client.post("/api/agent-router/acquire", json={"agent_name": "free-coder"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["active_count"] == 1

    def test_acquire_at_capacity(self, client: TestClient, catalog: AgentCatalog) -> None:
        """并发满时返回 409。"""
        _register_agents(catalog)
        # token-coder max_concurrent=1
        client.post("/api/agent-router/acquire", json={"agent_name": "token-coder"})
        resp = client.post("/api/agent-router/acquire", json={"agent_name": "token-coder"})
        assert resp.status_code == 409

    def test_acquire_not_found(self, client: TestClient) -> None:
        """Agent 不存在返回 409。"""
        resp = client.post("/api/agent-router/acquire", json={"agent_name": "nonexistent"})
        assert resp.status_code == 409


# ── 4. POST /api/agent-router/release ────────────────────────────


class TestReleaseSlot:
    """POST /api/agent-router/release 端点测试。"""

    def test_release_success(self, client: TestClient, catalog: AgentCatalog) -> None:
        """成功释放槽位。"""
        _register_agents(catalog)
        client.post("/api/agent-router/acquire", json={"agent_name": "free-coder"})
        resp = client.post("/api/agent-router/release", json={"agent_name": "free-coder"})
        assert resp.status_code == 200
        assert resp.json()["active_count"] == 0

    def test_release_no_active(self, client: TestClient) -> None:
        """无活跃槽位时释放仍返回 200（幂等）。"""
        resp = client.post("/api/agent-router/release", json={"agent_name": "free-coder"})
        assert resp.status_code == 200
        assert resp.json()["active_count"] == 0


# ── 5. GET /api/agent-router/active ──────────────────────────────


class TestActiveCounts:
    """GET /api/agent-router/active 端点测试。"""

    def test_active_empty(self, client: TestClient) -> None:
        """空 catalog 返回空 active。"""
        resp = client.get("/api/agent-router/active")
        assert resp.status_code == 200
        data = resp.json()
        assert data["active"] == {}
        assert data["count"] == 0

    def test_active_with_agents(self, client: TestClient, catalog: AgentCatalog) -> None:
        """有 Agent 但无活跃时全部为 0。"""
        _register_agents(catalog)
        resp = client.get("/api/agent-router/active")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 2
        assert data["active"]["free-coder"] == 0
        assert data["active"]["token-coder"] == 0

    def test_active_after_acquire(self, client: TestClient, catalog: AgentCatalog) -> None:
        """acquire 后 active 反映变化。"""
        _register_agents(catalog)
        client.post("/api/agent-router/acquire", json={"agent_name": "free-coder"})
        resp = client.get("/api/agent-router/active")
        assert resp.status_code == 200
        data = resp.json()
        assert data["active"]["free-coder"] == 1
        assert data["active"]["token-coder"] == 0