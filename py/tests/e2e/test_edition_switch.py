"""E2E tests for POST /api/info/edition — runtime edition switching.

Validates the full request lifecycle for edition switching:
  - admin can switch edition (personal <-> enterprise)
  - non-admin users are rejected (403)
  - unauthenticated requests are rejected (401)
  - invalid edition values are rejected (400)
  - switching to enterprise with invalid license degrades to personal

These tests complement test_auth_enabled.py and test_routing_rbac_tenant.py
by covering the edition-switch control plane endpoint.
"""

from __future__ import annotations

import os

# Set auth enabled BEFORE importing app
os.environ["MAOP_AUTH"] = "1"
os.environ["MAOP_ENV"] = "test"
os.environ.setdefault("MAOP_ADMIN_PASSWORD", "TestAdminPass123!")

import pytest
from httpx import ASGITransport, AsyncClient

from maop.dashboard import server as _server_mod
from maop.dashboard.routers import auth as _auth_mod

if not (_auth_mod._auth_enabled and _server_mod._auth_enabled):
    pytest.skip(
        "MAOP_AUTH=1 must be set before app import; run this test in isolation",
        allow_module_level=True,
    )

from maop.config.edition import reset_edition
from maop.dashboard.server import app

_TEST_PASSWORD = os.environ.get("MAOP_ADMIN_PASSWORD", "TestAdminPass123!")
_VIEWER_PASSWORD = "ViewerPass123!"


# -- Helpers --------------------------------------------------------


async def _login(client, username="admin", password=_TEST_PASSWORD):
    """Login and return the JWT token."""
    resp = await client.post(
        "/api/auth/login",
        json={"username": username, "password": password},
    )
    assert resp.status_code == 200, f"Login failed: {resp.status_code} {resp.text}"
    data = resp.json()
    assert data["status"] == "ok"
    return data["token"]


async def _register_viewer(client, admin_token):
    """Register a read-only user (via admin) and return its JWT token."""
    admin_headers = {"Authorization": f"Bearer {admin_token}"}
    resp = await client.post(
        "/api/auth/register",
        json={"username": "viewer", "password": _VIEWER_PASSWORD, "roles": ["read"]},
        headers=admin_headers,
    )
    assert resp.status_code == 200, f"Register viewer failed: {resp.status_code} {resp.text}"

    resp = await client.post(
        "/api/auth/login",
        json={"username": "viewer", "password": _VIEWER_PASSWORD},
    )
    assert resp.status_code == 200, f"Viewer login failed: {resp.status_code} {resp.text}"
    return resp.json()["token"]


# -- Fixtures -------------------------------------------------------


@pytest.fixture
async def client(tmp_path, monkeypatch):
    """Create test client with auth enabled, using isolated temp DB.

    Patches MAOP_ROOT in the auth router so the auth DB and JWT
    revocation file are created in a temp directory, keeping the
    test self-contained. Also manually sets app.state.auth_manager
    because ASGITransport does not run the ASGI lifespan event.
    """
    monkeypatch.setattr(_auth_mod, "MAOP_ROOT", tmp_path)
    monkeypatch.setenv("MAOP_ROOT_DIR", str(tmp_path))
    _auth_mod._auth_mgr = None
    mgr = _auth_mod.get_auth_mgr()
    app.state.auth_manager = mgr

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture(autouse=True)
def _reset_edition_state():
    """每个测试前后重置 edition 全局状态，避免测试间相互污染。"""
    reset_edition()
    yield
    reset_edition()


# -- Test: Edition Switch RBAC --------------------------------------


