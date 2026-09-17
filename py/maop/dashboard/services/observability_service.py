"""Observability, Audit & Tool Audit service layer.

Encapsulates business logic for three router modules:

  * ``observability.py`` — OTel/metrics/tracing status & recording
  * ``audit.py``         — unified audit event query + alert engine
  * ``tool_audit.py``    — tool invocation audit log

Extracted from the router layer so routers only do parameter parsing +
auth + service call + response formatting.

The service is intentionally framework-agnostic: it does not import
FastAPI and can be unit-tested or invoked without an HTTP context.
Shared runtime state (``MAOP_ROOT``, ``get_bridge``) is imported from
``maop.dashboard.routers.state`` so the service operates on the same
singletons the dashboard initialised.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time as _time
from pathlib import Path
from typing import Any

from maop.config.edition import FeatureFlag, get_edition, has_feature
from maop.core.observability import (
    observability_status,
    record_agent_execution,
    record_error,
    record_request,
    setup_observability,
    tracing_enabled,
)

# Shared runtime state — accessed via the ``state`` module so that tests
# which monkeypatch ``state.MAOP_ROOT`` take effect here too.
from maop.dashboard.routers import state

logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════════════════════
# Observability endpoints (observability.py)
# ════════════════════════════════════════════════════════════════════════════

def get_observability_status() -> Any:
    """Return the live observability stack status.

    Includes edition, tracing enabled flag, tracer type, metrics
    summary, and logging handler info.
    """
    return observability_status()


def get_observability_metrics() -> dict[str, Any]:
    """Return a JSON summary of the four canonical observability metrics."""
    from maop.core.observability.metrics import metrics_summary
    return {"status": "ok", "data": metrics_summary()}


def get_observability_prometheus() -> str:
    """Return all metrics in Prometheus text exposition format."""
    from maop.core.monitoring.monitoring import metrics as _global_metrics
    return _global_metrics.to_prometheus()


def get_observability_traces(limit: int) -> dict[str, Any]:
    """Return recent trace summary.

    When OTel is disabled (Personal mode), returns ``enabled=False`` so
    the frontend can show the lightweight-mode badge.
    """
    if not tracing_enabled():
        return {
            "status": "ok",
            "enabled": False,
            "edition": get_edition().value,
            "hint": "Tracing disabled. Set MAOP_OTEL_ENABLED=1 and install opentelemetry-sdk to enable.",
            "traces": [],
        }
    return {
        "status": "ok",
        "enabled": True,
        "edition": get_edition().value,
        "hint": "Traces are exported via OTLP to the Collector. Inspect them in Jaeger/Tempo.",
        "traces": [],
        "limit": limit,
    }


def record_observability_event(
    kind: str,
    method: str = "",
    path: str = "",
    status: int = 0,
    duration: float = 0.0,
    agent: str = "",
    phase: str = "",
    error_type: str = "",
    module: str = "",
) -> dict[str, Any]:
    """Record a custom metric / error event.

    Returns ``{"status": "ok", "kind": kind}`` on success.
    Raises ``ValueError`` for unknown ``kind``.
    """
    if kind == "request":
        record_request(method, path, status, duration)
    elif kind == "agent":
        record_agent_execution(agent, phase, duration)
    elif kind == "error":
        record_error(error_type, module)
    else:
        raise ValueError(f"unknown kind: {kind}")
    return {"status": "ok", "kind": kind}


def get_observability_health() -> dict[str, Any]:
    """Deep health check of the observability pipeline.

    Checks OTel SDK, TracerProvider, MeterProvider, and deploy/ configs.
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
        logger.debug("[observability] tracer provider check failed: %s", exc)
        checks["tracer_provider"] = {"ok": False, "error": "Tracer provider unavailable"}

    # MeterProvider
    try:
        from opentelemetry import metrics as otel_metrics
        meter_provider = otel_metrics.get_meter_provider()
        checks["meter_provider"] = {
            "ok": True,
            "type": type(meter_provider).__name__,
        }
    except Exception as exc:
        logger.debug("[observability] meter provider check failed: %s", exc)
        checks["meter_provider"] = {"ok": False, "error": "Meter provider unavailable"}

    # deploy/ configs
    otel_cfg = state.MAOP_ROOT / "deploy" / "otel-collector.yaml"
    grafana_cfg = state.MAOP_ROOT / "deploy" / "grafana" / "dashboards" / "maop-overview.json"
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


