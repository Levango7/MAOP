"""Agent Catalog 路由层测试。

覆盖 agent_catalog.py 的 7 个端点的正常路径 + 错误路径。
使用隔离 tmp_path DB + monkeypatch require_admin 为 no-op。

经验来源：
- 2026-09-12-fastapi-require-admin-guard-test-adaptation-by-call-type：
  conftest 设 MAOP_AUTH=0 授予 read-only 角色，需 monkeypatch require_admin
  为 no-op 绕过 admin 守卫（与 test_edition_switch_guard.py 等同模式）。
- 2026-09-12-fastapi-pydantic-route-param-direct-call-test-adaptation：
  使用 TestClient + 临时 FastAPI app + include_router 测试路由层。
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


# ── Fixtures ─────────────────────────────────────────────────────


@pytest.fixture
def catalog(tmp_path: Path) -> AgentCatalog:
    """使用隔离 tmp_path DB 的全新 AgentCatalog。"""
    return AgentCatalog(db_path=tmp_path / "test_agent_catalog_route.db")


@pytest.fixture
def app(catalog: AgentCatalog, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    """创建临时 FastAPI app，注入隔离 catalog + stub require_admin。

    conftest 设 MAOP_AUTH=0 → 中间件授予 read-only 角色。
    本测试关注路由层逻辑，非 admin 权限校验，因此绕过 require_admin 守卫
    （与 test_edition_switch_guard.py / test_dag_sse_endpoint.py 同模式）。
    """
    import maop.dashboard.routers.agent_catalog as route_mod

    route_mod._set_catalog(catalog)
    monkeypatch.setattr(route_mod, "require_admin", lambda request: None)

    app = FastAPI()
    app.include_router(route_mod.router)
    return app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


def _register_sample_agents(catalog: AgentCatalog) -> None:
    """注册一组测试用 Agent。"""
    catalog.register(AgentDescriptor(
        name="claude-code",
        display_name="Claude Code",
        vendor="Anthropic",
        capabilities=[AgentCapability.CODE_GENERATION, AgentCapability.TOOL_USE],
        billing_model=BillingModel.PER_TOKEN,
    ))
    catalog.register(AgentDescriptor(
        name="gpt-4",
        display_name="GPT-4",
        vendor="OpenAI",
        capabilities=[AgentCapability.CODE_GENERATION, AgentCapability.CHAT],
        billing_model=BillingModel.PER_TOKEN,
    ))
    catalog.register(AgentDescriptor(
        name="local-llama",
        display_name="Local Llama",
        vendor="Meta",
        capabilities=[AgentCapability.CHAT],
        billing_model=BillingModel.FREE,
        enabled=False,
    ))


# ── 1. GET /api/agent-catalog/agents ─────────────────────────────


class TestListAgents:
    """GET /api/agent-catalog/agents 端点测试。"""

    def test_list_empty(self, client: TestClient) -> None:
        """空 catalog 返回空列表。"""
        resp = client.get("/api/agent-catalog/agents")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["agents"] == []
        assert data["count"] == 0

    def test_list_all(self, client: TestClient, catalog: AgentCatalog) -> None:
        """列出所有 Agent。"""
        _register_sample_agents(catalog)
        resp = client.get("/api/agent-catalog/agents")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 3
        names = [a["name"] for a in data["agents"]]
        assert names == sorted(names)  # 按 name 排序

    def test_list_enabled_filter(self, client: TestClient, catalog: AgentCatalog) -> None:
        """enabled=true 过滤仅返回启用的 Agent。"""
        _register_sample_agents(catalog)
        resp = client.get("/api/agent-catalog/agents?enabled=true")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 2  # local-llama 被禁用
        assert all(a["enabled"] for a in data["agents"])

    def test_list_disabled_filter(self, client: TestClient, catalog: AgentCatalog) -> None:
        """enabled=false 过滤仅返回禁用的 Agent。"""
        _register_sample_agents(catalog)
        resp = client.get("/api/agent-catalog/agents?enabled=false")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        assert data["agents"][0]["name"] == "local-llama"


# ── 2. GET /api/agent-catalog/agents/{name} ──────────────────────


class TestGetAgent:
    """GET /api/agent-catalog/agents/{name} 端点测试。"""

    def test_get_existing(self, client: TestClient, catalog: AgentCatalog) -> None:
        """获取存在的 Agent。"""
        _register_sample_agents(catalog)
        resp = client.get("/api/agent-catalog/agents/claude-code")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["agent"]["name"] == "claude-code"
        assert data["agent"]["vendor"] == "Anthropic"

    def test_get_not_found(self, client: TestClient) -> None:
        """获取不存在的 Agent 返回 404。"""
        resp = client.get("/api/agent-catalog/agents/nonexistent")
        assert resp.status_code == 404
        data = resp.json()
        assert data["status"] == "error"


# ── 3. POST /api/agent-catalog/agents ────────────────────────────


class TestRegisterAgent:
    """POST /api/agent-catalog/agents 端点测试。"""

    def test_register_success(self, client: TestClient) -> None:
        """成功注册新 Agent。"""
        resp = client.post("/api/agent-catalog/agents", json={
            "name": "new-agent",
            "display_name": "New Agent",
            "vendor": "TestVendor",
            "capabilities": ["code_generation", "chat"],
            "billing_model": "free",
            "auth_method": "api_key",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["agent"]["name"] == "new-agent"

    def test_register_invalid_capability(self, client: TestClient) -> None:
        """无效能力返回 400。"""
        resp = client.post("/api/agent-catalog/agents", json={
            "name": "bad-agent",
            "capabilities": ["invalid_cap"],
        })
        assert resp.status_code == 400
        assert resp.json()["status"] == "error"

    def test_register_invalid_billing_model(self, client: TestClient) -> None:
        """无效计费模式返回 400。"""
        resp = client.post("/api/agent-catalog/agents", json={
            "name": "bad-agent",
            "billing_model": "invalid_billing",
        })
        assert resp.status_code == 400

    def test_register_invalid_auth_method(self, client: TestClient) -> None:
        """无效授权方式返回 400。"""
        resp = client.post("/api/agent-catalog/agents", json={
            "name": "bad-agent",
            "auth_method": "invalid_auth",
        })
        assert resp.status_code == 400

    def test_register_upsert(self, client: TestClient, catalog: AgentCatalog) -> None:
        """重复注册同名 Agent 执行 upsert。"""
        client.post("/api/agent-catalog/agents", json={
            "name": "dup-agent",
            "display_name": "Original",
        })
        resp = client.post("/api/agent-catalog/agents", json={
            "name": "dup-agent",
            "display_name": "Updated",
            "vendor": "NewVendor",
        })
        assert resp.status_code == 200
        assert resp.json()["agent"]["display_name"] == "Updated"
        # 确认 catalog 中只有 1 个
        assert catalog.count() == 1


# ── 4. DELETE /api/agent-catalog/agents/{name} ───────────────────


class TestDeleteAgent:
    """DELETE /api/agent-catalog/agents/{name} 端点测试。"""

    def test_delete_existing(self, client: TestClient, catalog: AgentCatalog) -> None:
        """删除存在的 Agent。"""
        _register_sample_agents(catalog)
        resp = client.delete("/api/agent-catalog/agents/claude-code")
        assert resp.status_code == 200
        assert resp.json()["deleted"] == "claude-code"
        assert catalog.get("claude-code") is None

    def test_delete_not_found(self, client: TestClient) -> None:
        """删除不存在的 Agent 返回 404。"""
        resp = client.delete("/api/agent-catalog/agents/nonexistent")
        assert resp.status_code == 404


# ── 5. PUT /api/agent-catalog/agents/{name}/health ───────────────


class TestUpdateHealth:
    """PUT /api/agent-catalog/agents/{name}/health 端点测试。"""

    def test_update_health_true(self, client: TestClient, catalog: AgentCatalog) -> None:
        """更新健康状态为 True。"""
        _register_sample_agents(catalog)
        resp = client.put("/api/agent-catalog/agents/claude-code/health", json={"healthy": True})
        assert resp.status_code == 200
        assert resp.json()["healthy"] is True
        assert catalog.get("claude-code").healthy is True

    def test_update_health_false(self, client: TestClient, catalog: AgentCatalog) -> None:
        """更新健康状态为 False。"""
        _register_sample_agents(catalog)
        resp = client.put("/api/agent-catalog/agents/claude-code/health", json={"healthy": False})
        assert resp.status_code == 200
        assert resp.json()["healthy"] is False
        assert catalog.get("claude-code").healthy is False

    def test_update_health_not_found(self, client: TestClient) -> None:
        """更新不存在的 Agent 健康状态返回 404。"""
        resp = client.put("/api/agent-catalog/agents/nonexistent/health", json={"healthy": True})
        assert resp.status_code == 404


# ── 6. GET /api/agent-catalog/search ─────────────────────────────


class TestSearchAgents:
    """GET /api/agent-catalog/search 端点测试。"""

    def test_search_no_capability(self, client: TestClient, catalog: AgentCatalog) -> None:
        """无能力参数返回所有启用 Agent。"""
        _register_sample_agents(catalog)
        resp = client.get("/api/agent-catalog/search")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 2  # 排除 disabled

    def test_search_single_capability(self, client: TestClient, catalog: AgentCatalog) -> None:
        """按单个能力搜索。"""
        _register_sample_agents(catalog)
        resp = client.get("/api/agent-catalog/search?capability=code_generation")
        assert resp.status_code == 200
        data = resp.json()
        names = [a["name"] for a in data["agents"]]
        assert "claude-code" in names
        assert "gpt-4" in names
        assert "local-llama" not in names  # disabled

    def test_search_multiple_capabilities(self, client: TestClient, catalog: AgentCatalog) -> None:
        """按多能力搜索（交集）。"""
        _register_sample_agents(catalog)
        resp = client.get("/api/agent-catalog/search?capability=code_generation&capability=chat")
        assert resp.status_code == 200
        data = resp.json()
        names = [a["name"] for a in data["agents"]]
        assert "gpt-4" in names  # 有 code_generation + chat
        assert "claude-code" not in names  # 只有 code_generation + tool_use，无 chat

    def test_search_invalid_capability(self, client: TestClient) -> None:
        """无效能力返回 400。"""
        resp = client.get("/api/agent-catalog/search?capability=invalid")
        assert resp.status_code == 400


# ── 7. GET /api/agent-catalog/stats ──────────────────────────────


class TestCatalogStats:
    """GET /api/agent-catalog/stats 端点测试。"""

    def test_stats_empty(self, client: TestClient) -> None:
        """空 catalog 统计。"""
        resp = client.get("/api/agent-catalog/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 0
        assert data["healthy_count"] == 0

    def test_stats_with_agents(self, client: TestClient, catalog: AgentCatalog) -> None:
        """有 Agent 的统计。"""
        _register_sample_agents(catalog)
        # claude-code 和 gpt-4 默认 healthy=True，local-llama healthy=True
        resp = client.get("/api/agent-catalog/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 3
        assert data["healthy_count"] == 3

    def test_stats_after_health_update(self, client: TestClient, catalog: AgentCatalog) -> None:
        """更新健康状态后统计反映变化。"""
        _register_sample_agents(catalog)
        client.put("/api/agent-catalog/agents/claude-code/health", json={"healthy": False})
        resp = client.get("/api/agent-catalog/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 3
        assert data["healthy_count"] == 2