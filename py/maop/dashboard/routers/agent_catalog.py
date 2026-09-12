"""MAOP Dashboard — Agent Catalog API endpoints.

暴露 ``AgentCatalog`` 注册中心 via REST API，供桌面应用调度层管理
Agent 元数据（注册、查询、搜索、健康状态、统计）。

所有端点要求 admin 角色（via ``require_admin`` 守卫）。
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from maop.core.security.middleware import require_admin
from maop.dashboard.error_handler import handle_api_errors

logger = logging.getLogger(__name__)

router = APIRouter()


# ── Pydantic 请求模型 (H-3 fix: 输入校验) ──────────────────────────
class RegisterAgentRequest(BaseModel):
    """POST /api/agent-catalog/agents 请求体。"""

    name: str = Field(..., min_length=1, max_length=256, description="Agent 唯一标识")
    display_name: str = Field(default="", max_length=256, description="展示名称")
    vendor: str = Field(default="", max_length=128, description="供应商")
    version: str = Field(default="", max_length=64, description="版本号")
    adapter_type: str = Field(default="", max_length=32, description="适配器类型")
    capabilities: list[str] = Field(default_factory=list, description="能力声明列表")
    billing_model: str = Field(default="blackbox", description="计费模式")
    auth_method: str = Field(default="none", description="授权方式")
    max_concurrent: int = Field(default=1, ge=1, description="最大并发数")
    rate_limit_per_min: int = Field(default=0, ge=0, description="每分钟速率限制")
    timeout_s: float = Field(default=30.0, gt=0, description="超时秒数")
    enabled: bool = Field(default=True, description="是否启用")


class UpdateHealthRequest(BaseModel):
    """PUT /api/agent-catalog/agents/{name}/health 请求体。"""

    healthy: bool = Field(..., description="健康状态")


# ── 单例（双重检查锁定，与 agent_proxy.py 风格对齐）──────────────────
_catalog: Any = None
_catalog_lock = threading.Lock()


def _get_catalog() -> Any:
    """惰性初始化全局 AgentCatalog 单例。"""
    global _catalog
    if _catalog is None:
        with _catalog_lock:
            if _catalog is None:
                from maop.core.agent.registry.agent_catalog import AgentCatalog
                _catalog = AgentCatalog.default()
    return _catalog


def _set_catalog(catalog: Any) -> None:
    """供测试注入自定义 catalog（隔离 DB）。"""
    global _catalog
    with _catalog_lock:
        _catalog = catalog


# ── 端点 ──────────────────────────────────────────────────────────
@router.get("/api/agent-catalog/agents")
@handle_api_errors(
    "List agents",
    error_value={"status": "error", "agents": [], "count": 0, "error": "List failed"},
)
async def api_list_agents(
    request: Request,
    enabled: bool | None = Query(None, description="过滤 enabled 状态"),
) -> dict[str, Any]:
    """列出所有已注册 Agent，可选按 enabled 过滤。"""
    require_admin(request)
    catalog = _get_catalog()
    if enabled is True:
        agents = catalog.list_enabled()
    elif enabled is False:
        agents = [a for a in catalog.list_all() if not a.enabled]
    else:
        agents = catalog.list_all()
    return {
        "status": "ok",
        "agents": [a.model_dump(mode="json") for a in agents],
        "count": len(agents),
    }


@router.get("/api/agent-catalog/agents/{name}")
@handle_api_errors(
    "Get agent",
    error_value={"status": "error", "agent": None, "error": "Not found"},
)
async def api_get_agent(name: str, request: Request) -> dict[str, Any]:
    """获取单个 Agent 详情。"""
    require_admin(request)
    catalog = _get_catalog()
    desc = catalog.get(name)
    if desc is None:
        raise HTTPException(404, f"Agent not found: {name}")
    return {"status": "ok", "agent": desc.model_dump(mode="json")}


@router.post("/api/agent-catalog/agents")
@handle_api_errors(
    "Register agent",
    error_value={"status": "error", "error": "Register failed"},
)
async def api_register_agent(
    body: RegisterAgentRequest, request: Request
) -> dict[str, Any]:
    """注册新 Agent（或更新同名 Agent）。"""
    require_admin(request)
    from maop.core.agent.registry.agent_catalog import (
        AgentCapability,
        AgentDescriptor,
        AuthMethod,
        BillingModel,
    )

    # 转换枚举
    try:
        caps = [AgentCapability(c) for c in body.capabilities]
    except ValueError as exc:
        raise HTTPException(400, f"Invalid capability: {exc}")
    try:
        bm = BillingModel(body.billing_model)
    except ValueError:
        raise HTTPException(400, f"Invalid billing_model: {body.billing_model}")
    try:
        am = AuthMethod(body.auth_method)
    except ValueError:
        raise HTTPException(400, f"Invalid auth_method: {body.auth_method}")

    desc = AgentDescriptor(
        name=body.name,
        display_name=body.display_name,
        vendor=body.vendor,
        version=body.version,
        adapter_type=body.adapter_type,
        capabilities=caps,
        billing_model=bm,
        auth_method=am,
        max_concurrent=body.max_concurrent,
        rate_limit_per_min=body.rate_limit_per_min,
        timeout_s=body.timeout_s,
        enabled=body.enabled,
    )
    catalog = _get_catalog()
    catalog.register(desc)
    logger.info("[agent_catalog_route] registered agent: %s", body.name)
    return {"status": "ok", "agent": desc.model_dump(mode="json")}


@router.delete("/api/agent-catalog/agents/{name}")
@handle_api_errors(
    "Delete agent",
    error_value={"status": "error", "error": "Delete failed"},
)
async def api_delete_agent(name: str, request: Request) -> dict[str, Any]:
    """删除（注销）一个 Agent。"""
    require_admin(request)
    catalog = _get_catalog()
    deleted = catalog.delete(name)
    if not deleted:
        raise HTTPException(404, f"Agent not found: {name}")
    return {"status": "ok", "deleted": name}


@router.put("/api/agent-catalog/agents/{name}/health")
@handle_api_errors(
    "Update agent health",
    error_value={"status": "error", "error": "Update failed"},
)
async def api_update_agent_health(
    name: str, body: UpdateHealthRequest, request: Request
) -> dict[str, Any]:
    """更新指定 Agent 的健康状态。"""
    require_admin(request)
    catalog = _get_catalog()
    desc = catalog.get(name)
    if desc is None:
        raise HTTPException(404, f"Agent not found: {name}")
    catalog.update_health(name, body.healthy)
    return {"status": "ok", "name": name, "healthy": body.healthy}


@router.get("/api/agent-catalog/search")
@handle_api_errors(
    "Search agents",
    error_value={"status": "error", "agents": [], "count": 0, "error": "Search failed"},
)
async def api_search_agents(
    request: Request,
    capability: list[str] = Query(  # noqa: B008
        default_factory=list, description="能力过滤（可重复: ?capability=a&capability=b）"
    ),
) -> dict[str, Any]:
    """按能力搜索 Agent，返回具备所有指定能力的启用 Agent。"""
    require_admin(request)
    from maop.core.agent.registry.agent_catalog import AgentCapability

    catalog = _get_catalog()
    if not capability:
        agents = catalog.list_enabled()
    else:
        try:
            caps = [AgentCapability(c) for c in capability]
        except ValueError as exc:
            raise HTTPException(400, f"Invalid capability: {exc}")
        # 逐个能力搜索取交集（search 已过滤 enabled=True）
        result_sets = [set(a.name for a in catalog.search(c)) for c in caps]
        common = set.intersection(*result_sets) if result_sets else set()
        all_agents = {a.name: a for a in catalog.list_enabled()}
        agents = [all_agents[n] for n in sorted(common)]
    return {
        "status": "ok",
        "agents": [a.model_dump(mode="json") for a in agents],
        "count": len(agents),
    }


@router.get("/api/agent-catalog/stats")
@handle_api_errors(
    "Catalog stats",
    error_value={"status": "error", "count": 0, "healthy_count": 0, "error": "Stats failed"},
)
async def api_catalog_stats(request: Request) -> dict[str, Any]:
    """返回 Agent Catalog 统计信息。"""
    require_admin(request)
    catalog = _get_catalog()
    return {
        "status": "ok",
        "count": catalog.count(),
        "healthy_count": catalog.count_healthy(),
    }