"""Tests for maop.enterprise.sso — t12 real OAuth code exchange.

Mocks urllib.request.urlopen via monkeypatch to avoid real network calls.
Sets MAOP_EDITION=enterprise so require_feature(FeatureFlag.SSO) passes.
"""

from __future__ import annotations

import io
import json
import time
from typing import Any

import pytest

from maop.config.edition import Edition, reset_edition, set_edition
from maop.enterprise.sso import (
    SSOConfig,
    SSOError,
    SSOManager,
    SSOProvider,
    SSOSession,
)


@pytest.fixture(autouse=True)
def _enterprise_edition(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force enterprise edition so SSOManager init succeeds."""
    monkeypatch.setenv("MAOP_EDITION", "enterprise")
    set_edition(Edition.ENTERPRISE)
    yield
    reset_edition()


def _make_oidc_config(**overrides: Any) -> SSOConfig:
    defaults: dict[str, Any] = {
        "provider": SSOProvider.OIDC,
        "client_id": "test-client-id",
        "client_secret": "test-client-secret",
        "issuer_url": "https://idp.example.com/",
        "authorize_url": "https://idp.example.com/authorize",
        "token_url": "https://idp.example.com/token",
        "userinfo_url": "https://idp.example.com/userinfo",
        "redirect_uri": "https://maop.local/cb",
        "scopes": ["openid", "profile", "email"],
        "default_role": "viewer",
    }
    defaults.update(overrides)
    return SSOConfig(**defaults)


class _FakeResponse:
    """Minimal file-like response object compatible with urllib context mgr."""

    def __init__(self, payload: bytes, status: int = 200) -> None:
        self._buf = io.BytesIO(payload)
        self.status = status
        self._closed = False

    def read(self, n: int = -1) -> bytes:
        return self._buf.read(n if n != -1 else -1)

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *args: object) -> None:
        self._closed = True


# ── handle_callback OIDC real exchange ──────────────────────────────


class TestHandleCallbackOIDC:
    def test_exchange_code_success_builds_real_session(self, monkeypatch):
        config = _make_oidc_config()
        mgr = SSOManager(config)

        token_payload = json.dumps({
            "access_token": "AT-abc-123",
            "refresh_token": "RT-def-456",
            "expires_in": 1800,
            "token_type": "Bearer",
            "id_token": "eyJ.idtoken.payload",
        }).encode()
        # Default config has userinfo_url set, so handle_callback will make
        # TWO urlopen calls: token endpoint + userinfo. Capture all of them
        # and assert on the FIRST (token) call.
        calls: list[dict[str, Any]] = []

        def fake_urlopen(req, timeout=None):
            calls.append({
                "url": req.full_url,
                "method": req.method,
                "data": req.data.decode() if req.data else "",
                "headers": dict(req.headers),
            })
            # Return token JSON for the token call; empty userinfo for the
            # userinfo call (so we don't need to fabricate claims here).
            if "token" in req.full_url:
                return _FakeResponse(token_payload)
            return _FakeResponse(b"{}")

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

        session = mgr.handle_callback("AUTH_CODE_123", state="xyz")

        assert isinstance(session, SSOSession)
        assert session.access_token == "AT-abc-123"
        assert session.refresh_token == "RT-def-456"
        # expires_at = now + 1800 (tolerate small clock drift)
        assert abs((session.expires_at - session.created_at) - 1800) < 5
        # First call must be the token endpoint.
        assert len(calls) >= 1
        token_call = calls[0]
        assert token_call["url"] == "https://idp.example.com/token"
        assert token_call["method"] == "POST"
        assert "grant_type=authorization_code" in token_call["data"]
        assert "code=AUTH_CODE_123" in token_call["data"]
        assert "client_id=test-client-id" in token_call["data"]
        assert "client_secret=test-client-secret" in token_call["data"]

    def test_handle_callback_fetches_userinfo_and_builds_user(self, monkeypatch):
        config = _make_oidc_config()
        mgr = SSOManager(config)

        token_payload = json.dumps({
            "access_token": "AT-xyz",
            "refresh_token": "",
            "expires_in": 3600,
        }).encode()
        userinfo_payload = json.dumps({
            "sub": "user-42",
            "email": "alice@example.com",
            "name": "Alice Lee",
            "roles": ["admin", "viewer"],
            "tenant_id": "tenant-9",
        }).encode()
        call_count = {"n": 0}

        def fake_urlopen(req, timeout=None):
            call_count["n"] += 1
            if "token" in req.full_url:
                return _FakeResponse(token_payload)
            if "userinfo" in req.full_url:
                # Verify Bearer header is set.
                assert req.headers.get("Authorization") == "Bearer AT-xyz"
                return _FakeResponse(userinfo_payload)
            raise AssertionError(f"unexpected URL: {req.full_url}")

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

        session = mgr.handle_callback("CODE", state="")

        assert call_count["n"] == 2  # token + userinfo
        user = session.user
        assert user.external_id == "oidc:user-42"
        assert user.email == "alice@example.com"
        assert user.display_name == "Alice Lee"
        assert user.roles == ["admin", "viewer"]
        assert user.tenant_id == "tenant-9"

    def test_handle_callback_userinfo_failure_is_non_fatal(self, monkeypatch):
        config = _make_oidc_config()
        mgr = SSOManager(config)

        token_payload = json.dumps({
            "access_token": "AT",
            "expires_in": 3600,
        }).encode()

        def fake_urlopen(req, timeout=None):
            if "token" in req.full_url:
                return _FakeResponse(token_payload)
            # userinfo endpoint raises.
            raise OSError("userinfo down")

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

        session = mgr.handle_callback("CODE")

        # Session still created; user built from token_resp only.
        assert session.access_token == "AT"
        assert session.user.external_id == "oidc:unknown"
        assert session.user.roles == ["viewer"]  # default role

    def test_handle_callback_empty_code_raises(self):
        config = _make_oidc_config()
        mgr = SSOManager(config)
        with pytest.raises(ValueError, match="code.*must not be empty"):
            mgr.handle_callback("")

    def test_handle_callback_missing_token_url_raises(self):
        config = _make_oidc_config(token_url="")
        mgr = SSOManager(config)
        with pytest.raises(ValueError, match="token_url is required"):
            mgr.handle_callback("CODE")

    def test_handle_callback_token_endpoint_http_error_raises(self, monkeypatch):
        import urllib.error
        config = _make_oidc_config()
        mgr = SSOManager(config)

        def fake_urlopen(req, timeout=None):
            raise urllib.error.HTTPError(
                url=req.full_url, code=400,
                msg="Bad Request",
                hdrs=None,
                fp=io.BytesIO(b'{"error":"invalid_grant"}'),
            )

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

        with pytest.raises(RuntimeError, match="HTTP 400"):
            mgr.handle_callback("CODE")

    def test_handle_callback_token_endpoint_error_field_raises(self, monkeypatch):
        config = _make_oidc_config()
        mgr = SSOManager(config)

        error_payload = json.dumps({
            "error": "invalid_grant",
            "error_description": "The authorization code is invalid",
        }).encode()

        def fake_urlopen(req, timeout=None):
            return _FakeResponse(error_payload)

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

        with pytest.raises(RuntimeError, match="invalid_grant"):
            mgr.handle_callback("CODE")

    def test_handle_callback_non_json_response_raises(self, monkeypatch):
        config = _make_oidc_config()
        mgr = SSOManager(config)

        def fake_urlopen(req, timeout=None):
            return _FakeResponse(b"<html>Not Found</html>")

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

        with pytest.raises(RuntimeError, match="non-JSON"):
            mgr.handle_callback("CODE")

    def test_handle_callback_url_error_raises_runtime(self, monkeypatch):
        import urllib.error
        config = _make_oidc_config()
        mgr = SSOManager(config)

        def fake_urlopen(req, timeout=None):
            raise urllib.error.URLError("connection refused")

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

        with pytest.raises(RuntimeError, match="unreachable"):
            mgr.handle_callback("CODE")

    def test_handle_callback_non_numeric_expires_in_defaults(self, monkeypatch):
        config = _make_oidc_config()
        mgr = SSOManager(config)

        token_payload = json.dumps({
            "access_token": "AT",
            "expires_in": "not-a-number",
        }).encode()

        def fake_urlopen(req, timeout=None):
            return _FakeResponse(token_payload)

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

        session = mgr.handle_callback("CODE")
        # Default 3600 applied.
        assert abs((session.expires_at - session.created_at) - 3600) < 5


# ── handle_callback SAML — 显式拒绝（不再返回 stub session） ─────────


class TestHandleCallbackSAML:
    def test_saml_callback_raises_sso_error(self):
        # SAML 未实现，handle_callback 应显式抛 SSOError 而非返回 stub session。
        config = _make_oidc_config(provider=SSOProvider.SAML)
        mgr = SSOManager(config)
        with pytest.raises(SSOError, match="SAML SSO is not yet implemented"):
            mgr.handle_callback("SAML_CODE")

    def test_saml_empty_code_still_raises(self):
        # Even SAML must reject empty code (fail-closed).
        config = _make_oidc_config(provider=SSOProvider.SAML)
        mgr = SSOManager(config)
        with pytest.raises(ValueError):
            mgr.handle_callback("")


# ── _roles_from_claims ────────────────────────────────────────────────


class TestRolesFromClaims:
    def test_roles_from_list(self):
        mgr = SSOManager(_make_oidc_config())
        assert mgr._roles_from_claims({"roles": ["admin", "ops"]}) == ["admin", "ops"]

    def test_role_from_string(self):
        mgr = SSOManager(_make_oidc_config())
        assert mgr._roles_from_claims({"role": "editor"}) == ["editor"]

    def test_groups_fallback(self):
        mgr = SSOManager(_make_oidc_config())
        assert mgr._roles_from_claims({"groups": ["g1", "g2"]}) == ["g1", "g2"]

    def test_no_roles_returns_empty(self):
        mgr = SSOManager(_make_oidc_config())
        assert mgr._roles_from_claims({"sub": "x"}) == []

    def test_empty_list_returns_empty(self):
        mgr = SSOManager(_make_oidc_config())
        assert mgr._roles_from_claims({"roles": []}) == []


# ── get_authorize_url ─────────────────────────────────────────────────


class TestGetAuthorizeUrl:
    def test_oidc_authorize_url_includes_params(self):
        mgr = SSOManager(_make_oidc_config())
        url = mgr.get_authorize_url(state="rand123")
        assert url.startswith("https://idp.example.com/authorize?")
        assert "client_id=test-client-id" in url
        assert "response_type=code" in url
        # get_authorize_url joins scopes with a raw space (not URL-encoded).
        assert "scope=openid profile email" in url
        assert "state=rand123" in url

    def test_saml_authorize_url_raises_sso_error(self):
        # SAML 未实现，get_authorize_url 应在重定向前就抛 SSOError。
        mgr = SSOManager(_make_oidc_config(provider=SSOProvider.SAML))
        with pytest.raises(SSOError, match="SAML SSO is not yet implemented"):
            mgr.get_authorize_url()


# ── validate_session / logout ─────────────────────────────────────────


class TestSessionLifecycle:
    def test_validate_session_returns_session(self, monkeypatch):
        mgr = SSOManager(_make_oidc_config())
        token_payload = json.dumps({
            "access_token": "AT", "expires_in": 3600,
        }).encode()
        monkeypatch.setattr(
            "urllib.request.urlopen",
            lambda req, timeout=None: _FakeResponse(token_payload),
        )
        session = mgr.handle_callback("CODE")
        assert mgr.validate_session(session.session_id) is not None

    def test_validate_expired_session_returns_none(self, monkeypatch):
        mgr = SSOManager(_make_oidc_config())
        token_payload = json.dumps({
            "access_token": "AT", "expires_in": 3600,
        }).encode()
        monkeypatch.setattr(
            "urllib.request.urlopen",
            lambda req, timeout=None: _FakeResponse(token_payload),
        )
        session = mgr.handle_callback("CODE")
        # Force expiry by setting expires_at to a clearly past timestamp.
        # (Using 0.0 would be treated as "no expiry set" by validate_session's
        # truthy guard, so use time.time() - 100 instead.)
        session.expires_at = time.time() - 100
        assert mgr.validate_session(session.session_id) is None

    def test_logout_removes_session(self, monkeypatch):
        mgr = SSOManager(_make_oidc_config())
        token_payload = json.dumps({"access_token": "AT", "expires_in": 3600}).encode()
        monkeypatch.setattr(
            "urllib.request.urlopen",
            lambda req, timeout=None: _FakeResponse(token_payload),
        )
        session = mgr.handle_callback("CODE")
        assert mgr.logout(session.session_id) is True
        assert mgr.validate_session(session.session_id) is None
        assert mgr.logout(session.session_id) is False
