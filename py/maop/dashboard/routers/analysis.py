"""MAOP Dashboard — Deep Data Analysis API Router.

Provides six analysis endpoints for the analytics report engine:

  * ``GET /api/analysis/agent-efficiency``       — per-Agent efficiency metrics
  * ``GET /api/analysis/task-trends``            — task success/failure/timeout time series
  * ``GET /api/analysis/resource-utilization``   — CPU / memory / DB pool / cache hit-rate time series
  * ``GET /api/analysis/cost-breakdown``         — cost decomposition by agent / model / tenant
  * ``GET /api/analysis/performance-bottlenecks``— slowest endpoints, most expensive agents, top memory consumers
  * ``GET /api/analysis/summary``                — KPI summary for a 7d / 30d / 90d window

All endpoints require admin role (``require_admin``) and are wrapped in
``@handle_api_errors`` for unified error handling. Response shape is
uniform: ``{"status": "ok", "data": ..., "meta": {...}}``.

Business logic lives in :mod:`maop.dashboard.services.data_service`; this
router only does request parsing, auth, service dispatch, and response
formatting.
"""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Query, Request

from maop.core.security.middleware import require_admin
from maop.dashboard.error_handler import handle_api_errors
from maop.dashboard.services import data_service

router = APIRouter(prefix="/api/analysis", tags=["analysis"])


# ── 1. Agent efficiency ────────────────────────────────────────────


@router.get("/agent-efficiency")
@handle_api_errors("analysis agent-efficiency")
async def agent_efficiency(
    request: Request,
    date_from: str = Query("", description="Start date (ISO 8601)"),
    date_to: str = Query("", description="End date (ISO 8601)"),
    agent_id: str = Query("", description="Filter by agent name"),
) -> dict[str, Any]:
    """Per-Agent efficiency analysis.

    Returns each agent's task completion rate, average execution time,
    token consumption, and cost efficiency (tasks per USD).
    """
    require_admin(request)
    return await data_service.analysis_agent_efficiency(
        date_from=date_from, date_to=date_to, agent_id=agent_id,
    )


# ── 2. Task trends ─────────────────────────────────────────────────


@router.get("/task-trends")
@handle_api_errors("analysis task-trends")
async def task_trends(
    request: Request,
    date_from: str = Query("", description="Start date (ISO 8601)"),
    date_to: str = Query("", description="End date (ISO 8601)"),
    granularity: Literal["hour", "day", "week"] = Query(
        "day", description="Bucket granularity: hour / day / week"
    ),
) -> dict[str, Any]:
    """Task success / failure / timeout time series."""
    require_admin(request)
    return await data_service.analysis_task_trends(
        date_from=date_from, date_to=date_to, granularity=granularity,
    )


# ── 3. Resource utilization ────────────────────────────────────────


@router.get("/resource-utilization")
@handle_api_errors("analysis resource-utilization")
async def resource_utilization(
    request: Request,
    date_from: str = Query("", description="Start date (ISO 8601)"),
    date_to: str = Query("", description="End date (ISO 8601)"),
) -> dict[str, Any]:
    """Resource utilization time series."""
    require_admin(request)
    return await data_service.analysis_resource_utilization(
        date_from=date_from, date_to=date_to,
    )


# ── 4. Cost breakdown ──────────────────────────────────────────────


@router.get("/cost-breakdown")
@handle_api_errors("analysis cost-breakdown")
async def cost_breakdown(
    request: Request,
    date_from: str = Query("", description="Start date (ISO 8601)"),
    date_to: str = Query("", description="End date (ISO 8601)"),
    group_by: Literal["agent", "model", "tenant"] = Query(
        "agent", description="Group costs by: agent / model / tenant"
    ),
) -> dict[str, Any]:
    """Cost decomposition by agent / model / tenant."""
    require_admin(request)
    return await data_service.analysis_cost_breakdown(
        date_from=date_from, date_to=date_to, group_by=group_by,
    )


# ── 5. Performance bottlenecks ─────────────────────────────────────


@router.get("/performance-bottlenecks")
@handle_api_errors("analysis performance-bottlenecks")
async def performance_bottlenecks(
    request: Request,
    date_from: str = Query("", description="Start date (ISO 8601)"),
    date_to: str = Query("", description="End date (ISO 8601)"),
    top_n: int = Query(10, ge=1, le=100, description="Number of top items per category"),
) -> dict[str, Any]:
    """Identify performance bottlenecks."""
    require_admin(request)
    return await data_service.analysis_performance_bottlenecks(
        date_from=date_from, date_to=date_to, top_n=top_n,
    )


# ── 6. Summary ─────────────────────────────────────────────────────


@router.get("/summary")
@handle_api_errors("analysis summary")
async def summary(
    request: Request,
    date_range: Literal["7d", "30d", "90d"] = Query(
        "7d", description="Time window: 7d / 30d / 90d"
    ),
) -> dict[str, Any]:
    """KPI summary for the requested window."""
    require_admin(request)
    return await data_service.analysis_summary(date_range=date_range)


__all__ = ["router"]
