"""MAOP Dashboard — Share API 的响应模型.

分享详情里的 ``summary`` 是 ``run_service._summarize()`` 的输出，逐键照它的返回写：
``started_at`` / ``updated_at`` 取事件行的 ``timestamp``（float），无事件时代码回落
``""``，故联合类型而不是单写 float——单写会让那条回落路径变成 500。

两条路由都带 ``error_value=``，降级 dict 会过 response_model，所以成功侧键一律给默认值。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ShareSummary(BaseModel):
    """被分享那一次运行的摘要（``run_service._summarize`` 的键）。"""

    trace_id: str = ""
    task: str = ""
    agent: str = ""
    routing_key: str = ""
    status: str = ""
    success: bool | None = None
    duration_ms: int = 0
    steps: int = 0
    started_at: float | str = ""
    updated_at: float | str = ""
    verify_status: str = ""
    verify_summary: str = ""
    cost: dict[str, Any] = Field(default_factory=dict)


class ShareCreatedEnvelope(BaseModel):
    """POST /api/share 响应。``path`` 是服务端给出的分享路径。"""

    status: str = "error"
    token: str = ""
    trace_id: str = ""
    path: str = ""
    created_at: float = 0.0
    expires_at: float = 0.0
    error: str = ""


class ShareDetailPayload(BaseModel):
    """``GET /api/share/{token}`` 里 ``share`` 键的形状。"""

    token: str = ""
    trace_id: str = ""
    created_at: float = 0.0
    expires_at: float = 0.0
    summary: ShareSummary = Field(default_factory=ShareSummary)


class ShareDetailEnvelope(BaseModel):
    """GET /api/share/{token} 响应。

    ``share`` 可为 None 是降级分支（``error_value``）的形状；token 不存在/过期走
    HTTPException → ErrorSchema，不会落在这个模型上。
    """

    status: str = "error"
    share: ShareDetailPayload | None = None
    error: str = ""


__all__ = [
    "ShareCreatedEnvelope",
    "ShareDetailEnvelope",
    "ShareDetailPayload",
    "ShareSummary",
]
