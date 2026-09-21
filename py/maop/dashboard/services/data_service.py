"""Data & Analysis service layer.

Encapsulates business logic for the data query/read endpoints (data.py)
and the deep data analysis endpoints (analysis.py). Extracted from the
router layer so routers only do parameter parsing + auth + service call
+ response formatting.

The service is intentionally framework-agnostic: it does not import
FastAPI and can be unit-tested or invoked without an HTTP context.
Shared runtime state (``MAOP_ROOT``, ``get_bridge``) is imported from
``maop.dashboard.routers.state`` so the service operates on the same
singletons the dashboard initialised.

``analysis_helpers`` remains a separate module of pure helper functions;
this service re-exports them for convenience so callers can import
everything from one place.
"""

from __future__ import annotations

import asyncio
import collections
import logging
import re
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path  # noqa: F401
from typing import Any, Literal

from maop.core.backends.db_utils import get_db_path

# Shared runtime state — accessed via the ``state`` module so that tests
# which monkeypatch ``state.MAOP_ROOT`` / ``state.get_bridge`` etc. take
# effect here too.
from maop.dashboard.routers import state

# Re-export analysis helpers for backward compatibility (analysis.py
# originally imported them from analysis_helpers; now both the router
# and this service can import from here).
from maop.dashboard.routers.analysis_helpers import (
    _bucket_index,
    _bucket_seconds,  # noqa: F401
    _get_audit_events,
    _get_cache_stats,
    _get_cost_tracker,
    _get_db_pool_stats,
    _iso_to_sqlite_str,
    _parse_date_range,
    _percentile,
    _safe_psutil,
)

logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════════════════════
# Tenant filtering (shared utility)
# ════════════════════════════════════════════════════════════════════════════

def tenant_filter(data: Any, tenant_id: str) -> Any:
    """Recursively filter data by tenant_id.

    If ``tenant_id`` is empty, returns data unchanged. Otherwise walks
    dicts/lists and drops items whose ``tenant_id`` field doesn't match.
    """
    if not tenant_id:
        return data
    if isinstance(data, dict):
        return {k: tenant_filter(v, tenant_id) for k, v in data.items()}
    if isinstance(data, list):
        return [
            tenant_filter(it, tenant_id) for it in data
            if not (isinstance(it, dict) and it.get("tenant_id") and it.get("tenant_id") != tenant_id)
        ]
    return data


# ════════════════════════════════════════════════════════════════════════════
# Overview endpoints (data.py)
# ════════════════════════════════════════════════════════════════════════════

async def get_report(hours: int, tenant_id: str = "") -> Any:
    """Fetch the overview report and apply tenant filtering."""
    return tenant_filter(await state.get_bridge().report(hours=hours), tenant_id)


async def get_agents_stats(tenant_id: str = "") -> dict[str, Any]:
    """Fetch agent stats and apply tenant filtering."""
    agents = await state.get_bridge().agent_stats()
    return tenant_filter({"agents": agents, "count": len(agents)}, tenant_id)


async def get_timeseries(tenant_id: str = "") -> Any:
    """Fetch timeseries data (168h window) and apply tenant filtering."""
    return tenant_filter(await state.get_bridge().timeseries(hours=168), tenant_id)


async def get_metrics(tenant_id: str = "") -> dict[str, Any]:
    """Real-time metrics from LoadBalancer, TimeSeries, and CircuitBreaker.

    Each subsystem is queried independently; failures are logged and
    returned as an error sub-object so the frontend can render partial
    data.
    """
    result: dict[str, Any] = {}
    try:
        from maop.core.routing.load_balancer import get_load_balancer
        lb = get_load_balancer()
        stats = lb.stats()
        result["load_balancer"] = stats.model_dump()
    except Exception as exc:
        logger.error('Load balancer stats failed: %s', exc)
        result["load_balancer"] = {"status": "error", "error": "Load balancer stats unavailable"}
    try:
        from maop.core.monitoring.timeseries import TimeSeriesStore
        ts = TimeSeriesStore(db_path=get_db_path("timeseries"))
        recent = ts.read_recent(hours=24)
        result["timeseries"] = recent if isinstance(recent, list) else []
    except Exception as exc:
        logger.error('Timeseries read failed: %s', exc)
        result["timeseries"] = {"status": "error", "error": "Timeseries data unavailable"}
    try:
        from maop.core.reliability.circuit_breaker import CircuitBreaker
        cb = CircuitBreaker(get_db_path())
        result["circuit_breaker"] = {
            name: {"state": entry.state.value, "failures": entry.failures}
            for name, entry in cb.all_states().items()
        }
    except Exception as exc:
        logger.error('Circuit breaker stats failed: %s', exc)
        result["circuit_breaker"] = {"status": "error", "error": "Circuit breaker stats unavailable"}
    try:
        from maop.core.reliability.cache import get_cache
        c = get_cache(name="metrics")
        result["cache"] = {"hits": getattr(c, "hits", 0), "misses": getattr(c, "misses", 0)}
    except Exception as exc:
        logger.error('Cache stats failed: %s', exc)
        result["cache"] = {"status": "error", "error": "Cache stats unavailable"}
    return tenant_filter(result, tenant_id)


