"""Data query/read endpoints for MAOP Dashboard.

Aggregates all read-only data endpoints organized by domain:
  - Overview: report, agents, timeseries, metrics, live, failures, chain, optimizer, batch
  - Graph: graph/stats, graph/nodes, graph/edges, graph/neighbors
  - Knowledge: vector/*, wiki/stats, prompts, coordination, teams, skills
  - Tools: tools/stats, guardrails, sandbox/list, human/pending, mcp/*
  - System: versions, providers, logs/*
"""

from __future__ import annotations

import logging
import sys
import time
from typing import Any

from fastapi import APIRouter, Query, Request

from maop.core.middleware import require_admin

from .state import MAOP_ROOT, get_bridge

logger = logging.getLogger(__name__)

router = APIRouter()


def _request_tenant_id(request: Request) -> str:
    tid = getattr(request.state, "tenant_id", "")
    return tid or ""


def _tenant_filter(data: Any, tenant_id: str) -> Any:
    if not tenant_id:
        return data
    if isinstance(data, dict):
        return {k: _tenant_filter(v, tenant_id) for k, v in data.items()}
    if isinstance(data, list):
        return [
            _tenant_filter(it, tenant_id) for it in data
            if not (isinstance(it, dict) and it.get("tenant_id") and it.get("tenant_id") != tenant_id)
        ]
    return data


# ── Overview ────────────────────────────────────────────────────────────

@router.get("/api/report")
async def api_report(request: Request, hours: int = Query(48, ge=1, le=720)) -> Any:
    require_admin(request)
    return _tenant_filter(await get_bridge().report(hours=hours), _request_tenant_id(request))


@router.get("/api/agents/stats")
async def api_agents_stats(request: Request) -> dict[str, Any]:
    agents = await get_bridge().agent_stats()
    return _tenant_filter({"agents": agents, "count": len(agents)}, _request_tenant_id(request))


@router.get("/api/timeseries")
async def api_timeseries(request: Request) -> Any:
    return _tenant_filter(await get_bridge().timeseries(hours=168), _request_tenant_id(request))


@router.get("/api/metrics")
async def api_metrics(request: Request) -> dict[str, Any]:
    require_admin(request)
    """Real-time metrics from LoadBalancer, TimeSeries, and CircuitBreaker."""
    result: dict[str, Any] = {}
    try:
        from maop.core.load_balancer import get_load_balancer
        lb = get_load_balancer()
        stats = lb.stats()
        result["load_balancer"] = stats.model_dump()
    except Exception as exc:
        logger.error('Load balancer stats failed: %s', exc)
        result["load_balancer"] = {"status": "error", "error": "Load balancer stats unavailable"}
    try:
        from maop.core.timeseries import TimeSeriesStore
        ts = TimeSeriesStore(db_path=MAOP_ROOT / "data" / "timeseries.db")
        recent = ts.read_recent(hours=24)
        result["timeseries"] = recent if isinstance(recent, list) else []
    except Exception as exc:
        logger.error('Timeseries read failed: %s', exc)
        result["timeseries"] = {"status": "error", "error": "Timeseries data unavailable"}
    try:
        from maop.core.circuit_breaker import CircuitBreaker
        cb = CircuitBreaker(MAOP_ROOT / "data" / "maop.db")
        result["circuit_breaker"] = {
            name: {"state": entry.state.value, "failures": entry.failures}
            for name, entry in cb.all_states().items()
        }
    except Exception as exc:
        logger.error('Circuit breaker stats failed: %s', exc)
        result["circuit_breaker"] = {"status": "error", "error": "Circuit breaker stats unavailable"}
    try:
        from maop.core.cache import get_cache
        c = get_cache(name="metrics")
        result["cache"] = {"hits": getattr(c, "hits", 0), "misses": getattr(c, "misses", 0)}
    except Exception as exc:
        logger.error('Cache stats failed: %s', exc)
        result["cache"] = {"status": "error", "error": "Cache stats unavailable"}
    return _tenant_filter(result, _request_tenant_id(request))


@router.get("/api/live")
async def api_live(request: Request) -> Any:
    require_admin(request)
    return _tenant_filter(await get_bridge().live(), _request_tenant_id(request))


