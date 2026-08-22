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

All GET endpoints are read-only and do not require admin auth (mirroring
the scheduling router's policy). POST endpoints require the ``admin`` role.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from maop.core.scheduling.failure_detector import get_supervisor
from maop.core.scheduling.supervisor import (
    AlertLevel,
    Supervisor,
    SupervisorActionRequest,
    SupervisorRule,
)
from maop.dashboard.error_handler import handle_api_errors

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/supervisor", tags=["supervisor"])


def _get_supervisor_or_404() -> Supervisor:
    """Return the process-wide Supervisor singleton or raise 404.

    When no Supervisor has been configured (passive-only mode), the
    endpoints return 404 so the dashboard can show a "supervisor not
    enabled" message rather than a confusing 500.
    """
    sup = get_supervisor()
    if sup is None or not isinstance(sup, Supervisor):
        raise HTTPException(
            status_code=404,
            detail="Supervisor not configured (passive-only mode). "
                   "Instantiate a Supervisor and call set_supervisor() to enable.",
        )
    return sup


def _require_admin(request: Request) -> None:
    """Lightweight admin guard — mirrors scheduling router's pattern."""
    roles = getattr(getattr(request, "state", None), "auth_roles", None) or []
    if "admin" not in roles:
        raise HTTPException(status_code=403, detail="admin role required")


# ── Status ─────────────────────────────────────────────────────


@router.get("/status")
@handle_api_errors(
    "Supervisor status",
    error_value={"agents": [], "patrol": {}, "pending_alerts": [],
                 "recent_actions": [], "config": {}, "rules": [],
                 "error": "Supervisor unavailable"},
)
async def api_supervisor_status() -> dict[str, Any]:
    """Return the full supervisor status snapshot."""
    sup = _get_supervisor_or_404()
    return sup.get_supervisor_status()


# ── Rules ──────────────────────────────────────────────────────


@router.get("/rules")
@handle_api_errors(
    "Supervisor rules",
    error_value={"rules": [], "error": "Query failed"},
)
async def api_supervisor_rules_list() -> dict[str, Any]:
    """Return the current supervision rule set."""
    sup = _get_supervisor_or_404()
    return {"rules": [r.model_dump() for r in sup.rules]}


@router.post("/rules")
@handle_api_errors(
    "Supervisor rules update",
    error_value={"ok": False, "error": "Update failed"},
)
async def api_supervisor_rule_update(
    request: Request,
    body: list[dict[str, Any]],
) -> dict[str, Any]:
    """Hot-update the supervision rule set (admin only).

    Body is a list of rule dicts; each is validated into
    :class:`SupervisorRule`. Invalid rules abort the update without
    partially applying.
    """
    _require_admin(request)
    sup = _get_supervisor_or_404()
    try:
        new_rules = [SupervisorRule(**r) for r in body]
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid rule schema: {exc}",
        ) from exc
    sup.set_rules(new_rules)
    logger.info(
        "[supervisor-api] rule set updated (%d rules, by=%s)",
        len(new_rules),
        getattr(getattr(request, "state", None), "auth_identity", "unknown"),
    )
    return {"ok": True, "rule_count": len(new_rules)}


# ── Actions ────────────────────────────────────────────────────


@router.get("/actions")
@handle_api_errors(
    "Supervisor actions",
    error_value={"actions": [], "error": "Query failed"},
)
async def api_supervisor_actions(
    agent_id: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """Return control action history (optionally filtered by agent)."""
    sup = _get_supervisor_or_404()
    actions = sup.get_actions(agent_id=agent_id, limit=limit)
    return {"actions": [a.model_dump() for a in actions]}


@router.post("/action")
@handle_api_errors(
    "Supervisor manual action",
    error_value={"ok": False, "error": "Action failed"},
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
    _require_admin(request)
    sup = _get_supervisor_or_404()
    action_str = body.action.strip().lower()
    params = body.params or {}
    reason = body.reason or f"manual {action_str} via API"
    triggered_by = "manual"

    try:
        if action_str == "alert":
            level = AlertLevel(params.get("level", "warning"))
            await sup.warn(
                body.agent_id, reason=reason, level=level,
                extra=params.get("extra"),
            )
            return {"ok": True, "action": "alert", "agent_id": body.agent_id}
        if action_str == "replace":
            replacement = params.get("replacement")
            if not replacement:
                raise HTTPException(
                    status_code=400,
                    detail="replace requires params.replacement",
                )
            record = await sup.replace(
                body.agent_id, str(replacement), reason=reason,
                routing_key=str(params.get("routing_key", "")),
                triggered_by=triggered_by,
            )
            return {"ok": True, "action": record.action.value,
                    "action_id": record.action_id}
        if action_str == "degrade":
            factor = float(params.get("factor", 0.5))
            record = await sup.degrade(
                body.agent_id, factor=factor, reason=reason,
                max_concurrency=params.get("max_concurrency"),
                timeout_s=params.get("timeout_s"),
                triggered_by=triggered_by,
            )
            return {"ok": True, "action": record.action.value,
                    "action_id": record.action_id}
        if action_str == "terminate":
            force = bool(params.get("force", False))
            record = await sup.terminate(
                body.agent_id, reason=reason,
                triggered_by=triggered_by, force=force,
            )
            return {"ok": True, "action": record.action.value,
                    "action_id": record.action_id}
        if action_str == "upgrade":
            target_version = params.get("target_version")
            if not target_version:
                raise HTTPException(
                    status_code=400,
                    detail="upgrade requires params.target_version",
                )
            record = await sup.upgrade(
                body.agent_id, str(target_version), reason=reason,
                triggered_by=triggered_by,
            )
            return {"ok": True, "action": record.action.value,
                    "action_id": record.action_id}
        raise HTTPException(
            status_code=400,
            detail=f"unknown action {action_str!r}; expected one of "
                   f"alert/replace/degrade/terminate/upgrade",
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(
            "[supervisor-api] manual action %s on %s failed",
            action_str, body.agent_id,
        )
        raise HTTPException(
            status_code=500,
            detail=f"action execution failed: {exc}",
        ) from exc


# ── Patrol ─────────────────────────────────────────────────────


@router.post("/patrol")
@handle_api_errors(
    "Supervisor manual patrol",
    error_value={"ok": False, "error": "Patrol failed"},
)
async def api_supervisor_patrol(
    request: Request,
) -> dict[str, Any]:
    """Manually trigger one patrol round (admin only).

    Returns the probes collected this round and the count of issues
    found (rules matched + unreachable strikes).
    """
    _require_admin(request)
    sup = _get_supervisor_or_404()
    probes = await sup.patrol()
    return {
        "ok": True,
        "agents_checked": len(probes),
        "probes": [p.model_dump() for p in probes],
        "patrol_duration_s": sup._last_patrol_duration_s,
    }


__all__ = ["router"]