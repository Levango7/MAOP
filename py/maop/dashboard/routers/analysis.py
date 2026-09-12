"""MAOP Dashboard — Deep Data Analysis API Router.

Provides six analysis endpoints for the analytics report engine:

  * ``GET /api/analysis/agent-efficiency``       — per-Agent efficiency metrics
  * ``GET /api/analysis/task-trends``            — task success/failure/timeout time series
  * ``GET /api/analysis/resource-utilization``   — CPU / memory / DB pool / cache hit-rate time series
  * ``GET /api/analysis/cost-breakdown``         — cost decomposition by agent / model / tenant
  * ``GET /api/analysis/performance-bottlenecks``— slowest endpoints, most expensive agents, top memory consumers
  * ``GET /api/analysis/summary``                — KPI summary for a 7d / 30d / 90d window

All endpoints require admin role (``require_admin``) and are wrapped in
``@handle_api_errors`` for unified error handling. Response shape is
uniform: ``{"status": "ok", "data": ..., "meta": {...}}``.

Data sources are reused from existing modules — no new persistence is
introduced:

  * ``maop.core.cost_tracker.get_cost_tracker`` — token / cost entries & summary
  * ``maop.core.monitoring.timeseries.TimeSeriesStore`` — metric time series
  * ``maop.core.monitoring.monitoring.metrics`` — Prometheus-style metric collector
  * ``maop.core.observability.metrics.metrics_summary`` — observability JSON snapshot
  * ``maop.control.audit.AuditLog`` — audit events (success / failure / timeout)
  * ``maop.dashboard.routers.state.get_bridge`` — agent stats (delegations + circuit breaker)
  * ``psutil`` (optional) — live CPU / memory utilization

When a data source is unavailable (personal edition, missing dep, empty
DB), endpoints return empty lists with an explanatory ``note`` in
``meta`` rather than raising — so the frontend can always render a
skeleton.
"""
from __future__ import annotations

import logging
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field

from maop.core.security.middleware import require_admin
from maop.dashboard.error_handler import handle_api_errors

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/analysis", tags=["analysis"])

# ── Project root (for locating logs/audit.jsonl) ───────────────────
try:
    from maop.dashboard.routers.state import MAOP_ROOT as _PROJECT_ROOT
except ImportError:  # pragma: no cover — state 模块不可用时回退
    _PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent


# ── Pydantic response models (for documentation & openapi) ─────────


class AnalysisMeta(BaseModel):
    """Common meta block returned by every analysis endpoint."""

    date_from: str = ""
    date_to: str = ""
    generated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    note: str = ""


# ── Helpers ────────────────────────────────────────────────────────


def _get_cost_tracker() -> Any:
    """Lazy accessor for the process-wide CostTracker singleton."""
    from maop.core.cost_tracker import get_cost_tracker
    return get_cost_tracker()


def _parse_date_range(date_from: str, date_to: str) -> tuple[float, float, str, str]:
    """Parse ISO date strings to (start_ts, end_ts, iso_from, iso_to).

    Falls back to "last 24h" when either side is blank. Raises ValueError
    on malformed input — callers wrap in try/except to emit a 400.
    """
    now = datetime.now(timezone.utc)
    if date_from and date_to:
        try:
            start_dt = datetime.fromisoformat(date_from.replace("Z", "+00:00"))
            end_dt = datetime.fromisoformat(date_to.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"Invalid date format: {exc}") from exc
    else:
        end_dt = now
        start_dt = now - timedelta(days=1)
    if start_dt > end_dt:
        start_dt, end_dt = end_dt, start_dt
    # Ensure timezone-aware for consistent .timestamp()
    if start_dt.tzinfo is None:
        start_dt = start_dt.replace(tzinfo=timezone.utc)
    if end_dt.tzinfo is None:
        end_dt = end_dt.replace(tzinfo=timezone.utc)
    return start_dt.timestamp(), end_dt.timestamp(), start_dt.isoformat(), end_dt.isoformat()


def _iso_to_sqlite_str(iso: str) -> str:
    """Convert ISO timestamp to the string format used in cost_entries.created_at.

    CostTracker stores ``created_at`` as ISO strings; we just pass through.
    """
    return iso


