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

File-size ratchet (2026-10-27): the two largest self-contained blocks —
raw log handling and the six deep-analysis endpoints — moved to
``maop.dashboard.services.data`` (submodules ``logs`` / ``analysis``).
Every public name is forwarded below, so this module's import surface and
``data_service.<name>`` monkeypatching are unchanged. The helpers those
implementations call are resolved **in the submodule**: patching
``data_service._get_cost_tracker`` no longer reaches the analysis code —
patch ``maop.dashboard.services.data.analysis._get_cost_tracker`` instead.
"""

from __future__ import annotations

import logging
import sys
import time
from pathlib import Path  # noqa: F401
from typing import Any

from maop.core.backends.db_utils import get_db_path

# Shared runtime state — accessed via the ``state`` module so that tests
# which monkeypatch ``state.MAOP_ROOT`` / ``state.get_bridge`` etc. take
# effect here too.
from maop.dashboard.routers import state

# Re-export analysis helpers for backward compatibility (analysis.py
# originally imported them from analysis_helpers; now both the router
# and this service can import from here). The moved implementations
# import them directly in ``services/data/analysis.py``; these bindings
# are kept so ``data_service.<helper>`` stays valid for callers/tests.
from maop.dashboard.routers.analysis_helpers import (  # noqa: F401
    _bucket_index,
    _bucket_seconds,
    _cpu_series,
    _get_audit_events,
    _get_cache_stats,
    _get_cost_tracker,
    _get_db_pool_stats,
    _iso_to_sqlite_str,
    _parse_date_range,
    _safe_psutil,
)

# Split-out domains (file-size ratchet) — forwarded by name so the
# module's public surface is unchanged.
from maop.dashboard.services.data import (  # noqa: F401
    _LOG_RE,
    _analysis_meta,
    _read_log_tail,
    _validate_log_name,
    analysis_agent_efficiency,
    analysis_cost_breakdown,
    analysis_performance_bottlenecks,
    analysis_resource_utilization,
    analysis_summary,
    analysis_task_trends,
    get_logs,
    get_logs_analysis,
    get_logs_checker,
    get_logs_delegations,
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
                        except OSError as exc:
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


#: Keys whose *values* are credentials wherever they appear in a server row.
_SECRET_ROW_KEYS = frozenset({"token", "api_key", "password", "secret", "auth_token"})
#: ``env`` entries are only masked when the variable name looks like a secret.
_SECRET_ENV_HINTS = ("key", "token", "secret", "password", "auth", "credential")


def _mask_mcp_row(row: dict[str, Any]) -> dict[str, Any]:
    """Redact credentials from one raw ``mcp_servers.yaml`` entry.

    The bridge hands back user-authored YAML, so ``headers`` / ``env`` may carry
    live bearer tokens. ``GET /api/mcp/servers/{id}`` already refuses to return
    header values (only ``auth_type`` / ``has_auth``); the combined endpoint has
    no reason to be the exception. Keys are kept so the UI can still show that
    something is configured — same convention as notification channel configs.
    """
    if not isinstance(row, dict):
        return row
    out = dict(row)
    headers = out.get("headers")
    if isinstance(headers, dict):
        out["headers"] = {name: "***" for name in headers}
    env = out.get("env")
    if isinstance(env, dict):
        out["env"] = {
            name: ("***" if any(h in name.lower() for h in _SECRET_ENV_HINTS) else value)
            for name, value in env.items()
        }
    for name in list(out):
        if isinstance(name, str) and name.lower() in _SECRET_ROW_KEYS:
            out[name] = "***"
    return out


async def get_mcp_combined() -> dict[str, Any]:
    """Fetch combined MCP servers + tools (credentials redacted)."""
    servers = await state.get_bridge().mcp_servers()
    tools = await state.get_bridge().mcp_tools()
    masked = [_mask_mcp_row(s) for s in servers]
    return {"servers": masked, "tools": tools, "server_count": len(masked), "tool_count": len(tools)}


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