"""Auth middleware + owner-mode helpers + shared admin guard.

原 ``middleware.py`` 的 Auth 块（2026-10-28 拆分）：模块身份说明见包级
``__init__.py``；本模块只承载 AuthMiddleware 及其直接依赖。
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any, cast

from fastapi import Request, Response
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


def require_admin(request: Request) -> None:
    """Raise HTTPException(403) if not authenticated as admin or superadmin."""
    roles = getattr(request.state, "auth_roles", None) or []
    # P1-13 fix: accept both "admin" and "superadmin" (RBAC hierarchy)
    if not ({"admin", "superadmin"} & set(roles)):
        from fastapi import HTTPException
        raise HTTPException(403, "Admin role required")