def _bucket_index(granularity: str, ts: float) -> str:
    """Return a bucket key for a Unix timestamp given a granularity."""
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    if granularity == "hour":
        return dt.strftime("%Y-%m-%dT%H:00:00")
    if granularity == "week":
        # ISO week: Monday as the bucket start
        monday = dt - timedelta(days=dt.weekday())
        return monday.strftime("%Y-%m-%d")
    # default: day
    return dt.strftime("%Y-%m-%d")


def _bucket_seconds(granularity: str) -> int:
    """Bucket size in seconds for the given granularity."""
    if granularity == "hour":
        return 3600
    if granularity == "week":
        return 7 * 86400
    return 86400  # day


def _safe_psutil() -> Any | None:
    """Return the psutil module if importable, else None."""
    try:
        import psutil
        return psutil
    except ImportError:  # pragma: no cover — psutil is a hard dep in pyproject
        return None


def _get_audit_events(limit: int = 5000) -> list[dict[str, Any]]:
    """Read recent audit events from the personal-edition JSONL log.

    Returns a list of dicts (already model_dump'd). On any failure returns
    an empty list — the caller treats this as "no audit data available".
    """
    try:
        from maop.control.audit import AuditLog
        log = AuditLog(_PROJECT_ROOT / "logs" / "audit.jsonl")
        events = log.read_recent(limit=limit)
        return [e.model_dump() for e in events]
    except Exception as exc:
        logger.debug("[analysis] audit events unavailable: %s", exc)
        return []


def _get_cache_stats() -> dict[str, Any]:
    """Return cache hit/miss stats from the global LRU cache backend.

    Returns an empty dict if the cache backend is unavailable.
    """
    try:
        from maop.core.backends.backends import get_cache_backend
        backend = get_cache_backend()
        # MemoryCacheBackend wraps an LRUCache in _cache
        inner = getattr(backend, "_cache", None)
        if inner is not None and hasattr(inner, "stats"):
            stats = inner.stats()
            return {
                "hits": stats.hits,
                "misses": stats.misses,
                "evictions": stats.evictions,
                "size": stats.size,
                "max_size": stats.max_size,
                "hit_rate": round(stats.hit_rate, 4),
            }
    except Exception as exc:
        logger.debug("[analysis] cache stats unavailable: %s", exc)
    return {}


def _get_db_pool_stats() -> dict[str, Any]:
    """Return DB connection pool stats (size + in-use) if available."""
    try:
        from maop.core.backends.db_utils import get_db_path, get_pool
        pool = get_pool(get_db_path())
        return {
            "max_size": getattr(pool, "_max_size", 0),
            "in_use": len(getattr(pool, "_in_use", [])) if hasattr(pool, "_in_use") else 0,
            "available": len(getattr(pool, "_available", [])) if hasattr(pool, "_available") else 0,
        }
    except Exception as exc:
        logger.debug("[analysis] db pool stats unavailable: %s", exc)
    return {}


# ── 1. Agent efficiency ────────────────────────────────────────────