async def get_live(tenant_id: str = "") -> Any:
    """Fetch live data and apply tenant filtering."""
    return tenant_filter(await state.get_bridge().live(), tenant_id)


async def get_snapshot(tenant_id: str = "") -> Any:
    """F-P0-2 fix: Aggregate snapshot for Overview.vue health metrics."""
    return tenant_filter(await state.get_bridge().snapshot(), tenant_id)


async def get_failures(tenant_id: str = "") -> Any:
    """Fetch failures and apply tenant filtering."""
    return tenant_filter(await state.get_bridge().failures(), tenant_id)


async def get_chain(tenant_id: str = "") -> Any:
    """Fetch chain data and apply tenant filtering."""
    return tenant_filter(await state.get_bridge().chain(), tenant_id)


async def get_optimizer(tenant_id: str = "") -> dict[str, Any]:
    """Fetch optimizer report with cache stats and recommendations.

    Raises ``Exception`` on failure so the router can map to HTTP 500.
    """
    bridge = state.get_bridge()
    report = await bridge.report()
    cache_stats: dict[str, Any] = {}
    try:
        from maop.core.reliability.cache import get_cache
        c = get_cache(name="optimizer")
        cache_stats = {"hits": c.hits if hasattr(c, "hits") else 0, "misses": c.misses if hasattr(c, "misses") else 0}
    except Exception as exc:
        logger.warning('Failed to get cache stats: %s', exc)
    return tenant_filter(
        {
            "report": report,
            "cache": cache_stats,
            "recommendations": [
                "Enable parallel execution for independent subtasks",
                "Increase cache TTL for stable results",
                "Use LoadBalancer for multi-agent tasks",
            ],
        },
        tenant_id,
    )


# ════════════════════════════════════════════════════════════════════════════
# Graph endpoints (data.py)
# ════════════════════════════════════════════════════════════════════════════

async def get_graph_stats() -> dict[str, Any]:
    """Compute graph statistics: node/edge counts, avg/max degree.

    Raises ``Exception`` on failure so the router can map to HTTP 500.
    """
    bridge = state.get_bridge()
    nodes = await bridge.graph_nodes()
    edges = await bridge.graph_edges()
    node_count = len(nodes) if isinstance(nodes, list) else 0
    edge_count = len(edges) if isinstance(edges, list) else 0
    degrees: dict[str, int] = {}
    for e in (edges if isinstance(edges, list) else []):
        if isinstance(e, dict):
            for key in ("source", "target"):
                n = e.get(key, "")
                if n:
                    degrees[n] = degrees.get(n, 0) + 1
    avg_degree = round(sum(degrees.values()) / len(degrees), 2) if degrees else 0
    return {
        "nodes": node_count,
        "edges": edge_count,
        "avg_degree": avg_degree,
        "max_degree": max(degrees.values()) if degrees else 0,
    }


async def get_graph_nodes() -> Any:
    """Fetch all graph nodes."""
    return await state.get_bridge().graph_nodes()


async def get_graph_edges() -> Any:
    """Fetch all graph edges."""
    return await state.get_bridge().graph_edges()


async def get_graph_neighbors(node: str) -> dict[str, Any]:
    """Find all edges connected to ``node``."""
    bridge = state.get_bridge()
    edges = await bridge.graph_edges()
    neighbors = [e for e in edges if isinstance(e, dict) and (e.get("source") == node or e.get("target") == node)]
    return {"node": node, "neighbors": neighbors, "count": len(neighbors)}


# ════════════════════════════════════════════════════════════════════════════
# Knowledge endpoints (data.py)
# ════════════════════════════════════════════════════════════════════════════

async def get_vector_stats() -> Any:
    """Fetch vector store statistics."""
    return await state.get_bridge().memory_stats()


