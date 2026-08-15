"""Enterprise SSO router — 多 IdP 管理 + OIDC + SAML 2.0 集成。

PRD ``docs/prd-sso-integration.md`` 实现：

- IdP CRUD：``/api/v1/sso/providers`` (POST/GET/PUT/DELETE)
- 测试连接：``/api/v1/sso/providers/{id}/test``
- SAML SP Metadata：``/api/v1/sso/providers/{id}/metadata``
- OIDC 登录/回调：``/api/v1/sso/oidc/{provider_id}/login`` + ``/callback``
- SAML 登录/ACS：``/api/v1/sso/saml/{provider_id}/login`` + ``/acs``
- 已启用 IdP 列表（登录页用）：``/api/v1/sso/enabled``

向后兼容：保留原有 ``/api/sso/authorize`` / ``/callback`` / ``/logout`` /
``/validate`` / ``/config`` 端点（单 IdP 模式，从环境变量加载）。

所有端点由 ``FeatureFlag.SSO`` 守卫，Personal 版返回 404。
管理端点（CRUD + test）需 ``require_admin``；登录/回调/metadata/enabled 公开。
"""

from __future__ import annotations

import logging
import os
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse
from pydantic import BaseModel

from maop.config.edition import FeatureFlag, has_feature
from maop.dashboard.error_handler import handle_api_errors

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/sso", tags=["sso"])


# ── Edition 守卫 helper ─────────────────────────────────────────────
def _require_sso() -> None:
    """SSO 特性开关守卫：Personal 版返回 404。"""
    if not has_feature(FeatureFlag.SSO):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="SSO not available in this edition",
        )


# ── Registry 单例（懒加载） ─────────────────────────────────────────
_registry: Any = None
_sso_manager: Any = None  # 向后兼容：单 IdP 模式


def _get_registry() -> Any:
    """获取 SSOProviderRegistry 单例。"""
    global _registry
    if _registry is None:
        from maop.enterprise.sso_registry import SSOProviderRegistry
        _registry = SSOProviderRegistry()
        # PRD NFR-C03：启动时从环境变量导入单 IdP 配置（向后兼容）
        try:
            from maop.enterprise.sso_store import import_env_provider_if_present
            import_env_provider_if_present(_registry.store)
        except Exception as exc:  # pragma: no cover — 防御性
            logger.warning("[sso] Failed to import env-based provider: %s", exc)
    return _registry


def _get_manager() -> Any:
    """向后兼容：单 IdP 模式从环境变量加载 SSOManager。"""
    global _sso_manager
    if _sso_manager is None:
        from maop.enterprise.sso import SSOConfig, SSOManager, SSOProvider
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


# ════════════════════════════════════════════════════════════════════
# 向后兼容端点（单 IdP 模式，保留以避免破坏现有客户端）
# ════════════════════════════════════════════════════════════════════


@router.get("/authorize")
@handle_api_errors
async def authorize(request: Request, state: str = "") -> Any:
    """Redirect to the IdP's authorize URL（单 IdP 向后兼容）。"""
    _require_sso()
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
) -> Any:
    """Handle OAuth callback — exchange code for session（单 IdP 向后兼容）。"""
    _require_sso()
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
    """Invalidate an SSO session（单 IdP 向后兼容）。"""
    _require_sso()
    mgr = _get_manager()
    logged_out = mgr.logout(body.session_id)
    return {"status": "ok" if logged_out else "not_found", "logged_out": logged_out}


@router.get("/validate")
@handle_api_errors
async def validate_session(request: Request, session_id: str = "") -> dict[str, Any]:
    """Validate an SSO session ID（单 IdP 向后兼容）。"""
    _require_sso()
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
    """Return non-secret SSO config for frontend（单 IdP 向后兼容）。"""
    _require_sso()
    mgr = _get_manager()
    config = mgr.config
    return {
        "status": "ok",
        "provider": config.provider.value,
        "client_id": config.client_id,
        "authorize_url": config.authorize_url,
        "redirect_uri": config.redirect_uri,
        "scopes": config.scopes,
        "configured": bool(config.client_id and config.authorize_url),
    }


# ════════════════════════════════════════════════════════════════════
# 多 IdP 管理端点（PRD 4.1）
# ════════════════════════════════════════════════════════════════════


@router.post("/providers")
@handle_api_errors
async def create_provider(request: Request, body: dict[str, Any]) -> dict[str, Any]:
    """添加 IdP 配置（PRD 4.1 / 4.2.1）。"""
    _require_sso()
    from maop.core.security.middleware import require_admin
    require_admin(request)
    from maop.enterprise.sso_store import SSOProviderCreate
    try:
        payload = SSOProviderCreate(**body)
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid SSO provider config: {exc}",
        ) from exc
    reg = _get_registry()
    try:
        resp = reg.store.create(payload)
    except ValueError as exc:
        # 名称冲突
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    reg.invalidate(resp.id)
    _audit(request, "sso.provider.create", resource=f"provider:{resp.id}", detail=resp.name)
    return {"status": "ok", "provider": reg.to_masked_response(resp)}


