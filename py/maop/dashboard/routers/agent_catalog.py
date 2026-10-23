"""MAOP Dashboard — Agent Catalog API endpoints.

暴露 ``AgentCatalog`` 注册中心 via REST API，供桌面应用调度层管理
Agent 元数据（注册、查询、搜索、健康状态、统计）。

所有端点要求 admin 角色（via ``require_admin`` 守卫）。

业务逻辑已提取至 ``maop.dashboard.services.agent_service``（§1）。
本 router 仅保留：路由定义 / 请求参数解析 / 权限检查 /
service 调用 / 响应格式化 / 错误处理。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from maop.core.security.middleware import require_admin
from maop.dashboard.error_handler import handle_api_errors
from maop.dashboard.services import agent_service

from .agent_catalog_schemas import (
    AgentDeleteEnvelope,
    AgentEnvelope,
    AgentListEnvelope,
    CatalogStatsPayload,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# ── Pydantic 请求模型 (H-3 fix: 输入校验) ──────────────────────────
class RegisterAgentRequest(BaseModel):
    """POST /api/agent-catalog/agents 请求体。

    未知字段 422：拼错的键（如 ``capabilitie``）若被静默丢弃，注册会以默认
    值成功，调用方却以为配置已生效。
    """

    model_config = ConfigDict(extra="forbid")

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
    retry_count: int = Field(default=3, ge=0, description="重试次数")
    fallback_agents: list[str] = Field(default_factory=list, description="降级链")
    enabled: bool = Field(default=True, description="是否启用")


class UpdateAgentRequest(BaseModel):
    """PUT /api/agent-catalog/agents/{name} 请求体（部分更新）。

    只有显式提供的字段会被修改，未提供的字段保留原值。``name`` 在路径里，
    不在请求体里——此前视图把整份表单（含 ``name``）发过来，被 Pydantic
    静默丢弃；现在未知字段一律 422，视图也不再发 ``name``。
    """

    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(default=None, max_length=256)
    vendor: str | None = Field(default=None, max_length=128)
    version: str | None = Field(default=None, max_length=64)
    adapter_type: str | None = Field(default=None, max_length=32)
    capabilities: list[str] | None = None
    billing_model: str | None = None
    auth_method: str | None = None
    max_concurrent: int | None = Field(default=None, ge=1)
    rate_limit_per_min: int | None = Field(default=None, ge=0)
    timeout_s: float | None = Field(default=None, gt=0)
    retry_count: int | None = Field(default=None, ge=0)
    fallback_agents: list[str] | None = None
    enabled: bool | None = None


class UpdateHealthRequest(BaseModel):
    """PUT /api/agent-catalog/agents/{name}/health 请求体。"""

    healthy: bool = Field(..., description="健康状态")


# ── 单例转发（供测试注入，转发到 service 层）──────────────────────
def _get_catalog() -> Any:
    return agent_service._get_catalog()


def _set_catalog(catalog: Any) -> None:
    agent_service._set_catalog(catalog)


# ── 端点 ──────────────────────────────────────────────────────────
@router.get("/api/agent-catalog/agents", response_model=AgentListEnvelope)
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
    result = agent_service.list_agents(enabled)
    return {"status": "ok", **result}


@router.get("/api/agent-catalog/agents/{name}")
@handle_api_errors(
    "Get agent",
    error_value={"status": "error", "agent": None, "error": "Not found"},
)
async def api_get_agent(name: str, request: Request) -> dict[str, Any]:
    """获取单个 Agent 详情。"""
    require_admin(request)
    agent = agent_service.get_agent(name)
    if agent is None:
        raise HTTPException(404, f"Agent not found: {name}")
    return {"status": "ok", "agent": agent}


@router.post("/api/agent-catalog/agents", response_model=AgentEnvelope)
@handle_api_errors(
    "Register agent",
    error_value={"status": "error", "error": "Register failed"},
)
async def api_register_agent(
    body: RegisterAgentRequest, request: Request
) -> dict[str, Any]:
    """注册新 Agent（或更新同名 Agent）。"""
    require_admin(request)
    try:
        agent = agent_service.register_agent(
            name=body.name,
            display_name=body.display_name,
            vendor=body.vendor,
            version=body.version,
            adapter_type=body.adapter_type,
            capabilities=body.capabilities,
            billing_model=body.billing_model,
            auth_method=body.auth_method,
            max_concurrent=body.max_concurrent,
            rate_limit_per_min=body.rate_limit_per_min,
            timeout_s=body.timeout_s,
            retry_count=body.retry_count,
            fallback_agents=body.fallback_agents,
            enabled=body.enabled,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"status": "ok", "agent": agent}


@router.put("/api/agent-catalog/agents/{name}", response_model=AgentEnvelope)
@handle_api_errors(
    "Update agent",
    error_value={"status": "error", "agent": None, "error": "Update failed"},
)
async def api_update_agent(
    name: str, body: UpdateAgentRequest, request: Request
) -> dict[str, Any]:
    """部分更新 Agent 元数据；未提供的字段保留原值。"""
    require_admin(request)
    changes = body.model_dump(exclude_unset=True)
    try:
        agent = agent_service.update_agent(name, changes)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    if agent is None:
        raise HTTPException(404, f"Agent not found: {name}")
    return {"status": "ok", "agent": agent}


@router.delete("/api/agent-catalog/agents/{name}", response_model=AgentDeleteEnvelope)
@handle_api_errors(
    "Delete agent",
    error_value={"status": "error", "error": "Delete failed"},
)
async def api_delete_agent(name: str, request: Request) -> dict[str, Any]:
    """删除（注销）一个 Agent。"""
    require_admin(request)
    deleted = agent_service.delete_agent(name)
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
    found = agent_service.update_agent_health(name, body.healthy)
    if not found:
        raise HTTPException(404, f"Agent not found: {name}")
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
    try:
        result = agent_service.search_agents(capability)
    except ValueError as exc:
        raise HTTPException(400, f"Invalid capability: {exc}")
    return {"status": "ok", **result}


@router.get("/api/agent-catalog/stats", response_model=CatalogStatsPayload)
@handle_api_errors(
    "Catalog stats",
    error_value={
        "status": "error",
        "count": 0,
        "healthy_count": 0,
        "enabled_count": 0,
        "capability_count": 0,
        "error": "Stats failed",
    },
)
async def api_catalog_stats(request: Request) -> dict[str, Any]:
    """返回 Agent Catalog 统计信息。"""
    require_admin(request)
    result = agent_service.catalog_stats()
    return {"status": "ok", **result}
