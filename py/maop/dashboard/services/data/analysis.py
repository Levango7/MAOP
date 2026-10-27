"""Deep-analysis endpoints split out of ``data_service`` (file-size ratchet).

Six aggregate endpoints (agent efficiency / task trends / resource
utilization / cost breakdown / bottlenecks / KPI summary). Helpers come
straight from ``analysis_helpers`` — the parent module still re-exports
them, but nothing here resolves through the parent, so importing this
module works on its own.

The parent forwards every public name below, so routers, callers and
``data_service.<name>`` monkeypatching keep working; patch the helpers
used *here* on this module (``services.data.analysis._get_cost_tracker``).
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from maop.core.backends.db_utils import get_db_path

# 共享运行时状态经 ``state`` 模块取——测试 monkeypatch
# ``state.MAOP_ROOT`` / ``state.get_bridge`` 时此处同样生效。
from maop.dashboard.routers import state
from maop.dashboard.routers.analysis_helpers import (
    _bucket_index,
    _cpu_series,
    _get_audit_events,
    _get_cache_stats,
    _get_cost_tracker,
    _get_db_pool_stats,
    _iso_to_sqlite_str,
    _parse_date_range,
    _safe_psutil,
)

logger = logging.getLogger(__name__)

__all__ = [
    "_analysis_meta",
    "analysis_agent_efficiency",
    "analysis_cost_breakdown",
    "analysis_performance_bottlenecks",
    "analysis_resource_utilization",
    "analysis_summary",
    "analysis_task_trends",
]


def _analysis_meta(date_from: str, date_to: str, note: str = "") -> dict[str, Any]:
    """Build the standard analysis meta block."""
    return {
        "date_from": date_from,
        "date_to": date_to,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "note": note,
    }


async def analysis_agent_efficiency(
    date_from: str,
    date_to: str,
    agent_id: str,
) -> dict[str, Any]:
    """Per-Agent efficiency analysis.

    Returns each agent's task completion rate, average execution time,
    token consumption, and cost efficiency (tasks per USD).
    """
    _start_ts, _end_ts, iso_from, iso_to = _parse_date_range(date_from, date_to)
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
        agent_stats = await state.get_bridge().agent_stats()
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
        "meta": _analysis_meta(iso_from, iso_to, note),
    }


async def analysis_task_trends(
    date_from: str,
    date_to: str,
    granularity: Literal["hour", "day", "week"],
) -> dict[str, Any]:
    """Task success / failure / timeout time series.

    Buckets audit events into the requested granularity and returns
    counts + rates per bucket.
    """
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
        "meta": _analysis_meta(iso_from, iso_to, note),
    }


async def analysis_resource_utilization(
    date_from: str,
    date_to: str,
) -> dict[str, Any]:
    """Resource utilization time series.

    Returns CPU %, memory %, DB connection pool usage, and cache hit-rate
    as a time series. Live samples via psutil; historical from TimeSeriesStore.
    """
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
        # Chart-ready CPU line. The view draws one line; without this key the
        # resource chart stays empty no matter how much history is stored.
        "series": _cpu_series(history),
        "db_pool": db_pool,
        "cache": cache_stats,
    }
    return {
        "status": "ok",
        "data": data,
        "meta": _analysis_meta(iso_from, iso_to, note),
    }


async def analysis_cost_breakdown(
    date_from: str,
    date_to: str,
    group_by: Literal["agent", "model", "tenant"],
) -> dict[str, Any]:
    """Cost decomposition by agent / model / tenant.

    Returns the cost breakdown and a per-bucket trend series.
    """
    _start_ts, _end_ts, iso_from, iso_to = _parse_date_range(date_from, date_to)
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
        "meta": _analysis_meta(iso_from, iso_to, note),
    }


async def analysis_performance_bottlenecks(
    date_from: str,
    date_to: str,
    top_n: int,
) -> dict[str, Any]:
    """Identify performance bottlenecks.

    Returns slowest endpoints, slowest agents, and top memory consumers.
    """
    _start_ts, _end_ts, iso_from, iso_to = _parse_date_range(date_from, date_to)
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

    # 2) Most expensive agents — ranked by cost, not by latency.
    #    The dashboard section is titled "Most Expensive Agents" and renders a
    #    cost column, so a latency ranking under a different key left that
    #    table permanently empty.
    expensive_agents: list[dict[str, Any]] = []
    try:
        tracker = _get_cost_tracker()
        cost_summary = tracker.summary(
            start_date=_iso_to_sqlite_str(iso_from),
            end_date=_iso_to_sqlite_str(iso_to),
        ).model_dump()
        rows = [
            {
                "agent": name,
                "calls": int(stats.get("calls", 0)),
                "cost_usd": round(float(stats.get("cost", 0.0)), 4),
                "tokens": int(stats.get("tokens", 0)),
            }
            for name, stats in cost_summary.get("by_agent", {}).items()
        ]
        rows.sort(key=lambda r: r["cost_usd"], reverse=True)
        expensive_agents = rows[:top_n]
    except Exception as exc:
        logger.debug("[analysis] agent cost ranking failed: %s", exc)
        note = (note + "; " if note else "") + "agent cost ranking unavailable"

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
        "expensive_agents": expensive_agents,
        "top_memory_consumers": top_memory,
    }
    return {
        "status": "ok",
        "data": data,
        "meta": _analysis_meta(iso_from, iso_to, note),
    }


async def analysis_summary(date_range: Literal["7d", "30d", "90d"]) -> dict[str, Any]:
    """KPI summary for the requested window.

    Returns total tasks, success rate, average latency, total cost,
    active agent count, and a daily task sparkline.
    """
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
    total_tokens = 0
    avg_latency_ms = 0.0
    try:
        tracker = _get_cost_tracker()
        csum = tracker.summary(
            start_date=_iso_to_sqlite_str(iso_from),
            end_date=_iso_to_sqlite_str(iso_to),
        ).model_dump()
        total_cost = csum.get("total_cost_usd", 0.0)
        total_calls = csum.get("total_calls", 0)
        total_tokens = csum.get("total_tokens", 0)
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
        agent_stats = await state.get_bridge().agent_stats()
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
        "total_tokens": total_tokens,
        "active_agents": active_agents,
        "sparkline": sparkline,
        "observability": obs_snapshot,
    }
    return {
        "status": "ok",
        "data": data,
        "meta": _analysis_meta(iso_from, iso_to, note),
    }