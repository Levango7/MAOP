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

All endpoints are read-only (GET) and do not require admin auth, mirroring
the ``tool_audit`` router's GET endpoints.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter

from maop.dashboard.error_handler import handle_api_errors

logger = logging.getLogger(__name__)

router = APIRouter()

_decision_store: Any = None


def _get_store() -> Any:
    """Lazy-init the global :class:`RoutingDecisionStore` singleton."""
    global _decision_store
    if _decision_store is None:
        from maop.core.routing.routing_decision import RoutingDecisionStore
        _decision_store = RoutingDecisionStore()
    return _decision_store


# ── Endpoints ─────────────────────────────────────────────────────
#
# Route registration order matters: ``/stats`` and ``/recent`` must be
# declared before ``/{trace_id}`` so the path-parameter route doesn't
# shadow them (FastAPI matches routes in declaration order).


@router.get("/api/routing/decisions/recent")
@handle_api_errors(
    "Routing decisions recent",
    error_value={"decisions": [], "total": 0, "error": "Query failed"},
)
async def api_routing_decisions_recent(
    limit: int = 100,
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
    store = _get_store()
    capped_limit = max(1, min(int(limit), 1000))
    stage_filter = stage.strip() or None
    decisions = store.query_recent(limit=capped_limit, stage=stage_filter)
    total = store.count(stage=stage_filter)
    return {
        "decisions": [d.to_dict() for d in decisions],
        "count": len(decisions),
        "total": total,
        "limit": capped_limit,
        "stage": stage_filter or "",
    }


@router.get("/api/routing/decisions/stats")
@handle_api_errors(
    "Routing decisions stats",
    error_value={"total": 0, "by_stage": {}, "last_24h": 0, "error": "Stats failed"},
)
async def api_routing_decisions_stats() -> dict[str, Any]:
    """Return aggregate decision counts.

    - ``total``: all-time decision count.
    - ``by_stage``: decision count per stage.
    - ``last_24h``: decisions recorded in the last 24 hours.
    """
    store = _get_store()
    stats = store.stats()
    return {
        "total": stats.get("total", 0),
        "by_stage": stats.get("by_stage", {}),
        "last_24h": stats.get("last_24h", 0),
    }


@router.get("/api/routing/decisions/{trace_id}")
@handle_api_errors(
    "Routing decisions by trace",
    error_value={"trace_id": "", "decisions": [], "stages": [], "error": "Query failed"},
)
async def api_routing_decisions_by_trace(trace_id: str) -> dict[str, Any]:
    """Return the full decision chain for a trace, oldest-first.

    The chain is reconstructed by querying all decisions sharing the
    same ``trace_id`` and sorting by timestamp ascending. The
    ``stages`` field lists the stages in call order so the caller can
    see the Plan → Route → LB → ModelSelect sequence at a glance.
    """
    store = _get_store()
    decisions = store.query_by_trace(trace_id)
    stages = [d.stage for d in decisions]
    return {
        "trace_id": trace_id,
        "decisions": [d.to_dict() for d in decisions],
        "count": len(decisions),
        "stages": stages,
    }