async def get_vector_list(limit: int, offset: int) -> dict[str, Any]:
    """List indexed vectors with pagination.

    Raises ``Exception`` on failure so the router can map to HTTP 500.
    """
    from maop.core.memory.vector import VectorStore
    vs = VectorStore(db_path=str(get_db_path("vectors")))
    if hasattr(vs, "list_all"):
        items = vs.list_all(limit=limit, offset=offset)
        total = vs.count() if hasattr(vs, "count") else len(items)
    else:
        items = []
        total = 0
    return {
        "vectors": items,
        "count": len(items),
        "total": total,
        "limit": limit,
        "offset": offset,
    }


async def get_vector_search(q: str, k: int) -> dict[str, Any]:
    """Search vectors with fallback to MemoryStore.

    Raises ``Exception`` on failure so the router can map to HTTP 500.
    """
    from maop.core.memory.vector import VectorStore
    vs = VectorStore(db_path=str(get_db_path("vectors")))
    raw_results = vs.search(query=q, top=k)
    results = [r.model_dump() if hasattr(r, 'model_dump') else (r if isinstance(r, dict) else {"content": str(r)}) for r in raw_results]
    return {"query": q, "results": results, "count": len(results)}


async def get_vector_search_fallback(q: str, k: int) -> dict[str, Any]:
    """Fallback vector search via MemoryStore.

    Raises ``Exception`` on failure so the router can map to HTTP 500.
    """
    from maop.memory.store import MemoryStore
    store = MemoryStore(root_dir=str(state.MAOP_ROOT))
    fallback_results: Any = store.search(query=q, top=k)
    results = [r.model_dump() if hasattr(r, 'model_dump') else (r if isinstance(r, dict) else {"content": str(r)}) for r in fallback_results]
    return {"query": q, "results": results, "count": len(results), "fallback": "memory"}


async def get_wiki_stats() -> dict[str, Any]:
    """Fetch wiki stats with vector count."""
    base = await state.get_bridge().memory_stats()
    try:
        from maop.core.memory.vector import VectorStore
        vs = VectorStore(db_path=str(get_db_path("vectors")))
        base["vector_count"] = vs.count() if hasattr(vs, "count") else 0
    except Exception as exc:
        logger.warning("Failed to get vector count: %s", exc)
        base["vector_count"] = 0
    return base


async def get_prompts() -> dict[str, Any]:
    """List available prompts with fallback to filesystem and defaults.

    Raises ``Exception`` on failure so the router can map to HTTP 500.
    """
    result = await state.get_bridge().prompts_list()
    if isinstance(result, dict) and "prompts" in result:
        return result
    items: list[Any] = result if isinstance(result, list) else []
    prompts: list[dict[str, Any]] = []
    for p in items:
        if isinstance(p, dict):
            prompts.append({"name": p.get("name", ""), "category": p.get("category", p.get("type", "general")),
                            "template": p.get("template", p.get("content", ""))})
        elif isinstance(p, str):
            prompts.append({"name": p, "category": "general"})
    if not prompts:
        prompts_dir = state.MAOP_ROOT / "prompts"
        if prompts_dir.exists():
            for f in sorted(prompts_dir.glob("*.md")):
                prompts.append({"name": f.stem, "category": "general"})
        if not prompts:
            prompts = [{"name": "default_task", "category": "general"},
                       {"name": "code_review", "category": "quality"},
                       {"name": "error_fix", "category": "debug"},
                       {"name": "planning", "category": "plan"},
                       {"name": "verification", "category": "verify"}]
    return {"prompts": prompts}


async def get_coordination() -> Any:
    """Fetch coordination report."""
    return await state.get_bridge().coordination_report()


async def get_teams() -> Any:
    """List agent teams from config, falling back to coordination report."""
    try:
        from maop.config.loader import ConfigLoader
        cfg = ConfigLoader(project_root=str(state.MAOP_ROOT)).load()
        teams: dict[str, list[str]] = {}
        for name, ad in cfg.agents.items():
            group = getattr(ad, "group", "default")
            teams.setdefault(group, []).append(name)
        return [{"team": k, "agents": v, "count": len(v)} for k, v in teams.items()]
    except Exception as exc:
        logger.warning("Teams from config failed: %s", exc)
        return (await state.get_bridge().coordination_report()).get("teams", [])


