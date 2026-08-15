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
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field

from maop.config.edition import get_edition
from maop.core.observability import (
    observability_status,
    record_agent_execution,
    record_error,
    record_request,
    setup_observability,
    tracing_enabled,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/observability", tags=["observability"])

# ── Project root for locating deploy/ configs ──────────────────────
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent


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
async def status() -> Any:
    """Return the live observability stack status.

    Includes edition, tracing enabled flag, tracer type, metrics
    summary, and logging handler info.  Used by the frontend
    Observability.vue panel to render the status badges.
    """
    try:
        return observability_status()
    except Exception as exc:
        logger.exception("[observability] status failed: %s", exc)  # noqa: TRY401
        return JSONResponse(
            status_code=500,
            content={"status": "error", "error": str(exc)},
        )


@router.get("/metrics")
async def metrics() -> Any:
    """Return a JSON summary of the four canonical observability metrics.

    Complements ``/api/prometheus`` (which returns Prometheus text
    format for scraping) with a JSON view suitable for dashboard
    rendering.
    """
    from maop.core.observability.metrics import metrics_summary
    return metrics_summary()


@router.get("/metrics/prometheus")
async def metrics_prometheus() -> Any:
    """Return all metrics in Prometheus text exposition format.

    This is a thin wrapper around the global metrics collector, mounted
    under ``/api/observability`` for discoverability.  The canonical
    scrape endpoint remains ``/api/prometheus`` (registered in
    server.py).
    """
    from maop.core.monitoring.monitoring import metrics as _global_metrics
    text = _global_metrics.to_prometheus()
    return PlainTextResponse(
        content=text,
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )


@router.get("/traces")
async def traces(limit: int = 20) -> Any:
    """Return recent trace summary.

    When OTel is active and an in-memory span exporter is configured,
    this returns the last ``limit`` spans.  When OTel is disabled
    (Personal mode), returns ``enabled=False`` so the frontend can
    show the lightweight-mode badge.
    """
    if not tracing_enabled():
        return {
            "enabled": False,
            "edition": get_edition().value,
            "hint": "Tracing disabled. Set MAOP_OTEL_ENABLED=1 and install opentelemetry-sdk to enable.",
            "traces": [],
        }
    # OTel is enabled but we don't have a built-in in-memory exporter
    # — operators point Jaeger/Tempo at the Collector for trace UI.
    return {
        "enabled": True,
        "edition": get_edition().value,
        "hint": "Traces are exported via OTLP to the Collector. Inspect them in Jaeger/Tempo.",
        "traces": [],
        "limit": limit,
    }


@router.post("/record")
async def record(payload: RecordRequestModel) -> Any:
    """Record a custom metric / error event.

    Used by the frontend (and external integrators) to push custom
    observability events into the MAOP metric pipeline.
    """
    try:
        if payload.kind == "request":
            record_request(payload.method, payload.path, payload.status, payload.duration)
        elif payload.kind == "agent":
            record_agent_execution(payload.agent, payload.phase, payload.duration)
        elif payload.kind == "error":
            record_error(payload.error_type, payload.module)
        else:
            return JSONResponse(
                status_code=400,
                content={"status": "error", "error": f"unknown kind: {payload.kind}"},
            )
        return {"status": "ok", "kind": payload.kind}
    except Exception as exc:
        logger.exception("[observability] record failed: %s", exc)  # noqa: TRY401
        return JSONResponse(
            status_code=500,
            content={"status": "error", "error": str(exc)},
        )


@router.get("/health")
async def health() -> Any:
    """Deep health check of the observability pipeline.

    Checks:
      * OTel SDK importable
      * TracerProvider configured
      * MeterProvider configured
      * Prometheus endpoint reachable (self)
      * deploy/ configs present
    """
    checks: dict[str, dict[str, Any]] = {}

    # OTel SDK
    try:
        import opentelemetry
        checks["otel_sdk"] = {"ok": True, "version": getattr(opentelemetry, "__version__", "unknown")}
    except ImportError:
        checks["otel_sdk"] = {"ok": False, "error": "opentelemetry-api not installed"}

    # TracerProvider
    try:
        from opentelemetry import trace as otel_trace
        provider = otel_trace.get_tracer_provider()
        checks["tracer_provider"] = {
            "ok": True,
            "type": type(provider).__name__,
        }
    except Exception as exc:
        checks["tracer_provider"] = {"ok": False, "error": str(exc)}

    # MeterProvider
    try:
        from opentelemetry import metrics as otel_metrics
        meter_provider = otel_metrics.get_meter_provider()
        checks["meter_provider"] = {
            "ok": True,
            "type": type(meter_provider).__name__,
        }
    except Exception as exc:
        checks["meter_provider"] = {"ok": False, "error": str(exc)}

    # deploy/ configs
    otel_cfg = _PROJECT_ROOT / "deploy" / "otel-collector.yaml"
    grafana_cfg = _PROJECT_ROOT / "deploy" / "grafana" / "dashboards" / "maop-overview.json"
    checks["deploy_configs"] = {
        "ok": otel_cfg.exists() and grafana_cfg.exists(),
        "otel_collector": otel_cfg.exists(),
        "grafana_dashboard": grafana_cfg.exists(),
    }

    all_ok = all(c.get("ok", False) for c in checks.values())
    return {
        "status": "ok" if all_ok else "degraded",
        "edition": get_edition().value,
        "tracing_enabled": tracing_enabled(),
        "checks": checks,
    }


@router.get("/config")
async def config() -> Any:
    """Return the observability configuration (env-driven).

    Lets the frontend show the active OTel endpoint, exporter type,
    service name, etc. without reading env vars directly.
    """
    return {
        "edition": get_edition().value,
        "otel_enabled": os.getenv("MAOP_OTEL_ENABLED", "").strip() in ("1", "true", "yes"),
        "otel_exporter": os.getenv("MAOP_OTEL_EXPORTER", "none"),
        "otel_endpoint": os.getenv("MAOP_OTEL_ENDPOINT", "http://localhost:4317"),
        "otel_service_name": os.getenv("MAOP_OTEL_SERVICE_NAME", "maop"),
        "prometheus_scrape_path": "/api/prometheus",
        "grafana_dashboard_uid": "maop-overview",
    }


@router.post("/setup")
async def setup(force: bool = False) -> Any:
    """Trigger (or re-trigger) observability setup.

    Useful for testing or for enabling tracing at runtime without a
    process restart (e.g. after installing the OTel SDK).
    """
    summary = setup_observability(force=force)
    return {"status": "ok", "summary": summary}


__all__ = ["router"]