@router.get("/api/snapshot")
async def api_snapshot(request: Request) -> Any:
    """F-P0-2 fix: Aggregate snapshot for Overview.vue health metrics."""
    require_admin(request)
    return _tenant_filter(await get_bridge().snapshot(), _request_tenant_id(request))


@router.get("/api/failures")
async def api_failures(request: Request) -> Any:
    return _tenant_filter(await get_bridge().failures(), _request_tenant_id(request))


@router.get("/api/chain")
async def api_chain(request: Request) -> Any:
    return _tenant_filter(await get_bridge().chain(), _request_tenant_id(request))


@router.get("/api/optimizer")
async def api_optimizer(request: Request) -> dict[str, Any]:
    try:
        bridge = get_bridge()
        report = await bridge.report()
        cache_stats = {}
        try:
            from maop.core.cache import get_cache
            c = get_cache(name="optimizer")
            cache_stats = {"hits": c.hits if hasattr(c, "hits") else 0, "misses": c.misses if hasattr(c, "misses") else 0}
        except Exception as exc:
            logger.warning('Failed to get cache stats: %s', exc)
        return _tenant_filter({"report": report, "cache": cache_stats,
                "recommendations": ["Enable parallel execution for independent subtasks",
                                    "Increase cache TTL for stable results",
                                    "Use LoadBalancer for multi-agent tasks"]}, _request_tenant_id(request))
    except Exception as exc:
        logger.error('Optimizer report failed: %s', exc)
        return {"status": "error", "error": "Optimizer report unavailable"}


@router.get("/api/batch", deprecated=True, description="Deprecated: use individual /api/* endpoints. Frontend does not call this.")
async def api_batch(request: Request, keys: str = Query("")) -> dict[str, Any]:
    if not keys:
        return {}
    requested = [k.strip() for k in keys.split(",") if k.strip()]
    bridge = get_bridge()
    dispatch = {
        "report": lambda: bridge.report(hours=48),
        "live": lambda: bridge.live(),
        "failures": lambda: bridge.failures(),
        "timeseries": lambda: bridge.timeseries(hours=168),
        "versions": lambda: bridge.versions_check(),
        "skills": lambda: bridge.skills_list(),
        "wiki": lambda: bridge.memory_stats(),
        "prompts": lambda: bridge.prompts_list(),
        "teams": lambda: bridge.coordination_report(),
        "guardrails": lambda: bridge.guardrail_report(),
        "providers": lambda: bridge.providers_report(),
    }
    result = {}
    for key in requested:
        if key in dispatch:
            result[key] = await dispatch[key]()
    return _tenant_filter(result, _request_tenant_id(request))


# ── Graph ───────────────────────────────────────────────────────────────

@router.get("/api/graph/stats")
async def api_graph_stats() -> dict[str, Any]:
    try:
        bridge = get_bridge()
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
        return {"nodes": node_count, "edges": edge_count,
                "avg_degree": avg_degree, "max_degree": max(degrees.values()) if degrees else 0}
    except Exception as exc:
        logger.error('Graph stats failed: %s', exc)
        return {"nodes": 0, "edges": 0, "status": "error", "error": "Graph stats unavailable"}


@router.get("/api/graph/nodes")
async def api_graph_nodes() -> Any:
    return await get_bridge().graph_nodes()


@router.get("/api/graph/edges")
async def api_graph_edges() -> Any:
    return await get_bridge().graph_edges()


@router.get("/api/graph/neighbors")
async def api_graph_neighbors(node: str = Query(...)) -> dict[str, Any]:
    bridge = get_bridge()
    edges = await bridge.graph_edges()
    neighbors = [e for e in edges if isinstance(e, dict) and (e.get("source") == node or e.get("target") == node)]
    return {"node": node, "neighbors": neighbors, "count": len(neighbors)}


# ── Knowledge ───────────────────────────────────────────────────────────

@router.get("/api/vector/stats")
async def api_vector_stats() -> Any:
    return await get_bridge().memory_stats()


