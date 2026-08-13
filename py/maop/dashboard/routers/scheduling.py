"""MAOP Dashboard — Scheduling API endpoints (F1-02 异常自适应调度).

Exposes the :class:`~maop.core.scheduling.failure_detector.FailurePatternDetector`
state so operators can answer "which agents are drained / recovering and
why?" from the dashboard.

Endpoints
---------
- ``GET /api/scheduling/failure-stats`` — per-agent health snapshot
  (failure rate, weight, status, window size, …) plus detector config.
- ``POST /api/scheduling/failure-stats/reset`` — clear recorded state
  for one agent (body: ``{"agent_id": "..."}``) or all agents (empty
  body). Admin-only.

All endpoints are read-/write-light and do not require admin auth for
GET (mirroring the routing decision-trace router's policy).
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Request

from maop.core.scheduling.failure_detector import (
    FailurePatternDetector,
    get_failure_detector,
)
from maop.dashboard.error_handler import handle_api_errors

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/scheduling", tags=["scheduling"])


def _detector() -> FailurePatternDetector:
    """Return the process-wide detector singleton.

    Imported lazily so the router module is import-safe even when the
    scheduling subsystem has not been initialised (e.g. personal edition
    fallback to in-process execution).
    """
    return get_failure_detector()


@router.get("/failure-stats")
@handle_api_errors(
    "Scheduling failure stats",
    error_value={"agents": [], "config": {}, "total_agents": 0, "error": "Query failed"},
)
async def api_scheduling_failure_stats() -> dict[str, Any]:
    """Return the per-agent failure-detector snapshot.

    Response shape::

        {
          "agents": [
            {"agent_id": "...", "failure_rate": 0.0, "avg_latency": 0.0,
             "timeout_rate": 0.0, "weight": 1.0, "status": "normal",
             "window_size": 0, "total_recorded": 0},
            ...
          ],
          "config": {"window_size": 50, "failure_rate_threshold": 0.3,
                     "timeout_threshold": 30.0,
                     "recovery_consecutive_successes": 5},
          "total_agents": 0
        }
    """
    return _detector().get_stats()


@router.post("/failure-stats/reset")
@handle_api_errors(
    "Scheduling failure stats reset",
    error_value={"ok": False, "error": "Reset failed"},
)
async def api_scheduling_failure_stats_reset(
    request: Request,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Clear failure-detector state.

    Body ``{"agent_id": "..."}`` resets only the named agent; an empty
    body (or ``agent_id`` omitted) resets all agents. Intended for
    operations — manual recovery without waiting for the grey-probe
    ladder.
    """
    # Lightweight admin guard — mirrors routing_preview's require_admin
    # pattern but degrades gracefully when auth is not configured.
    roles = getattr(getattr(request, "state", None), "auth_roles", None) or []
    if "admin" not in roles:
        from fastapi import HTTPException

        raise HTTPException(status_code=403, detail="admin role required")
    payload = body or {}
    agent_id = str(payload.get("agent_id", "") or "").strip() or None
    _detector().reset(agent_id)
    logger.info(
        "[scheduling-api] failure-detector reset (agent_id=%s, by=%s)",
        agent_id or "ALL",
        getattr(getattr(request, "state", None), "auth_identity", "unknown"),
    )
    return {"ok": True, "reset_agent": agent_id or "ALL"}


__all__ = ["router"]