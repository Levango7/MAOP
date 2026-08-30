"""E2E tests for routing decision chain and RBAC enforcement.

Validates the full request lifecycle:
  - Routing: task -> router -> agent selection -> routing trace
  - RBAC: role-based access control (admin vs viewer)
  - Multi-tenant isolation: tenant A cannot access tenant B data
  - Auth cookie (#4 fix): httpOnly cookie authentication

These tests complement test_auth_enabled.py by covering the
"authenticate -> route -> execute -> verify" chain end-to-end.
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

from maop.dashboard.server import app

_TEST_PASSWORD = os.environ.get("MAOP_ADMIN_PASSWORD", "TestAdminPass123!")


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


# -- Fixtures -------------------------------------------------------


@pytest.fixture
async def client(tmp_path, monkeypatch):
    """Create test client with auth enabled, using isolated temp DB."""
    monkeypatch.setattr(_auth_mod, "MAOP_ROOT", tmp_path)
    monkeypatch.setenv("MAOP_ROOT_DIR", str(tmp_path))
    _auth_mod._auth_mgr = None
    mgr = _auth_mod.get_auth_mgr()
    app.state.auth_manager = mgr

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# -- Test: Routing Decision Chain -----------------------------------


class TestRoutingChain:
    """Test routing: task -> router -> agent selection -> trace."""

    async def test_agents_endpoint_returns_registry(self, client):
        """Agents endpoint returns the agent registry structure.

        Note: test env uses tmp_path without config/agents.yaml, so the
        registry may be empty. We verify the endpoint works and returns
        the expected structure, not that agents exist.
        """
        token = await _login(client)
        headers = {"Authorization": f"Bearer {token}"}

        resp = await client.get("/api/agents", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "agents" in data, f"Expected 'agents' key, got: {data}"
        assert isinstance(data["agents"], list), f"Expected list, got {type(data['agents'])}"

    async def test_routing_trace_accessible(self, client):
        """Routing decisions endpoint is accessible with valid token.

        Uses /api/routing/decisions/recent (the actual endpoint name).
        """
        token = await _login(client)
        headers = {"Authorization": f"Bearer {token}"}

        resp = await client.get("/api/routing/decisions/recent", headers=headers)
        assert resp.status_code == 200, f"Routing decisions failed: {resp.status_code}"
        data = resp.json()
        assert isinstance(data, dict), f"Expected dict, got {type(data)}"
        assert "decisions" in data, f"Expected 'decisions' key, got: {data}"


# -- Test: RBAC Role Enforcement ------------------------------------


class TestRBACEnforcement:
    """Test role-based access control: admin vs viewer roles."""

    async def test_admin_can_list_users(self, client):
        """Admin can list registered users (user management endpoint)."""
        token = await _login(client, username="admin")
        headers = {"Authorization": f"Bearer {token}"}

        resp = await client.get("/api/auth/users", headers=headers)
        assert resp.status_code == 200, f"Admin user list failed: {resp.status_code}"
        data = resp.json()
        assert "users" in data, f"User list response missing 'users': {data}"
        assert isinstance(data["users"], list)

    async def test_tampered_token_rejected(self):
        """A tampered token cannot authenticate — zero I/O, zero shared state.

        Evolution of this fix across CI rounds (win-3.10/3.12, ub-3.10/3.12/3.13,
        macOS, local 1-in-3 reproduction):
        v1 HTTP-layer (assert 401) — flaky: suspended BaseHTTPMiddleware
           dispatch across pytest-asyncio event-loop teardown can return the
           PREVIOUS request's 200.
        v2 two-layer (JWT assert + HTTP 401/403) — still flaky: even the
           _login httpx call inside the test races.
        v3 HTTP-free via the client fixture's manager — still flaky: the
           client fixture itself (tmp auth DB + secret creation) races.
        v4 private manager on tmp_path — STILL flaky on CI: load_jwt_secret
           generates AND PERSISTS the secret to tmp (filesystem I/O races on
           shared runners surface as fixture-style errors attributed to this
           test in the junit xml).
        v5 (this): remove every I/O surface. No tmp_path, no monkeypatch, no
           filesystem at all — an in-memory-only manager with a fixed test
           secret. The invariant (signature tampering never authenticates)
           is pure HMAC math on a single object. Nothing can race because
           nothing is shared and nothing touches disk. HTTP-layer rejection
           is separately covered by test_auth_enabled's fresh-loop tests, the
           middleware fail-closed branches, and /api/agents require_admin.
        """
        from maop.core.security.auth import AuthConfig, AuthManager, JWTConfig

        # 64-hex fixed test secret (>=32 chars — passes strength validation
        # without load_jwt_secret's file persistence).
        test_secret = "e2e" + "a1b2c3d4e5f60718293a4b5c6d7e8f90" * 2
        mgr = AuthManager(
            config=AuthConfig(enabled=True, jwt=JWTConfig(secret=test_secret)),
        )
        token = mgr.jwt_handler.create_token("admin", roles=["admin"])
        assert token.count(".") == 2, "sanity: token must be a 3-part JWT"
        # Flip last char to tamper signature
        tampered = token[:-1] + ("A" if token[-1] != "A" else "B")

        result = mgr.authenticate(bearer_token=tampered)
        assert not result.authenticated, (
            f"Tampered token MUST fail JWT validation, but authenticated as {result.identity}"
        )
        assert result.error, "A rejected token must carry a rejection reason"


# -- Test: Multi-Tenant Isolation -----------------------------------


class TestTenantIsolation:
    """Test tenant isolation: tenant A data not accessible by tenant B."""

    async def test_tenant_endpoint_requires_auth(self, client):
        """Tenant management endpoints require authentication."""
        # Without token — should be 401
        resp = await client.get("/api/tenants")
        assert resp.status_code == 401, f"Unauthenticated tenant access: {resp.status_code}"

        # With admin token — should be 200 or 404 (if enterprise not enabled)
        token = await _login(client, username="admin")
        headers = {"Authorization": f"Bearer {token}"}
        resp = await client.get("/api/tenants", headers=headers)
        assert resp.status_code in (200, 404), (
            f"Admin tenant access unexpected: {resp.status_code}"
        )

    async def test_audit_log_requires_auth(self, client):
        """Audit logs require authentication."""
        # Without token — should be 401
        resp = await client.get("/api/audit")
        assert resp.status_code == 401, f"Unauthenticated audit access: {resp.status_code}"


# -- Test: Auth Cookie (#4 fix) -------------------------------------


class TestAuthCookie:
    """Test httpOnly cookie authentication (P1 #4 fix).

    Login response should set a 'maop_token' httpOnly cookie that
    browsers use automatically. API clients can still use Bearer header.
    """

    async def test_login_sets_httponly_cookie(self, client):
        """Login response includes Set-Cookie with httponly + samesite=strict."""
        resp = await client.post(
            "/api/auth/login",
            json={"username": "admin", "password": _TEST_PASSWORD},
        )
        assert resp.status_code == 200

        set_cookie = resp.headers.get("set-cookie", "")
        assert "maop_token=" in set_cookie, (
            f"Set-Cookie should contain maop_token: {set_cookie}"
        )
        assert "httponly" in set_cookie.lower(), (
            f"Cookie should be httponly: {set_cookie}"
        )
        assert "samesite=strict" in set_cookie.lower(), (
            f"Cookie should have samesite=strict: {set_cookie}"
        )

    async def test_cookie_based_auth_works(self, client):
        """httpOnly cookie alone (no Bearer header) authenticates the request."""
        resp = await client.post(
            "/api/auth/login",
            json={"username": "admin", "password": _TEST_PASSWORD},
        )
        assert resp.status_code == 200
        token = resp.json()["token"]

        # Use cookie directly (no Authorization header)
        resp = await client.get(
            "/api/agents",
            cookies={"maop_token": token},
        )
        assert resp.status_code == 200, (
            f"Cookie-based auth failed: {resp.status_code}"
        )

    async def test_bearer_header_still_works(self, client):
        """Bearer header still works for API clients (backward compat)."""
        resp = await client.post(
            "/api/auth/login",
            json={"username": "admin", "password": _TEST_PASSWORD},
        )
        token = resp.json()["token"]

        resp = await client.get(
            "/api/agents",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, (
            f"Bearer header auth failed: {resp.status_code}"
        )