@router.get("/agent-efficiency")
@handle_api_errors("analysis agent-efficiency")
async def agent_efficiency(
    request: Request,
    date_from: str = Query("", description="Start date (ISO 8601)"),
    date_to: str = Query("", description="End date (ISO 8601)"),
    agent_id: str = Query("", description="Filter by agent name"),
) -> dict[str, Any]:
    """Per-Agent efficiency analysis.

    Returns each agent's task completion rate, average execution time,
    token consumption, and cost efficiency (tasks per USD).

    Data sources: ``CostTracker.summary(by_agent)`` + ``DataProxy.agent_stats()``.
    """
    require_admin(request)
    start_ts, end_ts, iso_from, iso_to = _parse_date_range(date_from, date_to)
    note = ""

    # 1) Cost summary broken down by agent
    cost_summary: Any = None
    try:
        tracker = _get_cost_tracker()
        cost_summary = tracker.summary(
            agent=agent_id or "",
            start_date=_iso_to_sqlite_str(iso_from),
            end_date=_iso_to_sqlite_str(iso_to),
        )
        cost_summary_dict = cost_summary.model_dump()
    except Exception as exc:
        logger.debug("[analysis] cost summary failed: %s", exc)
        cost_summary_dict = {"by_agent": {}, "total_cost_usd": 0.0, "total_calls": 0}
        note = "cost data unavailable"

    # 2) Agent delegation stats (success rate, total delegations)
    agent_stats: list[dict[str, Any]] = []
    try:
        from maop.dashboard.routers.state import get_bridge
        agent_stats = await get_bridge().agent_stats()
    except Exception as exc:
        logger.debug("[analysis] agent_stats failed: %s", exc)
        note = (note + "; " if note else "") + "agent stats unavailable"

    # 3) Merge cost + delegation stats per agent
    by_agent_cost = cost_summary_dict.get("by_agent", {})
    agents: dict[str, dict[str, Any]] = {}

    for a in agent_stats:
        name = a.get("agent", "")
        if agent_id and name != agent_id:
            continue
        agents[name] = {
            "agent": name,
            "total_tasks": a.get("total_delegations", 0),
            "successes": a.get("successes", 0),
            "success_rate_pct": a.get("success_rate", 0.0),
            "circuit_breaker": a.get("circuit_breaker", "unknown"),
            "failures": a.get("failures", 0),
        }

    for name, cstat in by_agent_cost.items():
        if agent_id and name != agent_id:
            continue
        entry = agents.setdefault(name, {"agent": name})
        entry["tokens"] = cstat.get("tokens", 0)
        entry["cost_usd"] = round(cstat.get("cost", 0.0), 4)
        entry["calls"] = cstat.get("calls", 0)
        # cost efficiency = tasks per USD (guard divide-by-zero)
        cost = cstat.get("cost", 0.0)
        calls = cstat.get("calls", 0)
        entry["tasks_per_usd"] = round(calls / cost, 2) if cost > 0 else 0.0

    # 4) Average execution time from cost entries (latency_ms)
    try:
        tracker = _get_cost_tracker()
        entries = tracker.get_entries(
            agent=agent_id or "",
            start_date=_iso_to_sqlite_str(iso_from),
            end_date=_iso_to_sqlite_str(iso_to),
            limit=10000,
        )
        per_agent_latency: dict[str, list[int]] = defaultdict(list)
        for e in entries:
            per_agent_latency[e.agent].append(e.latency_ms)
        for name, lats in per_agent_latency.items():
            if agent_id and name != agent_id:
                continue
            entry = agents.setdefault(name, {"agent": name})
            entry["avg_latency_ms"] = round(sum(lats) / len(lats), 1) if lats else 0.0
    except Exception as exc:
        logger.debug("[analysis] latency aggregation failed: %s", exc)

    # Fill defaults for missing fields
    for entry in agents.values():
        entry.setdefault("total_tasks", 0)
        entry.setdefault("successes", 0)
        entry.setdefault("success_rate_pct", 0.0)
        entry.setdefault("tokens", 0)
        entry.setdefault("cost_usd", 0.0)
        entry.setdefault("calls", 0)
        entry.setdefault("tasks_per_usd", 0.0)
        entry.setdefault("avg_latency_ms", 0.0)

    data = {
        "agents": sorted(agents.values(), key=lambda x: x.get("cost_usd", 0.0), reverse=True),
        "total_cost_usd": round(cost_summary_dict.get("total_cost_usd", 0.0), 4),
        "total_calls": cost_summary_dict.get("total_calls", 0),
    }
    return {
        "status": "ok",
        "data": data,
        "meta": AnalysisMeta(date_from=iso_from, date_to=iso_to, note=note).model_dump(),
    }


# ── 2. Task trends ─────────────────────────────────────────────────


