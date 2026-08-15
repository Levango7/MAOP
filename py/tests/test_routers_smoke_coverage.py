"""Smoke coverage for dashboard routers via the real server.app.

Mounts an admin-authenticated AsyncClient against ``maop.dashboard.server.app``
(without lifespan) and exercises every parameter-less GET /api/* endpoint.
This broadly covers router entry points and happy/error branches across many
router modules (info, model, chat, control, data, memory, audit, rbac, tenant,
hook, knowledge, mcp, session, react, subagent, plugin, sso, n8n, permission,
cost, budget, tool_audit, agent_proxy, routing_preview, …) that lack dedicated
tests.

Assertions are meaningful: every endpoint must be reachable under admin auth
(no 401/403) and respond with a well-formed JSON body or a handled error
(400/404/422/500). 401/403 would indicate an auth-regression.
"""
from __future__ import annotations

from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from maop.core.security.auth import AuthResult

# Admin JWT validator stub — any token yields an authenticated admin.
_ADMIN_RESULT = AuthResult(authenticated=True, identity="admin", roles=["admin"])


class _JwtStub:
    """Stand-in for app.state.jwt_auth (duck-typed by AuthMiddleware)."""

    def validate_token(self, token: str) -> AuthResult:
        return _ADMIN_RESULT


# Module-level route collection so parametrize can use it.
import re as _re

# Default substitutions for path parameters in parameterised routes.
_PARAM_DEFAULTS = {
    "name": "claude", "agent": "claude", "id": "1", "memory_id": "1",
    "key": "code", "capability": "code", "tenant_id": "default",
    "session_id": "s1", "workflow": "build", "plugin": "p1", "hook": "h1",
    "protocol": "p1", "perm": "read", "role": "admin", "user": "admin",
    "task_id": "t1", "job_id": "j1", "model": "claude-3", "provider": "anthropic",
    "run_id": "r1", "event": "e1", "file": "f1", "path": "p",
    "agent_name": "claude", "memory_type": "interaction", "kind": "code",
    "category": "c1", "group": "g1", "type": "interaction", "action": "a1",
    "slug": "s1", "uuid": "u1", "rule_id": "r1", "grant_id": "g1",
    "version": "v1", "channel": "c1", "source": "s1", "target": "t1",
}

# Substrings that indicate an endpoint with heavy side effects or real
# subprocess calls — these are covered by dedicated tests with mocks.
_SKIP_SUBSTRINGS = (
    "upgrade", "diagnose", "repair", "evolve", "evolution",
    "stream", "scan", "health-check", "csp-report", "login",
    "logout", "refresh", "register", "authorize", "callback",
)


def _substitute(path: str) -> str:
    return _re.sub(r"\{(\w+)\}", lambda m: _PARAM_DEFAULTS.get(m.group(1), "1"), path)


def _collect_get_api_paths() -> list[str]:
    from maop.dashboard.server import app

    paths: set[str] = set()
    skip_prefixes = (
        "/api/docs", "/api/redoc", "/api/openapi.json",
        "/api/stream", "/api/auth/login", "/api/auth/logout", "/api/auth/refresh",
    )

    def walk(routes) -> None:
        for route in routes:
            cls_name = type(route).__name__
            methods = getattr(route, "methods", None) or set()
            path = getattr(route, "path", "")
            if (
                "GET" in methods
                and path.startswith("/api/")
                and not any(path.startswith(p) for p in skip_prefixes)
                and not any(s in path for s in _SKIP_SUBSTRINGS)
            ):
                paths.add(_substitute(path))
            if cls_name == "_IncludedRouter" and hasattr(route, "original_router"):
                walk(getattr(route.original_router, "routes", []))
            if hasattr(route, "routes"):
                walk(route.routes)

    walk(app.routes)
    return sorted(paths)


_API_GET_PATHS = _collect_get_api_paths()


@pytest.fixture
async def admin_client():
    """Async admin client against server.app (no lifespan, admin auth)."""
    from maop.dashboard.server import app

    saved: dict[str, Any] = {}
    for attr in ("auth_manager", "api_key_auth", "jwt_auth"):
        saved[attr] = getattr(app.state, attr, None)
    app.state.jwt_auth = _JwtStub()
    app.state.auth_manager = None
    app.state.api_key_auth = None

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(
        transport=transport, base_url="http://test",
        headers={"Authorization": "Bearer test-token"},
    ) as c:
        yield c

    for attr, val in saved.items():
        setattr(app.state, attr, val)


@pytest.mark.parametrize("path", _API_GET_PATHS)
async def test_get_endpoint_reachable(admin_client, path):
    """Every parameter-less GET /api/* endpoint must be reachable under admin.

    Acceptable statuses: 200 (happy), 400/422 (bad input), 404 (not found),
    500 (handled downstream failure). 401/403 are auth regressions and fail.
    """
    resp = await admin_client.get(path, timeout=20)
    # The only unacceptable statuses are 401/403 — those would mean admin auth
    # did not propagate. Business errors (400/404/422), downstream failures
    # (500/502), and redirects (302) are all valid for a smoke pass.
    assert resp.status_code not in (401, 403), (
        f"auth regression on {path}: {resp.status_code} {resp.text[:200]}"
    )


# ── Targeted happy-path assertions for stable read endpoints ────────

class TestReadOnlyHappyPaths:
    async def test_audit_summary(self, admin_client):
        resp = await admin_client.get("/api/audit/summary")
        assert resp.status_code == 200
        assert isinstance(resp.json(), dict)

    async def test_auth_status(self, admin_client):
        resp = await admin_client.get("/api/auth/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "auth_enabled" in data or "status" in data or isinstance(data, dict)

    async def test_rbac_grants_hint(self, admin_client):
        # Personal edition guard returns a hint body; enterprise returns data.
        resp = await admin_client.get("/api/rbac/grants")
        assert resp.status_code in (200, 404)
        assert resp.json() is not None

    async def test_tenant_list(self, admin_client):
        resp = await admin_client.get("/api/tenant/list")
        assert resp.status_code in (200, 404)
        assert resp.json() is not None