"""Tests for MAOP.core.middleware - Auth and Rate Limit middleware."""

from __future__ import annotations

from fastapi import Request as _Request

from maop.core.middleware import AuthMiddleware, RateLimitMiddleware, setup_middleware


class TestRateLimitMiddleware:
    def test_default_config(self):
        mw = RateLimitMiddleware(app=None, rate=30.0, burst=60, enabled=True)
        assert mw.enabled
        assert mw.rate == 30.0
        assert mw.burst == 60

    def test_disabled(self):
        mw = RateLimitMiddleware(app=None, enabled=False)
        assert not mw.enabled

    def test_custom_key_func(self):
        def custom(req):
            return "custom"
        mw = RateLimitMiddleware(app=None, key_func=custom)
        assert mw.key_func is custom


class TestAuthMiddleware:
    def test_default_config(self):
        mw = AuthMiddleware(app=None, enabled=True)
        assert mw.enabled
        assert "/api/health" in mw.public_paths
        assert "/" in mw.public_paths

    def test_disabled(self):
        mw = AuthMiddleware(app=None, enabled=False)
        assert not mw.enabled

    def test_custom_public_paths(self):
        mw = AuthMiddleware(app=None, public_paths=["/api/health", "/custom"])
        assert "/custom" in mw.public_paths
        assert "/api/health" in mw.public_paths

    def test_default_headers(self):
        mw = AuthMiddleware(app=None)
        assert mw.api_key_header == "X-API-Key"
        assert mw.auth_header == "Authorization"


class TestSetupMiddleware:
    def test_setup_creates_middleware(self):
        from fastapi import FastAPI
        app = FastAPI()
        setup_middleware(
            app,
            auth_enabled=False,
            rate_limit_enabled=True,
            rate_limit_per_sec=50.0,
            rate_limit_burst=100,
            cors_origins=["http://localhost:3000"],
        )
        # Verify middleware was added (Starlette stores them in user_middleware)
        assert len(app.user_middleware) >= 2  # CORS + RateLimit at minimum