def get_observability_config() -> dict[str, Any]:
    """Return the observability configuration (env-driven)."""
    from maop.core.monitoring.otel import get_otel_endpoint
    return {
        "edition": get_edition().value,
        "otel_enabled": os.getenv("MAOP_OTEL_ENABLED", "").strip() in ("1", "true", "yes"),
        "otel_exporter": os.getenv("MAOP_OTEL_EXPORTER", "none"),
        "otel_endpoint": get_otel_endpoint(),
        "otel_service_name": os.getenv("MAOP_OTEL_SERVICE_NAME", "maop"),
        "prometheus_scrape_path": "/api/prometheus",
        "grafana_dashboard_uid": "maop-overview",
    }


def run_observability_setup(force: bool = False) -> dict[str, Any]:
    """Trigger (or re-trigger) observability setup."""
    summary = setup_observability(force=force)
    return {"status": "ok", "summary": summary}


# ════════════════════════════════════════════════════════════════════════════
# Audit endpoints (audit.py) — Personal edition helpers
# ════════════════════════════════════════════════════════════════════════════

def _get_personal_events(
    *,
    action: str = "",
    actor: str = "",
    target: str = "",
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Read audit events from personal edition AuditLog (logs/audit.jsonl)."""
    try:
        from maop.control.audit import AuditLog
        events = AuditLog(state.MAOP_ROOT / "logs" / "audit.jsonl").read_recent(limit=limit * 5)
        result = []
        for e in events:
            if action and e.action != action:
                continue
            if actor and e.actor != actor:
                continue
            if target and e.target != target:
                continue
            result.append(e.model_dump())
        return result[:limit]
    except Exception as exc:
        logger.error("Personal audit events failed: %s", exc)
        return []


def _get_personal_summary() -> dict[str, Any]:
    """Get audit summary from personal edition AuditLog."""
    try:
        from maop.control.audit import AuditLog
        log = AuditLog(state.MAOP_ROOT / "logs" / "audit.jsonl")
        events = log.read_recent(limit=500)
        by_action: dict[str, int] = {}
        by_actor: dict[str, int] = {}
        for e in events:
            by_action[e.action] = by_action.get(e.action, 0) + 1
            by_actor[e.actor] = by_actor.get(e.actor, 0) + 1
        return {"total": len(events), "by_action": by_action, "by_actor": by_actor}
    except Exception as exc:
        logger.error("Personal audit summary failed: %s", exc)
        return {"total": 0, "by_action": {}, "by_actor": {}}


# ════════════════════════════════════════════════════════════════════════════
# Audit endpoints (audit.py) — Enterprise edition helpers
# ════════════════════════════════════════════════════════════════════════════

_enterprise_logger: Any = None
_enterprise_logger_lock = threading.Lock()


def _get_enterprise_logger() -> Any:
    """Lazy-init the singleton EnterpriseAuditLogger (enterprise only)."""
    # P1-18: 双重检查锁定保护单例初始化
    global _enterprise_logger
    if _enterprise_logger is None:
        with _enterprise_logger_lock:
            if _enterprise_logger is None:
                from maop.enterprise.audit import EnterpriseAuditLogger
                _enterprise_logger = EnterpriseAuditLogger()
    return _enterprise_logger


def _iter_enterprise_events(mgr: Any) -> list[Any]:
    """安全获取企业审计事件列表。

    P0-1: 优先调用公开方法 ``iter_events()``（若可用），
    否则回退到 ``query()`` 公开 API，避免直接访问 ``_events`` 私有属性。
    """
    iter_fn = getattr(mgr, "iter_events", None)
    if callable(iter_fn):
        try:
            return list(iter_fn())
        except Exception as exc:
            logger.debug("iter_events() failed, falling back to query(): %s", exc)
    max_limit = getattr(mgr, "_max_events", 100000)
    return mgr.query(limit=max_limit)


def _filter_enterprise_events(
    mgr: Any,
    *,
    tenant_id: str = "",
    action: str = "",
    severity: str = "",
    hours: int = 24,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[Any], int]:
    """Filter enterprise audit events by tenant/action/severity/time."""
    since = _time.time() - hours * 3600
    all_events = _iter_enterprise_events(mgr)
    events = [e for e in all_events if e.timestamp >= since]
    if tenant_id:
        events = [e for e in events if e.tenant_id == tenant_id]
    if action:
        events = [e for e in events if e.action.value == action]
    if severity:
        events = [e for e in events if e.severity.value == severity]
    total = len(events)
    return events[offset: offset + limit], total


# ── Alert engine singleton + WebSocket broadcaster ────────────────

def _ws_broadcast_alert(alert: dict[str, Any]) -> Any:
    """Push an alert to all connected dashboard WebSocket clients.

    Looks up the running server's ``_ws_broadcast`` coroutine and schedules
    it on the event loop. Falls back to no-op when the server module is not
    importable (e.g. unit tests that only mount the router).
    """
    try:
        from maop.dashboard import server as _server
        broadcast = getattr(_server, "_ws_broadcast", None)
        if broadcast is None:
            return None
        # P0-2: asyncio.get_event_loop() 在 Python 3.12+ 已弃用。
        try:
            asyncio.get_running_loop()
            asyncio.ensure_future(broadcast({"type": "audit_alert", "alert": alert}))
        except RuntimeError:
            logger.debug("ws_broadcast_alert skipped: no running event loop")
    except Exception as exc:
        logger.debug("ws_broadcast_alert skipped: %s", exc)
    return None


_alert_engine: Any = None
_alert_engine_lock = threading.Lock()


def _get_alert_engine() -> Any:
    """Lazy-init the singleton AuditAlertEngine (enterprise only)."""
    # P1-18: 双重检查锁定保护单例初始化
    global _alert_engine
    if _alert_engine is None:
        with _alert_engine_lock:
            if _alert_engine is None:
                from maop.enterprise.audit_enhanced import AuditAlertEngine
                _alert_engine = AuditAlertEngine(broadcaster=_ws_broadcast_alert)
    return _alert_engine


def _reset_alert_engine_for_tests() -> None:
    """Reset the singleton — used by unit tests, not by production code."""
    global _alert_engine
    _alert_engine = None


def _require_audit_feature() -> None:
    """Raise 404 if the audit_log feature is not available.

    Raises ``fastapi.HTTPException`` — imported lazily so the service
    stays framework-agnostic for non-audit callers.
    """
    if not has_feature(FeatureFlag.AUDIT_LOG):
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Audit enhancement requires enterprise edition")


def _collect_enterprise_events(
    *,
    tenant_id: str = "",
    hours: int = 24,
    limit: int = 10000,
) -> list[Any]:
    """Return up to ``limit`` AuditEvent objects from the enterprise logger."""
    mgr = _get_enterprise_logger()
    since = _time.time() - hours * 3600
    all_events = _iter_enterprise_events(mgr)
    events = [e for e in all_events if e.timestamp >= since]
    if tenant_id:
        events = [e for e in events if e.tenant_id == tenant_id]
    return events[-limit:]


# ════════════════════════════════════════════════════════════════════════════
# Audit endpoints (audit.py) — Unified query operations
# ════════════════════════════════════════════════════════════════════════════

async def list_audit_events(
    tenant_id: str = "",
    action: str = "",
    severity: str = "",
    hours: int = 24,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    """List audit events with optional filters (unified for both editions)."""
    if has_feature(FeatureFlag.AUDIT_LOG):
        mgr = _get_enterprise_logger()
        events, total = _filter_enterprise_events(
            mgr,
            tenant_id=tenant_id,
            action=action,
            severity=severity,
            hours=hours,
            limit=limit,
            offset=offset,
        )
        return {
            "status": "ok",
            "events": [e.model_dump() for e in events],
            "count": len(events),
            "total": total,
            "limit": limit,
            "offset": offset,
        }
    else:
        events = _get_personal_events(action=action, limit=limit)
        return {
            "status": "ok",
            "events": events,
            "count": len(events),
            "total": len(events),
            "limit": limit,
            "offset": offset,
        }


async def get_audit_summary(tenant_id: str = "", hours: int = 24) -> dict[str, Any]:
    """Get audit event summary (unified for both editions)."""
    if has_feature(FeatureFlag.AUDIT_LOG):
        mgr = _get_enterprise_logger()
        summary = mgr.summary(tenant_id=tenant_id, hours=hours)
        return {"status": "ok", "summary": summary}
    else:
        return {"status": "ok", "summary": _get_personal_summary()}


async def filter_audit_events(
    action: str = "",
    actor: str = "",
    target: str = "",
    limit: int = 100,
) -> dict[str, Any]:
    """Filter audit events by action/actor/target (unified for both editions)."""
    if has_feature(FeatureFlag.AUDIT_LOG):
        mgr = _get_enterprise_logger()
        events, total = _filter_enterprise_events(
            mgr,
            action=action,
            hours=24,
            limit=limit,
        )
        return {
            "status": "ok",
            "events": [e.model_dump() for e in events],
            "count": len(events),
            "total": total,
        }
    else:
        events = _get_personal_events(action=action, actor=actor, target=target, limit=limit)
        return {
            "status": "ok",
            "events": events,
            "count": len(events),
            "total": len(events),
        }


# ════════════════════════════════════════════════════════════════════════════
# Audit endpoints (audit.py) — Enhancement endpoints (enterprise-only)
# ════════════════════════════════════════════════════════════════════════════

async def advanced_query_audit_events(body: Any) -> dict[str, Any]:
    """Advanced multi-field filtering with pagination and sort.

    ``body`` is an ``AuditEventQuery`` Pydantic model (validated by FastAPI).
    """
    _require_audit_feature()
    from maop.enterprise.audit_enhanced import filter_events as _filter

    query = body
    events = _collect_enterprise_events(
        tenant_id=query.tenant_id,
        hours=int(max(1, (_time.time() - query.since) // 3600)) if query.since else 24,
        limit=10000,
    )
    page, total = _filter(events, query)
    return {
        "status": "ok",
        "events": [e.model_dump(mode="json") for e in page],
        "count": len(page),
        "total": total,
        "limit": query.limit,
        "offset": query.offset,
    }


async def export_audit_events(
    format: str,
    tenant_id: str = "",
    hours: int = 24,
    limit: int = 5000,
) -> tuple[str, str, str]:
    """Export audit events as CSV or JSON.

    Returns ``(content, media_type, filename)``.
    """
    _require_audit_feature()
    from maop.enterprise.audit_enhanced import export_events_csv, export_events_json

    events = _collect_enterprise_events(tenant_id=tenant_id, hours=hours, limit=limit)
    if format == "json":
        return (
            export_events_json(events),
            "application/json",
            "audit_events.json",
        )
    return (
        export_events_csv(events),
        "text/csv; charset=utf-8",
        "audit_events.csv",
    )


async def get_audit_stats(tenant_id: str = "", hours: int = 24) -> dict[str, Any]:
    """Aggregate statistics: counts by action / severity / risk / category / actor."""
    _require_audit_feature()
    from maop.enterprise.audit_enhanced import compute_stats

    events = _collect_enterprise_events(tenant_id=tenant_id, hours=hours, limit=100000)
    stats = compute_stats(events, hours=hours)
    return {"status": "ok", "stats": stats.model_dump()}


async def get_audit_timeline(
    tenant_id: str = "",
    hours: int = 24,
    bucket_s: int = 3600,
) -> dict[str, Any]:
    """Bucketed time series for charting."""
    _require_audit_feature()
    from maop.enterprise.audit_enhanced import compute_timeline

    events = _collect_enterprise_events(tenant_id=tenant_id, hours=hours, limit=100000)
    now = _time.time()
    since = now - hours * 3600
    points = compute_timeline(events, bucket_s=bucket_s, since=since, until=now)
    return {
        "status": "ok",
        "timeline": [p.model_dump() for p in points],
        "bucket_s": bucket_s,
        "hours": hours,
    }


async def get_audit_heatmap(tenant_id: str = "", hours: int = 168) -> dict[str, Any]:
    """7×24 day×hour heatmap of event volume."""
    _require_audit_feature()
    from maop.enterprise.audit_enhanced import compute_heatmap

    events = _collect_enterprise_events(tenant_id=tenant_id, hours=hours, limit=100000)
    cells = compute_heatmap(events)
    return {
        "status": "ok",
        "heatmap": [c.model_dump() for c in cells],
        "hours": hours,
    }


# ════════════════════════════════════════════════════════════════════════════
# Audit endpoints (audit.py) — Alert rule CRUD
# ════════════════════════════════════════════════════════════════════════════

async def create_alert_rule(body: Any, actor: str = "") -> dict[str, Any]:
    """Create a new alert rule."""
    _require_audit_feature()
    engine = _get_alert_engine()
    rule = engine.create_rule(body, created_by=actor)
    return {"status": "ok", "rule": rule.model_dump(mode="json")}


async def list_alert_rules(tenant_id: str = "", enabled_only: bool = False) -> dict[str, Any]:
    """List alert rules (optionally filtered by tenant / enabled)."""
    _require_audit_feature()
    engine = _get_alert_engine()
    rules = engine.list_rules(tenant_id=tenant_id, enabled_only=enabled_only)
    return {
        "status": "ok",
        "rules": [r.model_dump(mode="json") for r in rules],
        "count": len(rules),
    }


async def get_alert_rule(rule_id: str) -> dict[str, Any] | None:
    """Get a single alert rule by ID.

    Returns ``None`` if the rule is not found.
    """
    _require_audit_feature()
    engine = _get_alert_engine()
    rule = engine.get_rule(rule_id)
    if rule is None:
        return None
    return {"status": "ok", "rule": rule.model_dump(mode="json")}


async def update_alert_rule(rule_id: str, body: Any) -> dict[str, Any] | None:
    """Update an existing alert rule (partial update).

    Returns ``None`` if the rule is not found.
    """
    _require_audit_feature()
    engine = _get_alert_engine()
    rule = engine.update_rule(rule_id, body)
    if rule is None:
        return None
    return {"status": "ok", "rule": rule.model_dump(mode="json")}


async def delete_alert_rule(rule_id: str) -> bool:
    """Delete an alert rule. Returns ``True`` if deleted, ``False`` if not found."""
    _require_audit_feature()
    engine = _get_alert_engine()
    return engine.delete_rule(rule_id)


# ════════════════════════════════════════════════════════════════════════════
# Audit endpoints (audit.py) — Alert history + acknowledgement
# ════════════════════════════════════════════════════════════════════════════

async def list_alert_history(
    rule_id: str = "",
    tenant_id: str = "",
    acknowledged: bool | None = None,
    since: float = 0.0,
    limit: int = 100,
) -> dict[str, Any]:
    """List triggered-alert history."""
    _require_audit_feature()
    engine = _get_alert_engine()
    alerts = engine.list_alerts(
        rule_id=rule_id,
        tenant_id=tenant_id,
        acknowledged=acknowledged,
        since=since,
        limit=limit,
    )
    return {
        "status": "ok",
        "alerts": [a.model_dump(mode="json") for a in alerts],
        "count": len(alerts),
    }


async def acknowledge_alert(alert_id: str, actor: str = "") -> dict[str, Any] | None:
    """Acknowledge a triggered alert.

    Returns ``None`` if the alert is not found.
    """
    _require_audit_feature()
    engine = _get_alert_engine()
    alert = engine.acknowledge_alert(alert_id, acknowledged_by=actor)
    if alert is None:
        return None
    return {"status": "ok", "alert": alert.model_dump(mode="json")}


async def evaluate_alerts(hours: int = 1, tenant_id: str = "") -> dict[str, Any]:
    """Manually evaluate recent audit events against all enabled rules.

    Returns the list of newly-triggered alerts.
    """
    _require_audit_feature()
    engine = _get_alert_engine()
    events = _collect_enterprise_events(tenant_id=tenant_id, hours=hours, limit=100000)
    triggered = engine.evaluate_events(events)
    return {
        "status": "ok",
        "triggered": [a.model_dump(mode="json") for a in triggered],
        "count": len(triggered),
        "evaluated_events": len(events),
    }


# ════════════════════════════════════════════════════════════════════════════
# Tool Audit endpoints (tool_audit.py)
# ════════════════════════════════════════════════════════════════════════════

_tool_audit: Any = None


def _get_tool_audit() -> Any:
    """Lazy-init the singleton ToolAuditLog."""
    global _tool_audit
    if _tool_audit is None:
        from maop.core.agent.tools.tool_audit import ToolAuditLog
        _tool_audit = ToolAuditLog(root_dir=str(state.MAOP_ROOT))
    return _tool_audit


async def get_tool_audit_entries(
    tool_name: str = "",
    agent: str = "",
    success: bool | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """Query tool audit log entries."""
    audit = _get_tool_audit()
    entries = audit.query(tool_name=tool_name, agent=agent, success=success, limit=limit)
    return {"status": "ok", "entries": [e.model_dump() for e in entries], "count": len(entries)}


async def get_tool_audit_stats() -> dict[str, Any]:
    """Get tool audit statistics."""
    audit = _get_tool_audit()
    stats = audit.stats()
    return {"status": "ok", "stats": stats.model_dump()}


async def cleanup_tool_audit(max_age_days: int = 90) -> dict[str, Any]:
    """Clean up old tool audit log entries."""
    audit = _get_tool_audit()
    removed = audit.cleanup(max_age_days=max_age_days)
    return {"status": "ok", "removed": removed}