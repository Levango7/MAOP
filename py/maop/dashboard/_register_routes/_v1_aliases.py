# _register_routes._v1_aliases: API v1 alias registration.
# Split from _register_routes.py into _register_routes/_v1_aliases.py.

from __future__ import annotations

import logging

from fastapi import APIRouter

logger: logging.Logger = logging.getLogger(__name__)

__all__: list[str] = []


def _register_v1_aliases() -> None:
    """Register /api/v1/* aliases for all /api/* routes (except auth/stream/health)."""
    # 从包的 __dict__ 读取 _app，使 register_routers 设置的值可见。
    import maop.dashboard._register_routes as _rr

    app = _rr._app
    if app is None:
        raise RuntimeError("_register_v1_aliases called before register_routers")
    EXEMPT = {
        "/api/health",
        "/api/stream",
        "/api/auth/login",
        "/api/auth/logout",
        "/api/auth/refresh",
    }
    v1 = APIRouter()
    existing = {getattr(r, "path", None) for r in app.routes}
    for route in app.routes:
        path = getattr(route, "path", "")
        if not path.startswith("/api/") or path in EXEMPT:
            continue
        endpoint = getattr(route, "endpoint", None)
        if endpoint is None:
            continue
        v1_path = "/api/v1" + path[4:]
        if v1_path in existing:
            continue
        methods = getattr(route, "methods", None) or {"GET"}
        v1.add_api_route(v1_path, endpoint, methods=methods)
    app.include_router(v1)
    logger.info("[server] Registered %d /api/v1/* alias routes", len(v1.routes))
