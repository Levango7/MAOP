"""MAOP Enterprise SSO — SAML/OIDC Single Sign-On Integration.

Provides enterprise identity provider integration:
  - SAML 2.0 (via python3-saml / pysaml2)
  - OpenID Connect (via authlib)
  - Automatic user provisioning from IdP claims
  - Session management and token refresh
"""

from __future__ import annotations

import json
import logging
import secrets
import time
from enum import Enum
from typing import Any
import urllib.error
import urllib.parse
import urllib.request

from pydantic import BaseModel, Field

from maop.config.edition import require_feature, FeatureFlag

logger = logging.getLogger(__name__)


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

        For SAML: returns a placeholder session and logs a warning — full
        SAML requires XML signature verification and IdP metadata handling
        which is out of scope for this task.

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
        """
        if not code:
            raise ValueError("handle_callback: 'code' must not be empty")

        if self._config.provider == SSOProvider.SAML:
            logger.warning(
                "[sso] SAML handle_callback not yet implemented; "
                "returning stub session (no token exchange)"
            )
            return self._stub_session(code)

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
            try:
                detail = exc.read().decode("utf-8", errors="replace")
            except Exception:
                pass
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
            raise RuntimeError(
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

    def _stub_session(self, code: str) -> SSOSession:
        """Build a placeholder session without performing token exchange.

        Used for SAML (full implementation pending) and any future
        provider that lacks a real ``handle_callback`` path.
        """
        now = time.time()
        user = SSOUser(
            external_id=f"sso_{self._config.provider.value}_{abs(hash(code)) % 100000}",
            email="",
            display_name="SSO User",
            provider=self._config.provider,
            roles=[self._config.default_role],
            last_login=now,
        )
        session_id = f"sess_{abs(hash(code)) % 1000000}_{int(now)}"
        session = SSOSession(
            session_id=session_id,
            user=user,
            expires_at=now + 3600,
            created_at=now,
        )
        self._sessions[session_id] = session
        return session

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