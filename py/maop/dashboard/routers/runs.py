"""MAOP Dashboard — Run review endpoints (M1).

Reads the append-only ``run_events`` ledger written by ``MaopLoop`` so a
submitted task can be reviewed after the process is gone.

Endpoints
---------
- ``GET /api/runs`` — run summaries, newest first (``limit``).
- ``GET /api/runs/{trace_id}`` — one run's timeline plus the routing
  decisions that chose its agent.

Both require the ``admin`` role; the personal-edition loopback owner mode
satisfies that without a login. Business logic lives in
``maop.dashboard.services.run_service``.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from maop.core.security.middleware import require_admin
from maop.dashboard.error_handler import handle_api_errors
from maop.dashboard.services import run_service

router = APIRouter()


@router.get("/api/runs")
@handle_api_errors("runs list")
async def runs_list(request: Request, limit: int = Query(default=20, ge=1, le=200)) -> dict[str, Any]:
    """List recent runs."""
    require_admin(request)
    data = run_service.list_runs(limit=limit)
    return {"status": "ok", "data": data}


@router.get("/api/runs/{trace_id}")
@handle_api_errors("run detail")
async def run_detail(trace_id: str, request: Request) -> dict[str, Any]:
    """Return one run's timeline; 404 when no events were recorded."""
    require_admin(request)
    detail = run_service.get_run_detail(trace_id)
    if detail is None:
        raise HTTPException(404, "run not found")
    return {"status": "ok", "data": detail}