@router.get("/task-trends")
@handle_api_errors("analysis task-trends")
async def task_trends(
    request: Request,
    date_from: str = Query("", description="Start date (ISO 8601)"),
    date_to: str = Query("", description="End date (ISO 8601)"),
    granularity: Literal["hour", "day", "week"] = Query(
        "day", description="Bucket granularity: hour / day / week"
    ),
) -> dict[str, Any]:
    """Task success / failure / timeout time series.

    Buckets audit events (action=task.* or control.run / control.stop) into
    the requested granularity and returns counts + rates per bucket.

    Data source: ``AuditLog.read_recent()`` (personal) or
    ``EnterpriseAuditLogger.iter_events()`` (enterprise, when available).
    """
    require_admin(request)
    start_ts, end_ts, iso_from, iso_to = _parse_date_range(date_from, date_to)
    note = ""

    # Collect audit events — try enterprise first, fall back to personal
    events: list[dict[str, Any]] = []
    try:
        from maop.config.edition import FeatureFlag, has_feature
        if has_feature(FeatureFlag.AUDIT_LOG):
            from maop.enterprise.audit import EnterpriseAuditLogger
            mgr = EnterpriseAuditLogger()
            iter_fn = getattr(mgr, "iter_events", None)
            if callable(iter_fn):
                events = [e.model_dump() for e in iter_fn() if getattr(e, "timestamp", 0) >= start_ts]
            else:
                events = []
    except Exception as exc:
        logger.debug("[analysis] enterprise audit events unavailable: %s", exc)

    if not events:
        events = _get_audit_events(limit=10000)

    if not events:
        note = "no audit events available — time series empty"

    # Bucket events by granularity
    buckets: dict[str, dict[str, int]] = defaultdict(
        lambda: {"success": 0, "failure": 0, "timeout": 0, "total": 0}
    )

    # Heuristics for classifying an audit event:
    #   - timeout: action contains "timeout" or detail has timeout flag
    #   - failure: level in (error, critical) or action contains "fail"
    #   - success: everything else with action matching task/control
    _TASK_ACTIONS = ("task", "control.run", "control.stop", "delegation", "dispatch")

    for e in events:
        ts = e.get("timestamp", 0.0)
        if ts < start_ts or ts > end_ts:
            continue
        action = str(e.get("action", ""))
        if not any(tag in action for tag in _TASK_ACTIONS):
            continue
        bucket_key = _bucket_index(granularity, ts)
        b = buckets[bucket_key]
        b["total"] += 1

        level = str(e.get("level", "")).lower()
        detail = e.get("detail", {}) or {}
        if "timeout" in action.lower() or detail.get("timeout"):
            b["timeout"] += 1
        elif level in ("error", "critical") or "fail" in action.lower():
            b["failure"] += 1
        else:
            b["success"] += 1

    # Build sorted time series with rates
    series: list[dict[str, Any]] = []
    for key in sorted(buckets.keys()):
        b = buckets[key]
        total = b["total"]
        series.append({
            "bucket": key,
            "success": b["success"],
            "failure": b["failure"],
            "timeout": b["timeout"],
            "total": total,
            "success_rate": round(b["success"] / total, 4) if total else 0.0,
            "failure_rate": round(b["failure"] / total, 4) if total else 0.0,
            "timeout_rate": round(b["timeout"] / total, 4) if total else 0.0,
        })

    data = {
        "granularity": granularity,
        "series": series,
        "bucket_count": len(series),
    }
    return {
        "status": "ok",
        "data": data,
        "meta": AnalysisMeta(date_from=iso_from, date_to=iso_to, note=note).model_dump(),
    }


# ── 3. Resource utilization ────────────────────────────────────────


@router.get("/resource-utilization")
@handle_api_errors("analysis resource-utilization")
async def resource_utilization(
    request: Request,
    date_from: str = Query("", description="Start date (ISO 8601)"),
    date_to: str = Query("", description="End date (ISO 8601)"),
) -> dict[str, Any]:
    """Resource utilization time series.

    Returns CPU %, memory %, DB connection pool usage, and cache hit-rate
    as a time series. Live samples are taken via ``psutil`` for CPU/memory;
    DB pool and cache stats come from the in-process backends.

    For historical data, this endpoint also queries the TimeSeriesStore
    for ``system.cpu.percent`` and ``system.memory.percent`` metrics if
    they were recorded.
    """
    require_admin(request)
    start_ts, end_ts, iso_from, iso_to = _parse_date_range(date_from, date_to)
    note = ""

    # 1) Live snapshot via psutil
    live_cpu: float | None = None
    live_mem: float | None = None
    mem_used_mb: float | None = None
    mem_total_mb: float | None = None
    ps = _safe_psutil()
    if ps is not None:
        try:
            live_cpu = ps.cpu_percent(interval=0.1)
            mem = ps.virtual_memory()
            live_mem = mem.percent
            mem_used_mb = round(mem.used / (1024 * 1024), 1)
            mem_total_mb = round(mem.total / (1024 * 1024), 1)
        except Exception as exc:
            logger.debug("[analysis] psutil sampling failed: %s", exc)
            note = "psutil sampling failed"
    else:
        note = "psutil not installed — live CPU/memory unavailable"

    # 2) Historical time series from TimeSeriesStore
    history: dict[str, list[dict[str, Any]]] = {}
    try:
        from maop.core.backends.db_utils import get_db_path
        from maop.core.monitoring.timeseries import TimeSeriesQuery, TimeSeriesStore
        ts_db = get_db_path("timeseries")
        store = TimeSeriesStore(db_path=ts_db)
        for metric in ("system.cpu.percent", "system.memory.percent"):
            q = TimeSeriesQuery(
                metric=metric,
                start=start_ts,
                end=end_ts,
                aggregation="avg",
                interval_s=300,  # 5-min buckets
            )
            points = store.query(q)
            history[metric] = points
    except Exception as exc:
        logger.debug("[analysis] timeseries history failed: %s", exc)
        note = (note + "; " if note else "") + "timeseries history unavailable"

    # 3) DB connection pool stats
    db_pool = _get_db_pool_stats()

    # 4) Cache hit-rate
    cache_stats = _get_cache_stats()

    data = {
        "live": {
            "cpu_percent": live_cpu,
            "memory_percent": live_mem,
            "memory_used_mb": mem_used_mb,
            "memory_total_mb": mem_total_mb,
        },
        "history": history,
        "db_pool": db_pool,
        "cache": cache_stats,
    }
    return {
        "status": "ok",
        "data": data,
        "meta": AnalysisMeta(date_from=iso_from, date_to=iso_to, note=note).model_dump(),
    }


