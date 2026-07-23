"""MAOP Dashboard — Cost Tracker API endpoints."""

from __future__ import annotations
from typing import Any

from fastapi import APIRouter, Query, Request

from maop.dashboard.error_handler import handle_api_errors
from maop.core.middleware import require_admin

router = APIRouter(prefix="/api/cost", tags=["cost"])


def _get_cost_tracker():
    from maop.core.cost_tracker import CostTracker
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent.parent.parent
    return CostTracker(root_dir=str(root))


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
    status = tracker.budget_status()
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
