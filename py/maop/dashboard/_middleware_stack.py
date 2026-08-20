"""Middleware stack configuration for the MAOP dashboard server.

Extracted from ``server.py``.  Each ``setup_*`` / ``register_*`` function
configures one layer of the FastAPI middleware stack or an exception
handler.  ``_normalize_api_path`` and ``_global_exception_handler`` are
kept module-level so tests can import them directly.

Re-exported by ``server.py`` for backward compatibility.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any

from fastapi import FastAPI
from fastapi import Request as _Req
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse as _JResp

from maop.config.edition import FeatureFlag, has_feature
from maop.config.settings import get_settings as _get_settings

logger = logging.getLogger(__name__)

# ── Enterprise API 404 Guard helpers ────────────────────────────────
# OPS-7 fix: a version-prefixed path such as /api/v1/tenant/... previously
# bypassed the guard because it does not start with "/api/tenant". Normalize
# away an optional /vN segment so both forms are treated identically.
_API_VERSION_RE = re.compile(r"^/api/v\d+/")

# In personal edition, enterprise-only API paths return 404 instead of
# leaking route existence or causing confusing 500 errors.
_ENTERPRISE_API_PREFIXES = (
    "/api/tenant",
    "/api/sso",
    "/api/rbac",
    "/api/n8n",
    "/api/licenses",
    "/api/quotas",
)


def _normalize_api_path(path: str) -> str:
    return _API_VERSION_RE.sub("/api/", path, count=1)


# ── Global Exception Handler ──────────────────────────────────────
# Catches unhandled exceptions: logs full details server-side,
# returns generic message to client (no internal info leakage).
async def _global_exception_handler(request: _Req, exc: Exception) -> Any:
    logger.error("Unhandled exception on %s %s: %s", request.method, request.url.path, exc)
    return _JResp(status_code=500, content={"status": "error", "error": "Internal server error"})


def register_exception_handler(app: FastAPI) -> None:
    """Register the global exception handler on ``app``."""
    app.exception_handler(Exception)(_global_exception_handler)


# ── CORS ───────────────────────────────────────────────────────────
def setup_cors(app: FastAPI, is_prod_env: bool) -> list[str]:
    """Configure CORS middleware.  Returns the resolved origin list (used
    by ``server.py`` ``__main__`` for startup logging)."""
    _cors_origins = os.environ.get("MAOP_CORS_ORIGINS", "").split(",")
    _cors_origins = [o.strip() for o in _cors_origins if o.strip()]
    if not _cors_origins:
        _cors_origins = ["http://localhost:9079", "http://127.0.0.1:9079", "http://localhost:8080"]
    # Fail-closed: reject wildcard origins. With allow_credentials=True a "*"
    # origin lets any site make authenticated cross-origin requests, so in production
    # we refuse and fall back to an empty allow-list.
    if "*" in _cors_origins:
        if is_prod_env:
            logger.warning("CORS allow_origins is wildcard '*' in production — rejecting; falling back to empty allow-list")
            _cors_origins = []
        else:
            logger.warning("CORS allow_origins contains '*' — any origin will be allowed (non-production only)")
    # #9 fix: CORS narrowed — explicit methods/headers instead of wildcard
    app.add_middleware(CORSMiddleware, allow_origins=_cors_origins, allow_credentials=True, allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"], allow_headers=["Authorization", "Content-Type", "X-Requested-With", "X-Trace-Id", "X-Request-Id"])
    return _cors_origins


# ── Rate Limit + Auth + CSP Middleware ─────────────────────────────
def setup_security_middleware(app: FastAPI) -> dict[str, Any]:
    """Register RateLimit / Quota / Auth / CSP middleware.

    Returns a dict with the resolved config flags (used by ``server.py``
    ``__main__`` for startup logging and re-exported as module-level
    names for e2e tests that read ``server._auth_enabled``).
    """
    from maop.core.security.middleware import AuthMiddleware, CSPMiddleware, RateLimitMiddleware

    _rl_enabled = os.environ.get("MAOP_RATE_LIMIT", os.environ.get("MAOP_RATE_LIMIT_ENABLED", "1")) == "1"
    _rl_rate = float(os.environ.get("MAOP_RATE_LIMIT_RPS", "30"))
    _rl_burst = int(os.environ.get("MAOP_RATE_LIMIT_BURST", "60"))
    app.add_middleware(RateLimitMiddleware, rate=_rl_rate, burst=_rl_burst, enabled=_rl_enabled)
    # ── Quota Middleware (Enterprise only, lazy-loaded) ───────────────
    # 注册顺序: 在 Auth 之前注册,使执行顺序为 CSP → Auth → Quota → RateLimit → CORS.
    # 这样 Auth 先注入 request.state.tenant_id,Quota 才能拿到租户上下文.
    # 中间件内部通过 has_feature(FeatureFlag.TENANT_ISOLATION) 守卫,Personal 版 no-op.
    # quota_manager=None 触发惰性加载(首次请求时从 routers.quotas 获取单例).
    try:
        from maop.enterprise.quota_middleware import QuotaMiddleware
        _quota_mw_enabled = os.environ.get("MAOP_QUOTA_MIDDLEWARE", "1") == "1"
        app.add_middleware(QuotaMiddleware, quota_manager=None, enabled=_quota_mw_enabled)
        logger.info("[server] QuotaMiddleware registered (enabled=%s)", _quota_mw_enabled)
    except ImportError as _e:
        logger.warning("[server] QuotaMiddleware not registered (import error: %s)", _e)
    # C-P0-1 fix: production defaults to auth enabled for safety
    # 配置统一由 settings.py 的 MAOPSettings.auth_enabled 提供（支持 MAOP_AUTH_ENABLED
    # 和 MAOP_AUTH 两个 env alias），消除 server.py 直接读 os.getenv 的双真相源问题。
    _env_is_prod = os.environ.get("MAOP_ENV", "").strip().lower() == "production"
    _auth_enabled = _get_settings().auth_enabled
    if _env_is_prod and not _auth_enabled:
        logger.warning("[security] MAOP_AUTH=0 in production — all write endpoints exposed!")
    app.add_middleware(
        AuthMiddleware,
        enabled=_auth_enabled,
        public_paths=[
            "/", "/api/health", "/api/prometheus",
            "/style.css", "/favicon.svg",
            "/api/docs", "/openapi.json",
            "/api/auth/status", "/api/auth/login",
            "/api/csp-report",
            "/api/alerts/webhook",  # Alertmanager webhook receiver (server-to-server, no auth)
            "/api/stream",  # P0 fix: SSE token validated via _check_sse_token in handler
            # SSO 公开端点（PRD 4.1）：登录跳转/回调/metadata/enabled 需在未认证下可访问
            "/api/sso/enabled",
            "/api/sso/oidc",  # /api/sso/oidc/{id}/login + /callback
            "/api/sso/saml",  # /api/sso/saml/{id}/login + /acs
            "/api/sso/providers",  # GET /providers/{id}/metadata 公开；CRUD 由 require_admin 守卫
            "/api/v1/sso/enabled",
            "/api/v1/sso/oidc",
            "/api/v1/sso/saml",
            "/api/v1/sso/providers",
        ],
    )
    # CSP & security headers (Content-Security-Policy, X-Frame-Options, etc.)
    _csp_enabled = os.environ.get("MAOP_CSP", "1") == "1"
    _csp_report_only = os.environ.get("MAOP_CSP_REPORT_ONLY", "0") == "1"
    _csp_report_uri = os.environ.get("MAOP_CSP_REPORT_URI", "") or None
    _csp_connect_src = os.environ.get("MAOP_CSP_CONNECT_SRC", "") or "'self'"
    app.add_middleware(
        CSPMiddleware,
        enabled=_csp_enabled,
        report_only=_csp_report_only,
        report_uri=_csp_report_uri,
        connect_src=_csp_connect_src,
    )
    return {
        "auth_enabled": _auth_enabled,
        "rl_enabled": _rl_enabled,
        "rl_rate": _rl_rate,
        "rl_burst": _rl_burst,
        "csp_enabled": _csp_enabled,
        "csp_report_only": _csp_report_only,
        "csp_connect_src": _csp_connect_src,
    }


# ── Enterprise API 404 Guard ────────────────────────────────────────
def setup_enterprise_guard(app: FastAPI) -> None:
    """In personal edition, register a middleware that returns 404 (or a
    soft 200 hint for grants/tenant-list) for enterprise-only API paths."""
    if not has_feature(FeatureFlag.MULTI_USER):
        @app.middleware("http")
        async def enterprise_api_guard(request: _Req, call_next: Any) -> Any:
            path = _normalize_api_path(request.url.path)
            # 判定是否为 enterprise-only API 路径（含 version-prefixed 变体）。
            is_grants = path.startswith("/api/rbac/grants")
            is_tenant_list = path.startswith("/api/tenant/list")
            is_enterprise_api = (
                is_grants
                or is_tenant_list
                or any(path.startswith(p) for p in _ENTERPRISE_API_PREFIXES)
            )
            if not is_enterprise_api:
                return await call_next(request)
            # 先放行让 AuthMiddleware 处理认证；未认证请求由 AuthMiddleware
            # 返回 401（而非此处 404），避免泄露端点存在性并符合 RBAC 语义。
            response = await call_next(request)
            if response.status_code == 401:
                return response
            # 认证通过后，personal edition 对 enterprise API 返回软降级响应。
            if is_grants:
                return _JResp(content={"grants": [], "hint": "Enterprise only"})
            if is_tenant_list:
                return _JResp(content={"tenants": [], "hint": "Enterprise only"})
            return _JResp(status_code=404, content={"status": "error", "error": "Not found", "hint": "This endpoint requires MAOP Enterprise edition"})