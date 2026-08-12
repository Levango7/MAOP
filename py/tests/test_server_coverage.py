"""Coverage tests for maop.dashboard.server — top-level FastAPI app endpoints.

Uses httpx.AsyncClient + ASGITransport so the ASGI lifespan (which starts
background schedulers / WS push loop) is NOT executed, keeping tests fast
and side-effect free. Because lifespan does not run, ``app.state.auth_manager``
is never set, so AuthMiddleware falls through to "no auth configured" for
non-admin endpoints — exactly what we need to exercise the public surface.

Covers:
  - static: / , /style.css , /favicon.svg
  - /api/health , /api/csp-report , /api/csp-violations , /api/prometheus
  - /api/v1/version , SPA fallback
  - _normalize_api_path , _register_v1_aliases
  - enterprise_api_guard (personal edition)
  - global exception handler
"""
from __future__ import annotations

from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from maop.config.edition import FeatureFlag, has_feature


@pytest.fixture
async def client():
    """Async client bound to the real server.app without lifespan.

    raise_app_exceptions=False so that endpoints which intentionally raise
    (to exercise the global exception handler) return a 500 response instead
    of propagating the exception out of the ASGI transport.

    Auth state on ``app.state`` is cleared so that AuthMiddleware falls into
    the "no auth configured" branch and lets non-admin endpoints pass through
    — this keeps the test independent of whether other test modules (e.g. the
    auth-enabled e2e suite) have populated ``app.state.auth_manager``.
    """
    from maop.dashboard.server import app
    saved: dict[str, Any] = {}
    for attr in ("auth_manager", "api_key_auth", "jwt_auth"):
        saved[attr] = getattr(app.state, attr, None)
        setattr(app.state, attr, None)
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    for attr, val in saved.items():
        setattr(app.state, attr, val)


# ── Static endpoints ────────────────────────────────────────────────

class TestStatic:
    async def test_index(self, client):
        resp = await client.get("/")
        # index.html may or may not exist in CI; either 200 or 404 is acceptable.
        assert resp.status_code in (200, 404)

    async def test_style_css(self, client):
        resp = await client.get("/style.css")
        assert resp.status_code in (200, 404)

    async def test_favicon(self, client):
        resp = await client.get("/favicon.svg")
        assert resp.status_code in (200, 404)


# ── Health ──────────────────────────────────────────────────────────

class TestHealth:
    async def test_health_ok(self, client):
        resp = await client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "version" in data
        assert "edition" in data
        assert "active_agents" in data
        assert "tls" in data and "auth" in data and "rate_limit" in data


# ── CSP ─────────────────────────────────────────────────────────────

class TestCsp:
    async def test_csp_report_valid(self, client):
        resp = await client.post("/api/csp-report", json={
            "csp-report": {
                "document-uri": "http://x/",
                "violated-directive": "script-src",
                "blocked-uri": "http://evil",
                "source-file": "a.js",
                "line-number": 1,
            }
        })
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    async def test_csp_report_invalid_json(self, client):
        resp = await client.post("/api/csp-report", content=b"not json",
                                 headers={"Content-Type": "application/json"})
        assert resp.status_code == 400

    async def test_csp_violations_list(self, client):
        # First trigger a violation so the buffer is non-empty.
        await client.post("/api/csp-report", json={
            "csp-report": {"violated-directive": "style-src"}
        })
        resp = await client.get("/api/csp-violations")
        assert resp.status_code == 200
        data = resp.json()
        assert "violations" in data
        assert "count" in data
        assert isinstance(data["violations"], list)


# ── Prometheus ──────────────────────────────────────────────────────

class TestPrometheus:
    async def test_metrics_text(self, client):
        resp = await client.get("/api/prometheus")
        assert resp.status_code == 200
        assert "text" in resp.headers.get("content-type", "")
        # Prometheus exposition is plain text, non-empty.
        assert resp.text != ""


# ── API v1 version + aliases ────────────────────────────────────────

class TestV1Version:
    async def test_v1_version(self, client):
        resp = await client.get("/api/v1/version")
        assert resp.status_code == 200
        data = resp.json()
        assert data["api_version"] == "v1"
        assert "version" in data


