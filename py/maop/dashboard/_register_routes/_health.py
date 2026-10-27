# _register_routes._health: health check endpoint.
# Split from _register_routes.py into _register_routes/_health.py.

from __future__ import annotations

import logging
import time
from typing import Any

from fastapi.responses import JSONResponse as _JResp

from maop import __version__ as MAOP_VERSION
from maop.config.edition import get_edition
from maop.dashboard.routers import state as _state

logger: logging.Logger = logging.getLogger(__name__)

__all__: list[str] = []


async def health() -> Any:
    # Minimal critical-dependency probe: the metadata storage (SQLite/PostgreSQL)
    # that backs the dashboard. A lightweight `SELECT 1` forces a real round-trip
    # without scanning tables, so the endpoint stays cheap and is safe to call
    # from Docker/K8s probes. If storage is unreachable the process is unhealthy
    # and we return 503 so orchestrators stop routing traffic / alert.
    storage_ok = True
    try:
        from maop.core.backends.db_utils import get_db_path, get_pool
        pool = get_pool(get_db_path())
        conn = pool.acquire()
        try:
            conn.execute("SELECT 1")
        finally:
            pool.release(conn)
    except Exception as exc:
        storage_ok = False
        logger.warning("[health] storage dependency check failed: %s", exc)

    active_agents = 0
    try:
        _agents = await _state.get_bridge().agent_stats()
        active_agents = len(_agents) if isinstance(_agents, list) else 0
    except Exception:
        logger.warning("获取 active_agents 数量失败（健康检查路由），将按 0 处理", exc_info=True)

    if not storage_ok:
        return _JResp(
            status_code=503,
            content={
                "status": "error",
                "reason": "storage_unavailable",
                "version": MAOP_VERSION,
                "edition": get_edition().value,
            },
        )
    return {"status": "ok", "version": MAOP_VERSION, "edition": get_edition().value,
            "dashboard": f"MAOP Dashboard v{MAOP_VERSION} (FastAPI)",
            "uptime_ms": round((time.time() - _state.start_time) * 1000),
            "active_agents": active_agents,
            "tls": _state.tls_enabled, "auth": _state.auth_enabled, "rate_limit": _state.rl_enabled}
