"""MAOP Dashboard — Cost Tracker API endpoints.

业务逻辑已提取至 ``maop.dashboard.services.budget_service``（§2）。
本 router 仅保留：路由定义 / 请求参数解析 / 权限检查 /
service 调用 / 响应格式化 / 错误处理。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field

from maop.core.security.middleware import require_admin
from maop.dashboard.error_handler import handle_api_errors
from maop.dashboard.services import budget_service

router = APIRouter(prefix="/api/cost", tags=["cost"])


# ── 单例转发（供测试注入，转发到 service 层）──────────────────────
def _get_cost_tracker() -> Any:
    return budget_service._get_cost_tracker()


def _set_cost_tracker(tracker: Any) -> None:
    budget_service._set_cost_tracker(tracker)


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
    entries = budget_service.get_cost_entries(
        session_id=session_id or "",
        agent=agent or "",
        model=model or "",
        start_date=start_date or "",
        end_date=end_date or "",
        limit=limit,
    )
    return {"status": "ok", "entries": entries}


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
    summary = budget_service.get_cost_summary(
        session_id=session_id or "",
        agent=agent or "",
        start_date=start_date or "",
        end_date=end_date or "",
    )
    return {"status": "ok", "summary": summary}


@router.get("/budget")
@handle_api_errors
async def get_budget_status(request: Request) -> dict[str, Any]:
    require_admin(request)
    budget = await budget_service.get_cost_budget_status()
    return {"status": "ok", "budget": budget}


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
    budget = budget_service.update_cost_budget(
        daily_limit_usd=body.daily_limit_usd,
        monthly_limit_usd=body.monthly_limit_usd,
        alert_threshold=body.alert_threshold,
    )
    return {"status": "ok", "budget": budget}


@router.get("/pricing")
@handle_api_errors
async def get_pricing(request: Request) -> dict[str, Any]:
    require_admin(request)
    pricing = budget_service.get_pricing()
    return {"status": "ok", "pricing": pricing}


@router.put("/pricing/{model}")
@handle_api_errors
async def update_pricing(model: str, body: UpdatePricingRequest, request: Request) -> dict[str, Any]:
    require_admin(request)
    budget_service.update_pricing(
        model=model,
        prompt_per_1m=body.prompt_per_1m,
        completion_per_1m=body.completion_per_1m,
    )
    return {"status": "ok", "updated": model}


@router.post("/record")
@handle_api_errors
async def record_cost(body: RecordCostRequest, request: Request) -> dict[str, Any]:
    require_admin(request)
    entry = await budget_service.record_cost(
        session_id=body.session_id,
        agent=body.agent,
        model=body.model,
        prompt_tokens=body.prompt_tokens,
        completion_tokens=body.completion_tokens,
        total_tokens=body.total_tokens,
        latency_ms=body.latency_ms,
        metadata=body.metadata,
    )
    return {"status": "ok", "entry": entry}
