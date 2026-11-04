"""MAOP Dashboard — Long Running Task API 的响应模型.

任务体直接复用领域模型 ``LongRunningTask``：``session_service`` 返回的就是它的
``model_dump()``（时间戳本来就是 ISO 字符串、``status`` 是普通 str），另写一份镜像
只会在领域模型加字段时静默丢数据。

``GET /api/long-running-tasks/{task_id}/progress`` **不在这里**：它是 SSE
（``StreamingResponse``，``media_type=text/event-stream``），而路由一旦返回
``Response`` 实例，FastAPI 就完全不执行 response_model —— 给它挂模型等于挂一个
永不生效的假守卫。该端点记在
``tests/fixtures/frontend_non_json_endpoints.txt`` 里，由那条豁免账看着。

信封的 ``status`` 一律默认 ``"error"``（与 agent_catalog_schemas 同口径）：处理器漏写 status 时，缺省值不能替它宣称成功。
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from maop.core.agent.router.long_running_task import LongRunningTask


class LongRunningTaskListEnvelope(BaseModel):
    """GET /api/long-running-tasks 响应（pending / running）。"""

    status: str = "error"
    count: int = 0
    tasks: list[LongRunningTask] = Field(default_factory=list)


class LongRunningTaskCancelEnvelope(BaseModel):
    """POST /api/long-running-tasks/{task_id}/cancel 响应。"""

    status: str = "error"
    task_id: str = ""


__all__ = [
    "LongRunningTaskCancelEnvelope",
    "LongRunningTaskListEnvelope",
]