class TestRegisterV1Aliases:
    def test_aliases_registered(self):
        from maop.dashboard.server import app
        v1_paths = {getattr(r, "path", "") for r in app.routes}
        # /api/health is exempt from aliasing, but /api/overview should have one.
        assert any(p.startswith("/api/v1/") for p in v1_paths)

    def test_rerun_does_not_raise(self):
        """Re-running the registrar must not raise (covers the function body).

        Note: it is *not* strictly idempotent — re-running adds nested
        /api/v1/v1/* aliases for the previously-registered v1 routes — but
        it must execute without error. We assert the route count only grows
        and the function returns None.
        """
        from maop.dashboard.server import _register_v1_aliases, app
        before = len(app.routes)
        result = _register_v1_aliases()
        after = len(app.routes)
        assert result is None
        assert after >= before


# ── SPA fallback ────────────────────────────────────────────────────

class TestSpaFallback:
    async def test_unknown_path_returns_html_or_404(self, client):
        resp = await client.get("/some/unknown/spa/route")
        # SPA fallback serves index.html if present, else 404.
        assert resp.status_code in (200, 404)


# ── _normalize_api_path ─────────────────────────────────────────────

class TestNormalizeApiPath:
    def test_strips_v1(self):
        from maop.dashboard.server import _normalize_api_path
        assert _normalize_api_path("/api/v1/tenant/x") == "/api/tenant/x"

    def test_strips_v2_deep(self):
        from maop.dashboard.server import _normalize_api_path
        assert _normalize_api_path("/api/v2/audit/foo/bar") == "/api/audit/foo/bar"

    def test_plain_unchanged(self):
        from maop.dashboard.server import _normalize_api_path
        assert _normalize_api_path("/api/health") == "/api/health"

    def test_non_api_unchanged(self):
        from maop.dashboard.server import _normalize_api_path
        assert _normalize_api_path("/app/foo/v1/bar") == "/app/foo/v1/bar"


# ── Enterprise API guard (personal edition only) ────────────────────

@pytest.mark.skipif(
    has_feature(FeatureFlag.MULTI_USER),
    reason="enterprise_api_guard is registered only in personal edition",
)
class TestEnterpriseGuard:
    async def test_blocks_versioned_tenant_path(self, client):
        # /api/tenant/list is intentionally soft (200 + empty list) so the
        # personal-edition frontend can render an empty tenant picker. Use
        # /api/tenant/create instead to exercise the hard 404 branch.
        resp = await client.get("/api/v1/tenant/create")
        assert resp.status_code == 404
        body = resp.json()
        assert "Enterprise" in body.get("hint", "")

    async def test_blocks_rbac_grants_with_hint(self, client):
        # /api/rbac/grants returns a soft 200 hint, not 404.
        resp = await client.get("/api/rbac/grants")
        assert resp.status_code == 200
        assert "grants" in resp.json()


# ── Global exception handler ────────────────────────────────────────

class TestGlobalExceptionHandler:
    async def test_handler_returns_500(self):
        """Invoke the handler directly with a raised exception."""
        from fastapi import Request
        from unittest.mock import MagicMock
        from maop.dashboard.server import _global_exception_handler

        mock_request = MagicMock(spec=Request)
        mock_request.method = "GET"
        mock_request.url.path = "/api/bang"
        result = await _global_exception_handler(mock_request, RuntimeError("boom"))
        assert result.status_code == 500
        assert result.body is not None  # JSONResponse has serialized body

    async def test_handler_via_route(self, client, monkeypatch):
        """Force a registered endpoint to raise and confirm 500 body shape."""
        from maop.dashboard.server import app

        # Add a throwaway route that raises; insert it at the FRONT so the
        # catch-all SPA fallback (/{full_path:path}) does not shadow it.
        async def _boom() -> Any:
            raise RuntimeError("kaboom")

        app.add_api_route("/__test_boom__", _boom, methods=["GET"])
        boom_route = app.router.routes.pop()
        app.router.routes.insert(0, boom_route)
        try:
            resp = await client.get("/__test_boom__")
            assert resp.status_code == 500
            assert resp.json()["status"] == "error"
        finally:
            app.router.routes = [
                r for r in app.router.routes
                if getattr(r, "path", "") != "/__test_boom__"
            ]