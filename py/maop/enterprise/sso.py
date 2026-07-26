"""MAOP Enterprise SSO — SAML/OIDC Single Sign-On Integration.

SAML provider is currently disabled. OIDC is fully supported.

Provides enterprise identity provider integration:
  - OpenID Connect (via authlib) — 完整支持，含 token exchange 与 userinfo
  - SAML 2.0 — **当前未实现**，显式拒绝以避免 stub session 安全风险。
    如需 SAML 支持，请安装 pysaml2 并实现 IdP metadata 解析与 XML 签名验证。
  - Automatic user provisioning from IdP claims
  - Session management and token refresh
"""

from __future__ import annotations

import contextlib
import json
import logging
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from maop.config.edition import FeatureFlag, require_feature

logger = logging.getLogger(__name__)


class SSOError(RuntimeError):
    """SSO 相关错误（如未实现的 provider、配置缺失等）。

    继承 RuntimeError 以保持与现有 handle_callback 中 RuntimeError
    抛出风格的兼容性，同时提供更精确的类型供上层 catch。
    """


class SSOProvider(str, Enum):
    SAML = "saml"
    OIDC = "oidc"


class SSOConfig(BaseModel):
    provider: SSOProvider = SSOProvider.OIDC
    client_id: str = ""
    client_secret: str = ""
    issuer_url: str = ""
    authorize_url: str = ""
    token_url: str = ""
    userinfo_url: str = ""
    saml_metadata_url: str = ""
    saml_entity_id: str = ""
    redirect_uri: str = ""
    scopes: list[str] = Field(default_factory=lambda: ["openid", "profile", "email"])
    auto_provision: bool = True
    default_role: str = "viewer"


class SSOUser(BaseModel):
    external_id: str
    email: str = ""
    display_name: str = ""
    roles: list[str] = Field(default_factory=list)
    tenant_id: str = ""
    provider: SSOProvider = SSOProvider.OIDC
    last_login: float = 0.0


class SSOSession(BaseModel):
    session_id: str
    user: SSOUser
    access_token: str = ""
    refresh_token: str = ""
    expires_at: float = 0.0
    created_at: float = 0.0


