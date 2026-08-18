"""MAOP Dashboard — Cost Tracker API endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field

from maop.core.security.middleware import require_admin
from maop.dashboard.error_handler import handle_api_errors

router = APIRouter(prefix="/api/cost", tags=["cost"])


def _get_cost_tracker():
    # 使用进程级单例，保证读写的预算配置与 llm_provider 的 auto-record
    # 共享同一份限额/阈值状态（否则每次新建实例会导致配置丢失）。
    from maop.core.cost_tracker import get_cost_tracker
    return get_cost_tracker()


@router.get("/entries")
@handle_api_errors
async def get_cost_entries(
    session_id: str = Query("", description="Filter by session"),
    agent: str = Query("", description="Filter by agent"),
    model: str = Query("", description="Filter by model"),
    start_date: str = Query("", description="Start date (ISO)"),
    end_date: str = Query("", description="End date (ISO)"),
    limit: int = Query(100, ge=1, le=1000),
) -> dict[str, Any]:
    tracker = _get_cost_tracker()
    entries = tracker.get_entries(
        session_id=session_id or "",
        agent=agent or "",
        model=model or "",
        start_date=start_date or "",
        end_date=end_date or "",
        limit=limit,
    )
    return {"entries": [e.model_dump() for e in entries]}


@router.get("/summary")
@handle_api_errors
async def get_cost_summary(
    session_id: str = Query("", description="Filter by session"),
    agent: str = Query("", description="Filter by agent"),
    start_date: str = Query("", description="Start date (ISO)"),
    end_date: str = Query("", description="End date (ISO)"),
) -> dict[str, Any]:
    tracker = _get_cost_tracker()
    summary = tracker.summary(
        session_id=session_id or "",
        agent=agent or "",
        start_date=start_date or "",
        end_date=end_date or "",
    )
    return {"summary": summary.model_dump()}


@router.get("/budget")
@handle_api_errors
async def get_budget_status() -> dict[str, Any]:
    tracker = _get_cost_tracker()
    if hasattr(tracker, "budget_status_async"):
        status = await tracker.budget_status_async()
    else:
        status = tracker.budget_status()
    return {"budget": status.model_dump()}


class BudgetConfigRequest(BaseModel):
    """预算配置请求体（所有字段可选，缺省表示不修改）."""

    daily_limit_usd: float | None = Field(default=None, ge=0)
    monthly_limit_usd: float | None = Field(default=None, ge=0)
    alert_threshold: float | None = Field(default=None, ge=0, le=1)


@router.put("/budget")
@handle_api_errors
async def update_budget(body: BudgetConfigRequest, request: Request) -> dict[str, Any]:
    """更新成本预算限额与告警阈值（管理员）。

    未传入的字段保持不变；``0`` 表示无限额。
    """
    require_admin(request)
    tracker = _get_cost_tracker()
    status = tracker.set_budget(
        daily_limit_usd=body.daily_limit_usd,
        monthly_limit_usd=body.monthly_limit_usd,
        alert_threshold=body.alert_threshold,
    )
    return {"budget": status.model_dump()}


@router.get("/pricing")
@handle_api_errors
async def get_pricing() -> dict[str, Any]:
    tracker = _get_cost_tracker()
    return {"pricing": tracker.get_pricing()}


@router.put("/pricing/{model}")
@handle_api_errors
async def update_pricing(model: str, body: dict, request: Request) -> dict[str, Any]:
    require_admin(request)
    tracker = _get_cost_tracker()
    tracker.update_pricing(
        model=model,
        prompt_per_1m=body.get("prompt_per_1m", 0.0),
        completion_per_1m=body.get("completion_per_1m", 0.0),
    )
    return {"updated": model}


@router.post("/record")
@handle_api_errors
async def record_cost(body: dict, request: Request) -> dict[str, Any]:
    require_admin(request)
    tracker = _get_cost_tracker()
    if hasattr(tracker, "record_async"):
        entry = await tracker.record_async(
            session_id=body.get("session_id", ""),
            agent=body.get("agent", ""),
            model=body.get("model", ""),
            prompt_tokens=body.get("prompt_tokens", 0),
            completion_tokens=body.get("completion_tokens", 0),
            total_tokens=body.get("total_tokens", 0),
            latency_ms=body.get("latency_ms", 0),
            metadata=body.get("metadata"),
        )
    else:
        entry = tracker.record(
            session_id=body.get("session_id", ""),
            agent=body.get("agent", ""),
            model=body.get("model", ""),
            prompt_tokens=body.get("prompt_tokens", 0),
            completion_tokens=body.get("completion_tokens", 0),
            total_tokens=body.get("total_tokens", 0),
            latency_ms=body.get("latency_ms", 0),
            metadata=body.get("metadata"),
        )
    return {"entry": entry.model_dump()}
