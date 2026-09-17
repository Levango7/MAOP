"""Configuration history & rollback API.

Endpoints:
    GET  /api/config/history             — list recent config snapshots
    GET  /api/config/history/{version}   — get a specific snapshot (with payload)
    POST /api/config/rollback/{version}  — restore a previous config version

All endpoints require admin role: config snapshots may contain sensitive
runtime values (model keys, route tables, …) and rollback is a
destructive operation.

The router looks up the :class:`ConfigHistory` instance via
``request.app.state.config_history`` first, falling back to the
process-wide singleton.  Tests can therefore inject a fresh instance on
``app.state`` for isolation.

Business logic (list/get/rollback) lives in
:mod:`maop.dashboard.services.system_service`; this router only does
request parsing, auth, service dispatch, and response formatting.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from maop.core.config.config_history import ConfigHistory, get_config_history
from maop.core.security.middleware import require_admin
from maop.dashboard.error_handler import handle_api_errors
from maop.dashboard.services import system_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/config", tags=["config-history"])


def _history(request: Request) -> ConfigHistory:
    """Return the ConfigHistory instance for this request.

    Prefer ``app.state.config_history`` (set by tests or explicit wiring)
    over the global singleton so each test can isolate its DB.
    """
    inst = getattr(request.app.state, "config_history", None)
    if inst is not None:
        return inst
    return get_config_history()


def _actor(request: Request) -> str:
    """Identity of the current caller for the ``changed_by`` audit field."""
    return (
        getattr(request.state, "auth_identity", None)
        or getattr(request.state, "auth_user", None)
        or "unknown"
    )


# ── List history ──────────────────────────────────────────────────
@router.get("/history")
@handle_api_errors("config history list")
async def list_config_history(
    request: Request,
    limit: int = Query(50, ge=1, le=500, description="Max snapshots to return"),
) -> dict[str, Any]:
    """Return recent configuration snapshots (newest first).

    Response shape::

        {"history": [{"version": int, "changed_by": str, "changed_at": str}, …],
         "count": int}
    """
    require_admin(request)
    hist = _history(request)
    items = system_service.list_config_history(hist, limit=limit)
    return {"status": "ok", "history": items, "count": len(items)}


# ── Get a specific version ────────────────────────────────────────
@router.get("/history/{version}")
@handle_api_errors("config history detail")
async def get_config_version(
    version: int,
    request: Request,
) -> dict[str, Any]:
    """Return a single snapshot including the parsed config payload.

    Raises 404 if the version does not exist.
    """
    require_admin(request)
    hist = _history(request)
    record = system_service.get_config_version(hist, version)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Config version {version} not found")
    return {"status": "ok", **record}


# ── Rollback ──────────────────────────────────────────────────────
@router.post("/rollback/{version}")
@handle_api_errors("config rollback")
async def rollback_config(
    version: int,
    request: Request,
) -> dict[str, Any]:
    """Restore the configuration to a previously saved snapshot.

    The rollback itself is recorded as a new snapshot (so the audit
    trail remains linear) and a ``config_changed`` event is fired on
    the global event bus.

    Raises 404 if the target version does not exist.
    """
    require_admin(request)
    hist = _history(request)
    try:
        restored = system_service.rollback_config(hist, version)
    except ValueError as exc:
        # 批次3A: 脱敏——ValueError 细节不暴露给客户端，仅日志记录。
        logger.warning("[config-api] Rollback failed: %s", exc)
        raise HTTPException(status_code=404, detail="Version not found") from exc
    logger.info(
        "[config-api] Rollback to v%d by %s → new v%d",
        version, _actor(request), restored["version"],
    )
    return {
        "status": "ok",
        "restored_from_version": version,
        "new_version": restored["version"],
        "changed_by": restored["changed_by"],
        "changed_at": restored["changed_at"],
    }