@router.get("/providers")
@handle_api_errors
async def list_providers(
    request: Request,
    protocol: str = "",
    enabled: bool | None = None,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """列出所有 IdP（PRD 4.2.2）。"""
    _require_sso()
    from maop.core.security.middleware import require_admin
    require_admin(request)
    reg = _get_registry()
    rows, total = reg.store.list(
        protocol=protocol,
        enabled=enabled,
        limit=limit,
        offset=offset,
    )
    return {
        "status": "ok",
        "providers": [reg.to_masked_response(r) for r in rows],
        "count": len(rows),
        "total": total,
    }


@router.get("/providers/{provider_id}")
@handle_api_errors
async def get_provider(request: Request, provider_id: int) -> dict[str, Any]:
    """查看 IdP 详情。"""
    _require_sso()
    from maop.core.security.middleware import require_admin
    require_admin(request)
    reg = _get_registry()
    resp = reg.store.get(provider_id)
    if resp is None:
        raise HTTPException(
            status_code=404,
            detail=f"SSO provider not found: id={provider_id}",
        )
    return {"status": "ok", "provider": reg.to_masked_response(resp)}


@router.put("/providers/{provider_id}")
@handle_api_errors
async def update_provider(
    request: Request,
    provider_id: int,
    body: dict[str, Any],
) -> dict[str, Any]:
    """更新 IdP 配置。"""
    _require_sso()
    from maop.core.security.middleware import require_admin
    require_admin(request)
    from maop.enterprise.sso_store import SSOProviderUpdate
    try:
        payload = SSOProviderUpdate(**body)
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid SSO provider update: {exc}",
        ) from exc
    reg = _get_registry()
    try:
        resp = reg.store.update(provider_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if resp is None:
        raise HTTPException(
            status_code=404,
            detail=f"SSO provider not found: id={provider_id}",
        )
    reg.invalidate(provider_id)
    _audit(request, "sso.provider.update", resource=f"provider:{provider_id}", detail=resp.name)
    return {"status": "ok", "provider": reg.to_masked_response(resp)}


@router.delete("/providers/{provider_id}")
@handle_api_errors
async def delete_provider(request: Request, provider_id: int) -> dict[str, Any]:
    """删除 IdP。"""
    _require_sso()
    from maop.core.security.middleware import require_admin
    require_admin(request)
    reg = _get_registry()
    ok = reg.store.delete(provider_id)
    if not ok:
        raise HTTPException(
            status_code=404,
            detail=f"SSO provider not found: id={provider_id}",
        )
    reg.invalidate(provider_id)
    _audit(request, "sso.provider.delete", resource=f"provider:{provider_id}")
    return {"status": "ok", "deleted": True}


@router.post("/providers/{provider_id}/test")
@handle_api_errors
async def test_provider(request: Request, provider_id: int) -> dict[str, Any]:
    """测试 IdP 连接（PRD 4.2.3）。"""
    _require_sso()
    from maop.core.security.middleware import require_admin
    require_admin(request)
    reg = _get_registry()
    try:
        result = reg.test_connection(provider_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    _audit(
        request,
        "sso.provider.test",
        resource=f"provider:{provider_id}",
        result="success" if result.get("reachable") else "failure",
    )
    return {"status": "ok", **result}


@router.get("/providers/{provider_id}/metadata")
@handle_api_errors
async def get_provider_metadata(request: Request, provider_id: int) -> Any:
    """SAML SP Metadata（PRD 4.2.4）。公开端点（IdP 端拉取）。"""
    _require_sso()
    reg = _get_registry()
    try:
        xml = reg.get_sp_metadata(provider_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return PlainTextResponse(content=xml, media_type="application/xml")


# ════════════════════════════════════════════════════════════════════
# OIDC 登录/回调（PRD 4.3）
# ════════════════════════════════════════════════════════════════════


@router.get("/oidc/{provider_id}/login")
@handle_api_errors
async def oidc_login(request: Request, provider_id: int, state: str = "") -> Any:
    """OIDC 登录跳转（PRD 4.3）。公开端点。"""
    _require_sso()
    reg = _get_registry()
    try:
        url, _state = reg.prepare_oidc_authorize(provider_id, state=state)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(url=url, status_code=302)


@router.get("/oidc/{provider_id}/callback")
@handle_api_errors
async def oidc_callback(
    request: Request,
    provider_id: int,
    code: str = "",
    state: str = "",
    error: str = "",
) -> Any:
    """OIDC 回调（PRD 4.3）。公开端点。"""
    _require_sso()
    if error:
        _audit(
            request,
            "sso.login.failure",
            resource=f"provider:{provider_id}",
            result="failure",
            detail=f"oidc error: {error}",
        )
        return JSONResponse(
            status_code=400,
            content={
                "status": "error",
                "error": f"SSO provider error: {error}",
                "code": "SSO_CALLBACK_ERROR",
            },
        )
    reg = _get_registry()
    try:
        session = reg.handle_oidc_callback(provider_id, code, state=state)
    except ValueError as exc:
        _audit(
            request,
            "sso.login.failure",
            resource=f"provider:{provider_id}",
            result="failure",
            detail=str(exc),
        )
        return JSONResponse(
            status_code=400,
            content={
                "status": "error",
                "error": str(exc),
                "code": "SSO_CALLBACK_ERROR",
            },
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        _audit(
            request,
            "sso.login.failure",
            resource=f"provider:{provider_id}",
            result="failure",
            detail=str(exc),
        )
        return JSONResponse(
            status_code=401,
            content={
                "status": "error",
                "error": str(exc),
                "code": "SSO_TOKEN_EXCHANGE_FAILED",
            },
        )
    _audit(
        request,
        "sso.login.success",
        resource=f"provider:{provider_id}",
        detail=session.user.external_id,
    )
    return {
        "status": "ok",
        "session_id": session.session_id,
        "user": session.user.model_dump(),
        "expires_at": session.expires_at,
    }


# ════════════════════════════════════════════════════════════════════
# SAML 登录/ACS（PRD 4.4）
# ════════════════════════════════════════════════════════════════════


@router.get("/saml/{provider_id}/login")
@handle_api_errors
async def saml_login(request: Request, provider_id: int, relay_state: str = "") -> Any:
    """SAML 登录跳转（PRD 4.4）。公开端点。"""
    _require_sso()
    reg = _get_registry()
    try:
        url, _rs = reg.prepare_saml_authorize(provider_id, relay_state=relay_state)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(url=url, status_code=302)


class SAMLACSRequest(BaseModel):
    """SAML ACS 请求体（HTTP-POST binding）。"""

    SAMLResponse: str
    RelayState: str = ""


@router.post("/saml/{provider_id}/acs")
@handle_api_errors
async def saml_acs(
    request: Request,
    provider_id: int,
    body: SAMLACSRequest,
) -> Any:
    """SAML ACS 端点（PRD 4.4）。公开端点。"""
    _require_sso()
    reg = _get_registry()
    try:
        session = reg.handle_saml_acs(
            provider_id,
            body.SAMLResponse,
            relay_state=body.RelayState,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        # SAML 验证失败（签名/Conditions/Audience）→ 403
        from maop.enterprise.sso import SSOError
        _audit(
            request,
            "sso.login.failure",
            resource=f"provider:{provider_id}",
            result="failure",
            detail=str(exc),
        )
        if isinstance(exc, SSOError):
            return JSONResponse(
                status_code=403,
                content={
                    "status": "error",
                    "error": str(exc),
                    "code": "SSO_SIGNATURE_INVALID",
                },
            )
        return JSONResponse(
            status_code=400,
            content={
                "status": "error",
                "error": str(exc),
                "code": "SSO_CALLBACK_ERROR",
            },
        )
    _audit(
        request,
        "sso.login.success",
        resource=f"provider:{provider_id}",
        detail=session.user.external_id,
    )
    return {
        "status": "ok",
        "session_id": session.session_id,
        "user": session.user.model_dump(),
        "expires_at": session.expires_at,
    }


# ════════════════════════════════════════════════════════════════════
# 登录页：列出已启用 IdP（PRD 4.2.5）
# ════════════════════════════════════════════════════════════════════


@router.get("/enabled")
@handle_api_errors
async def list_enabled_providers(request: Request) -> dict[str, Any]:
    """列出已启用 IdP（登录页用，PRD 4.2.5）。公开端点，不返回敏感配置。"""
    _require_sso()
    reg = _get_registry()
    return {"status": "ok", **reg.list_enabled_for_login()}


# ════════════════════════════════════════════════════════════════════
# 审计 helper
# ════════════════════════════════════════════════════════════════════


def _audit(
    request: Request,
    action: str,
    *,
    resource: str = "",
    detail: str = "",
    result: str = "success",
) -> None:
    """记录审计事件到 EnterpriseAuditLogger（若可用）。"""
    try:
        from maop.enterprise.audit import AuditSeverity, EnterpriseAuditLogger
        logger_ = EnterpriseAuditLogger()
        actor = getattr(getattr(request, "state", None), "auth_identity", "") or ""
        tenant_id = getattr(getattr(request, "state", None), "tenant_id", "") or ""
        ip = request.client.host if request.client else ""
        logger_.log(
            action,
            actor=actor,
            tenant_id=tenant_id,
            resource=resource,
            detail=detail,
            result=result,
            severity=AuditSeverity.INFO,
            ip_address=ip,
        )
    except Exception as exc:  # pragma: no cover — 审计失败不影响主流程
        logger.debug("[sso] audit log failed: %s", exc)
