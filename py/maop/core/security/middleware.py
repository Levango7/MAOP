"""MAOP Middleware - FastAPI middleware for Auth and Rate Limiting.

Provides:
  1. AuthMiddleware: API Key / JWT authentication for protected routes
  2. RateLimitMiddleware: Per-IP or per-key rate limiting
  3. setup_middleware: One-call setup for production middleware stack
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any, cast

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)


# ── Auth Middleware ────────────────────────────────────────────────

class AuthMiddleware(BaseHTTPMiddleware):
    """FastAPI middleware that validates API Key or JWT on protected routes.

    Public routes (no auth required) are defined in `public_paths`.
    Auth is checked via MAOP.core.auth module.
    """

    def __init__(
        self,
        app: Any,
        *,
        public_paths: list[str] | None = None,
        api_key_header: str = "X-API-Key",
        auth_header: str = "Authorization",
        enabled: bool = True,
    ):
        super().__init__(app)
        # P2-1 fix: 与实际路由定义对齐。/api/info/* 下 pillars/roles/modules/
        # workflows/architecture/edition/config/adrs/activity 等端点为公开元信息，
        # 不需要认证（admin.py 中的 POST /edition 自带 require_admin 守卫）。
        # /api/csp-report / /api/csp-violations 也需公开，否则浏览器无法上报
        # CSP 违规（无凭证）。/favicon.svg 由静态资源分支放行，此处一并列入。
        self.public_paths = public_paths or [
            "/", "/api/health",
            "/style.css", "/app.js", "/favicon.svg",
            "/api/docs", "/openapi.json",
            "/api/auth/login", "/api/auth/status",
            "/api/info",  # 前缀匹配覆盖 /api/info/pillars 等
            "/api/csp-report", "/api/csp-violations",
        ]
        self.api_key_header = api_key_header
        self.auth_header = auth_header
        self.enabled = enabled

    def _is_public_path(self, path: str) -> bool:
        """Exact match, or prefix match for non-root public paths.

        ``"/api/stream"`` therefore also exempts ``/api/stream/agent/{id}``
        (SSE endpoints that validate their own token via ``_check_sse_token``
        + ``require_admin`` in the handler). The root ``"/"`` stays exact so
        no other route is accidentally exempted.
        """
        for p in self.public_paths:
            if path == p:
                return True
            if p != "/" and path.startswith(p.rstrip("/") + "/"):
                return True
        return False

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # P0-1 FIX (R3 audit): restore if-guard that was lost in P2-3 fix.
        # Without this guard, ALL auth logic below is dead code and every
        # request gets anonymous/read role regardless of MAOP_AUTH setting.
        #
        # Security (C-1 fix): when auth is disabled (dev mode), default to
        # ``read`` role — NOT ``admin`` — so a misconfigured deployment
        # does not grant anonymous users write access to admin endpoints.
        # Operators who explicitly want admin role in disabled mode can set
        # ``MAOP_AUTH_DISABLED_ADMIN=1`` (NOT recommended outside isolated
        # local dev). Production should set ``MAOP_AUTH=1`` instead.
        if not self.enabled:
            import os
            _disabled_admin = os.environ.get("MAOP_AUTH_DISABLED_ADMIN", "0") == "1"
            if _disabled_admin:
                logger.warning(
                    "DANGEROUS flag MAOP_AUTH_DISABLED_ADMIN is enabled — auth is "
                    "disabled AND anonymous requests are granted admin role. Never use "
                    "in production or any shared/multi-tenant environment."
                )
            disabled_role = "admin" if _disabled_admin else "read"
            request.state.auth_roles = [disabled_role]
            request.state.auth_identity = "anonymous"
            return cast(Response, await call_next(request))

        # Skip public paths — prefix match so subtree endpoints (e.g.
        # /api/stream/agent/{id}, /api/auth/login/*) inherit the public
        # status of their mount point. Per-handler auth (require_admin /
        # _check_sse_token) still applies inside the route.
        path = request.url.path
        if self._is_public_path(path):
            return cast(Response, await call_next(request))

        # Skip static assets
        # P2-2 fix: 放行字体文件（.woff/.woff2/.ttf），否则浏览器加载字体时
        # 会被认证中间件拦截，导致页面字体回退到系统默认字体。
        if path.startswith("/static/") or path.endswith((".css", ".js", ".ico", ".png", ".svg", ".woff", ".woff2", ".ttf")):
            return cast(Response, await call_next(request))

        auth_manager = getattr(request.app.state, "auth_manager", None)
        # New structured ApiKeyManager (scopes / IP allow-list / rate limit / usage)
        api_key_mgr = getattr(request.app.state, "api_key_manager", None)

        # Check API Key header — prefer the new ApiKeyManager when available.
        api_key = request.headers.get(self.api_key_header, "")
        if api_key:
            # New manager path: structured validation + usage recording.
            if api_key_mgr is not None and hasattr(api_key_mgr, "validate_key"):
                resp = await self._authenticate_with_api_key_manager(
                    api_key_mgr, api_key, request, call_next
                )
                if resp is not None:
                    return resp
                # validate_key returned a non-None sentinel? fall through to legacy.
            try:
                auth_checker = getattr(request.app.state, "api_key_auth", None)
                if auth_checker is not None and hasattr(auth_checker, "validate"):
                    result = auth_checker.validate(api_key)
                elif auth_checker is not None and hasattr(auth_checker, "validate_key"):
                    result = auth_checker.validate_key(api_key)
                elif auth_manager is not None and hasattr(auth_manager, "authenticate"):
                    result = auth_manager.authenticate(api_key=api_key)
                else:
                    # P1安全-6 fix: API key present but no auth checker configured.
                    # Returning 503 instead of pass-through to avoid silently
                    # authenticating as anonymous when auth is misconfigured.
                    logger.warning(
                        "[auth] API key present but no auth checker configured "
                        "(api_key_auth/auth_manager missing on app.state) — "
                        "rejecting request as auth misconfiguration."
                    )
                    return JSONResponse(
                        status_code=503,
                        content={"error": "Auth configuration error",
                                 "detail": "No auth checker configured"},
                    )

                if result.authenticated:
                    request.state.auth_identity = result.identity
                    request.state.auth_roles = result.roles
                    return cast(Response, await call_next(request))
                return JSONResponse(
                    status_code=401,
                    content={"error": "Invalid API key", "detail": result.error},
                )
            except Exception as e:
                logger.warning("Auth check failed: %s", e)
                return JSONResponse(status_code=500, content={"error": "Auth check failed"})

        # Check Authorization header (Bearer JWT or Bearer maop_{key_id}_{secret})
        auth_header = request.headers.get(self.auth_header, "")
        # #4 fix: fallback to httpOnly cookie if no Authorization header
        if not auth_header.startswith("Bearer ") and "maop_token" in request.cookies:
            auth_header = "Bearer " + request.cookies["maop_token"]
        if auth_header.startswith("Bearer "):
            try:
                token = auth_header[7:]
                # If the bearer token looks like a structured API key
                # (maop_{key_id}_{secret}), validate via ApiKeyManager.
                if (
                    token.startswith("maop_")
                    and api_key_mgr is not None
                    and hasattr(api_key_mgr, "validate_key")
                ):
                    resp = await self._authenticate_with_api_key_manager(
                        api_key_mgr, token, request, call_next
                    )
                    if resp is not None:
                        return resp

                jwt_checker = getattr(request.app.state, "jwt_auth", None)
                if jwt_checker is not None and hasattr(jwt_checker, "validate_token"):
                    result = jwt_checker.validate_token(token)
                elif auth_manager is not None and hasattr(auth_manager, "authenticate"):
                    result = auth_manager.authenticate(bearer_token=token)
                else:
                    # P1安全-6 fix: JWT token present but no jwt checker configured.
                    # Returning 503 instead of pass-through to avoid silently
                    # authenticating as anonymous when auth is misconfigured.
                    logger.warning(
                        "[auth] JWT token present but no jwt checker configured "
                        "(jwt_auth/auth_manager missing on app.state) — "
                        "rejecting request as auth misconfiguration."
                    )
                    return JSONResponse(
                        status_code=503,
                        content={"error": "Auth configuration error",
                                 "detail": "No auth checker configured"},
                    )

                if result.authenticated:
                    request.state.auth_identity = result.identity
                    request.state.auth_roles = result.roles
                    return cast(Response, await call_next(request))
                return JSONResponse(
                    status_code=401,
                    content={"error": "Invalid token", "detail": result.error},
                )
            except Exception as e:
                logger.warning("JWT check failed: %s", e)
                return JSONResponse(status_code=500, content={"error": "Auth check failed"})

        # No auth provided - check if any auth is configured
        has_auth = (
            getattr(request.app.state, "api_key_auth", None) is not None
            or getattr(request.app.state, "jwt_auth", None) is not None
            or getattr(request.app.state, "auth_manager", None) is not None
            or api_key_mgr is not None
        )
        if has_auth:
            return JSONResponse(
                status_code=401,
                content={"error": "Authentication required", "detail": "Provide X-API-Key or Authorization header"},
            )

        # No auth configured = pass through
        return cast(Response, await call_next(request))

    async def _authenticate_with_api_key_manager(
        self,
        mgr: Any,
        plaintext: str,
        request: Request,
        call_next: Callable,
    ) -> Response | None:
        """Validate ``plaintext`` via the new ApiKeyManager and dispatch.

        On success, injects ``auth_identity`` / ``auth_roles`` / ``auth_key_id``
        / ``auth_scopes`` onto ``request.state`` and records usage after the
        downstream handler completes. Returns ``None`` to signal "not handled
        here, fall through to legacy path" — currently never used but kept
        for future extensibility.
        """
        client_ip = request.client.host if request.client else ""
        # Trust-proxy awareness for real client IP.
        import os
        if os.environ.get("MAOP_TRUST_PROXY", "0") == "1":
            xff = request.headers.get("x-forwarded-for", "")
            if xff:
                client_ip = xff.split(",")[0].strip()

        result = mgr.validate_key(plaintext, client_ip=client_ip)
        if not result.valid:
            status = 429 if result.rate_limit_exceeded else 401
            return JSONResponse(
                status_code=status,
                content={"error": "Invalid API key", "detail": result.error},
            )

        # Inject auth state.
        request.state.auth_identity = result.name or result.key_id
        request.state.auth_roles = result.roles
        request.state.auth_key_id = result.key_id
        request.state.auth_scopes = result.scopes

        # Dispatch and record usage after completion.
        start = time.monotonic()
        response = cast(Response, await call_next(request))
        latency_ms = (time.monotonic() - start) * 1000.0
        try:
            mgr.record_usage(
                result.key_id,
                endpoint=request.url.path,
                method=request.method,
                ip_address=client_ip,
                status_code=getattr(response, "status_code", 0),
                latency_ms=latency_ms,
            )
        except Exception as exc:  # pragma: no cover — usage recording is best-effort
            logger.debug("[auth] usage recording failed: %s", exc)
        return response


# ── Rate Limit Middleware ─────────────────────────────────────────

class RateLimitMiddleware(BaseHTTPMiddleware):
    """FastAPI middleware for per-IP rate limiting.

    Uses MAOP.core.rate_limiter.TokenBucket by default.

    P2-4 note (多 worker 限制): ``_buckets`` / ``_lock_time`` / ``_request_count``
    均为进程内字典/计数器，仅在单 worker 模式下精确。在多 worker（uvicorn
    --workers N > 1 或 gunicorn 多进程）模式下，每个 worker 拥有独立的计数器，
    实际限流上限会放大到约 ``N × burst``，限流效果减弱。

    生产环境多 worker 部署时，应改用 Redis 作为共享计数器（见
    ``maop.core.backends.redis`` —— 使用 INCR + EXPIRE 实现分布式令牌桶），
    或在反向代理层（nginx limit_req / envoy local_rate_limit）做限流。
    本中间件保留作为单 worker / 开发环境的轻量级方案。
    """

    def __init__(
        self,
        app: Any,
        *,
        rate: float = 30.0,        # Requests per second
        burst: int = 60,           # Max burst
        enabled: bool = True,
        key_func: Callable | None = None,  # Custom key extraction (default: client IP)
    ):
        super().__init__(app)
        self.enabled = enabled
        self.rate = rate
        self.burst = burst
        self.key_func = key_func or self._default_key
        self._buckets: dict[str, Any] = {}
        self._lock_time: dict[str, float] = {}
        self._request_count = 0  # for periodic cleanup

    @staticmethod
    def _default_key(request: Request) -> str:
        """Extract client IP as rate limit key.

        When MAOP_TRUST_PROXY is enabled, use X-Forwarded-For header
        to get the real client IP behind a reverse proxy.
        """
        import os

        if os.environ.get("MAOP_TRUST_PROXY", "0") == "1":
            xff = request.headers.get("x-forwarded-for", "")
            if xff:
                # XFF can contain multiple IPs, take the first (original client)
                return xff.split(",")[0].strip()  # type: ignore
        return request.client.host if request.client else "unknown"

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if not self.enabled:
            return cast(Response, await call_next(request))

        # Skip health checks and static
        path = request.url.path
        if path in ("/api/health", "/"):
            return cast(Response, await call_next(request))

        key = self.key_func(request)

        # Simple in-memory token bucket per key
        now = time.monotonic()

        # Periodic cleanup: remove stale buckets every 200 requests
        self._request_count += 1
        if self._request_count % 200 == 0:
            stale_cutoff = now - 300  # 5 minutes
            stale_keys = [k for k, v in self._buckets.items() if v["last"] < stale_cutoff]
            for k in stale_keys:
                del self._buckets[k]

        if key not in self._buckets:
            self._buckets[key] = {"tokens": float(self.burst), "last": now}

        bucket = self._buckets[key]
        elapsed = now - bucket["last"]
        bucket["tokens"] = min(self.burst, bucket["tokens"] + elapsed * self.rate)
        bucket["last"] = now

        if bucket["tokens"] < 1.0:
            retry_after = (1.0 - bucket["tokens"]) / self.rate
            return JSONResponse(
                status_code=429,
                content={
                    "error": "Rate limit exceeded",
                    "retry_after_s": round(retry_after, 2),
                },
                headers={"Retry-After": str(int(retry_after) + 1)},
            )

        bucket["tokens"] -= 1.0
        return cast(Response, await call_next(request))


# ── CSP & Security Headers Middleware ─────────────────────────────

class CSPMiddleware(BaseHTTPMiddleware):
    """Content-Security-Policy and security response headers.

    Sets CSP header on all responses to mitigate XSS, clickjacking,
    MIME-sniffing, and other injection attacks.

    The policy is tuned for the MAOP Dashboard frontend which uses:
      - External scripts from /js/       -> script-src 'self'
      - External CSS from /style.css     -> style-src  'self'
      - WebSocket to /ws (same origin)   -> connect-src 'self'
      - Chart.js (may use data: URIs)    -> img-src    'self' data:

    Inline event handlers (onclick) have been migrated to addEventListener
    in app-events.js, so script-src no longer needs 'unsafe-inline'.
    Inline style attributes have been migrated to CSS classes in style.css,
    so style-src no longer needs 'unsafe-inline'.

    Config (env vars):
      MAOP_CSP=0              Disable CSP entirely
      MAOP_CSP_REPORT_ONLY=1  Use Content-Security-Policy-Report-Only header
      MAOP_CSP_REPORT_URI     Reporting endpoint URL
      MAOP_CSP_CONNECT_SRC    Override connect-src (e.g. "'self' wss://other.host")
    """

    def __init__(
        self,
        app: Any,
        *,
        enabled: bool = True,
        report_only: bool = False,
        report_uri: str | None = None,
        connect_src: str = "'self'",
        extra_directives: str = "",
    ):
        super().__init__(app)
        self.enabled = enabled
        self.report_only = report_only
        self.report_uri = report_uri

        # Build CSP policy string
        directives = [
            "default-src 'self'",
            "script-src 'self'",
            "style-src 'self'",
            "img-src 'self' data:",
            "font-src 'self'",
            f"connect-src {connect_src}",
            "object-src 'none'",
            "base-uri 'self'",
            "form-action 'self'",
            "frame-ancestors 'none'",
        ]
        if report_uri:
            directives.append(f"report-uri {report_uri}")
        if extra_directives:
            directives.append(extra_directives)
        self._csp_value = "; ".join(directives)

        # Security headers applied to every response
        self._security_headers = {
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
            "Referrer-Policy": "strict-origin-when-cross-origin",
            "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
            "Strict-Transport-Security": "max-age=31536000; includeSubDomains; preload",
            "X-DNS-Prefetch-Control": "off",
            "X-Permitted-Cross-Domain-Policies": "none",
            "Cross-Origin-Opener-Policy": "same-origin",
            "Cross-Origin-Resource-Policy": "same-origin",
        }

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        response = cast(Response, await call_next(request))
        if not self.enabled:
            return response

        # Set CSP header (report-only vs enforce)
        header_name = (
            "Content-Security-Policy-Report-Only"
            if self.report_only
            else "Content-Security-Policy"
        )
        response.headers[header_name] = self._csp_value

        # Additional security headers
        for key, val in self._security_headers.items():
            response.headers.setdefault(key, val)

        return response


# ── One-call setup ────────────────────────────────────────────────

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
    origins = cors_origins or ["http://localhost:9079", "http://127.0.0.1:9079"]
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


# ── Shared auth helpers ──────────────────────────────────────────

def should_set_secure_cookie() -> bool:
    """判断是否应为认证 cookie 设置 Secure 标志。

    P2-3 fix: 生产环境（MAOP_ENV=production）下必须设置 Secure=True，
    防止认证 cookie 通过 HTTP 明文连接发送（OWASP A02:2021 Cryptographic
    Failures）。dev/development/local/test 等开发环境下允许 HTTP，故
    不设置 Secure 标志，否则浏览器在 http://localhost 上不会回传 cookie，
    导致登录态丢失。

    Returns
    -------
    bool
        True 表示应设置 Secure 标志（生产环境）；
        False 表示不设置（开发环境，允许 HTTP）。
    """
    import os
    env = os.environ.get("MAOP_ENV", "").strip().lower()
    return env == "production"


def require_admin(request: Request) -> None:
    """Raise HTTPException(403) if not authenticated as admin or superadmin."""
    roles = getattr(request.state, "auth_roles", None) or []
    # P1-13 fix: accept both "admin" and "superadmin" (RBAC hierarchy)
    if not ({"admin", "superadmin"} & set(roles)):
        from fastapi import HTTPException
        raise HTTPException(403, "Admin role required")
