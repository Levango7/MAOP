"""MAOP Dashboard — 用户反馈 API 的响应模型.

字段名逐字来自服务层 ``FEEDBACK_COLUMNS`` + ``chat_service.row_to_dict()``
（tags 已在服务层从 JSON 字符串解析成数组），不另造别名。

``rating_distribution`` / ``tag_frequency`` 的键是字符串：SQLite 的
``GROUP BY rating`` 经 ``{str(k): v}`` 之后就是字符串键，视图两种都接受。
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class FeedbackRow(BaseModel):
    """一条反馈记录（``chat_service.row_to_dict()`` 的键）。"""

    feedback_id: str = ""
    target_type: str = ""
    target_id: str = ""
    user_id: str = ""
    tenant_id: str = ""
    rating: int = 0
    comment: str = ""
    tags: list[str] = Field(default_factory=list)
    created_at: float = 0.0
    updated_at: float = 0.0


class FeedbackListEnvelope(BaseModel):
    """GET /api/feedback 响应。"""

    status: str = "error"
    data: list[FeedbackRow] = Field(default_factory=list)
    total: int = 0
    page: int = 0
    page_size: int = 0
    error: str = ""


class FeedbackSummary(BaseModel):
    """GET /api/feedback/summary 的 summary 段。"""

    total: int = 0
    average_rating: float = 0.0
    rating_distribution: dict[str, int] = Field(default_factory=dict)
    comment_count: int = 0
    tag_frequency: dict[str, int] = Field(default_factory=dict)


class FeedbackSummaryEnvelope(BaseModel):
    """GET /api/feedback/summary 响应。"""

    status: str = "error"
    summary: FeedbackSummary = Field(default_factory=FeedbackSummary)
    error: str = ""


class FeedbackCreateEnvelope(BaseModel):
    """POST /api/feedback 响应。"""

    status: str = "error"
    feedback_id: str = ""
    error: str = ""


class FeedbackUpdateEnvelope(BaseModel):
    """PUT /api/feedback/{feedback_id} 响应。"""

    status: str = "error"
    feedback: FeedbackRow = Field(default_factory=FeedbackRow)
    error: str = ""


class FeedbackDeleteEnvelope(BaseModel):
    """DELETE /api/feedback/{feedback_id} 响应。"""

    status: str = "error"
    deleted: bool = False
    error: str = ""
