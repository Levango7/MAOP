"""One-call middleware stack setups（基础版 + 全量安全版）.

原 ``middleware.py`` 的装配块（2026-10-28 拆分）。挂载顺序语义见
``setup_security_middleware`` docstring（Starlette 后加者在外层）。
"""

from __future__ import annotations

from fastapi import FastAPI

from maop.core.security.middleware._auth import AuthMiddleware
from maop.core.security.middleware._csp import CSPMiddleware
from maop.core.security.middleware._protection import (
    AuditMiddleware,
    IPAllowlistMiddleware,
    RateLimitMiddleware,
    RequestSizeLimitMiddleware,
)
from maop.core.security.middleware._scope import ScopeMiddleware


def setup_middleware(
    app: FastAPI,
    *,
    auth_enabled: bool = False,
    rate_limit_enabled: bool = True,
    rate_limit_per_sec: float = 30.0,
    rate_limit_burst: int = 60,
    cors_origins: list[str] | None = None,
) -> None:
    """Configure production middleware stack on a FastAPI app.

    Call this after creating the app but before adding routes.
    """
    # CORS - configurable origins
    from fastapi.middleware.cors import CORSMiddleware
    if not cors_origins:
        from maop.config.settings import get_settings
        cors_origins = get_settings().cors_origin_list()
    origins = cors_origins
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "X-API-Key", "Content-Type"],
    )

    # Rate limiting
    if rate_limit_enabled:
        app.add_middleware(
            RateLimitMiddleware,
            rate=rate_limit_per_sec,
            burst=rate_limit_burst,
            enabled=True,
        )

    # Auth
    if auth_enabled:
        app.add_middleware(AuthMiddleware, enabled=True)


def setup_security_middleware(
    app: FastAPI,
    *,
    auth_enabled: bool = False,
    rate_limit_enabled: bool = True,
    rate_limit_per_sec: float = 30.0,
    rate_limit_burst: int = 60,
    redis_url: str | None = None,
    cors_origins: list[str] | None = None,
    audit_enabled: bool = True,
    ip_blocklist: list[str] | None = None,
    ip_allowlist: list[str] | None = None,
    request_size_limit_enabled: bool = True,
    request_size_max_bytes: int = 10 * 1024 * 1024,
    csp_enabled: bool = True,
    csp_report_only: bool = False,
    csp_connect_src: str = "'self'",
    csp_extra_directives: str = "",
    production: bool = False,
) -> None:
    """Configure the full production security middleware stack.

    Registration order (Starlette applies last-added first, outermost):
      1. CORS              — outermost, adds CORS headers first
      2. CSP / Security headers — applied on response
      3. RateLimit         — throttle traffic
      4. Auth              — authenticate + authorize
      5. Audit             — log every request (after IP gate, before auth)
      6. IPAllowlist/Blocklist — network-level gate before auth
      7. RequestSizeLimit  — reject oversized bodies first (cheap, pre-body)

    Call this after creating the app but before adding routes.
    """
    if production is None:
        from maop.config.settings import is_production

        production = is_production()

    # 1. Request size limit (cheapest reject, before body parse) — innermost
    if request_size_limit_enabled:
        app.add_middleware(
            RequestSizeLimitMiddleware,
            enabled=True,
            max_bytes=request_size_max_bytes,
        )

    # 2. IP allowlist / blocklist (network gate, before auth)
    app.add_middleware(
        IPAllowlistMiddleware,
        enabled=True,
        blocklist=ip_blocklist or [],
        allowlist=ip_allowlist or [],
    )

    # 3. Audit logging (every request after IP gate)
    if audit_enabled:
        app.add_middleware(AuditMiddleware, enabled=True)

    # Auth (business-logic layer)
    if auth_enabled:
        app.add_middleware(AuthMiddleware, enabled=True)

    # Scope authorization (after Auth, injects auth_scopes/auth_roles onto state)
    if auth_enabled:
        app.add_middleware(ScopeMiddleware, enabled=True)

    # Rate limiting
    if rate_limit_enabled:
        app.add_middleware(
            RateLimitMiddleware,
            rate=rate_limit_per_sec,
            burst=rate_limit_burst,
            enabled=True,
            redis_url=redis_url,
        )

    # CSP / Security headers (outermost response layer, before CORS)
    if csp_enabled:
        app.add_middleware(
            CSPMiddleware,
            enabled=True,
            report_only=csp_report_only,
            connect_src=csp_connect_src,
            extra_directives=csp_extra_directives,
            production=production,
        )

    # CORS - outermost
    from fastapi.middleware.cors import CORSMiddleware
    if not cors_origins:
        from maop.config.settings import get_settings
        cors_origins = get_settings().cors_origin_list()
    origins = cors_origins
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "X-API-Key", "Content-Type"],
    )