# ── 4. Cost breakdown ──────────────────────────────────────────────


@router.get("/cost-breakdown")
@handle_api_errors("analysis cost-breakdown")
async def cost_breakdown(
    request: Request,
    date_from: str = Query("", description="Start date (ISO 8601)"),
    date_to: str = Query("", description="End date (ISO 8601)"),
    group_by: Literal["agent", "model", "tenant"] = Query(
        "agent", description="Group costs by: agent / model / tenant"
    ),
) -> dict[str, Any]:
    """Cost decomposition by agent / model / tenant.

    Returns the cost breakdown and a per-bucket trend series.

    Data source: ``CostTracker.summary()`` (which already provides
    ``by_agent`` / ``by_model`` / ``by_session``). Tenant grouping is only
    available in enterprise edition; in personal edition it falls back to
    session_id grouping with a note.
    """
    require_admin(request)
    start_ts, end_ts, iso_from, iso_to = _parse_date_range(date_from, date_to)
    note = ""

    try:
        tracker = _get_cost_tracker()
        summary = tracker.summary(
            start_date=_iso_to_sqlite_str(iso_from),
            end_date=_iso_to_sqlite_str(iso_to),
        )
        summary_dict = summary.model_dump()
    except Exception as exc:
        logger.debug("[analysis] cost summary failed: %s", exc)
        summary_dict = {"by_agent": {}, "by_model": {}, "by_session": {}, "total_cost_usd": 0.0, "total_tokens": 0, "total_calls": 0}
        note = "cost data unavailable"

    # Select grouping dimension
    if group_by == "agent":
        groups = summary_dict.get("by_agent", {})
    elif group_by == "model":
        groups = summary_dict.get("by_model", {})
    elif group_by == "tenant":
        # Enterprise edition would have by_tenant; fall back to by_session
        groups = summary_dict.get("by_tenant", summary_dict.get("by_session", {}))
        if "by_tenant" not in summary_dict:
            note = (note + "; " if note else "") + "tenant grouping not available — falling back to session"
    else:
        groups = {}

    # Build breakdown list
    breakdown: list[dict[str, Any]] = []
    for name, stats in groups.items():
        cost = stats.get("cost", 0.0)
        tokens = stats.get("tokens", 0)
        calls = stats.get("calls", 0)
        breakdown.append({
            "key": name,
            "cost_usd": round(cost, 4),
            "tokens": tokens,
            "calls": calls,
            "avg_cost_per_call": round(cost / calls, 6) if calls else 0.0,
        })
    breakdown.sort(key=lambda x: x["cost_usd"], reverse=True)

    # Build per-day trend from raw entries
    trend: dict[str, float] = defaultdict(float)
    try:
        tracker = _get_cost_tracker()
        entries = tracker.get_entries(
            start_date=_iso_to_sqlite_str(iso_from),
            end_date=_iso_to_sqlite_str(iso_to),
            limit=10000,
        )
        for e in entries:
            day = (e.created_at or "")[:10]  # YYYY-MM-DD
            if day:
                trend[day] += e.cost_usd
    except Exception as exc:
        logger.debug("[analysis] cost trend failed: %s", exc)
        note = (note + "; " if note else "") + "cost trend unavailable"

    trend_series = [
        {"date": day, "cost_usd": round(cost, 4)}
        for day, cost in sorted(trend.items())
    ]

    data = {
        "group_by": group_by,
        "breakdown": breakdown,
        "trend": trend_series,
        "total_cost_usd": round(summary_dict.get("total_cost_usd", 0.0), 4),
        "total_tokens": summary_dict.get("total_tokens", 0),
        "total_calls": summary_dict.get("total_calls", 0),
    }
    return {
        "status": "ok",
        "data": data,
        "meta": AnalysisMeta(date_from=iso_from, date_to=iso_to, note=note).model_dump(),
    }


