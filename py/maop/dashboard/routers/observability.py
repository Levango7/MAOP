"""MAOP Dashboard — Observability API Router.

Exposes the F1-04 observability stack to the frontend:

  * ``GET  /api/observability/status``    — tracing/metrics/logging status
  * ``GET  /api/observability/metrics``   — JSON metric summary
  * ``GET  /api/observability/traces``    — recent trace summary (when OTel active)
  * ``POST /api/observability/record``    — record a custom metric / error
  * ``GET  /api/observability/health``    — deep health check of the OTel pipeline

The router is edition-aware: in Personal mode the OTel-specific
endpoints return ``enabled=False`` but still respond (so the frontend
panel can render a "lightweight mode" badge instead of 404).

Business logic lives in :mod:`maop.dashboard.services.observability_service`;
this router only does request parsing, auth, service dispatch, and
response formatting.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field

from maop.core.security.middleware import require_admin
from maop.dashboard.error_handler import handle_api_errors
from maop.dashboard.services import observability_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/observability", tags=["observability"])


# ── Request models ─────────────────────────────────────────────────
class RecordRequestModel(BaseModel):
    """Payload for POST /api/observability/record."""
    kind: str = Field(description="Metric kind: request | agent | error")
    method: str = ""
    path: str = ""
    status: int = 0
    duration: float = 0.0
    agent: str = ""
    phase: str = ""
    error_type: str = ""
    module: str = ""


# ── Endpoints ──────────────────────────────────────────────────────
@router.get("/status")
@handle_api_errors("observability status")
async def status(request: Request) -> Any:
    """Return the live observability stack status."""
    require_admin(request)
    try:
        return observability_service.get_observability_status()
    except Exception as exc:
        # 批次3A: 脱敏——错误细节不暴露给客户端，仅日志记录。
        logger.exception("[observability] status failed: %s", exc)  # noqa: TRY401
        return JSONResponse(
            status_code=500,
            content={"status": "error", "error": "Observability status error"},
        )


@router.get("/metrics")
@handle_api_errors("observability metrics")
async def metrics(request: Request) -> Any:
    """Return a JSON summary of the four canonical observability metrics."""
    require_admin(request)
    return observability_service.get_observability_metrics()


@router.get("/metrics/prometheus")
@handle_api_errors("observability prometheus metrics")
async def metrics_prometheus(request: Request) -> Any:
    """Return all metrics in Prometheus text exposition format."""
    # P1 fix: metrics 端点需要 admin 鉴权，防止指标信息泄露。
    require_admin(request)
    text = observability_service.get_observability_prometheus()
    return PlainTextResponse(
        content=text,
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )


@router.get("/traces")
@handle_api_errors("observability traces")
async def traces(request: Request, limit: int = Query(20, ge=1, le=1000)) -> Any:
    """Return recent trace summary."""
    require_admin(request)
    return observability_service.get_observability_traces(limit=limit)


@router.post("/record")
@handle_api_errors("observability record")
async def record(payload: RecordRequestModel, request: Request) -> Any:
    """Record a custom metric / error event."""
    require_admin(request)
    try:
        return observability_service.record_observability_event(
            kind=payload.kind,
            method=payload.method,
            path=payload.path,
            status=payload.status,
            duration=payload.duration,
            agent=payload.agent,
            phase=payload.phase,
            error_type=payload.error_type,
            module=payload.module,
        )
    except ValueError:
        return JSONResponse(
            status_code=400,
            content={"status": "error", "error": f"unknown kind: {payload.kind}"},
        )
    except Exception as exc:
        # 批次3A: 脱敏——错误细节不暴露给客户端，仅日志记录。
        logger.exception("[observability] record failed: %s", exc)  # noqa: TRY401
        return JSONResponse(
            status_code=500,
            content={"status": "error", "error": "Observability record failed"},
        )


@router.get("/health")
@handle_api_errors("observability health")
async def health(request: Request) -> Any:
    """Deep health check of the observability pipeline."""
    # P1 fix: health 端点需要 admin 鉴权，防止健康检查细节泄露。
    require_admin(request)
    return observability_service.get_observability_health()


@router.get("/config")
@handle_api_errors("observability config")
async def config(request: Request) -> Any:
    """Return the observability configuration (env-driven)."""
    require_admin(request)
    return observability_service.get_observability_config()


@router.post("/setup")
@handle_api_errors("observability setup")
async def setup(request: Request, force: bool = False) -> Any:
    """Trigger (or re-trigger) observability setup."""
    require_admin(request)
    return observability_service.run_observability_setup(force=force)


__all__ = ["router"]
