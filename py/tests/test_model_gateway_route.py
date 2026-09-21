"""Tests for maop.dashboard.routers.model_gateway — 模型授权网关 API 端点.

覆盖 7 个端点的正常路径 + 错误路径，使用隔离的 tmp_path DB 避免污染主库。
每个测试通过自定义中间件注入 admin 角色，绕过 require_admin 鉴权。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any  # noqa: F401

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from maop.core.agent.llm_chat.model_gateway import (
    ModelGateway,
    ModelGatewayConfig,  # noqa: F401
    ModelPermission,  # noqa: F401
)
from maop.dashboard.routers import model_gateway as gateway_route
from maop.dashboard.services import model_service

# ── Fixtures ─────────────────────────────────────────────────────


def _make_app() -> FastAPI:
    """创建临时 app + 注入 admin 角色 + 注册 model_gateway 路由。"""
    app = FastAPI()

    @app.middleware("http")
    async def _inject_admin(request, call_next):
        request.state.auth_roles = ["admin"]
        return await call_next(request)

    app.include_router(gateway_route.router)
    return app


@pytest.fixture
def isolated_gateway(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ModelGateway:
    """提供隔离 DB 的 ModelGateway 并注入到 service 层单例。

    重构后单例从 router 迁移至 ``model_service``，测试注入点同步调整。
    """
    db_path = tmp_path / "gateway_test.db"
    gateway = ModelGateway(db_path=db_path)
    monkeypatch.setattr(model_service, "_model_gateway", gateway)
    return gateway


@pytest.fixture
def client(isolated_gateway: ModelGateway) -> TestClient:
    """TestClient with admin role injected."""
    return TestClient(_make_app())


# ── 1. GET /api/model-gateway/permissions ───────────────────────


class TestPermissionsList:
    def test_list_empty(self, client: TestClient):
        """无权限规则时返回空列表。"""
        resp = client.get("/api/model-gateway/permissions")
        assert resp.status_code == 200
        data = resp.json()
        assert data["permissions"] == []
        assert data["count"] == 0

    def test_list_after_add(self, client: TestClient):
        """添加规则后列表应包含该规则。"""
        client.post("/api/model-gateway/permissions", json={
            "model_pattern": "gpt-4*",
            "allowed": True,
            "priority": 10,
        })
        resp = client.get("/api/model-gateway/permissions")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        assert data["permissions"][0]["model_pattern"] == "gpt-4*"


# ── 2. POST /api/model-gateway/permissions ─────────────────────


class TestPermissionsAdd:
    def test_add_basic_permission(self, client: TestClient):
        """添加基本权限规则。"""
        resp = client.post("/api/model-gateway/permissions", json={
            "model_pattern": "claude-*",
            "allowed": True,
            "priority": 50,
        })
        assert resp.status_code == 200
        assert resp.json()["model_pattern"] == "claude-*"

    def test_add_deny_permission(self, client: TestClient):
        """添加拒绝规则。"""
        resp = client.post("/api/model-gateway/permissions", json={
            "model_pattern": "gpt-3.5*",
            "allowed": False,
        })
        assert resp.status_code == 200

    def test_add_overwrites_existing(self, client: TestClient):
        """同 pattern 添加应覆盖。"""
        client.post("/api/model-gateway/permissions", json={
            "model_pattern": "gpt-4*", "allowed": True, "priority": 10,
        })
        client.post("/api/model-gateway/permissions", json={
            "model_pattern": "gpt-4*", "allowed": False, "priority": 20,
        })
        resp = client.get("/api/model-gateway/permissions")
        data = resp.json()
        assert data["count"] == 1
        assert data["permissions"][0]["allowed"] is False

    def test_add_missing_pattern(self, client: TestClient):
        """缺少 model_pattern 返回 422（Pydantic v2 校验错误）。"""
        resp = client.post("/api/model-gateway/permissions", json={
            "allowed": True,
        })
        # Pydantic v2 BaseModel 校验失败返回 422，而非路由层 400。
        # 来源：2026-09-12-pydantic-basemodel-migration-422-vs-400-test-compatibility
        assert resp.status_code == 422


# ── 3. DELETE /api/model-gateway/permissions/{model_pattern} ───


class TestPermissionsDelete:
    def test_delete_existing(self, client: TestClient):
        """删除存在的规则。"""
        client.post("/api/model-gateway/permissions", json={
            "model_pattern": "gpt-4*", "allowed": True,
        })
        resp = client.delete("/api/model-gateway/permissions/gpt-4*")
        assert resp.status_code == 200

    def test_delete_nonexistent(self, client: TestClient):
        """删除不存在的规则返回 404。"""
        resp = client.delete("/api/model-gateway/permissions/nonexistent*")
        assert resp.status_code == 404


# ── 4. POST /api/model-gateway/check ────────────────────────────


class TestModelCheck:
    def test_check_allowed_by_default(self, client: TestClient):
        """默认策略 allow 时允许访问。"""
        resp = client.post("/api/model-gateway/check", json={
            "model": "gpt-4",
            "agent": "agent1",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["decision"]["allowed"] is True

    def test_check_denied_by_rule(self, client: TestClient):
        """拒绝规则应阻止访问。"""
        client.post("/api/model-gateway/permissions", json={
            "model_pattern": "gpt-4*", "allowed": False,
        })
        resp = client.post("/api/model-gateway/check", json={"model": "gpt-4-turbo"})
        assert resp.status_code == 200
        assert resp.json()["decision"]["allowed"] is False

    def test_check_missing_model(self, client: TestClient):
        """缺少 model 返回 400。"""
        resp = client.post("/api/model-gateway/check", json={"agent": "a1"})
        assert resp.status_code == 400


# ── 5. POST /api/model-gateway/usage ────────────────────────────


class TestUsageRecord:
    def test_record_usage(self, client: TestClient):
        """记录使用量。"""
        resp = client.post("/api/model-gateway/usage", json={
            "model": "gpt-4",
            "tokens": 500,
            "agent": "agent1",
        })
        assert resp.status_code == 200
        assert resp.json()["tokens"] == 500

    def test_record_usage_missing_model(self, client: TestClient):
        """缺少 model 返回 400。"""
        resp = client.post("/api/model-gateway/usage", json={"tokens": 100})
        assert resp.status_code == 400

    def test_record_zero_tokens(self, client: TestClient):
        """记录 0 token 应成功。"""
        resp = client.post("/api/model-gateway/usage", json={
            "model": "gpt-4", "tokens": 0,
        })
        assert resp.status_code == 200


# ── 6. GET /api/model-gateway/usage ─────────────────────────────


class TestUsageGet:
    def test_get_usage_empty(self, client: TestClient):
        """无使用量记录时返回空。"""
        resp = client.get("/api/model-gateway/usage")
        assert resp.status_code == 200
        data = resp.json()
        assert data["usage"] == {}
        assert data["total_tokens"] == 0

    def test_get_usage_after_record(self, client: TestClient):
        """记录后查询应反映使用量。"""
        client.post("/api/model-gateway/usage", json={
            "model": "gpt-4", "tokens": 300,
        })
        resp = client.get("/api/model-gateway/usage")
        assert resp.status_code == 200
        data = resp.json()
        assert data["usage"]["gpt-4"] == 300
        assert data["total_tokens"] == 300

    def test_get_usage_by_model(self, client: TestClient):
        """按模型过滤使用量。"""
        client.post("/api/model-gateway/usage", json={
            "model": "gpt-4", "tokens": 200,
        })
        client.post("/api/model-gateway/usage", json={
            "model": "claude-3", "tokens": 100,
        })
        resp = client.get("/api/model-gateway/usage", params={"model": "gpt-4"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["usage"]["gpt-4"] == 200


# ── 7. PUT /api/model-gateway/config ────────────────────────────


class TestConfigUpdate:
    def test_update_config_basic(self, client: TestClient):
        """更新基本配置。"""
        resp = client.put("/api/model-gateway/config", json={
            "default_policy": "deny",
            "permissions": [],
            "global_daily_token_limit": 100000,
            "enable_quota_check": True,
        })
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_update_config_with_permissions(self, client: TestClient):
        """更新配置含权限规则。"""
        resp = client.put("/api/model-gateway/config", json={
            "default_policy": "allow",
            "permissions": [
                {"model_pattern": "gpt-4*", "allowed": True, "priority": 10},
            ],
            "enable_quota_check": False,
        })
        assert resp.status_code == 200
        # 验证权限已更新
        perms = client.get("/api/model-gateway/permissions").json()
        assert perms["count"] == 1