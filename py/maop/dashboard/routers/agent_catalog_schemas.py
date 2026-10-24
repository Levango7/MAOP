"""MAOP Dashboard — Agent Catalog API 的响应模型.

Agent 行直接复用领域模型 ``AgentDescriptor``：``agent_service.list_agents()``
返回的就是它的 ``model_dump(mode="json")``，另写一份 23 字段的镜像只会制造
第二份真相（加字段时漏改一处就静默丢数据）。

``CatalogStatsPayload`` 的四个计数是 ``AgentRegistry.vue`` 四张统计卡片读取
的键：``count``（总数）、``enabled_count``、``healthy_count``、
``capability_count``（去重后的能力数）。
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from maop.core.agent.registry.agent_catalog import AgentDescriptor


class AgentListEnvelope(BaseModel):
    """GET /api/agent-catalog/agents 响应。"""

    status: str = "error"
    agents: list[AgentDescriptor] = Field(default_factory=list)
    count: int = 0
    error: str = ""


class AgentEnvelope(BaseModel):
    """POST / PUT 单个 Agent 的响应（注册或部分更新的结果）。"""

    status: str = "error"
    agent: AgentDescriptor | None = None
    error: str = ""


class AgentDeleteEnvelope(BaseModel):
    """DELETE /api/agent-catalog/agents/{name} 响应。"""

    status: str = "error"
    deleted: str = ""
    error: str = ""


class CatalogStatsPayload(BaseModel):
    """GET /api/agent-catalog/stats 响应。"""

    status: str = "error"
    count: int = 0
    healthy_count: int = 0
    enabled_count: int = 0
    capability_count: int = 0
    error: str = ""
