"""MAOP Dashboard — M3 Blackboard Operations API.

Exposes the M3 blackboard modules via HTTP:

- ``POST /api/bb-m3/debate/run`` — start a debate round
- ``GET  /api/bb-m3/consensus/scan`` — scan for conflicts
- ``POST /api/bb-m3/convergence/start`` — start async convergence solve
- ``GET  /api/bb-m3/convergence/{solve_id}`` — get solve status
- ``GET  /api/bb-m3/health`` — health monitor snapshot
- ``GET  /api/bb-m3/probes`` — liveness/readiness probe status
- ``GET  /api/bb-m3/metrics`` — Prometheus-text metrics

POST endpoints require admin auth. GET endpoints are read-only.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from maop.core.security.middleware import require_admin
from maop.dashboard.error_handler import handle_api_errors
from maop.dashboard.services import bb_m3_ops_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/bb-m3", tags=["bb-m3"])


# ── Request models ─────────────────────────────────────────────────


class DebateRequest(BaseModel):
    """Request body for running a debate."""

    question_domain: str = Field("decision", description="Domain containing the question entry")
    question_entry_id: str | None = Field(None, description="Specific entry ID (latest if omitted)")
    participants: list[dict[str, Any]] | None = Field(
        None, description="List of {name, stance, confidence}"
    )
    consensus_threshold: float = Field(0.6, ge=0.0, le=1.0)
    quorum: int = Field(1, ge=1, le=10)


class ConvergenceStartRequest(BaseModel):
    """Request body for starting a convergence solve."""

    solve_id: str | None = Field(None, description="Optional solve ID (auto-generated if omitted)")
    max_iterations: int = Field(50, ge=1, le=10000)
    goal_domain: str = Field("decision", description="Domain to watch for goal state")
    goal_state: str = Field("finalized", description="Target state value in metadata")


# ── Debate ──────────────────────────────────────────────────────────


@router.post("/debate/run")
@handle_api_errors("bb-m3 debate run")
async def debate_run(body: DebateRequest, request: Request) -> dict[str, Any]:
    """Run a debate round on a blackboard entry (admin)."""
    require_admin(request)
    try:
        result = await bb_m3_ops_service.run_debate(
            question_domain=body.question_domain,
            question_entry_id=body.question_entry_id,
            participants=body.participants,
            consensus_threshold=body.consensus_threshold,
            quorum=body.quorum,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.error("[bb-m3] debate run failed: %s", exc)
        raise HTTPException(status_code=500, detail="Debate execution failed") from exc
    return {"status": "ok", "data": result}


# ── Consensus ───────────────────────────────────────────────────────


@router.get("/consensus/scan")
@handle_api_errors("bb-m3 consensus scan")
async def consensus_scan(
    domain: str | None = Query(None, description="Restrict scan to one domain"),
    deadlock_cycles: int = Query(3, ge=1, le=100),
) -> dict[str, Any]:
    """Scan the blackboard for cross-agent conflicts (read-only)."""
    return await bb_m3_ops_service.scan_conflicts(
        domain=domain,
        deadlock_cycles=deadlock_cycles,
    )


# ── Convergence ─────────────────────────────────────────────────────


@router.post("/convergence/start")
@handle_api_errors("bb-m3 convergence start")
async def convergence_start(
    body: ConvergenceStartRequest, request: Request
) -> dict[str, Any]:
    """Start an asynchronous convergence solve (admin).

    Returns immediately with a ``solve_id``. Poll ``GET /convergence/{solve_id}``
    to monitor progress.
    """
    require_admin(request)
    return await bb_m3_ops_service.start_convergence_solve(
        solve_id=body.solve_id,
        max_iterations=body.max_iterations,
        goal_domain=body.goal_domain,
        goal_state=body.goal_state,
    )


@router.get("/convergence/{solve_id}")
@handle_api_errors("bb-m3 convergence status")
async def convergence_status(solve_id: str) -> dict[str, Any]:
    """Get the status of a convergence solve (read-only)."""
    try:
        return await bb_m3_ops_service.get_convergence_status(solve_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Solve not found")


# ── Health & Metrics ────────────────────────────────────────────────


@router.get("/health")
@handle_api_errors("bb-m3 health")
async def health_snapshot() -> dict[str, Any]:
    """Return the full health monitor snapshot (read-only)."""
    return {"status": "ok", "data": bb_m3_ops_service.get_health_snapshot()}


@router.get("/probes")
@handle_api_errors("bb-m3 probes")
async def probe_status() -> dict[str, Any]:
    """Return liveness + readiness probe status (read-only)."""
    return {"status": "ok", "data": bb_m3_ops_service.get_probe_status()}


@router.get("/metrics", response_class=None)
@handle_api_errors("bb-m3 prometheus metrics")
async def prometheus_metrics() -> Any:
    """Return Prometheus-text-format metrics (read-only)."""
    from fastapi.responses import PlainTextResponse

    metrics_text = bb_m3_ops_service.get_prometheus_metrics()
    return PlainTextResponse(content=metrics_text, media_type="text/plain")