async def get_skills() -> dict[str, Any]:
    """List available skills with filesystem fallback.

    Raises ``Exception`` on failure so the router can map to HTTP 500.
    """
    result = await state.get_bridge().skills_list()
    items = result if isinstance(result, list) else (result.get("skills", []) if isinstance(result, dict) else [])
    skills = []
    for s in items:
        if isinstance(s, dict):
            skills.append({"name": s.get("name", ""), "category": s.get("category", ""),
                           "usage_count": s.get("usage_count", s.get("used", 0)),
                           "path": s.get("path", "")})
        elif isinstance(s, str):
            skills.append({"name": s, "category": "", "usage_count": 0})
    if not skills:
        skills_dir = state.MAOP_ROOT / "skills"
        if skills_dir.exists():
            for d in sorted(skills_dir.iterdir()):
                if d.is_dir():
                    cat = ""
                    skill_md = d / "SKILL.md"
                    if skill_md.exists():
                        try:
                            first_line = skill_md.read_text(encoding="utf-8", errors="replace").strip().split("\n")[0]
                            cat = first_line.replace("#", "").strip()[:30]
                        except Exception as exc:
                            logger.warning('Failed to read skill metadata: %s', exc)
                    skills.append({"name": d.name, "category": cat, "usage_count": 0, "path": str(d)})
    return {"skills": skills, "count": len(skills)}


# ════════════════════════════════════════════════════════════════════════════
# Tools endpoints (data.py)
# ════════════════════════════════════════════════════════════════════════════

async def get_tools_stats() -> Any:
    """Fetch tool statistics."""
    return await state.get_bridge().tools_stats()


async def get_guardrails() -> Any:
    """Fetch guardrail report."""
    return await state.get_bridge().guardrail_report()


async def get_sandbox_list() -> Any:
    """Fetch sandbox list."""
    return await state.get_bridge().sandbox_list()


async def get_human_pending() -> Any:
    """Fetch pending human-in-the-loop approvals."""
    return await state.get_bridge().human_pending()


async def get_mcp_servers() -> Any:
    """Fetch MCP server list."""
    return await state.get_bridge().mcp_servers()


async def get_mcp_tools() -> Any:
    """Fetch MCP tool list."""
    return await state.get_bridge().mcp_tools()


async def get_mcp_combined() -> dict[str, Any]:
    """Fetch combined MCP servers + tools."""
    servers = await state.get_bridge().mcp_servers()
    tools = await state.get_bridge().mcp_tools()
    return {"servers": servers, "tools": tools, "server_count": len(servers), "tool_count": len(tools)}


# ════════════════════════════════════════════════════════════════════════════
# System endpoints (data.py)
# ════════════════════════════════════════════════════════════════════════════

async def get_versions() -> dict[str, Any]:
    """Fetch MAOP version, Python version, and timestamp."""
    try:
        from maop import __version__ as MAOP_ver
    except ImportError:
        MAOP_ver = "unknown"
    return {
        "MAOP_VERSION": MAOP_ver,
        "python": sys.version.split()[0],
        "ps_bridge_active": False,
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }


async def get_providers() -> Any:
    """Fetch providers report."""
    return await state.get_bridge().providers_report()


# ── Log helpers ────────────────────────────────────────────────────

def _read_log_tail(path, limit: int) -> list[str]:
    """Read last `limit` lines from a log file (bounded read, P2-9 fix)."""
    with open(path, encoding="utf-8", errors="replace") as fh:
        return list(collections.deque(fh, maxlen=limit))


_LOG_RE = re.compile(
    r'^(?P<ts>\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?)\s*'
    r'(?:\[(?P<level>\w+)\])?\s*'
    r'(?:\[(?P<agent>[^\]]+)\])?\s*'
    r'(?P<msg>.*)$'
)


def _validate_log_name(log_name: str) -> bool:
    """Validate log name to prevent glob injection (P0-3 fix)."""
    return bool(re.match(r"^[a-zA-Z0-9_\-\.]+$", log_name))


