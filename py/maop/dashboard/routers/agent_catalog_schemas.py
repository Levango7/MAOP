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

from maop.core.agent.ops.usage_statistics import AgentRecommendation
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


class RecommendAgentsPayload(BaseModel):
    """GET /api/agent-catalog/recommend 响应。

    ``agents`` 直接复用领域模型 ``AgentRecommendation``（与上面
    ``AgentDescriptor`` 同一哲学：不写字段镜像）。

    ``scored=False`` 表示**当前没有任何历史统计数据**（如全新部署），此时
    ``agents`` 为空是**预期结果**，不是错误。调用方应据此提示"先跑几次任务"，
    而不是把空列表渲染成"推荐失败"。
    """

    status: str = "error"
    agents: list[AgentRecommendation] = Field(default_factory=list)
    count: int = 0
    scored: bool = False
    error: str = ""