@router.get("/api/vector/list")
async def api_vector_list() -> dict[str, Any]:
    try:
        from maop.core.vector import VectorStore
        vs = VectorStore(db_path=str(MAOP_ROOT / "data" / "vectors.db"))
        items = vs.list_all() if hasattr(vs, "list_all") else []
        return {"vectors": items, "count": len(items)}
    except Exception as exc:
        logger.error('Vector list failed: %s', exc)
        return {"vectors": [], "count": 0, "status": "error", "error": "Vector list unavailable"}


@router.get("/api/vector/search")
async def api_vector_search(q: str = Query(...), k: int = Query(5, alias="topk")) -> dict[str, Any]:
    try:
        from maop.core.vector import VectorStore
        vs = VectorStore(db_path=str(MAOP_ROOT / "data" / "vectors.db"))
        raw_results = vs.search(query=q, top=k)
        results = [r.model_dump() if hasattr(r, 'model_dump') else (r if isinstance(r, dict) else {"content": str(r)}) for r in raw_results]
        return {"query": q, "results": results, "count": len(results)}
    except Exception:
        try:
            from maop.memory.store import MemoryStore
            store = MemoryStore(root_dir=str(MAOP_ROOT))
            fallback_results: Any = store.search(query=q, top=k)
            results = [r.model_dump() if hasattr(r, 'model_dump') else (r if isinstance(r, dict) else {"content": str(r)}) for r in fallback_results]
            return {"query": q, "results": results, "count": len(results), "fallback": "memory"}
        except Exception as exc:
            logger.error('Vector search fallback failed: %s', exc)
            return {"query": q, "results": [], "count": 0, "status": "error", "error": "Vector search unavailable"}


@router.get("/api/wiki/stats")
async def api_wiki_stats() -> dict[str, Any]:
    base = await get_bridge().memory_stats()
    try:
        from maop.core.vector import VectorStore
        vs = VectorStore(db_path=str(MAOP_ROOT / "data" / "vectors.db"))
        base["vector_count"] = vs.count() if hasattr(vs, "count") else 0
    except Exception as exc:
        logger.warning("Failed to get vector count: %s", exc)
        base["vector_count"] = 0
    return base


@router.get("/api/prompts")
async def api_prompts() -> dict[str, Any]:
    try:
        result = await get_bridge().prompts_list()
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
            prompts_dir = MAOP_ROOT / "prompts"
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
    except Exception as exc:
        logger.error('Prompts list failed: %s', exc)
        return {"prompts": [], "status": "error", "error": "Prompts list unavailable"}


@router.get("/api/coordination")
async def api_coordination() -> Any:
    return await get_bridge().coordination_report()


@router.get("/api/teams")
async def api_teams() -> Any:
    try:
        from maop.config.loader import ConfigLoader
        cfg = ConfigLoader(project_root=str(MAOP_ROOT)).load()
        teams: dict[str, list[str]] = {}
        for name, ad in cfg.agents.items():
            group = getattr(ad, "group", "default")
            teams.setdefault(group, []).append(name)
        return [{"team": k, "agents": v, "count": len(v)} for k, v in teams.items()]
    except Exception as exc:
        logger.warning("Teams from config failed: %s", exc)
        return (await get_bridge().coordination_report()).get("teams", [])


@router.get("/api/skills")
async def api_skills() -> dict[str, Any]:
    try:
        result = await get_bridge().skills_list()
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
            skills_dir = MAOP_ROOT / "skills"
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
    except Exception as exc:
        logger.error('Skills list failed: %s', exc)
        return {"skills": [], "count": 0, "status": "error", "error": "Skills list unavailable"}


# ── Tools ───────────────────────────────────────────────────────────────

@router.get("/api/tools/stats")
async def api_tools_stats() -> Any:
    return await get_bridge().tools_stats()


@router.get("/api/guardrails")
async def api_guardrails() -> Any:
    return await get_bridge().guardrail_report()


@router.get("/api/sandbox/list")
async def api_sandbox_list() -> Any:
    return await get_bridge().sandbox_list()


@router.get("/api/human/pending")
async def api_human_pending() -> Any:
    return await get_bridge().human_pending()


