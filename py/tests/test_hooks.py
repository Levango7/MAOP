"""Tests for maop.dashboard.routers.hooks — Hook 可视化配置 CRUD API（任务199）.

覆盖：
  - 列出空 hook 列表
  - 创建 hook（成功 + 无效事件 400 + 无效 method 400）
  - 获取单个 hook（成功 + 不存在 404）
  - 更新 hook
  - 删除 hook（成功 + 不存在 404）
  - 启用/禁用 hook
  - 测试 hook 触发
  - 列出可用事件类型

使用真实 HookManager（基于 tmp_path 隔离的 SQLite），通过 FastAPI TestClient
同步调用端点；admin 角色通过中间件注入。
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _make_app() -> TestClient:
    """构造仅挂载 hooks 路由的 TestClient，并注入 admin 角色。"""
    app = FastAPI()

    @app.middleware("http")
    async def _inject_admin(request, call_next):
        request.state.auth_roles = ["admin"]
        request.state.auth_identity = "admin"
        return await call_next(request)

    from maop.dashboard.routers.hooks import router
    app.include_router(router)
    return TestClient(app)


# ── 真实 HookManager fixture ─────────────────────────────────────────
@pytest.fixture
def real_mgr(tmp_path, monkeypatch):
    """使用真实 HookManager，root_dir 隔离到 tmp_path。"""
    from maop.core.agent.plugins_hooks.hook_manager import HookManager
    mgr = HookManager(root_dir=str(tmp_path))
    # 替换路由模块的 _get_hook_mgr，使其返回我们的实例
    import maop.dashboard.routers.hooks as hooks_mod
    monkeypatch.setattr(hooks_mod, "_get_hook_mgr", lambda: mgr)
    return mgr


@pytest.fixture
def client(real_mgr):
    return _make_app()


# ── Mock HookManager fixture（用于测试端点逻辑而不依赖持久化）──────
@pytest.fixture
def mock_mgr(monkeypatch):
    """Mock HookManager，用于测试端点逻辑而不依赖持久化。"""
    mock = MagicMock()
    # 默认 list_hooks 返回空
    mock.list_hooks = MagicMock(return_value=[])
    mock.get_hook = MagicMock(return_value=None)
    mock.register = MagicMock(return_value=SimpleNamespace(
        id="hk-test1", event="loop.complete", url="https://example.com/hook",
        enabled=True, priority=0, description="test-hook",
        created_at="2026-08-14T00:00:00Z", source="api",
        model_dump=lambda: {"id": "hk-test1"},
    ))
    mock.unregister = MagicMock(return_value=True)
    mock.enable = MagicMock(return_value=True)
    mock.disable = MagicMock(return_value=True)
    mock.trigger = AsyncMock(return_value=[SimpleNamespace(
        success=True, response="HTTP 200", error="", duration_ms=10,
        model_dump=lambda: {"success": True},
    )])
    import maop.dashboard.routers.hooks as hooks_mod
    monkeypatch.setattr(hooks_mod, "_get_hook_mgr", lambda: mock)
    return mock


@pytest.fixture
def mock_client(mock_mgr):
    return _make_app()


# ── 测试用例 ─────────────────────────────────────────────────────────
class TestListHooks:
    def test_list_hooks_empty(self, client):
        """列出空 hook 列表。"""
        resp = client.get("/api/hooks")
        assert resp.status_code == 200
        data = resp.json()
        assert data["hooks"] == []
        assert data["count"] == 0

    def test_list_hooks_with_data(self, client, real_mgr):
        """列出非空 hook 列表。"""
        real_mgr.register(
            event="loop.complete",
            url="https://example.com/hook",
            description="my-hook",
        )
        resp = client.get("/api/hooks")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        assert data["hooks"][0]["event"] == "loop.complete"
        assert data["hooks"][0]["url"] == "https://example.com/hook"


class TestCreateHook:
    def test_create_hook(self, client):
        """创建 hook 成功。"""
        resp = client.post("/api/hooks", json={
            "name": "my-webhook",
            "event": "loop.complete",
            "url": "https://example.com/hook",
            "method": "POST",
            "headers": {"X-Token": "abc"},
            "enabled": True,
            "timeout": 10,
            "retry_count": 0,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["event"] == "loop.complete"
        assert data["url"] == "https://example.com/hook"
        assert data["name"] == "my-webhook"
        assert data["id"].startswith("hk-")

    def test_create_hook_invalid_event(self, client):
        """无效事件类型返回 400。"""
        resp = client.post("/api/hooks", json={
            "name": "bad-hook",
            "event": "nonexistent.event",
            "url": "https://example.com/hook",
        })
        assert resp.status_code == 400
        # handle_api_errors 把 HTTPException.detail 转到 ErrorSchema.error 字段
        assert "Invalid event" in resp.json()["error"]

    def test_create_hook_invalid_method(self, client):
        """不支持的 HTTP method 返回 400。"""
        resp = client.post("/api/hooks", json={
            "name": "get-hook",
            "event": "loop.complete",
            "url": "https://example.com/hook",
            "method": "GET",
        })
        assert resp.status_code == 400
        assert "Unsupported method" in resp.json()["error"]

    def test_create_hook_wildcard_event(self, client):
        """通配符事件（如 agent.*）应被接受。"""
        resp = client.post("/api/hooks", json={
            "name": "wildcard-hook",
            "event": "agent.*",
            "url": "https://example.com/hook",
        })
        assert resp.status_code == 200


class TestGetHook:
    def test_get_hook(self, client, real_mgr):
        """获取单个 hook 成功。"""
        hdef = real_mgr.register(
            event="loop.complete",
            url="https://example.com/hook",
            description="my-hook",
        )
        resp = client.get(f"/api/hooks/{hdef.id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == hdef.id
        assert data["event"] == "loop.complete"

    def test_get_hook_not_found(self, client):
        """不存在的 hook 返回 404。"""
        resp = client.get("/api/hooks/nonexistent-id")
        assert resp.status_code == 404
        assert "not found" in resp.json()["error"]


class TestUpdateHook:
    def test_update_hook(self, client, real_mgr):
        """更新 hook 字段。"""
        hdef = real_mgr.register(
            event="loop.complete",
            url="https://example.com/hook",
            description="old-name",
        )
        resp = client.put(f"/api/hooks/{hdef.id}", json={
            "name": "new-name",
            "url": "https://example.com/new-hook",
            "enabled": False,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "new-name"
        assert data["url"] == "https://example.com/new-hook"
        # 验证持久化
        updated = real_mgr.get_hook(hdef.id)
        assert updated is not None
        assert updated.url == "https://example.com/new-hook"
        assert updated.enabled is False

    def test_update_hook_not_found(self, client):
        """更新不存在的 hook 返回 404。"""
        resp = client.put("/api/hooks/nonexistent-id", json={"name": "x"})
        assert resp.status_code == 404

    def test_update_hook_invalid_event(self, client, real_mgr):
        """更新到无效事件返回 400。"""
        hdef = real_mgr.register(
            event="loop.complete",
            url="https://example.com/hook",
        )
        resp = client.put(f"/api/hooks/{hdef.id}", json={
            "event": "invalid.event.name",
        })
        assert resp.status_code == 400


class TestDeleteHook:
    def test_delete_hook(self, client, real_mgr):
        """删除 hook 成功。"""
        hdef = real_mgr.register(
            event="loop.complete",
            url="https://example.com/hook",
        )
        resp = client.delete(f"/api/hooks/{hdef.id}")
        assert resp.status_code == 200
        assert resp.json()["removed"] is True
        # 验证已删除
        assert real_mgr.get_hook(hdef.id) is None

    def test_delete_hook_not_found(self, client):
        """删除不存在的 hook 返回 404。"""
        resp = client.delete("/api/hooks/nonexistent-id")
        assert resp.status_code == 404


class TestEnableDisableHook:
    def test_enable_disable_hook(self, client, real_mgr):
        """启用/禁用 hook。"""
        hdef = real_mgr.register(
            event="loop.complete",
            url="https://example.com/hook",
        )
        # 禁用
        resp = client.post(f"/api/hooks/{hdef.id}/disable")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
        assert real_mgr.get_hook(hdef.id).enabled is False

        # 启用
        resp = client.post(f"/api/hooks/{hdef.id}/enable")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
        assert real_mgr.get_hook(hdef.id).enabled is True

    def test_enable_not_found(self, client):
        """启用不存在的 hook 返回 404。"""
        resp = client.post("/api/hooks/nonexistent-id/enable")
        assert resp.status_code == 404


class TestTestHookEndpoint:
    def test_test_hook_endpoint(self, mock_client, mock_mgr):
        """测试 hook 触发（使用 mock 避免真实 HTTP 调用）。"""
        # 配置 mock：get_hook 返回一个有效 hook
        mock_mgr.get_hook = MagicMock(return_value=SimpleNamespace(
            id="hk-test1", event="loop.complete", url="https://example.com/hook",
            enabled=True, priority=0, description="test",
            created_at="2026-08-14T00:00:00Z", source="api",
        ))
        resp = mock_client.post("/api/hooks/hk-test1/test")
        assert resp.status_code == 200
        data = resp.json()
        assert data["hook_id"] == "hk-test1"
        assert data["success"] is True

    def test_test_hook_not_found(self, mock_client, mock_mgr):
        """测试不存在的 hook 返回 404。"""
        mock_mgr.get_hook = MagicMock(return_value=None)
        resp = mock_client.post("/api/hooks/nonexistent-id/test")
        assert resp.status_code == 404


class TestListEvents:
    def test_list_events(self, client):
        """列出可用事件类型。"""
        resp = client.get("/api/hooks/events")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] > 0
        # 验证包含已知事件
        event_names = [e["name"] for e in data["events"]]
        assert "loop.complete" in event_names
        assert "agent.pre_dispatch" in event_names
        # 验证每个事件都有 domain 和 phase
        for e in data["events"]:
            assert e["domain"]
            assert e["phase"]