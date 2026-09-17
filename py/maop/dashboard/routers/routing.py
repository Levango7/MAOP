"""MAOP Dashboard — Routing Decision Trace API endpoints.

Phase γ-4: exposes the :class:`~maop.core.routing_decision.RoutingDecisionStore`
via three read-only GET endpoints so operators can answer "why was agent X /
model Y picked for trace Z?" without needing a full OTel backend.

Endpoints
---------
- ``GET /api/routing/decisions/recent`` — recent decisions (newest-first),
  optionally filtered by ``stage``.
- ``GET /api/routing/decisions/{trace_id}`` — full decision chain for a
  trace, oldest-first (call order).
- ``GET /api/routing/decisions/stats`` — aggregate counts (total, by_stage,
  last_24h).

All endpoints require the ``admin`` role (via ``require_admin`` middleware).

Router 层只保留：路由定义、请求解析、权限检查、调用 service、响应
格式化。业务逻辑在 ``maop.dashboard.services.routing_service`` 中，
框架无关。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Query, Request

from maop.core.security.middleware import require_admin
from maop.dashboard.error_handler import handle_api_errors
from maop.dashboard.services import routing_service

logger = logging.getLogger(__name__)

router = APIRouter()


# ── Endpoints ─────────────────────────────────────────────────────
#
# WARNING: route order matters - static paths must come before parameterized paths.
# ``/stats`` and ``/recent`` must be declared before ``/{trace_id}`` so the
# path-parameter route doesn't shadow them (FastAPI matches routes in declaration order).


@router.get("/api/routing/decisions/recent")
@handle_api_errors(
    "Routing decisions recent",
    error_value={"decisions": [], "total": 0, "error": "Query failed"},
)
async def api_routing_decisions_recent(
    request: Request,
    limit: int = Query(100, ge=1, le=1000),
    stage: str = "",
) -> dict[str, Any]:
    """List recent routing decisions, newest-first.

    Parameters
    ----------
    limit : int
        Maximum number of decisions to return (default 100, capped at 1000).
    stage : str
        Optional stage filter (``route_scorer`` / ``load_balancer`` /
        ``model_selector`` / ``dispatcher``). Empty string = all stages.
    """
    require_admin(request)
    return {"status": "ok", **routing_service.query_recent_decisions(limit, stage)}


@router.get("/api/routing/decisions/stats")
@handle_api_errors(
    "Routing decisions stats",
    error_value={"total": 0, "by_stage": {}, "last_24h": 0, "error": "Stats failed"},
)
async def api_routing_decisions_stats(request: Request) -> dict[str, Any]:
    """Return aggregate decision counts.

    - ``total``: all-time decision count.
    - ``by_stage``: decision count per stage.
    - ``last_24h``: decisions recorded in the last 24 hours.
    """
    require_admin(request)
    return {"status": "ok", **routing_service.get_decision_stats()}


@router.get("/api/routing/decisions/{trace_id}")
@handle_api_errors(
    "Routing decisions by trace",
    error_value={"trace_id": "", "decisions": [], "stages": [], "error": "Query failed"},
)
async def api_routing_decisions_by_trace(request: Request, trace_id: str) -> dict[str, Any]:
    """Return the full decision chain for a trace, oldest-first.

    The chain is reconstructed by querying all decisions sharing the
    same ``trace_id`` and sorting by timestamp ascending. The
    ``stages`` field lists the stages in call order so the caller can
    see the Plan → Route → LB → ModelSelect sequence at a glance.
    """
    require_admin(request)
    return {"status": "ok", **routing_service.query_decisions_by_trace(trace_id)}
