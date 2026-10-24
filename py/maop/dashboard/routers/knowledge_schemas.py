"""MAOP Dashboard — ``/api/knowledge-graph`` 端点的响应模型.

行类型直接复用 ``KnowledgeGraph.query_graph()`` 的生产者模型
（``GraphNodeV2`` / ``GraphEdgeV2``），不另写一份镜像。
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from maop.core.memory.knowledge_graph import GraphEdgeV2, GraphNodeV2


class KnowledgeGraphStats(BaseModel):
    """``query_knowledge_graph_v2()`` 在 data 里追加的计数。"""

    node_count: int = 0
    edge_count: int = 0


class KnowledgeGraphData(BaseModel):
    """``KnowledgeGraphResponse.model_dump()`` + ``stats``。"""

    nodes: list[GraphNodeV2] = Field(default_factory=list)
    edges: list[GraphEdgeV2] = Field(default_factory=list)
    stats: KnowledgeGraphStats = Field(default_factory=KnowledgeGraphStats)


class KnowledgeGraphPayload(BaseModel):
    """GET /api/knowledge-graph 响应。

    400（time_range 非法）与 403（非 admin）是 ErrorSchema JSONResponse，
    绕过本模型。
    """

    status: str = "error"
    data: KnowledgeGraphData = Field(default_factory=KnowledgeGraphData)
