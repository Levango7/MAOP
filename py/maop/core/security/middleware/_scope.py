"""Route-level scope authorization middleware.

原 ``middleware.py`` 的 Scope 块（2026-10-28 拆分）：在 AuthMiddleware 之后
运行，按路径前缀表校验 auth_scopes / auth_roles。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware


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