class TestCSPMiddleware:
    """Tests for CSPMiddleware — Content-Security-Policy & security headers."""

    def test_default_config(self):
        from maop.core.middleware import CSPMiddleware
        mw = CSPMiddleware(app=None)
        assert mw.enabled
        assert not mw.report_only
        assert mw.report_uri is None

    def test_disabled(self):
        from maop.core.middleware import CSPMiddleware
        mw = CSPMiddleware(app=None, enabled=False)
        assert not mw.enabled

    def test_csp_value_contains_required_directives(self):
        from maop.core.middleware import CSPMiddleware
        mw = CSPMiddleware(app=None)
        csp = mw._csp_value
        assert "default-src 'self'" in csp
        assert "script-src 'self'" in csp
        assert "'unsafe-inline'" not in csp.split("script-src")[1].split(";")[0]
        assert "style-src 'self'" in csp
        assert "'unsafe-inline'" not in csp.split("style-src")[1].split(";")[0]
        assert "object-src 'none'" in csp
        assert "frame-ancestors 'none'" in csp
        assert "base-uri 'self'" in csp
        assert "form-action 'self'" in csp

    def test_security_headers_present(self):
        from maop.core.middleware import CSPMiddleware
        mw = CSPMiddleware(app=None)
        headers = mw._security_headers
        assert headers["X-Content-Type-Options"] == "nosniff"
        assert headers["X-Frame-Options"] == "DENY"
        assert headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
        assert "Permissions-Policy" in headers

    def test_hsts_header_present(self):
        """HSTS (Strict-Transport-Security) header is set for HTTPS enforcement."""
        from maop.core.middleware import CSPMiddleware
        mw = CSPMiddleware(app=None)
        hsts = mw._security_headers["Strict-Transport-Security"]
        assert "max-age=31536000" in hsts
        assert "includeSubDomains" in hsts
        assert "preload" in hsts

    def test_additional_security_headers(self):
        """Additional restrictive security headers are present."""
        from maop.core.middleware import CSPMiddleware
        mw = CSPMiddleware(app=None)
        headers = mw._security_headers
        assert headers["X-DNS-Prefetch-Control"] == "off"
        assert headers["X-Permitted-Cross-Domain-Policies"] == "none"
        assert headers["Cross-Origin-Opener-Policy"] == "same-origin"
        assert headers["Cross-Origin-Resource-Policy"] == "same-origin"

    def test_hsts_header_on_response(self):
        """Integration: HSTS header appears on actual HTTP response."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from maop.core.middleware import CSPMiddleware

        app = FastAPI()
        app.add_middleware(CSPMiddleware, enabled=True)

        @app.get("/test")
        def _test():
            return {"ok": True}

        client = TestClient(app)
        resp = client.get("/test")
        assert resp.status_code == 200
        assert "strict-transport-security" in {k.lower() for k in resp.headers}
        assert "x-dns-prefetch-control" in {k.lower() for k in resp.headers}
        assert "cross-origin-opener-policy" in {k.lower() for k in resp.headers}

    def test_report_only_mode(self):
        from maop.core.middleware import CSPMiddleware
        mw = CSPMiddleware(app=None, report_only=True)
        assert mw.report_only

    def test_report_uri_added_to_directives(self):
        from maop.core.middleware import CSPMiddleware
        mw = CSPMiddleware(app=None, report_uri="/api/csp-report")
        assert "report-uri /api/csp-report" in mw._csp_value

    def test_extra_directives_appended(self):
        from maop.core.middleware import CSPMiddleware
        mw = CSPMiddleware(app=None, extra_directives="upgrade-insecure-requests")
        assert "upgrade-insecure-requests" in mw._csp_value

    def test_csp_header_set_on_response(self):
        """Integration: verify CSP header appears on actual HTTP response."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from maop.core.middleware import CSPMiddleware

        app = FastAPI()
        app.add_middleware(CSPMiddleware, enabled=True)

        @app.get("/test")
        def _test():
            return {"ok": True}

        client = TestClient(app)
        resp = client.get("/test")
        assert resp.status_code == 200
        assert "content-security-policy" in {k.lower() for k in resp.headers}
        csp = resp.headers["content-security-policy"]
        assert "default-src 'self'" in csp
        assert resp.headers["x-content-type-options"] == "nosniff"
        assert resp.headers["x-frame-options"] == "DENY"

    def test_csp_disabled_no_header(self):
        """When disabled, no CSP header should be present."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from maop.core.middleware import CSPMiddleware

        app = FastAPI()
        app.add_middleware(CSPMiddleware, enabled=False)

        @app.get("/test")
        def _test():
            return {"ok": True}

        client = TestClient(app)
        resp = client.get("/test")
        assert "content-security-policy" not in {k.lower() for k in resp.headers}

    def test_report_only_uses_correct_header_name(self):
        """Report-only mode uses Content-Security-Policy-Report-Only."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from maop.core.middleware import CSPMiddleware

        app = FastAPI()
        app.add_middleware(CSPMiddleware, enabled=True, report_only=True)

        @app.get("/test")
        def _test():
            return {"ok": True}

        client = TestClient(app)
        resp = client.get("/test")
        header_keys_lower = {k.lower() for k in resp.headers}
        assert "content-security-policy-report-only" in header_keys_lower
        assert "content-security-policy" not in header_keys_lower

    def test_custom_connect_src(self):
        """connect_src parameter allows cross-origin WebSocket/API."""
        from maop.core.middleware import CSPMiddleware
        mw = CSPMiddleware(app=None, connect_src="'self' wss://other.host:9079")
        assert "connect-src 'self' wss://other.host:9079" in mw._csp_value

    def test_default_connect_src_is_self(self):
        """Default connect-src should be 'self'."""
        from maop.core.middleware import CSPMiddleware
        mw = CSPMiddleware(app=None)
        assert "connect-src 'self'" in mw._csp_value

    def test_csp_report_endpoint(self):
        """POST /api/csp-report accepts violation reports."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        app = FastAPI()
        _violations: list = []

        @app.post("/api/csp-report")
        async def csp_report(request: _Request):
            body = await request.json()
            _violations.append(body)
            return {"status": "ok"}

        client = TestClient(app)
        resp = client.post("/api/csp-report", json={
            "csp-report": {
                "document-uri": "http://localhost:9079/",
                "violated-directive": "script-src",
                "blocked-uri": "inline",
            }
        })
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
        assert len(_violations) == 1


# ── Regression tests for security Critical fixes ──

from fastapi import FastAPI, Request
from starlette.testclient import TestClient


class TestAuthDisabledDefaultRole:
    """C-1: when auth is disabled, default role MUST be 'read' (not 'admin').

    A misconfigured deployment (e.g. MAOP_AUTH=0 in non-prod) must not
    grant anonymous users admin privileges. Operators who explicitly opt
    in can set MAOP_AUTH_DISABLED_ADMIN=1, but the default is safe.
    """

    def _make_app(self, *, enabled: bool) -> FastAPI:
        from maop.core.middleware import AuthMiddleware
        app = FastAPI()

        @app.get("/api/whoami")
        def whoami(request: Request):
            return {
                "identity": getattr(request.state, "auth_identity", "anonymous"),
                "roles": getattr(request.state, "auth_roles", []),
            }

        app.add_middleware(AuthMiddleware, enabled=enabled)
        return app

    def test_disabled_auth_defaults_to_read_role(self, monkeypatch):
        """Default (no env var) → read role."""
        monkeypatch.delenv("MAOP_AUTH_DISABLED_ADMIN", raising=False)
        app = self._make_app(enabled=False)
        with TestClient(app) as client:
            resp = client.get("/api/whoami")
        assert resp.status_code == 200
        body = resp.json()
        assert body["roles"] == ["read"], (
            "Auth-disabled default role must be 'read' (got {body['roles']!r}). "
            "Set MAOP_AUTH_DISABLED_ADMIN=1 to opt into admin role."
        )

    def test_disabled_auth_admin_opt_in_via_env(self, monkeypatch):
        """Explicit env var opt-in still works for legacy/dev workflows."""
        monkeypatch.setenv("MAOP_AUTH_DISABLED_ADMIN", "1")
        app = self._make_app(enabled=False)
        with TestClient(app) as client:
            resp = client.get("/api/whoami")
        assert resp.status_code == 200
        assert resp.json()["roles"] == ["admin"]

    def test_disabled_auth_explicit_zero_is_read(self, monkeypatch):
        """MAOP_AUTH_DISABLED_ADMIN=0 explicitly → read role."""
        monkeypatch.setenv("MAOP_AUTH_DISABLED_ADMIN", "0")
        app = self._make_app(enabled=False)
        with TestClient(app) as client:
            resp = client.get("/api/whoami")
        assert resp.json()["roles"] == ["read"]
