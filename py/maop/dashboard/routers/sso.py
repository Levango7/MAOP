"""Enterprise SSO router — exposes SSOManager via FastAPI endpoints.

Phase C (C4, 2026-07-22): bridges the gap between the enterprise SSOManager
(``maop.enterprise.sso``) and SSO-enabled clients. Before this router existed,
``SSOManager`` had 0 callers in the main code path — it was "floating island
code". This router provides standard OAuth/OIDC endpoints:

- ``GET /api/sso/authorize`` — redirect to IdP authorize URL
- ``GET /api/sso/callback`` — handle OAuth callback, exchange code for session
- ``POST /api/sso/logout`` — invalidate session
- ``GET /api/sso/validate`` — validate a session ID
- ``GET /api/sso/config`` — return non-secret SSO config (for frontend login button)

SSOConfig is loaded from environment variables (MAOP_SSO_PROVIDER,
MAOP_SSO_CLIENT_ID, etc.) on first use.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import RedirectResponse, JSONResponse
from pydantic import BaseModel

from maop.config.edition import FeatureFlag, has_feature
from maop.dashboard.error_handler import handle_api_errors

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/sso", tags=["sso"])

_sso_manager: Any = None


def _get_manager() -> Any:
    global _sso_manager
    if _sso_manager is None:
        from maop.enterprise.sso import SSOManager, SSOConfig, SSOProvider
        provider = SSOProvider(os.getenv("MAOP_SSO_PROVIDER", "oidc"))
        config = SSOConfig(
            provider=provider,
            client_id=os.getenv("MAOP_SSO_CLIENT_ID", ""),
            client_secret=os.getenv("MAOP_SSO_CLIENT_SECRET", ""),
            authorize_url=os.getenv("MAOP_SSO_AUTHORIZE_URL", ""),
            token_url=os.getenv("MAOP_SSO_TOKEN_URL", ""),
            userinfo_url=os.getenv("MAOP_SSO_USERINFO_URL", ""),
            redirect_uri=os.getenv("MAOP_SSO_REDIRECT_URI", ""),
            scopes=[s.strip() for s in os.getenv("MAOP_SSO_SCOPES", "openid profile email").split(",")],
        )
        _sso_manager = SSOManager(config=config)
    return _sso_manager


class LogoutRequest(BaseModel):
    session_id: str


# ── Endpoints ─────────────────────────────────────────────────────


@router.get("/authorize")
@handle_api_errors
async def authorize(request: Request, state: str = "") -> dict[str, Any]:
    """Redirect to the IdP's authorize URL."""
    # 企业版特性开关守卫：Personal 版直接返回 404，避免 import maop.enterprise.* 抛 500
    if not has_feature(FeatureFlag.SSO):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="SSO not available in this edition",
        )
    mgr = _get_manager()
    url = mgr.get_authorize_url(state=state)
    return RedirectResponse(url=url, status_code=302)


@router.get("/callback")
@handle_api_errors
async def callback(
    request: Request,
    code: str = "",
    state: str = "",
    error: str = "",
) -> dict[str, Any]:
    """Handle OAuth callback — exchange code for session."""
    # 企业版特性开关守卫：Personal 版直接返回 404，避免 import maop.enterprise.* 抛 500
    if not has_feature(FeatureFlag.SSO):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="SSO not available in this edition",
        )
    if error:
        return JSONResponse(
            status_code=400,
            content={"status": "error", "error": f"SSO provider error: {error}"},
        )
    if not code:
        return JSONResponse(
            status_code=400,
            content={"status": "error", "error": "Missing authorization code"},
        )
    mgr = _get_manager()
    session = mgr.handle_callback(code, state=state)
    return {
        "status": "ok",
        "session_id": session.session_id,
        "user": session.user.model_dump(),
        "expires_at": session.expires_at,
    }


@router.post("/logout")
@handle_api_errors
async def logout(body: LogoutRequest, request: Request) -> dict[str, Any]:
    """Invalidate an SSO session."""
    # 企业版特性开关守卫：Personal 版直接返回 404，避免 import maop.enterprise.* 抛 500
    if not has_feature(FeatureFlag.SSO):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="SSO not available in this edition",
        )
    mgr = _get_manager()
    logged_out = mgr.logout(body.session_id)
    return {"status": "ok" if logged_out else "not_found", "logged_out": logged_out}


@router.get("/validate")
@handle_api_errors
async def validate_session(request: Request, session_id: str = "") -> dict[str, Any]:
    """Validate an SSO session ID."""
    # 企业版特性开关守卫：Personal 版直接返回 404，避免 import maop.enterprise.* 抛 500
    if not has_feature(FeatureFlag.SSO):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="SSO not available in this edition",
        )
    if not session_id:
        return {"status": "error", "error": "Missing session_id"}
    mgr = _get_manager()
    session = mgr.validate_session(session_id)
    if session is None:
        return {"status": "invalid", "valid": False}
    return {
        "status": "ok",
        "valid": True,
        "user": session.user.model_dump(),
        "expires_at": session.expires_at,
    }


@router.get("/config")
@handle_api_errors
async def get_config(request: Request) -> dict[str, Any]:
    """Return non-secret SSO config for frontend login button rendering.

    Does NOT expose client_secret. Returns provider type, authorize URL,
    client_id, and scopes so the frontend can build the login redirect.
    """
    # 企业版特性开关守卫：Personal 版直接返回 404，避免 import maop.enterprise.* 抛 500
    if not has_feature(FeatureFlag.SSO):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="SSO not available in this edition",
        )
    mgr = _get_manager()
    config = mgr.config()
    return {
        "status": "ok",
        "provider": config.provider.value,
        "client_id": config.client_id,
        "authorize_url": config.authorize_url,
        "redirect_uri": config.redirect_uri,
        "scopes": config.scopes,
        "configured": bool(config.client_id and config.authorize_url),
    }
