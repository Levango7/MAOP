"""MAOP Middleware - FastAPI middleware for Auth and Rate Limiting.

Provides:
  1. AuthMiddleware: API Key / JWT authentication for protected routes
  2. RateLimitMiddleware: Per-IP or per-key rate limiting
  3. setup_middleware: One-call setup for production middleware stack
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from typing import Any, cast

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)


def _is_loopback_host(host: str) -> bool:
    """Return True only for loopback client addresses.

    ``request.client.host`` is the transport peer, not a spoofable
    ``X-Forwarded-For`` header, so owner mode cannot be claimed by a
    remote caller behind the same server.
    """
    value = (host or "").strip().lower()
    return value in {"127.0.0.1", "::1", "localhost"} or value.startswith("127.")


def _disabled_auth_roles(host: str) -> tuple[list[str], str]:
    """Roles/identity for an auth-disabled request (owner mode or guest)."""
    import os

    if os.environ.get("MAOP_OWNER_MODE", "0").strip().lower() in {
        "1", "true", "yes", "on"
    } and _is_loopback_host(host):
        return ["owner", "admin", "superadmin"], "owner"
    return ["read"], "anonymous"


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
        public_prefix_paths: list[str] | None = None,
        api_key_header: str = "X-API-Key",
        auth_header: str = "Authorization",
        enabled: bool = True,
    ):
        super().__init__(app)
        self.public_paths = public_paths or [
            "/", "/api/health",
            "/style.css", "/app.js",
            "/api/docs", "/openapi.json",
            "/api/auth/login", "/api/auth/status",
        ]
        # P0-3 fix: prefix matching is opt-in via an explicit allowlist.
        # Only paths listed here get subtree exemption (e.g. /api/stream SSE
        # sub-routes that carry their own _check_sse_token + require_admin).
        # This prevents /api/auth/login/anything from being exempted just
        # because /api/auth/login is a public path.
        self.prefix_public_paths = public_prefix_paths or ["/api/stream"]
        self.api_key_header = api_key_header
        self.auth_header = auth_header
        self.enabled = enabled

    def _is_public_path(self, path: str) -> bool:
        """Exact match for all public paths; prefix match only for
        explicitly-listed prefix paths (e.g. /api/stream SSE sub-routes).

        P0-3 fix: previously every non-root public path was prefix-matched,
        so ``/api/auth/login`` being public also exempted
        ``/api/auth/login/anything`` — an auth bypass. Now only paths in
        ``self.prefix_public_paths`` get prefix matching; everything else
        is exact-match only.
        """
        # 精确匹配
        if path in self.public_paths:
            return True
        # 仅对白名单前缀路径做前缀匹配
        for p in self.prefix_public_paths:
            if path.startswith(p.rstrip("/") + "/"):
                return True
        return False

    async def _dispatch_disabled(
        self, request: Request, call_next: Callable
    ) -> Response:
        """Handle the auth-disabled branch.

        Default is still anonymous read-only. The one exception is the
        single-user *owner mode* that ``maop start`` enables for a personal
        edition bound to loopback: without it the local dashboard is a
        read-only museum and every run endpoint answers 403. Owner mode is
        gated on both the explicit ``MAOP_OWNER_MODE`` env and a loopback
        client, so disabling auth on a remote bind never grants admin.
        """
        import os
        # 检测废弃标志并警告
        if os.environ.get("MAOP_AUTH_DISABLED_ADMIN", "0") == "1":
            logger.warning(
                "MAOP_AUTH_DISABLED_ADMIN is DEPRECATED and IGNORED. "
                "Use MAOP_OWNER_MODE=1 with a loopback bind for the local "
                "single-user owner role."
            )
        client_host = getattr(getattr(request, "client", None), "host", "") or ""
        roles, identity = _disabled_auth_roles(client_host)
        request.state.auth_roles = roles
        request.state.auth_identity = identity
        request.state.auth_scopes = []
        request.state.tenant_id = ""
        return cast(Response, await call_next(request))

    def _is_static_asset(self, path: str) -> bool:
        """Return True for static asset paths that bypass auth."""
        return path.startswith("/static/") or path.endswith(
            (".css", ".js", ".ico", ".png", ".svg")
        )

    async def _authenticate_api_key(
        self,
        api_key: str,
        request: Request,
        call_next: Callable,
        api_key_mgr: Any,
        auth_manager: Any,
    ) -> Response:
        """Validate the X-API-Key header and dispatch.

        Tries the new structured ApiKeyManager first, then falls back to the
        legacy auth_checker / auth_manager paths.
        """
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
                return cast(Response, await call_next(request))  # No auth configured = pass through

            if result.authenticated:
                request.state.auth_identity = result.identity
                request.state.auth_roles = result.roles
                # P0-1 fix: legacy results carry no tenant → "" (unchanged
                # behaviour); new-ApiKeyManager results propagate theirs.
                request.state.tenant_id = getattr(result, "tenant_id", "") or ""
                # Legacy paths carry no scopes → empty list (ScopeMiddleware
                # will treat missing/empty scopes as deny for protected routes).
                request.state.auth_scopes = []
                return cast(Response, await call_next(request))
            return JSONResponse(
                status_code=401,
                content={"error": "Invalid API key", "detail": result.error},
            )
        except Exception as e:
            logger.warning("Auth check failed: %s", e)
            return JSONResponse(status_code=500, content={"error": "Auth check failed"})

    async def _authenticate_bearer(
        self,
        token: str,
        request: Request,
        call_next: Callable,
        api_key_mgr: Any,
        auth_manager: Any,
    ) -> Response:
        """Validate a Bearer token (JWT or structured API key) and dispatch."""
        try:
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
                # P1-10 fix: fail-closed. A credential was PRESENTED but no
                # verifier is wired (e.g. a race window during e2e fixture
                # teardown where app.state.auth_manager is momentarily None).
                # Passing through anonymously let a TAMPERED token through
                # (e2e test_tampered_token_rejected failed intermittently
                # with 200). Presenting a credential commits this request to
                # verification — reject with 401 instead of anonymous pass.
                logger.warning(
                    "Bearer credential present but no verifier configured; rejecting (fail-closed)"
                )
                return JSONResponse(
                    status_code=401,
                    content={"error": "Authentication verifier unavailable", "detail": "auth manager not initialized"},
                )

            if result.authenticated:
                request.state.auth_identity = result.identity
                request.state.auth_roles = result.roles
                # P0-1 fix: JWT results carry no tenant claim yet → "".
                request.state.tenant_id = getattr(result, "tenant_id", "") or ""
                # JWT/legacy paths carry no scopes → empty list.
                request.state.auth_scopes = []
                return cast(Response, await call_next(request))
            return JSONResponse(
                status_code=401,
                content={"error": "Invalid token", "detail": result.error},
            )
        except Exception as e:
            logger.warning("JWT check failed: %s", e)
            return JSONResponse(status_code=500, content={"error": "Auth check failed"})

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # P0-1 FIX (R3 audit): restore if-guard that was lost in P2-3 fix.
        # Without this guard, ALL auth logic below is dead code and every
        # request gets anonymous/read role regardless of MAOP_AUTH setting.
        if not self.enabled:
            return await self._dispatch_disabled(request, call_next)

        # P0-1 fix: default tenant context for EVERY request. Downstream
        # consumers (enterprise QuotaMiddleware, RBAC/compliance/notification
        # routers) read ``request.state.tenant_id``; before this fix the
        # attribute was never assigned on any path, so every consumer fell
        # back to "" and tenant isolation silently never engaged. API-key
        # authentication below overrides this default with the key's real
        # tenant. JWT/dashboard sessions have no tenant claim yet and keep
        # "" (documented single-tenant fallback in _tenant_id_from_jwt).
        request.state.tenant_id = ""

        # Skip public paths — exact match for most paths; prefix match only
        # for opt-in prefix_public_paths (e.g. /api/stream/agent/{id}).
        # Per-handler auth (require_admin / _check_sse_token) still applies
        # inside the route. (P0-3 fix: /api/auth/login/* is NO LONGER
        # exempted — only /api/stream/* is, via prefix_public_paths.)
        path = request.url.path
        if self._is_public_path(path):
            return cast(Response, await call_next(request))

        # Skip static assets
        if self._is_static_asset(path):
            return cast(Response, await call_next(request))

        auth_manager = getattr(request.app.state, "auth_manager", None)
        # New structured ApiKeyManager (scopes / IP allow-list / rate limit / usage)
        api_key_mgr = getattr(request.app.state, "api_key_manager", None)

        # Check API Key header — prefer the new ApiKeyManager when available.
        api_key = request.headers.get(self.api_key_header, "")
        if api_key:
            return await self._authenticate_api_key(
                api_key, request, call_next, api_key_mgr, auth_manager
            )

        # Check Authorization header (Bearer JWT or Bearer maop_{key_id}_{secret})
        auth_header = request.headers.get(self.auth_header, "")
        # #4 fix: fallback to httpOnly cookie if no Authorization header
        if not auth_header.startswith("Bearer ") and "maop_token" in request.cookies:
            auth_header = "Bearer " + request.cookies["maop_token"]
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
            return await self._authenticate_bearer(
                token, request, call_next, api_key_mgr, auth_manager
            )

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

        # P1-10 fix (second path, balanced): no verifier wired AND no
        # credential presented. Two consumers need this branch:
        #   - test_server_coverage.py / test_enterprise_guard.py exercise the
        #     PUBLIC surface (guard/CSP/exception handlers/SPA fallback) with
        #     app.state auth deliberately cleared, relying on anonymous
        #     pass-through;
        #   - a transient teardown race window (e2e fixtures) can leave
        #     auth_manager momentarily None — the earlier hard fail-closed
        #     broke the former and did not fix the tampered-token flake
        #     (that one carries a credential and is rejected by the
        #     _authenticate_bearer fail-closed branch).
        # Keep pass-through here — but grant the LEAST-privileged role (read,
        # same as _dispatch_disabled's C-1 hardening) instead of nothing, so
        # admin-gated handlers still 403 in this window.
        request.state.auth_identity = "anonymous"
        request.state.auth_roles = ["read"]
        request.state.auth_scopes = []
        request.state.tenant_id = ""
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
        # P0-1 fix: propagate the API key's tenant so quota / RBAC /
        # compliance isolation actually engages for key-authenticated
        # requests instead of silently running tenant-less.
        request.state.tenant_id = getattr(result, "tenant_id", "") or ""

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


# ── Scope Middleware ─────────────────────────────────────────

class ScopeMiddleware(BaseHTTPMiddleware):
    """Route-level scope authorization middleware.

    Runs *after* AuthMiddleware so ``request.state.auth_scopes`` / ``auth_roles``
    are already populated. Checks each protected path against a configured
    scope-requirement table and rejects (403) requests whose scopes/roles do
    not satisfy the requirement.

    Design notes
    ------------
    - Route registration happens *after* middleware setup, so path-based
      requirements are expressed as path *prefixes* matched against
      ``request.url.path`` rather than route names.
    - If no requirement matches a path and ``default_required_scopes`` is
      empty, the path is allowed through (auth already happened in
      AuthMiddleware; this middleware only adds scope gating).
    - ``auth_scopes`` comes from the new ApiKeyManager; legacy/JWT/auth-disabled
      paths set ``auth_scopes = []``, so they satisfy no scope requirement
      beyond roles. Roles are checked via ``default_required_roles`` if set.
    """

    def __init__(
        self,
        app: Any,
        *,
        enabled: bool = True,
        scope_requirements: dict[str, list[str]] | None = None,
        default_required_scopes: list[str] | None = None,
        default_required_roles: list[str] | None = None,
        exempt_paths: list[str] | None = None,
    ):
        super().__init__(app)
        self.enabled = enabled
        # path prefix -> list of required scopes (ALL must be present)
        self.scope_requirements = scope_requirements or {}
        self.default_required_scopes = set(default_required_scopes or [])
        self.default_required_roles = set(default_required_roles or [])
        self.exempt_paths = set(exempt_paths or [
            "/", "/api/health",
            "/api/docs", "/openapi.json",
            "/api/auth/login", "/api/auth/status",
        ])

    def _required_scopes_for(self, path: str) -> list[str] | None:
        """Return required scopes for ``path`` if any scope_requirement matches,
        else None. Longest prefix wins."""
        matched: list[tuple[int, list[str]]] = []
        for prefix, scopes in self.scope_requirements.items():
            if path == prefix or path.startswith(prefix.rstrip("/") + "/"):
                matched.append((len(prefix), scopes))
        if not matched:
            return None
        matched.sort(key=lambda x: x[0], reverse=True)
        return matched[0][1]

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if not self.enabled:
            return cast(Response, await call_next(request))

        path = request.url.path
        if path in self.exempt_paths:
            return cast(Response, await call_next(request))

        # Determine required scopes for this path.
        required_scopes = self._required_scopes_for(path)
        if required_scopes is None:
            required_scopes = list(self.default_required_scopes)

        required_roles = set(self.default_required_roles)

        # Gather effective scopes/roles from auth state.
        auth_scopes = set(getattr(request.state, "auth_scopes", []) or [])
        auth_roles = set(getattr(request.state, "auth_roles", []) or [])

        # Scope check: ALL required scopes must be present.
        if required_scopes and not set(required_scopes).issubset(auth_scopes):
            return JSONResponse(
                status_code=403,
                content={
                    "error": "Forbidden",
                    "detail": f"Missing required scopes: {', '.join(required_scopes)}",
                },
            )

        # Role check: at least one required role must be present (if any required).
        if required_roles and not (auth_roles & required_roles):
            return JSONResponse(
                status_code=403,
                content={
                    "error": "Forbidden",
                    "detail": f"Missing required roles: {', '.join(sorted(required_roles))}",
                },
            )

        return cast(Response, await call_next(request))


# ── Rate Limit Middleware ─────────────────────────────────────────

class RateLimitMiddleware(BaseHTTPMiddleware):
    """FastAPI middleware for per-IP rate limiting.

    Uses Redis-backed sliding-window counter when ``MAOP_REDIS_URL`` is set
    (or ``redis_url`` param passed), falling back to an in-memory token bucket
    when Redis is unavailable or not configured.

    .. note::
        **Multi-process behavior**:
        - Redis backend: shared across all workers, consistent rate limiting.
        - In-memory fallback: per-process only; actual threshold is
          ``rate × worker_count`` under uvicorn/gunicorn multi-worker.

    .. note::
        **Skip paths**: health checks, Prometheus metrics, and static assets
        are excluded from rate limiting by default so monitoring probes are
        never throttled.
    """

    _REDIS_LUA_SLIDING_WINDOW = """
        local key = KEYS[1]
        local now = tonumber(ARGV[1])
        local window = tonumber(ARGV[2])
        local limit = tonumber(ARGV[3])
        -- Remove entries outside the window
        redis.call('ZREMRANGEBYSCORE', key, '-inf', now - window)
        local count = redis.call('ZCARD', key)
        if count >= limit then
            local ttl = redis.call('TTL', key)
            return {-1, ttl}
        end
        redis.call('ZADD', key, now, now .. ':' .. math.random(1,1000000))
        redis.call('EXPIRE', key, window)
        return {count + 1, 0}
    """

    def __init__(
        self,
        app: Any,
        *,
        rate: float = 30.0,        # Requests per second
        burst: int = 60,           # Max burst
        enabled: bool = True,
        key_func: Callable | None = None,  # Custom key extraction (default: client IP)
        redis_url: str | None = None,
        skip_paths: list[str] | None = None,
    ):
        super().__init__(app)
        self.enabled = enabled
        self.rate = rate
        self.burst = burst
        self.key_func = key_func or self._default_key
        self.redis_url = redis_url or __import__("os").environ.get("MAOP_REDIS_URL")
        self._redis: Any = None
        self._redis_ttl = max(1, int(burst / rate)) + 5  # seconds to keep keys
        self._skip_paths = set(skip_paths or [
            "/api/health", "/", "/api/prometheus", "/metrics",
            "/api/docs", "/openapi.json",
        ])
        self._skip_extensions = (".css", ".js", ".ico", ".png", ".svg", ".woff2", ".woff", ".ttf")
        self._buckets: dict[str, Any] = {}
        self._request_count = 0  # for periodic cleanup
        # B20: 保护 _buckets/_request_count 的并发读写。
        self._lock = threading.Lock()
        self._redis_available = self._init_redis()

    def _init_redis(self) -> bool:
        """Best-effort Redis connection. Returns True if available."""
        if not self.redis_url:
            return False
        try:
            import redis  # type: ignore
            self._redis = redis.Redis.from_url(self.redis_url, socket_connect_timeout=1, socket_timeout=1)
            self._redis.ping()
            logger.info("RateLimitMiddleware: Redis backend connected (%s)", self.redis_url)
            return True
        except Exception as exc:  # pragma: no cover — Redis is optional
            logger.warning("RateLimitMiddleware: Redis unavailable (%s), falling back to in-memory", exc)
            self._redis = None
            return False

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

    def _should_skip(self, path: str) -> bool:
        """Return True if the path should bypass rate limiting."""
        return path in self._skip_paths or any(
            path.endswith(ext) for ext in self._skip_extensions
        )

    def _redis_check(self, key: str) -> tuple[bool, float]:
        """Check rate limit via Redis sliding window.

        Returns (allowed, retry_after_seconds).
        """
        assert self._redis is not None
        now = time.time()
        window = int(self._redis_ttl)
        count, ttl = self._redis.eval(
            self._REDIS_LUA_SLIDING_WINDOW,
            1,
            f"ratelimit:{key}",
            now,
            window,
            self.burst,
        )
        if count == -1:
            return False, float(ttl or 0)
        return True, 0.0

    def _memory_check(self, key: str) -> tuple[bool, float]:
        """Check rate limit via in-memory token bucket.

        Returns (allowed, retry_after_seconds).
        """
        now = time.monotonic()

        # B20: 加锁保护 _buckets/_request_count 的并发读写。
        with self._lock:
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
                return False, retry_after

            bucket["tokens"] -= 1.0
        return True, 0.0

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if not self.enabled:
            return cast(Response, await call_next(request))

        # Skip health checks, metrics, static assets
        path = request.url.path
        if self._should_skip(path):
            return cast(Response, await call_next(request))

        key = self.key_func(request)

        # Prefer Redis backend; fall back to in-memory transparently.
        if self._redis_available and self._redis is not None:
            try:
                allowed, retry_after = self._redis_check(key)
            except Exception as exc:  # pragma: no cover — Redis transient failure
                logger.warning("RateLimitMiddleware: Redis check failed (%s), falling back to in-memory", exc)
                self._redis_available = False
                allowed, retry_after = self._memory_check(key)
        else:
            allowed, retry_after = self._memory_check(key)

        if not allowed:
            return JSONResponse(
                status_code=429,
                content={
                    "error": "Rate limit exceeded",
                    "retry_after_s": round(retry_after, 2),
                },
                headers={"Retry-After": str(int(retry_after) + 1)},
            )

        return cast(Response, await call_next(request))


# ── CSP & Security Headers Middleware ─────────────────────────────

# t88-L9: Dangerous connect-src tokens that weaken CSP.
# These are logged at WARNING but not rejected, because some
# deployments legitimately need them.
_CSP_DANGEROUS_TOKENS: dict[str, str] = {
    "*": "wildcard '*' allows connections to ANY origin — effectively disables connect-src",
    "http:": "plain HTTP scheme allows unencrypted connections (MITM risk)",
    "ws:": "plain WebSocket scheme allows unencrypted WS (MITM risk)",
    "data:": "data: URIs in connect-src allow inline content injection",
    "blob:": "blob: URIs in connect-src allow dynamic content sources",
}


def _validate_connect_src(connect_src: str, *, production: bool = False) -> None:
    """Validate CSP ``connect-src`` value for dangerous patterns.

    This is a **trust-policy audit**. The CSP ``connect-src`` directive controls
    where the browser can issue ``fetch()``, ``XMLHttpRequest``, and WebSocket
    connections. A permissive value undermines the entire CSP XSS mitigation.

    Trust policy
    ------------
    - ``'self'`` (default): trusted — same-origin only.
    - ``'none'``: trusted — most restrictive.
    - ``https://host`` / ``wss://host``: trusted — encrypted, explicit host.
    - Dangerous tokens (see ``_CSP_DANGEROUS_TOKENS``):
        * Non-production: logged at WARNING but NOT rejected, because dev
          environments may need ``http:`` for localhost or ``*`` for a public
          API gateway.
        * Production (``production=True``): **fail-closed** — raises
          ``ValueError`` so the server refuses to start with a weakened CSP.
    - Empty string: rejected (would produce ``connect-src`` with no sources,
      which defaults to ``default-src`` — confusing and likely unintended).

    Args:
        connect_src: The raw ``connect-src`` value (e.g. ``"'self'"``,
            ``"'self' wss://api.example.com"``).
        production: When True, dangerous tokens cause a hard failure instead
            of a warning.

    Raises:
        ValueError: If ``connect_src`` is empty, or (when production=True)
            when a dangerous token is present.
    """
    if production is None:
        from maop.config.settings import is_production

        production = is_production()

    if not connect_src or not connect_src.strip():
        raise ValueError(
            "CSP connect-src must not be empty — an empty value produces "
            "'connect-src' with no sources, which falls back to default-src "
            "and is likely unintended. Use 'none' to explicitly block all "
            "connections."
        )

    # Tokenize on whitespace and check each token.
    tokens = connect_src.split()
    for token in tokens:
        # Check exact-match dangerous tokens (e.g. "*", "http:")
        if token in _CSP_DANGEROUS_TOKENS:
            msg = (
                f"[CSP] connect-src contains dangerous token {token!r}: "
                f"{_CSP_DANGEROUS_TOKENS[token]}. "
            )
            if production:
                raise ValueError(msg + "Production CSP must not contain dangerous tokens. Use explicit https:// hosts.")
            logger.warning(msg + "This weakens the Content-Security-Policy. Ensure this is intentional (e.g. dev environment).")
        # Check prefix-match for http:// and ws:// (scheme + explicit host)
        elif token.startswith("http://"):
            msg = (
                f"[CSP] connect-src allows plain HTTP origin {token!r} — "
                "connections to this host are unencrypted (MITM risk). "
            )
            if production:
                raise ValueError(msg + "Production CSP must not allow plain HTTP origins. Prefer https://.")
            logger.warning(msg + "Prefer https:// in production.")
        elif token.startswith("ws://"):
            msg = (
                f"[CSP] connect-src allows plain WebSocket origin {token!r} — "
                "connections to this host are unencrypted (MITM risk). "
            )
            if production:
                raise ValueError(msg + "Production CSP must not allow plain WebSocket origins. Prefer wss://.")
            logger.warning(msg + "Prefer wss:// in production.")


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
        production: bool = False,
    ):
        super().__init__(app)
        self.enabled = enabled
        self.report_only = report_only
        self.report_uri = report_uri

        # t88-L9: validate connect-src before building the policy.
        # connect-src governs XHR/fetch/WebSocket destinations — a
        # misconfigured value can punch a hole through the CSP. In production
        # mode, dangerous tokens cause a hard failure (fail-closed).
        _validate_connect_src(connect_src, production=production)

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


# ── Shared auth helpers ──────────────────────────────────────────

def require_admin(request: Request) -> None:
    """Raise HTTPException(403) if not authenticated as admin or superadmin."""
    roles = getattr(request.state, "auth_roles", None) or []
    # P1-13 fix: accept both "admin" and "superadmin" (RBAC hierarchy)
    if not ({"admin", "superadmin"} & set(roles)):
        from fastapi import HTTPException
        raise HTTPException(403, "Admin role required")


# ── Audit Middleware ─────────────────────────────────────────────────

class AuditMiddleware(BaseHTTPMiddleware):
    """Records every API request as an audit log entry.

    Fields captured: timestamp, method, path, user identity/roles, tenant_id,
    status_code, latency_ms, client_ip, user_agent.
    Output goes to a structured ``maop.audit`` logger (JSON-ish log record),
    which operators can ship to SIEM / log aggregation.
    """

    def __init__(
        self,
        app: Any,
        *,
        enabled: bool = True,
        exclude_paths: list[str] | None = None,
    ):
        super().__init__(app)
        self.enabled = enabled
        self.exclude_paths = set(exclude_paths or ["/api/health", "/", "/api/docs", "/openapi.json"])
        self._logger = logging.getLogger("maop.audit")

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if not self.enabled:
            return cast(Response, await call_next(request))

        path = request.url.path
        if path in self.exclude_paths:
            return cast(Response, await call_next(request))

        start = time.monotonic()
        response = cast(Response, await call_next(request))
        latency_ms = round((time.monotonic() - start) * 1000.0, 2)

        self._logger.info(
            "audit: method=%s path=%s status=%s latency_ms=%s "
            "identity=%s roles=%s tenant=%s ip=%s ua=%s",
            request.method,
            path,
            getattr(response, "status_code", 0),
            latency_ms,
            getattr(request.state, "auth_identity", "") or "",
            ",".join(getattr(request.state, "auth_roles", []) or []),
            getattr(request.state, "tenant_id", "") or "",
            (request.client.host if request.client else ""),
            request.headers.get("user-agent", ""),
        )
        return response


# ── IP Allowlist / Blocklist Middleware ──────────────────────────────

class IPAllowlistMiddleware(BaseHTTPMiddleware):
    """Global IP allowlist/blocklist middleware.

    Operates before auth: requests from blocked IPs are rejected with 403
    regardless of credentials. Requests from IPs not on the allowlist (when
    allowlist is non-empty) are also rejected with 403. This provides a
    network-level gate independent of per-API-key IP restrictions.

    Config via env vars:
      MAOP_IP_BLOCKLIST   comma-separated CIDRs / IPs to block
      MAOP_IP_ALLOWLIST   comma-separated CIDRs / IPs to allow (empty = allow all)
    """

    def __init__(
        self,
        app: Any,
        *,
        enabled: bool = True,
        blocklist: list[str] | None = None,
        allowlist: list[str] | None = None,
    ):
        super().__init__(app)
        self.enabled = enabled
        self.blocklist = blocklist or []
        self.allowlist = allowlist or []

    def _matches(self, ip: str, patterns: list[str]) -> bool:
        import ipaddress
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            return False
        for pat in patterns:
            pat = pat.strip()
            if not pat:
                continue
            try:
                if "/" in pat:
                    if addr in ipaddress.ip_network(pat, strict=False):
                        return True
                elif addr == ipaddress.ip_address(pat):
                    return True
            except ValueError:
                continue  # silent-ok: scan-continue — 单个非法网段/地址模式跳过继续匹配后续
        return False

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if not self.enabled:
            return cast(Response, await call_next(request))

        client_ip = request.client.host if request.client else ""
        # Trust-proxy awareness for real client IP.
        from maop.config.settings import trust_proxy_enabled

        if trust_proxy_enabled():
            xff = request.headers.get("x-forwarded-for", "")
            if xff:
                client_ip = xff.split(",")[0].strip()

        if client_ip and self._matches(client_ip, self.blocklist):
            return JSONResponse(status_code=403, content={"error": "IP blocked"})

        if self.allowlist and client_ip and not self._matches(client_ip, self.allowlist):
            return JSONResponse(status_code=403, content={"error": "IP not allowed"})

        return cast(Response, await call_next(request))


# ── Request Size Limit Middleware ────────────────────────────────────

class RequestSizeLimitMiddleware(BaseHTTPMiddleware):
    """Rejects requests whose body exceeds a configured byte limit.

    FastAPI/Starlette do not enforce a request-body size cap by default,
    making the service vulnerable to large-body DoS. This middleware reads
    the ``Content-Length`` header and short-circuits oversized requests
    before the body is parsed.

    Config:
      max_bytes: maximum allowed body size in bytes (default 10 MiB)
    """

    def __init__(
        self,
        app: Any,
        *,
        enabled: bool = True,
        max_bytes: int = 10 * 1024 * 1024,
    ):
        super().__init__(app)
        self.enabled = enabled
        self.max_bytes = max_bytes

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if not self.enabled:
            return cast(Response, await call_next(request))

        content_length = request.headers.get("content-length")
        if content_length is not None:
            try:
                size = int(content_length)
            except ValueError:
                size = 0
            if size > self.max_bytes:
                return JSONResponse(
                    status_code=413,
                    content={
                        "error": "Request body too large",
                        "max_bytes": self.max_bytes,
                    },
                )

        return cast(Response, await call_next(request))


# ── One-call setup (extended) ────────────────────────────────────────

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
