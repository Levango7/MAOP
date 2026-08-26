"""MAOP Dashboard — Budget Guard API endpoints."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Request

from maop.core.security.middleware import require_admin
from maop.dashboard.error_handler import handle_api_errors

from .state import MAOP_ROOT

logger = logging.getLogger(__name__)

router = APIRouter()

_budget_guard = None

def _get_budget_guard() -> Any:
    global _budget_guard
    if _budget_guard is None:
        from maop.core.budget_guard import BudgetGuard
        _budget_guard = BudgetGuard(root_dir=str(MAOP_ROOT))
    return _budget_guard


@router.get("/api/budget/status")
@handle_api_errors("Budget status", error_value={"status": "error", "error": "Status failed"})
async def api_budget_status() -> dict[str, Any]:
    """Return current budget usage status."""
    guard = _get_budget_guard()
    status = guard.get_status()
    return {"status": "ok", "budget": status.model_dump()}


@router.post("/api/budget/reset")
@handle_api_errors("Budget reset", error_value={"status": "error", "error": "Reset failed"})
async def api_budget_reset(request: Request) -> dict[str, Any]:
    """Reset budget counters to zero."""
    require_admin(request)
    guard = _get_budget_guard()
    guard.reset_daily()
    return {"status": "ok"}


@router.post("/api/budget/record")
@handle_api_errors("Budget record", error_value={"status": "error", "error": "Record failed"})
async def api_budget_record(request: Request) -> dict[str, Any]:
    """Record a budget usage entry."""
    require_admin(request)
    body = await request.json()
    prompt_tokens = body.get("prompt_tokens", 0)
    completion_tokens = body.get("completion_tokens", 0)
    cost_usd = body.get("cost_usd", 0.0)
    guard = _get_budget_guard()
    result = guard.record_usage(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        cost_usd=cost_usd,
    )
    return {"status": "ok", "budget": result.model_dump()}