class SSOManager:
    """Enterprise SSO integration manager."""

    def __init__(self, config: SSOConfig | None = None) -> None:
        require_feature(FeatureFlag.SSO)
        self._config = config or SSOConfig()
        self._sessions: dict[str, SSOSession] = {}

    @property
    def config(self) -> SSOConfig:
        return self._config

    def get_authorize_url(self, state: str = "") -> str:
        # SAML 当前未实现，在重定向前就显式拒绝，避免用户跳转后才失败。
        if self._config.provider == SSOProvider.SAML:
            raise SSOError(
                "SAML SSO is not yet implemented. Please configure OIDC instead."
            )
        if self._config.provider == SSOProvider.OIDC:
            params = {
                "client_id": self._config.client_id,
                "redirect_uri": self._config.redirect_uri,
                "response_type": "code",
                "scope": " ".join(self._config.scopes),
            }
            if state:
                params["state"] = state
            query = "&".join(f"{k}={v}" for k, v in params.items())
            return f"{self._config.authorize_url}?{query}"
        return ""

    def handle_callback(self, code: str, state: str = "") -> SSOSession:
        """Exchange an OAuth authorization code for tokens and create a session.

        For OIDC: POST to ``SSOConfig.token_url`` with the standard
        ``authorization_code`` grant, parse ``{access_token, refresh_token,
        expires_in, id_token}``, optionally fetch userinfo from
        ``userinfo_url`` with Bearer auth, then build a real ``SSOSession``
        with the returned tokens and expiry.

        For SAML: raises ``SSOError`` — SAML is not yet implemented.
        Full SAML requires XML signature verification and IdP metadata
        handling (python3-saml / pysaml2),工作量较大故当前显式禁用。

        Args:
            code: Authorization code received at the redirect_uri.
            state: Optional OAuth state value (echoed, not yet validated).

        Returns:
            A new SSOSession persisted in ``self._sessions``.

        Raises:
            ValueError: ``code`` is empty, or OIDC config is missing
                ``token_url``.
            RuntimeError: Token endpoint returned an error response, a
                non-JSON body, or was unreachable.
            SSOError: Provider is SAML (not yet implemented).
        """
        if not code:
            raise ValueError("handle_callback: 'code' must not be empty")

        if self._config.provider == SSOProvider.SAML:
            return self._handle_saml_callback(code)

        # OIDC: real OAuth authorization_code exchange.
        if not self._config.token_url:
            raise ValueError(
                "SSOConfig.token_url is required for OIDC handle_callback"
            )

        token_resp = self._exchange_code(code, state)
        access_token = str(token_resp.get("access_token", ""))
        refresh_token = str(token_resp.get("refresh_token", ""))
        try:
            expires_in = float(token_resp.get("expires_in", 3600))
        except (TypeError, ValueError):
            logger.warning(
                "[sso] token endpoint returned non-numeric expires_in=%r; "
                "defaulting to 3600",
                token_resp.get("expires_in"),
            )
            expires_in = 3600.0

        now = time.time()

        user_claims: dict[str, Any] = {}
        if access_token and self._config.userinfo_url:
            user_claims = self._fetch_userinfo(access_token)

        user = self._build_user_from_claims(user_claims, token_resp)
        session_id = f"sess_{secrets.token_hex(16)}_{int(now)}"
        session = SSOSession(
            session_id=session_id,
            user=user,
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=now + expires_in,
            created_at=now,
        )
        self._sessions[session_id] = session
        logger.info(
            "[sso] OIDC session=%s user=%s expires_in=%ss",
            session_id, user.external_id, int(expires_in),
        )
        return session

    def _exchange_code(self, code: str, state: str) -> dict[str, Any]:
        """POST authorization_code grant to ``token_url``; return parsed JSON.

        Fail-closed on HTTP errors, network errors, non-JSON responses, and
        OAuth ``error`` fields in the response body.
        """
        body = urllib.parse.urlencode({
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self._config.redirect_uri,
            "client_id": self._config.client_id,
            "client_secret": self._config.client_secret,
        }).encode("utf-8")
        req = urllib.request.Request(
            self._config.token_url,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                payload = resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = ""
            with contextlib.suppress(Exception):
                detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(
                f"SSO token endpoint returned HTTP {exc.code}: {detail}"
            ) from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(
                f"SSO token endpoint unreachable: {exc.reason}"
            ) from exc

        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"SSO token endpoint returned non-JSON response: "
                f"{payload[:200]!r}"
            ) from exc

        if not isinstance(parsed, dict):
            raise TypeError(
                f"SSO token endpoint returned non-object JSON: {type(parsed).__name__}"
            )
        if "error" in parsed:
            err = parsed.get("error")
            desc = parsed.get("error_description", "")
            raise RuntimeError(
                f"SSO token endpoint error: {err} — {desc}"
            )
        return parsed

    def _fetch_userinfo(self, access_token: str) -> dict[str, Any]:
        """GET ``userinfo_url`` with Bearer auth; return parsed claims.

        Non-fatal: returns ``{}`` on any error (logged at warning level)
        so a broken userinfo endpoint doesn't block login.
        """
        req = urllib.request.Request(
            self._config.userinfo_url,
            method="GET",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                payload = resp.read().decode("utf-8")
            parsed: Any = json.loads(payload)
            return parsed if isinstance(parsed, dict) else {}
        except (urllib.error.URLError, json.JSONDecodeError, OSError) as exc:
            logger.warning("[sso] userinfo fetch failed: %s", exc)
            return {}

    def _build_user_from_claims(
        self, claims: dict[str, Any], token_resp: dict[str, Any]
    ) -> SSOUser:
        """Construct ``SSOUser`` from IdP claims + token response.

        Claim precedence (first non-empty wins):
          - external_id: ``sub`` | ``user_id`` | first 16 chars of id_token
          - email: ``email`` | ``email_verified`` (bool -> "")
          - display_name: ``name`` | ``preferred_username`` | ``nickname``
          - roles: ``roles`` (list) | ``role`` (str|list) | ``groups``
          - tenant_id: ``tenant_id`` | ``tid``
        """
        now = time.time()
        sub = (
            claims.get("sub")
            or claims.get("user_id")
            or ""
        )
        if not sub and token_resp.get("id_token"):
            sub = str(token_resp["id_token"])[:16]
        if not sub:
            sub = "unknown"

        email = str(claims.get("email", "") or "")
        name = (
            claims.get("name")
            or claims.get("preferred_username")
            or claims.get("nickname")
            or ""
        )
        roles = self._roles_from_claims(claims)
        if not roles:
            roles = [self._config.default_role]
        tenant_id = (
            claims.get("tenant_id")
            or claims.get("tid")
            or ""
        )
        return SSOUser(
            external_id=f"{self._config.provider.value}:{sub}",
            email=email,
            display_name=str(name),
            roles=roles,
            tenant_id=str(tenant_id),
            provider=self._config.provider,
            last_login=now,
        )

    def _roles_from_claims(self, claims: dict[str, Any]) -> list[str]:
        """Extract roles from common claim shapes.

        Supports ``roles`` (list), ``role`` (str or list), and ``groups``
        (list). Returns an empty list if none are present (caller applies
        the default role).
        """
        for key in ("roles", "role", "groups"):
            v = claims.get(key)
            if isinstance(v, list) and v:
                return [str(r) for r in v]
            if isinstance(v, str) and v.strip():
                return [v.strip()]
        return []

    def _handle_saml_callback(self, code: str) -> SSOSession:
        """处理 SAML 回调。

        注意：SAML 完整实现需要 python3-saml 或 pysaml2 库。
        当前版本未集成，显式拒绝以避免 stub session 安全风险
        （原实现返回无 token 的占位 session，可被绕过认证）。
        如需 SAML 支持，请安装 pysaml2 并实现 IdP metadata 解析与
        XML 签名验证。

        Raises:
            SSOError: 始终抛出，SAML 当前未实现。
        """
        raise SSOError(
            "SAML SSO is not yet implemented. "
            "Use OIDC provider instead, or install pysaml2 and implement "
            "SAML response parsing with XML signature verification."
        )

    def validate_session(self, session_id: str) -> SSOSession | None:
        session = self._sessions.get(session_id)
        if not session:
            return None
        if session.expires_at and time.time() > session.expires_at:
            del self._sessions[session_id]
            return None
        return session

    def logout(self, session_id: str) -> bool:
        if session_id in self._sessions:
            del self._sessions[session_id]
            return True
        return False