class TestEditionSwitchRBAC:
    """Test RBAC enforcement on POST /api/info/edition."""

    async def test_unauthenticated_cannot_switch(self, client):
        """未认证请求切换 edition 返回 401。"""
        resp = await client.post("/api/info/edition", json={"edition": "personal"})
        assert resp.status_code == 401, (
            f"Unauthenticated switch should be 401, got {resp.status_code}"
        )

    async def test_non_admin_cannot_switch(self, client):
        """非 admin 用户（read 角色）切换 edition 被拒绝，返回 403。"""
        admin_token = await _login(client)
        viewer_token = await _register_viewer(client, admin_token)
        viewer_headers = {"Authorization": f"Bearer {viewer_token}"}

        resp = await client.post(
            "/api/info/edition",
            json={"edition": "personal"},
            headers=viewer_headers,
        )
        assert resp.status_code == 403, (
            f"Non-admin switch should be 403, got {resp.status_code}"
        )

    async def test_admin_can_switch_edition(self, client):
        """admin 用户可以成功切换 edition。"""
        token = await _login(client)
        headers = {"Authorization": f"Bearer {token}"}

        # 切换到 personal
        resp = await client.post(
            "/api/info/edition",
            json={"edition": "personal"},
            headers=headers,
        )
        assert resp.status_code == 200, (
            f"Admin switch to personal failed: {resp.status_code} {resp.text}"
        )
        data = resp.json()
        assert data["status"] == "ok"
        assert data["edition"] == "personal"
        assert "previous" in data

    async def test_invalid_edition_rejected(self, client):
        """无效的 edition 值返回 400。"""
        token = await _login(client)
        headers = {"Authorization": f"Bearer {token}"}

        resp = await client.post(
            "/api/info/edition",
            json={"edition": "invalid-edition"},
            headers=headers,
        )
        assert resp.status_code == 400, (
            f"Invalid edition should be 400, got {resp.status_code}"
        )

    async def test_missing_edition_field_rejected(self, client):
        """缺少 edition 字段返回 400。"""
        token = await _login(client)
        headers = {"Authorization": f"Bearer {token}"}

        resp = await client.post(
            "/api/info/edition",
            json={},
            headers=headers,
        )
        assert resp.status_code == 400, (
            f"Missing edition field should be 400, got {resp.status_code}"
        )

    async def test_switch_to_enterprise_returns_actual_edition(self, client):
        """切换到 enterprise 时，如果 license 无效则被明确拒绝（403），
        响应中包含授权指引错误消息。
        测试环境未安装 enterprise 包，因此预期 403 拒绝。

        P1-2 (edition 切换门禁): 无有效 license 切换到 enterprise 不再
        静默降级返回 200+degraded，而是返回 403 + 明确错误消息，让用户
        知道需要 MAOS 商业包 + License。"""
        token = await _login(client)
        headers = {"Authorization": f"Bearer {token}"}

        # 先切到 personal 确保起点状态
        resp = await client.post(
            "/api/info/edition",
            json={"edition": "personal"},
            headers=headers,
        )
        assert resp.status_code == 200

        # 尝试切到 enterprise —— 测试环境无 enterprise 包/license，应被 403 拒绝
        resp = await client.post(
            "/api/info/edition",
            json={"edition": "enterprise"},
            headers=headers,
        )
        assert resp.status_code == 403, (
            f"Switch to enterprise without license should be 403, "
            f"got {resp.status_code}"
        )
        # 错误消息应包含授权指引（MAOS / License 关键词）
        # handle_api_errors 将 HTTPException.detail 渲染为 ErrorSchema.error 字段
        body = resp.json()
        msg = body.get("error", "") or body.get("detail", "")
        assert "MAOS" in msg or "License" in msg or "license" in msg, (
            f"403 error should mention MAOS/License authorization, got: {msg!r}"
        )

    async def test_switch_updates_get_edition(self, client):
        """POST 切换后，GET /api/info/edition 返回新 edition。"""
        token = await _login(client)
        headers = {"Authorization": f"Bearer {token}"}

        # 切换到 personal
        resp = await client.post(
            "/api/info/edition",
            json={"edition": "personal"},
            headers=headers,
        )
        assert resp.status_code == 200

        # GET 验证
        resp = await client.get("/api/info/edition", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["edition"] == "personal", (
            f"GET edition after switch should be personal, got {data['edition']}"
        )
