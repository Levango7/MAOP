"""MAOP Dashboard — 计费摘要 API 的响应模型.

``summary.quota`` 复用额度域的三桶视图（``quota_schemas.QuotaView``）：
摘要页的额度条要读每个桶的 total/used，没有它就只剩"无限"。
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from maop.dashboard.routers.quota_schemas import QuotaView


class BillingSummaryPayload(BaseModel):
    """GET /api/billing/summary/{agent_name} 的 summary 段。"""

    agent_name: str = ""
    billing_model: str = ""
    total_calls: int = 0
    total_tokens: int = 0
    total_cost_usd: float = 0.0
    total_quota_consumed: int = 0
    quota_remaining: dict[str, int] = Field(default_factory=dict)
    quota: QuotaView = Field(default_factory=QuotaView)


class BillingSummaryEnvelope(BaseModel):
    """GET /api/billing/summary/{agent_name} 响应。"""

    status: str = "error"
    summary: BillingSummaryPayload = Field(default_factory=BillingSummaryPayload)
    error: str = ""


class BillingRecordView(BaseModel):
    """单条计费记录。

    字段名与领域模型 ``BillingRecord`` 一致（``tokens_consumed`` /
    ``calls_made`` / ``cost_usd`` / ``quota_source``），不另造别名——
    前端此前读的是 ``tokens``/``calls``/``cost``/``source``，那几个键
    生产者从来不发，记录表整片渲染成"—"。
    """

    agent_name: str = ""
    billing_model: str = ""
    tokens_consumed: int = 0
    calls_made: int = 0
    cost_usd: float = 0.0
    quota_consumed: int = 0
    quota_source: str = ""
    model: str = ""
    timestamp: str = ""


class BillingRecordsSummary(BaseModel):
    """GET /api/billing/records 的 summary 段。"""

    total_tokens: int = 0
    total_cost: float = 0.0
    total_calls: int = 0


class BillingRecordsEnvelope(BaseModel):
    """GET /api/billing/records 响应。"""

    status: str = "error"
    records: list[BillingRecordView] = Field(default_factory=list)
    count: int = 0
    summary: BillingRecordsSummary = Field(default_factory=BillingRecordsSummary)
    error: str = ""