# ── 5. Performance bottlenecks ─────────────────────────────────────


@router.get("/performance-bottlenecks")
@handle_api_errors("analysis performance-bottlenecks")
async def performance_bottlenecks(
    request: Request,
    date_from: str = Query("", description="Start date (ISO 8601)"),
    date_to: str = Query("", description="End date (ISO 8601)"),
    top_n: int = Query(10, ge=1, le=100, description="Number of top items per category"),
) -> dict[str, Any]:
    """Identify performance bottlenecks.

    Returns:
      - ``slowest_endpoints``: API endpoints with the highest avg latency
        (from MetricsCollector histograms)
      - ``slowest_agents``: Agents with the highest avg execution time
        (from CostTracker latency_ms)
      - ``top_memory_consumers``: Processes using the most memory
        (from psutil, if available)

    Data sources: ``MetricsCollector.to_json()`` + ``CostTracker.get_entries()``
    + ``psutil``.
    """
    require_admin(request)
    start_ts, end_ts, iso_from, iso_to = _parse_date_range(date_from, date_to)
    note = ""

    # 1) Slowest endpoints from MetricsCollector histograms
    slowest_endpoints: list[dict[str, Any]] = []
    try:
        from maop.core.monitoring.monitoring import metrics as _metrics
        metrics_json = _metrics.to_json()
        for name, info in metrics_json.items():
            if info.get("type") != "histogram":
                continue
            count = info.get("count", 0)
            total_sum = info.get("sum", 0.0)
            if count <= 0:
                continue
            avg = total_sum / count
            slowest_endpoints.append({
                "metric": name,
                "count": count,
                "sum_seconds": round(total_sum, 4),
                "avg_seconds": round(avg, 6),
            })
        slowest_endpoints.sort(key=lambda x: x["avg_seconds"], reverse=True)
        slowest_endpoints = slowest_endpoints[:top_n]
    except Exception as exc:
        logger.debug("[analysis] metrics histograms failed: %s", exc)
        note = "metrics histograms unavailable"

    # 2) Slowest agents from CostTracker latency_ms
    slowest_agents: list[dict[str, Any]] = []
    try:
        tracker = _get_cost_tracker()
        entries = tracker.get_entries(
            start_date=_iso_to_sqlite_str(iso_from),
            end_date=_iso_to_sqlite_str(iso_to),
            limit=10000,
        )
        per_agent: dict[str, list[int]] = defaultdict(list)
        for e in entries:
            per_agent[e.agent].append(e.latency_ms)
        for name, lats in per_agent.items():
            if not lats:
                continue
            slowest_agents.append({
                "agent": name,
                "calls": len(lats),
                "avg_latency_ms": round(sum(lats) / len(lats), 1),
                "max_latency_ms": max(lats),
                "p95_latency_ms": _percentile(lats, 95),
            })
        slowest_agents.sort(key=lambda x: x["avg_latency_ms"], reverse=True)
        slowest_agents = slowest_agents[:top_n]
    except Exception as exc:
        logger.debug("[analysis] agent latency failed: %s", exc)
        note = (note + "; " if note else "") + "agent latency unavailable"

    # 3) Top memory consumers via psutil
    top_memory: list[dict[str, Any]] = []
    ps = _safe_psutil()
    if ps is not None:
        try:
            procs = []
            for p in ps.process_iter(attrs=["pid", "name", "memory_info", "cpu_percent"]):
                mi = p.info.get("memory_info")
                if mi is None:
                    continue
                procs.append({
                    "pid": p.info["pid"],
                    "name": p.info["name"],
                    "memory_mb": round(mi.rss / (1024 * 1024), 1),
                    "cpu_percent": p.info.get("cpu_percent", 0.0),
                })
            procs.sort(key=lambda x: x["memory_mb"], reverse=True)
            top_memory = procs[:top_n]
        except Exception as exc:
            logger.debug("[analysis] psutil process scan failed: %s", exc)
            note = (note + "; " if note else "") + "psutil process scan failed"
    else:
        note = (note + "; " if note else "") + "psutil not installed"

    data = {
        "slowest_endpoints": slowest_endpoints,
        "slowest_agents": slowest_agents,
        "top_memory_consumers": top_memory,
    }
    return {
        "status": "ok",
        "data": data,
        "meta": AnalysisMeta(date_from=iso_from, date_to=iso_to, note=note).model_dump(),
    }


