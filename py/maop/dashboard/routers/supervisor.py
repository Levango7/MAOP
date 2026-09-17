"""MAOP Dashboard — Supervisor API endpoints (proactive multi-agent supervision).

Exposes the :class:`~maop.core.scheduling.supervisor.Supervisor` state so
operators can answer "which agents are degraded / terminated / upgrading
and why?" and manually trigger patrol / control actions from the dashboard.

Endpoints
---------
- ``GET /api/supervisor/status`` — full supervisor status (agents, patrol,
  pending alerts, recent actions, config, rules).
- ``GET /api/supervisor/rules`` — current supervision rule set.
- ``POST /api/supervisor/rules`` — hot-update the rule set (admin).
- ``GET /api/supervisor/actions`` — control action history (optional agent filter).
- ``POST /api/supervisor/patrol`` — manually trigger one patrol round (admin).
- ``POST /api/supervisor/action`` — manually execute a control action (admin).

All GET endpoints are read-only and do not require admin auth.
POST endpoints require the ``admin`` role (via ``require_admin`` middleware).

业务逻辑已提取至 ``maop.dashboard.services.scheduling_service``（§2）。
本 router 仅保留：路由定义 / 请求参数解析 / 权限检查 /
service 调用 / 响应格式化 / 错误处理。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from maop.core.scheduling.supervisor import (
    SupervisorActionRequest,
    SupervisorRule,
)
from maop.core.security.middleware import require_admin
from maop.dashboard.error_handler import handle_api_errors
from maop.dashboard.services import scheduling_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/supervisor", tags=["supervisor"])


def _get_supervisor_or_404() -> Any:
    """Return the process-wide Supervisor singleton or raise 404.

    When no Supervisor has been configured (passive-only mode), the
    endpoints return 404 so the dashboard can show a "supervisor not
    enabled" message rather than a confusing 500.
    """
    sup = scheduling_service._get_supervisor()
    if sup is None:
        raise HTTPException(
            status_code=404,
            detail="Supervisor not configured (passive-only mode). "
                   "Instantiate a Supervisor and call set_supervisor() to enable.",
        )
    # 保持与原 router 一致的 isinstance 检查
    from maop.core.scheduling.supervisor import Supervisor
    if not isinstance(sup, Supervisor):
        raise HTTPException(
            status_code=404,
            detail="Supervisor not configured (passive-only mode). "
                   "Instantiate a Supervisor and call set_supervisor() to enable.",
        )
    return sup



# ── Status ─────────────────────────────────────────────────────


@router.get("/status")
@handle_api_errors(
    "Supervisor status",
    error_value={"agents": [], "patrol": {}, "pending_alerts": [],
                 "recent_actions": [], "config": {}, "rules": [],
                 "error": "Supervisor unavailable"},
)
async def api_supervisor_status(request: Request) -> dict[str, Any]:
    """Return the full supervisor status snapshot."""
    require_admin(request)
    _get_supervisor_or_404()
    # M-1 fix: 包裹为 {status, data} 统一响应格式。
    return {"status": "ok", "data": scheduling_service.get_supervisor_status()}


# ── Rules ──────────────────────────────────────────────────────


@router.get("/rules")
@handle_api_errors(
    "Supervisor rules",
    error_value={"rules": [], "error": "Query failed"},
)
async def api_supervisor_rule_list(request: Request) -> dict[str, Any]:
    """Return the current supervision rule set."""
    require_admin(request)
    _get_supervisor_or_404()
    rules = scheduling_service.list_supervisor_rules()
    return {"status": "ok", "rules": [r.model_dump() for r in rules]}


@router.post("/rules")
@handle_api_errors(
    "Supervisor rules update",
    error_value={"status": "error", "error": "Update failed"},
)
async def api_supervisor_rule_update(
    request: Request,
    body: list[SupervisorRule],
) -> dict[str, Any]:
    """Hot-update the supervision rule set (admin only).

    Body is a list of rule dicts; each is validated into
    :class:`SupervisorRule`. Invalid rules abort the update without
    partially applying.
    """
    require_admin(request)
    _get_supervisor_or_404()
    rule_count = scheduling_service.update_supervisor_rules(body)
    logger.info(
        "[supervisor-api] rule set updated (%d rules, by=%s)",
        rule_count,
        getattr(getattr(request, "state", None), "auth_identity", "unknown"),
    )
    return {"status": "ok", "rule_count": rule_count}


# ── Actions ────────────────────────────────────────────────────


@router.get("/actions")
@handle_api_errors(
    "Supervisor actions",
    error_value={"actions": [], "error": "Query failed"},
)
async def api_supervisor_actions(
    request: Request,
    agent_id: str | None = None,
    limit: int = Query(50, ge=1, le=1000),
) -> dict[str, Any]:
    """Return control action history (optionally filtered by agent)."""
    require_admin(request)
    _get_supervisor_or_404()
    actions = scheduling_service.get_supervisor_actions(agent_id=agent_id, limit=limit)
    return {"status": "ok", "actions": [a.model_dump() for a in actions]}


@router.post("/action")
@handle_api_errors(
    "Supervisor manual action",
    error_value={"status": "error", "error": "Action failed"},
)
async def api_supervisor_action(
    request: Request,
    body: SupervisorActionRequest,
) -> dict[str, Any]:
    """Manually execute a control action on an agent (admin only).

    Body shape::

        {
          "agent_id": "agent_x",
          "action": "degrade",        # replace|degrade|terminate|upgrade|alert
          "params": {"factor": 0.5},  # action-specific params
          "reason": "manual degrade: latency"
        }
    """
    require_admin(request)
    _get_supervisor_or_404()
    try:
        result = await scheduling_service.supervisor_action(
            agent_id=body.agent_id,
            action=body.action,
            params=body.params or {},
            reason=body.reason,
        )
        return {"status": "ok", **result}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(
            "[supervisor-api] manual action %s on %s failed",
            body.action, body.agent_id,
        )
        raise HTTPException(
            status_code=500,
            detail="Supervisor action execution failed",
        ) from exc


# ── Patrol ─────────────────────────────────────────────────────


@router.post("/patrol")
@handle_api_errors(
    "Supervisor manual patrol",
    error_value={"status": "error", "error": "Patrol failed"},
)
async def api_supervisor_patrol(
    request: Request,
) -> dict[str, Any]:
    """Manually trigger one patrol round (admin only).

    Returns the probes collected this round and the count of issues
    found (rules matched + unreachable strikes).
    """
    require_admin(request)
    _get_supervisor_or_404()
    probes, duration_s = await scheduling_service.supervisor_patrol()
    return {
        "status": "ok",
        "agents_checked": len(probes),
        "probes": [p.model_dump() for p in probes],
        "patrol_duration_s": round(duration_s, 4),
    }


__all__ = ["router"]
