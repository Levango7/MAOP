"""MAOP Dashboard — Budget Guard API endpoints.

业务逻辑已提取至 ``maop.dashboard.services.budget_service``（§1）。
本 router 仅保留：路由定义 / 请求参数解析 / 权限检查 /
service 调用 / 响应格式化 / 错误处理。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from maop.core.security.middleware import require_admin
from maop.dashboard.error_handler import handle_api_errors
from maop.dashboard.services import budget_service

logger = logging.getLogger(__name__)

router = APIRouter()


class BudgetRecordRequest(BaseModel):
    """POST /api/budget/record 请求体。"""
    # M-3 fix: 添加值域约束，防止负数 token/cost。
    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    cost_usd: float = Field(default=0.0, ge=0.0)


# ── 单例转发（供测试注入，转发到 service 层）──────────────────────
def _get_budget_guard() -> Any:
    return budget_service._get_budget_guard()


def _set_budget_guard(guard: Any) -> None:
    budget_service._set_budget_guard(guard)


@router.get("/api/budget/status")
@handle_api_errors("Budget status", error_value={"status": "error", "error": "Status failed"})
async def api_budget_status(request: Request) -> dict[str, Any]:
    """Return current budget usage status."""
    require_admin(request)
    result = budget_service.get_budget_status()
    return {"status": "ok", **result}


@router.post("/api/budget/reset")
@handle_api_errors("Budget reset", error_value={"status": "error", "error": "Reset failed"})
async def api_budget_reset(request: Request) -> dict[str, Any]:
    """Reset budget counters to zero."""
    require_admin(request)
    budget_service.reset_budget()
    return {"status": "ok"}


@router.post("/api/budget/record")
@handle_api_errors("Budget record", error_value={"status": "error", "error": "Record failed"})
async def api_budget_record(request: Request, body: BudgetRecordRequest) -> dict[str, Any]:
    """Record a budget usage entry."""
    require_admin(request)
    result = budget_service.record_budget_usage(
        prompt_tokens=body.prompt_tokens,
        completion_tokens=body.completion_tokens,
        cost_usd=body.cost_usd,
    )
    return {"status": "ok", **result}
