"""Cost attribution endpoints（M-X3 M0 端点切片）.

- ``GET  /api/cost-attribution`` —— 三维聚合（start_date/end_date 直通
  ``attribution_summary`` 的窗口参数，ISO 日期串）；
- ``POST /api/cost-attribution/check-budget`` —— 预算越阈检查（**只查不落库**；
  告警入库走 ``core.cost_attribution.emit_budget_alerts``，由调度/CLI 触发）。

admin-only：成本数据含各 agent 的真实花费，沿库内 ``require_admin`` 惯例。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, Request

from maop.core.cost_attribution import attribution_summary, check_budget_thresholds
from maop.core.security.middleware import require_admin
from maop.dashboard.error_handler import handle_api_errors
from maop.dashboard.routers.cost_attribution_schemas import (
    CostAttributionEnvelope,
    CostBudgetCheckEnvelope,
)

router = APIRouter(prefix="/api/cost-attribution", tags=["cost-attribution"])


@router.get("", response_model=CostAttributionEnvelope)
@handle_api_errors(
    "Cost attribution",
    error_value={
        "status": "error",
        "error": "Cost attribution unavailable",
        "summary": None,
    },
)
async def api_cost_attribution(
    request: Request,
    start_date: str = Query("", description="ISO 起始日期（含），如 2026-10-01"),
    end_date: str = Query("", description="ISO 结束日期（含）"),
) -> dict[str, Any]:
    require_admin(request)
    summary = attribution_summary(start_date=start_date, end_date=end_date)
    return {"status": "ok", "summary": summary}


@router.post("/check-budget", response_model=CostBudgetCheckEnvelope)
@handle_api_errors(
    "Cost budget check",
    error_value={
        "status": "error",
        "error": "Cost budget check unavailable",
        "alerts": [],
    },
)
async def api_cost_budget_check(
    request: Request,
    start_date: str = Query(""),
    end_date: str = Query(""),
    total_budget_usd: float | None = Query(None, gt=0),
    per_agent_budget_usd: float | None = Query(None, gt=0),
    per_phase_budget_usd: float | None = Query(None, gt=0),
) -> dict[str, Any]:
    require_admin(request)
    summary = attribution_summary(start_date=start_date, end_date=end_date)
    alerts = check_budget_thresholds(
        summary,
        total_budget_usd=total_budget_usd,
        per_agent_budget_usd=per_agent_budget_usd,
        per_phase_budget_usd=per_phase_budget_usd,
    )
    return {"status": "ok", "alerts": alerts}
