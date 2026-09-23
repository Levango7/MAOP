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


# ── 8. PUT /api/model-gateway/permissions/{model_pattern} ────────
# 2026-09-23 新增：前端 AgentGateway 的权限编辑此前调用本端点，但后端只有
# POST/DELETE —— 请求落到 SPA 兜底返回 200 + HTML，前端报
# "Unexpected token '<'"，编辑功能不可用。


class TestPermissionsUpdate:
    def test_update_existing_rule(self, client: TestClient):
        """原地更新（pattern 不变）：规则数不变，字段被覆盖。"""
        client.post("/api/model-gateway/permissions", json={
            "model_pattern": "gpt-4*",
            "allowed": True,
            "priority": 10,
        })
        resp = client.put("/api/model-gateway/permissions/gpt-4*", json={
            "model_pattern": "gpt-4*",
            "allowed": False,
            "priority": 99,
        })
        assert resp.status_code == 200
        perms = client.get("/api/model-gateway/permissions").json()
        assert perms["count"] == 1, "原地更新不应新增规则"
        assert perms["permissions"][0]["allowed"] is False
        assert perms["permissions"][0]["priority"] == 99

    def test_update_with_rename_removes_old(self, client: TestClient):
        """改名场景（URL pattern ≠ body pattern）：旧规则必须被删除。"""
        client.post("/api/model-gateway/permissions", json={
            "model_pattern": "old-*",
            "allowed": True,
            "priority": 10,
        })
        resp = client.put("/api/model-gateway/permissions/old-*", json={
            "model_pattern": "new-*",
            "allowed": True,
            "priority": 10,
        })
        assert resp.status_code == 200
        perms = client.get("/api/model-gateway/permissions").json()
        patterns = {p["model_pattern"] for p in perms["permissions"]}
        assert patterns == {"new-*"}, f"改名后应只剩新规则，实际 {patterns}"

    def test_update_missing_pattern_rejected(self, client: TestClient):
        """body 缺 model_pattern → 422。

        ``ModelPermission.model_pattern`` 是必填字段（``model_pattern: str``
        无默认值），Pydantic 在路由函数体之前就拦截了，返回 422 而非 400。
        """
        resp = client.put("/api/model-gateway/permissions/x", json={"allowed": True})
        assert resp.status_code == 422

    def test_update_empty_pattern_rejected(self, client: TestClient):
        """body 传空字符串 → 路由层的显式校验返回 400（Pydantic 拦不到）。"""
        resp = client.put(
            "/api/model-gateway/permissions/x", json={"model_pattern": ""}
        )
        assert resp.status_code == 400


# ── 9. DELETE /api/model-gateway/usage ───────────────────────────
# 2026-09-23 新增：前端 AgentGateway 的「清空今日用量」。
# 关键：用量是**内存 + SQLite 双写**，只清一边会导致进程重启后数据复活。


class TestUsageClear:
    def test_clear_empties_usage(self, client: TestClient):
        """清空后今日用量归零。"""
        client.post("/api/model-gateway/usage", json={"model": "gpt-4", "tokens": 500})
        assert client.get("/api/model-gateway/usage").json()["total_tokens"] == 500

        resp = client.delete("/api/model-gateway/usage")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        # 内存侧必须真的清空
        assert client.get("/api/model-gateway/usage").json()["total_tokens"] == 0

    def test_clear_persisted_side(self, client: TestClient, isolated_gateway):
        """持久化侧也必须清空 —— 否则重启后旧数据会读回来。

        这是本端点最容易写错的地方：``_daily_usage`` 是内存缓存，
        SQLite 是持久层，两者都必须清。
        """
        from maop.core.backends.db_utils import sqlite_connect

        client.post("/api/model-gateway/usage", json={"model": "gpt-4", "tokens": 500})
        with sqlite_connect(isolated_gateway._db_path) as conn:
            before = conn.execute("SELECT COUNT(*) FROM model_gateway_usage").fetchone()[0]
        assert before == 1, "持久化侧应先有 1 条记录"

        client.delete("/api/model-gateway/usage")
        with sqlite_connect(isolated_gateway._db_path) as conn:
            after = conn.execute("SELECT COUNT(*) FROM model_gateway_usage").fetchone()[0]
        assert after == 0, "持久化侧未清空 —— 重启后用量会复活"

    def test_clear_by_model_keeps_others(self, client: TestClient):
        """指定 model 时只清该模型，其余保留。"""
        client.post("/api/model-gateway/usage", json={"model": "gpt-4", "tokens": 100})
        client.post("/api/model-gateway/usage", json={"model": "claude", "tokens": 200})

        resp = client.delete("/api/model-gateway/usage", params={"model": "gpt-4"})
        assert resp.status_code == 200
        usage = client.get("/api/model-gateway/usage").json()["usage"]
        assert "gpt-4" not in usage
        assert usage.get("claude") == 200

    def test_clear_empty_is_ok(self, client: TestClient):
        """无用量时清空不报错。"""
        resp = client.delete("/api/model-gateway/usage")
        assert resp.status_code == 200
        assert resp.json()["cleared"] == 0