@router.get("/api/mcp/servers")
async def api_mcp_servers() -> Any:
    return await get_bridge().mcp_servers()


@router.get("/api/mcp/tools")
async def api_mcp_tools() -> Any:
    return await get_bridge().mcp_tools()


@router.get("/api/mcp")
async def api_mcp_combined() -> dict[str, Any]:
    servers = await get_bridge().mcp_servers()
    tools = await get_bridge().mcp_tools()
    return {"servers": servers, "tools": tools, "server_count": len(servers), "tool_count": len(tools)}


# ── System ──────────────────────────────────────────────────────────────

@router.get("/api/versions")
async def api_versions() -> dict[str, Any]:
    try:
        from maop import __version__ as MAOP_ver
    except ImportError:
        MAOP_ver = "unknown"
    return {"MAOP_VERSION": MAOP_ver, "python": sys.version.split()[0],
            "ps_bridge_active": False, "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S")}


@router.get("/api/providers")
async def api_providers() -> Any:
    return await get_bridge().providers_report()


@router.get("/api/logs")
async def api_logs(type: str = "", limit: int = Query(500, ge=1, le=5000)) -> Any:
    """Read log files with bounded size (P2-9 fix: prevents unbounded read_text).

    Args:
        type: log type name (dashboard, delegations, checker, etc.)
        limit: max number of lines to return (default 500, max 5000)
    """
    log_name = type if type and type != "all" else "dashboard"
    if log_name == "delegations":
        return await get_bridge().logs_get(name="delegations", limit=limit)
    elif log_name == "checker":
        return await get_bridge().logs_get(name="checker", limit=limit)
    result = await get_bridge().logs_get(name=log_name, limit=limit)
    log_dir = MAOP_ROOT / "logs"
    if log_dir.exists():
        for f in sorted(log_dir.glob(f"*{log_name}*"), reverse=True):
            try:
                # P2-9 fix: bounded read — only tail last `limit` lines
                import collections
                with open(f, encoding="utf-8", errors="replace") as fh:
                    tail = collections.deque(fh, maxlen=limit)
                content = "\n".join(tail)
                if content:
                    import re
                    entries = []
                    _log_re = re.compile(
                        r'^(?P<ts>\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?)\s*'
                        r'(?:\[(?P<level>\w+)\])?\s*'
                        r'(?:\[(?P<agent>[^\]]+)\])?\s*'
                        r'(?P<msg>.*)$'
                    )
                    for raw in tail:
                        line = raw.rstrip('\r\n')
                        m = _log_re.match(line)
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
    return result


@router.get("/api/logs/delegations")
async def api_logs_delegations(limit: int = Query(500, ge=1, le=5000)) -> Any:
    return await get_bridge().logs_get(name="delegations", limit=limit)


@router.get("/api/logs/checker")
async def api_logs_checker(limit: int = Query(500, ge=1, le=5000)) -> Any:
    return await get_bridge().logs_get(name="checker", limit=limit)


@router.get("/api/logs/analysis")
async def api_logs_analysis() -> dict[str, Any]:
    try:
        logs = await get_bridge().logs_get(name="delegations")
        if not isinstance(logs, list):
            logs = []
        total = len(logs)
        by_agent: dict[str, int] = {}
        by_status: dict[str, int] = {"success": 0, "failure": 0, "timeout": 0, "other": 0}
        error_patterns: dict[str, int] = {}
        for e in logs:
            if not isinstance(e, dict):
                continue
            ag = e.get("agent", "unknown")
            by_agent[ag] = by_agent.get(ag, 0) + 1
            st = e.get("status", "other")
            if st in by_status:
                by_status[st] += 1
            else:
                by_status["other"] += 1
            if st == "failure":
                ek = str(e.get("error", "unknown"))[:80]
                error_patterns[ek] = error_patterns.get(ek, 0) + 1
        return {"total": total, "by_agent": by_agent, "by_status": by_status,
                "error_patterns": sorted(error_patterns.items(), key=lambda x: -x[1])[:10]}
    except Exception as exc:
        logger.error('Logs analysis failed: %s', exc)
        return {"total": 0, "status": "error", "error": "Logs analysis unavailable"}
