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
    request: Request,
    session_id: str = Query("", description="Filter by session"),
    agent: str = Query("", description="Filter by agent"),
    model: str = Query("", description="Filter by model"),
    start_date: str = Query("", description="Start date (ISO)"),
    end_date: str = Query("", description="End date (ISO)"),
    limit: int = Query(100, ge=1, le=1000),
) -> dict[str, Any]:
    require_admin(request)
    tracker = _get_cost_tracker()
    entries = tracker.get_entries(
        session_id=session_id or "",
        agent=agent or "",
        model=model or "",
        start_date=start_date or "",
        end_date=end_date or "",
        limit=limit,
    )
    return {"status": "ok", "entries": [e.model_dump() for e in entries]}


@router.get("/summary")
@handle_api_errors
async def get_cost_summary(
    request: Request,
    session_id: str = Query("", description="Filter by session"),
    agent: str = Query("", description="Filter by agent"),
    start_date: str = Query("", description="Start date (ISO)"),
    end_date: str = Query("", description="End date (ISO)"),
) -> dict[str, Any]:
    require_admin(request)
    tracker = _get_cost_tracker()
    summary = tracker.summary(
        session_id=session_id or "",
        agent=agent or "",
        start_date=start_date or "",
        end_date=end_date or "",
    )
    return {"status": "ok", "summary": summary.model_dump()}


@router.get("/budget")
@handle_api_errors
async def get_budget_status(request: Request) -> dict[str, Any]:
    require_admin(request)
    tracker = _get_cost_tracker()
    if hasattr(tracker, "budget_status_async"):
        status = await tracker.budget_status_async()
    else:
        status = tracker.budget_status()
    return {"status": "ok", "budget": status.model_dump()}


class BudgetConfigRequest(BaseModel):
    """预算配置请求体（所有字段可选，缺省表示不修改）."""

    daily_limit_usd: float | None = Field(default=None, ge=0)
    monthly_limit_usd: float | None = Field(default=None, ge=0)
    alert_threshold: float | None = Field(default=None, ge=0, le=1)


class UpdatePricingRequest(BaseModel):
    """更新定价请求体."""

    prompt_per_1m: float = Field(default=0.0, ge=0)
    completion_per_1m: float = Field(default=0.0, ge=0)


class RecordCostRequest(BaseModel):
    """记录成本请求体."""

    session_id: str = ""
    agent: str = ""
    model: str = ""
    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)
    latency_ms: float = Field(default=0.0, ge=0)
    metadata: dict[str, Any] | None = None


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
    return {"status": "ok", "budget": status.model_dump()}


@router.get("/pricing")
@handle_api_errors
async def get_pricing(request: Request) -> dict[str, Any]:
    require_admin(request)
    tracker = _get_cost_tracker()
    return {"status": "ok", "pricing": tracker.get_pricing()}


@router.put("/pricing/{model}")
@handle_api_errors
async def update_pricing(model: str, body: UpdatePricingRequest, request: Request) -> dict[str, Any]:
    require_admin(request)
    tracker = _get_cost_tracker()
    tracker.update_pricing(
        model=model,
        prompt_per_1m=body.prompt_per_1m,
        completion_per_1m=body.completion_per_1m,
    )
    return {"status": "ok", "updated": model}


@router.post("/record")
@handle_api_errors
async def record_cost(body: RecordCostRequest, request: Request) -> dict[str, Any]:
    require_admin(request)
    tracker = _get_cost_tracker()
    if hasattr(tracker, "record_async"):
        entry = await tracker.record_async(
            session_id=body.session_id,
            agent=body.agent,
            model=body.model,
            prompt_tokens=body.prompt_tokens,
            completion_tokens=body.completion_tokens,
            total_tokens=body.total_tokens,
            latency_ms=body.latency_ms,
            metadata=body.metadata,
        )
    else:
        entry = tracker.record(
            session_id=body.session_id,
            agent=body.agent,
            model=body.model,
            prompt_tokens=body.prompt_tokens,
            completion_tokens=body.completion_tokens,
            total_tokens=body.total_tokens,
            latency_ms=body.latency_ms,
            metadata=body.metadata,
        )
    return {"status": "ok", "entry": entry.model_dump()}
