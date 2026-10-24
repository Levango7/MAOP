"""MAOP Dashboard — ``/api/graph`` 端点的响应模型."""

from __future__ import annotations

from pydantic import BaseModel


class GraphStatsPayload(BaseModel):
    """GET /api/graph/stats ← ``data_service.get_graph_stats()``.

    成功响应恰好这四个键。失败路径是路由把服务异常转成 HTTPException、
    再由 ``handle_api_errors`` 渲染的 500 ErrorSchema JSONResponse，绕过
    本模型，所以这里不声明 ``status``/``error``（声明了也只会让成功响应
    平白多出两个生产者从不发送的键）。

    ``avg_degree`` 在空图时由生产者返回整数 ``0``，Pydantic 的 ``float``
    会把 JSON 表示归一成 ``0.0``；语义不变。
    """

    nodes: int = 0
    edges: int = 0
    avg_degree: float = 0
    max_degree: int = 0
