"""Data query/read endpoints for MAOP Dashboard.

Aggregates all read-only data endpoints organized by domain:
  - Overview: report, agents, timeseries, metrics, live, failures, chain, optimizer, batch
  - Graph: graph/stats, graph/nodes, graph/edges, graph/neighbors
  - Knowledge: vector/*, wiki/stats, prompts, coordination, teams, skills
  - Tools: tools/stats, guardrails, sandbox/list, human/pending, mcp/*
  - System: versions, providers, logs/*

Business logic lives in :mod:`maop.dashboard.services.data_service`; this
router only does request parsing, auth, service dispatch, and response
formatting.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from maop.core.security.middleware import require_admin
from maop.dashboard.error_handler import handle_api_errors  # 批次3A: 统一异常处理装饰器
from maop.dashboard.services import data_service

# Re-export shared state for backward compatibility (tests / other routers
# may import ``get_bridge`` / ``MAOP_ROOT`` from this module).
from .state import MAOP_ROOT, get_bridge  # noqa: F401

logger = logging.getLogger(__name__)

router = APIRouter()


def _request_tenant_id(request: Request) -> str:
    tid = getattr(request.state, "tenant_id", "")
    return tid or ""


# ── Overview ────────────────────────────────────────────────────────────

@router.get("/api/report")
@handle_api_errors("Report", error_value={"status": "error", "error": "Report unavailable"})
async def api_report(request: Request, hours: int = Query(48, ge=1, le=720)) -> Any:
    require_admin(request)
    return await data_service.get_report(hours=hours, tenant_id=_request_tenant_id(request))


@router.get("/api/agents/stats")
@handle_api_errors("Agents stats", error_value={"agents": [], "count": 0, "error": "Agents stats unavailable"})
async def api_agents_stats(request: Request) -> dict[str, Any]:
    require_admin(request)
    return await data_service.get_agents_stats(tenant_id=_request_tenant_id(request))


@router.get("/api/timeseries")
@handle_api_errors("Timeseries", error_value={"status": "error", "error": "Timeseries unavailable"})
async def api_timeseries(request: Request) -> Any:
    require_admin(request)
    return await data_service.get_timeseries(tenant_id=_request_tenant_id(request))


@router.get("/api/metrics")
@handle_api_errors("Metrics", error_value={"status": "error", "error": "Metrics unavailable"})
async def api_metrics(request: Request) -> dict[str, Any]:
    """Real-time metrics from LoadBalancer, TimeSeries, and CircuitBreaker."""
    require_admin(request)
    return await data_service.get_metrics(tenant_id=_request_tenant_id(request))


@router.get("/api/live")
@handle_api_errors("Live", error_value={"status": "error", "error": "Live data unavailable"})
async def api_live(request: Request) -> Any:
    require_admin(request)
    return await data_service.get_live(tenant_id=_request_tenant_id(request))


@router.get("/api/snapshot")
@handle_api_errors("Snapshot", error_value={"status": "error", "error": "Snapshot unavailable"})
async def api_snapshot(request: Request) -> Any:
    """F-P0-2 fix: Aggregate snapshot for Overview.vue health metrics."""
    require_admin(request)
    return await data_service.get_snapshot(tenant_id=_request_tenant_id(request))


@router.get("/api/failures")
@handle_api_errors("Failures", error_value={"status": "error", "error": "Failures unavailable"})
async def api_failures(request: Request) -> Any:
    require_admin(request)
    return await data_service.get_failures(tenant_id=_request_tenant_id(request))


@router.get("/api/chain")
@handle_api_errors("Chain", error_value={"status": "error", "error": "Chain unavailable"})
async def api_chain(request: Request) -> Any:
    require_admin(request)
    return await data_service.get_chain(tenant_id=_request_tenant_id(request))


@router.get("/api/optimizer")
@handle_api_errors("Optimizer", error_value={"status": "error", "error": "Optimizer report unavailable"})
async def api_optimizer(request: Request) -> dict[str, Any]:
    require_admin(request)
    try:
        return await data_service.get_optimizer(tenant_id=_request_tenant_id(request))
    except Exception as exc:
        logger.error('Optimizer report failed: %s', exc)
        raise HTTPException(status_code=500, detail="Optimizer report unavailable")



# ── Graph ───────────────────────────────────────────────────────────────

@router.get("/api/graph/stats")
@handle_api_errors("Graph stats", error_value={"nodes": 0, "edges": 0, "status": "error", "error": "Graph stats unavailable"})
async def api_graph_stats(request: Request) -> dict[str, Any]:
    require_admin(request)
    try:
        return await data_service.get_graph_stats()
    except Exception as exc:
        logger.error('Graph stats failed: %s', exc)
        raise HTTPException(status_code=500, detail="Graph stats unavailable")


@router.get("/api/graph/nodes")
@handle_api_errors("Graph nodes", error_value={"nodes": [], "error": "Graph nodes unavailable"})
async def api_graph_nodes(request: Request) -> Any:
    require_admin(request)
    return await data_service.get_graph_nodes()


@router.get("/api/graph/edges")
@handle_api_errors("Graph edges", error_value={"edges": [], "error": "Graph edges unavailable"})
async def api_graph_edges(request: Request) -> Any:
    require_admin(request)
    return await data_service.get_graph_edges()


@router.get("/api/graph/neighbors")
@handle_api_errors("Graph neighbors", error_value={"neighbors": [], "count": 0, "error": "Graph neighbors unavailable"})
async def api_graph_neighbors(request: Request, node: str = Query(...)) -> dict[str, Any]:
    require_admin(request)
    return await data_service.get_graph_neighbors(node=node)


# ── Knowledge ───────────────────────────────────────────────────────────

@router.get("/api/vector/stats")
@handle_api_errors("Vector stats", error_value={"status": "error", "error": "Vector stats unavailable"})
async def api_vector_stats(request: Request) -> Any:
    require_admin(request)
    return await data_service.get_vector_stats()


@router.get("/api/vector/list")
@handle_api_errors("Vector list", error_value={"vectors": [], "count": 0, "total": 0, "status": "error", "error": "Vector list unavailable"})
async def api_vector_list(
    request: Request,
    limit: int = Query(1000, ge=1, le=10000, description="最大返回条数 (1..10000)"),
    offset: int = Query(0, ge=0, description="跳过条数 (>=0)"),
) -> dict[str, Any]:
    """列出已索引向量（分页）。

    P2-P3 fix (M4): 暴露 limit/offset 分页参数，避免全量加载。
    - limit: 1..10000，默认 1000
    - offset: >=0，默认 0
    - 返回 total 字段，便于前端分页控件计算总页数
    """
    require_admin(request)
    try:
        return await data_service.get_vector_list(limit=limit, offset=offset)
    except Exception as exc:
        logger.error('Vector list failed: %s', exc)
        raise HTTPException(status_code=500, detail="Vector list unavailable")


@router.get("/api/vector/search")
@handle_api_errors("Vector search", error_value={"query": "", "results": [], "count": 0, "status": "error", "error": "Vector search unavailable"})
async def api_vector_search(request: Request, q: str = Query(...), k: int = Query(5, alias="topk", ge=1, le=1000)) -> dict[str, Any]:
    require_admin(request)
    try:
        return await data_service.get_vector_search(q=q, k=k)
    except Exception:
        try:
            return await data_service.get_vector_search_fallback(q=q, k=k)
        except Exception as exc:
            logger.error('Vector search fallback failed: %s', exc)
            raise HTTPException(status_code=500, detail="Vector search unavailable")


@router.get("/api/wiki/stats")
@handle_api_errors("Wiki stats", error_value={"status": "error", "error": "Wiki stats unavailable"})
async def api_wiki_stats(request: Request) -> dict[str, Any]:
    require_admin(request)
    return await data_service.get_wiki_stats()


@router.get("/api/prompts")
@handle_api_errors("Prompts", error_value={"prompts": [], "status": "error", "error": "Prompts list unavailable"})
async def api_prompts(request: Request) -> dict[str, Any]:
    require_admin(request)
    try:
        return await data_service.get_prompts()
    except Exception as exc:
        logger.error('Prompts list failed: %s', exc)
        raise HTTPException(status_code=500, detail="Prompts list unavailable")


@router.get("/api/coordination")
@handle_api_errors("Coordination", error_value={"status": "error", "error": "Coordination report unavailable"})
async def api_coordination(request: Request) -> Any:
    require_admin(request)
    return await data_service.get_coordination()


@router.get("/api/teams")
@handle_api_errors("Teams", error_value={"status": "error", "error": "Teams unavailable"})
async def api_teams(request: Request) -> Any:
    require_admin(request)
    return await data_service.get_teams()


@router.get("/api/skills")
@handle_api_errors("Skills", error_value={"skills": [], "count": 0, "status": "error", "error": "Skills list unavailable"})
async def api_skills(request: Request) -> dict[str, Any]:
    require_admin(request)
    try:
        return await data_service.get_skills()
    except Exception as exc:
        logger.error('Skills list failed: %s', exc)
        raise HTTPException(status_code=500, detail="Skills list unavailable")


# ── Tools ───────────────────────────────────────────────────────────────

@router.get("/api/tools/stats")
@handle_api_errors("Tools stats", error_value={"status": "error", "error": "Tools stats unavailable"})
async def api_tools_stats(request: Request) -> Any:
    require_admin(request)
    return await data_service.get_tools_stats()


@router.get("/api/guardrails")
@handle_api_errors("Guardrails", error_value={"status": "error", "error": "Guardrails report unavailable"})
async def api_guardrails(request: Request) -> Any:
    require_admin(request)
    return await data_service.get_guardrails()


@router.get("/api/sandbox/list")
@handle_api_errors("Sandbox list", error_value={"status": "error", "error": "Sandbox list unavailable"})
async def api_sandbox_list(request: Request) -> Any:
    require_admin(request)
    return await data_service.get_sandbox_list()


@router.get("/api/human/pending")
@handle_api_errors("Human pending", error_value={"status": "error", "error": "Human pending unavailable"})
async def api_human_pending(request: Request) -> Any:
    require_admin(request)
    return await data_service.get_human_pending()


@router.get("/api/mcp/servers")
@handle_api_errors("MCP servers", error_value={"status": "error", "error": "MCP servers unavailable"})
async def api_mcp_servers(request: Request) -> Any:
    require_admin(request)
    return await data_service.get_mcp_servers()


@router.get("/api/mcp/tools")
@handle_api_errors("MCP tools", error_value={"status": "error", "error": "MCP tools unavailable"})
async def api_mcp_tools(request: Request) -> Any:
    require_admin(request)
    return await data_service.get_mcp_tools()


@router.get("/api/mcp")
@handle_api_errors("MCP combined", error_value={"servers": [], "tools": [], "server_count": 0, "tool_count": 0, "error": "MCP combined unavailable"})
async def api_mcp_combined(request: Request) -> dict[str, Any]:
    require_admin(request)
    return await data_service.get_mcp_combined()


# ── System ──────────────────────────────────────────────────────────────

@router.get("/api/versions")
@handle_api_errors("Versions", error_value={"status": "error", "error": "Versions unavailable"})
async def api_versions(request: Request) -> dict[str, Any]:
    require_admin(request)
    return await data_service.get_versions()


@router.get("/api/providers")
@handle_api_errors("Providers", error_value={"status": "error", "error": "Providers report unavailable"})
async def api_providers(request: Request) -> Any:
    require_admin(request)
    return await data_service.get_providers()


@router.get("/api/logs")
@handle_api_errors("Logs", error_value={"logs": [], "count": 0, "status": "error", "error": "Logs unavailable"})
async def api_logs(request: Request, type: str = "", limit: int = Query(500, ge=1, le=5000)) -> Any:
    """Read log files with bounded size (P2-9 fix: prevents unbounded read_text).

    Args:
        type: log type name (dashboard, delegations, checker, etc.)
        limit: max number of lines to return (default 500, max 5000)
    """
    # 批次3A: 日志端点添加 require_admin 鉴权，防止未授权用户读取系统日志。
    require_admin(request)
    log_name = type if type and type != "all" else "dashboard"
    # P0-3 fix: validate log_name to prevent glob injection (e.g. '*' enumerating all files)
    try:
        return await data_service.get_logs(log_name=log_name, limit=limit)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="invalid log type: only alphanumeric, dots, hyphens, underscores allowed",
        )
    except Exception as exc:
        logger.error('Logs read failed: %s', exc)
        raise HTTPException(status_code=500, detail="Logs unavailable")


@router.get("/api/logs/delegations")
@handle_api_errors("Logs delegations", error_value={"status": "error", "error": "Delegations logs unavailable"})
async def api_logs_delegations(request: Request, limit: int = Query(500, ge=1, le=5000)) -> Any:
    # 批次3A: 日志端点添加 require_admin 鉴权，防止未授权用户读取系统日志。
    require_admin(request)
    return await data_service.get_logs_delegations(limit=limit)


@router.get("/api/logs/checker")
@handle_api_errors("Logs checker", error_value={"status": "error", "error": "Checker logs unavailable"})
async def api_logs_checker(request: Request, limit: int = Query(500, ge=1, le=5000)) -> Any:
    # 批次3A: 日志端点添加 require_admin 鉴权，防止未授权用户读取系统日志。
    require_admin(request)
    return await data_service.get_logs_checker(limit=limit)


@router.get("/api/logs/analysis")
@handle_api_errors("Logs analysis", error_value={"total": 0, "status": "error", "error": "Logs analysis unavailable"})
async def api_logs_analysis(request: Request, type: str = Query("delegations", description="分析哪一路日志流（与日志页所选类型一致）", pattern=r"^[a-zA-Z0-9_\-\.]+$")) -> dict[str, Any]:
    # 批次3A: 日志端点添加 require_admin 鉴权，防止未授权用户读取系统日志。
    require_admin(request)
    # 一号用户实测修复（2026-08-31）：原实现硬编码 name="delegations" ——
    # 用户看 dashboard/checker 日志时，"日志分析"卡片统计的却是另一路日志
    # 的数据，与页面内容完全脱节。改为跟随 ?type=（前端传当前所选类型），
    # delegations 走既有结果语义（exit_code→success/failure），其他类型按
    # 行结构统计（level→success/error、agent 维度分布）。
    try:
        return await data_service.get_logs_analysis(log_type=type)
    except Exception as exc:
        logger.error('Logs analysis failed: %s', exc)
        raise HTTPException(status_code=500, detail="Logs analysis unavailable")