async def get_logs(log_name: str, limit: int) -> dict[str, Any]:
    """Read log files with bounded size (P2-9 fix).

    Handles delegations/checker specially, then falls back to globbing
    the logs directory for files matching ``log_name``.

    Raises ``ValueError`` if ``log_name`` fails validation.
    Raises ``Exception`` on other failures (router maps to HTTP 500).
    """
    if not _validate_log_name(log_name):
        raise ValueError("invalid log type: only alphanumeric, dots, hyphens, underscores allowed")
    if log_name == "delegations":
        entries = await state.get_bridge().logs_get(name="delegations", limit=limit)
        return {"logs": entries, "count": len(entries), "source": "logs/delegations.json", "type": "delegations"}
    if log_name == "checker":
        entries = await state.get_bridge().logs_get(name="checker", limit=limit)
        return {"logs": entries, "count": len(entries), "source": "logs/checker_*.log", "type": "checker"}
    result = await state.get_bridge().logs_get(name=log_name, limit=limit)
    log_dir = state.MAOP_ROOT / "logs"
    if log_dir.exists():
        for f in sorted(log_dir.glob(f"*{log_name}*"), reverse=True):
            # P2-23: glob 可能匹配目录，只处理文件
            if not f.is_file():
                continue
            try:
                # P2-9 fix: bounded read — only tail last `limit` lines
                tail = await asyncio.to_thread(_read_log_tail, f, limit)
                content = "\n".join(tail)
                if content:
                    entries = []
                    for raw in tail:
                        line = raw.rstrip('\r\n')
                        m = _LOG_RE.match(line)
                        if m:
                            entries.append({
                                "ts": m.group("ts"),
                                "level": (m.group("level") or "info").lower(),
                                "agent": m.group("agent") or "system",
                                "msg": m.group("msg") or line,
                            })
                        else:
                            entries.append({"ts": None, "level": "info", "agent": "system", "msg": line})
                    return {"logs": entries, "count": len(entries), "source": str(f), "type": log_name}
            except Exception as exc:
                logger.warning('Failed to read log file: %s', exc)
    return {"logs": result, "count": len(result), "source": f"error_log:{log_name}", "type": log_name}


async def get_logs_delegations(limit: int) -> Any:
    """Fetch delegations log."""
    return await state.get_bridge().logs_get(name="delegations", limit=limit)


async def get_logs_checker(limit: int) -> Any:
    """Fetch checker log."""
    return await state.get_bridge().logs_get(name="checker", limit=limit)


async def get_logs_analysis(log_type: str) -> dict[str, Any]:
    """Analyze log entries: count by agent/status, extract error patterns.

    ``log_type`` follows the frontend's selected log stream. For
    ``delegations``, uses exit_code→success/failure; for other types,
    uses level→success/error and agent dimension distribution.

    Raises ``Exception`` on failure so the router can map to HTTP 500.
    """
    logs = await state.get_bridge().logs_get(name=log_type or "delegations", limit=10000)
    if not isinstance(logs, list):
        logs = []
    total = len(logs)
    by_agent: dict[str, int] = {}
    by_status: dict[str, int] = {"success": 0, "failure": 0, "timeout": 0, "other": 0}
    error_patterns: dict[str, int] = {}
    is_delegations = (log_type or "delegations") == "delegations"
    for e in logs:
        if not isinstance(e, dict):
            continue
        ag = e.get("agent") or e.get("ts_agent") or "unknown"
        by_agent[ag] = by_agent.get(ag, 0) + 1
        if is_delegations:
            res = e.get("result") if isinstance(e.get("result"), dict) else {}
            ec = res.get("exit_code") if res else None
            if ec == 0:
                st = "success"
            elif ec is not None:
                st = "failure"
            else:
                st = e.get("status", "other")
        else:
            # dashboard / checker rows carry level + ts/msg fields
            lvl = str(e.get("level", "")).lower()
            st = "failure" if lvl in ("error", "critical", "fatal") else (
                "success" if lvl in ("success", "info", "ok") else "other")
            if e.get("status"):
                st = e["status"]
        if st in by_status:
            by_status[st] += 1
        else:
            by_status["other"] += 1
        if st == "failure":
            ek = str((e.get("result") or {}).get("error") or e.get("error") or e.get("msg") or "unknown")[:80]
            error_patterns[ek] = error_patterns.get(ek, 0) + 1
    return {
        "total": total,
        "by_agent": by_agent,
        "by_status": by_status,
        "error_patterns": sorted(error_patterns.items(), key=lambda x: -x[1])[:10],
        "type": log_type or "delegations",
    }


# ════════════════════════════════════════════════════════════════════════════
# Analysis endpoints (analysis.py)
# ════════════════════════════════════════════════════════════════════════════

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
        "active_agents": active_agents,
        "sparkline": sparkline,
        "observability": obs_snapshot,
    }
    return {
        "status": "ok",
        "data": data,
        "meta": _analysis_meta(iso_from, iso_to, note),
    }