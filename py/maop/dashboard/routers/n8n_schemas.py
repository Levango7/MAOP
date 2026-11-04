"""n8n 集成路由（``/api/n8n``）的响应模型。

两条形状的来路不同：

* **工作流列表**是 n8n REST API 原样透传的 dict（``/workflows`` 的 ``data``），
  键集合由对方版本与工作流节点配置决定，本仓无权收窄。
* **执行详情**来自企业包的 ``N8nWorkflowExecution.model_dump()``。

两处条目都开 ``extra="allow"``：前者若按已知键严格建模会**丢掉** ``nodes`` /
``lastExecutedAt`` 等前端在用的字段；后者是企业包独立演进，新增字段同样不该
在本仓被抹掉。必填键缺失（改名/删字段）仍是 500，属于响亮失败。

``trigger`` / ``executions`` 的响应**没有信封**：router 写的是
``{"status": "ok", **execution}``，而 execution 自带 ``status``（running /
success / ...），后展开的键覆盖了字面量——线上的 ``status`` 一直是执行状态，
前端也按执行状态读。模型据此按执行对象建模，不要再套一层信封。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class N8nWorkflowItem(BaseModel):
    """n8n ``/workflows`` 列表项（其余字段原样透传）。"""

    model_config = ConfigDict(extra="allow")

    id: str | int | None = None
    name: str = ""
    active: bool | None = None


class N8nWorkflowListEnvelope(BaseModel):
    status: str = "error"
    workflows: list[N8nWorkflowItem] = Field(default_factory=list)
    count: int = 0


class N8nExecutionItem(BaseModel):
    """执行详情（``N8nWorkflowExecution`` 的线缆形状）。"""

    model_config = ConfigDict(extra="allow")

    execution_id: str
    workflow_id: str
    status: str
    started_at: datetime
    finished_at: datetime | None = None
    data: dict[str, Any] | None = None
    error: str | None = None


class N8nHealthEnvelope(BaseModel):
    status: str = "error"
    n8n_reachable: bool = False
    base_url: str = ""