def _percentile(values: list[int], pct: int) -> float:
    """Compute the pct-th percentile of a list of integers (linear interpolation)."""
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * (pct / 100.0)
    f = int(k)
    c = f + 1 if f + 1 < len(s) else f
    if f == c:
        return float(s[f])
    return round(s[f] + (s[c] - s[f]) * (k - f), 1)


# ── 6. Summary ─────────────────────────────────────────────────────


@router.get("/summary")
@handle_api_errors("analysis summary")
async def summary(
    request: Request,
    date_range: Literal["7d", "30d", "90d"] = Query(
        "7d", description="Time window: 7d / 30d / 90d"
    ),
) -> dict[str, Any]:
    """KPI summary for the requested window.

    Returns: total tasks, success rate, average latency, total cost,
    active agent count, and a small sparkline of daily task counts.

    Aggregates data from CostTracker, DataProxy.agent_stats, AuditLog,
    and the live MetricsCollector snapshot.
    """
    require_admin(request)
    days = int(date_range.rstrip("d")) if date_range.endswith("d") else 7
    now = datetime.now(timezone.utc)
    start_dt = now - timedelta(days=days)
    iso_from = start_dt.isoformat()
    iso_to = now.isoformat()
    start_ts = start_dt.timestamp()
    note = ""

    # 1) Cost totals
    total_cost = 0.0
    total_calls = 0
    avg_latency_ms = 0.0
    try:
        tracker = _get_cost_tracker()
        csum = tracker.summary(
            start_date=_iso_to_sqlite_str(iso_from),
            end_date=_iso_to_sqlite_str(iso_to),
        ).model_dump()
        total_cost = csum.get("total_cost_usd", 0.0)
        total_calls = csum.get("total_calls", 0)
        avg_latency_ms = csum.get("avg_latency_ms", 0.0)
    except Exception as exc:
        logger.debug("[analysis] summary cost failed: %s", exc)
        note = "cost data unavailable"

    # 2) Active agents + task success rate
    active_agents = 0
    total_tasks = 0
    successes = 0
    success_rate_pct = 0.0
    try:
        from maop.dashboard.routers.state import get_bridge
        agent_stats = await get_bridge().agent_stats()
        active_agents = len(agent_stats)
        for a in agent_stats:
            total_tasks += a.get("total_delegations", 0)
            successes += a.get("successes", 0)
        if total_tasks > 0:
            success_rate_pct = round(successes / total_tasks * 100, 1)
    except Exception as exc:
        logger.debug("[analysis] summary agent stats failed: %s", exc)
        note = (note + "; " if note else "") + "agent stats unavailable"

    # 3) Daily task sparkline from audit events
    daily_counts: dict[str, int] = defaultdict(int)
    events = _get_audit_events(limit=10000)
    for e in events:
        ts = e.get("timestamp", 0.0)
        if ts < start_ts:
            continue
        day = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
        daily_counts[day] += 1
    sparkline = [
        {"date": day, "count": daily_counts[day]}
        for day in sorted(daily_counts.keys())
    ]

    # 4) Observability metrics snapshot (optional)
    obs_snapshot: dict[str, Any] = {}
    try:
        from maop.core.observability.metrics import metrics_summary
        obs_snapshot = metrics_summary()
    except Exception as exc:
        logger.debug("[analysis] observability snapshot failed: %s", exc)

    data = {
        "date_range": date_range,
        "total_tasks": total_tasks,
        "success_rate_pct": success_rate_pct,
        "avg_latency_ms": avg_latency_ms,
        "total_cost_usd": round(total_cost, 4),
        "total_calls": total_calls,
        "active_agents": active_agents,
        "sparkline": sparkline,
        "observability": obs_snapshot,
    }
    return {
        "status": "ok",
        "data": data,
        "meta": AnalysisMeta(date_from=iso_from, date_to=iso_to, note=note).model_dump(),
    }


__all__ = ["router"]