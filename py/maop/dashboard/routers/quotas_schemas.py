"""配额路由（``/api/quotas``）的响应模型。

企业包在纯净 CI 检出里不存在（ADR-017 双仓），而 router 要能导入、OpenAPI 才会
带类型，故这里只镜像线缆形状；字段一致性由
``tests/test_enterprise_router_contracts.py`` 在装包环境下对账。

本文件只覆盖**前端在调用**的端点（棘轮口径）。同一 router 里未被前端调用的
CRUD 端点保持原样，不借这一批顺手改契约。
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class QuotaItem(BaseModel):
    tenant_id: str
    resource: str
    hard_limit: int
    soft_limit: int
    period: str
    created_at: float
    updated_at: float


class QuotaUsageItem(BaseModel):
    tenant_id: str
    resource: str
    period: str
    used: int
    hard_limit: int
    soft_limit: int
    remaining: int
    exceeded_soft: bool
    exceeded_hard: bool


class QuotaAlertItem(BaseModel):
    alert_id: str
    tenant_id: str
    resource: str
    alert_type: str
    current_value: int
    limit_value: int
    severity: str
    message: str
    created_at: float
    resolved: bool
    resolved_at: float | None = None


class QuotaHistoryItem(BaseModel):
    id: str
    tenant_id: str
    resource: str
    field: str
    old_value: str = ""
    new_value: str = ""
    changed_by: str = ""
    changed_at: float


class QuotaEnvelope(BaseModel):
    status: str = "error"
    quota: QuotaItem | None = None


class QuotaUsageListEnvelope(BaseModel):
    status: str = "error"
    usages: list[QuotaUsageItem] = Field(default_factory=list)
    count: int = 0


class QuotaAlertListEnvelope(BaseModel):
    status: str = "error"
    alerts: list[QuotaAlertItem] = Field(default_factory=list)
    count: int = 0


class QuotaAlertResolvedEnvelope(BaseModel):
    status: str = "error"
    resolved: bool = False


class QuotaHistoryEnvelope(BaseModel):
    status: str = "error"
    history: list[QuotaHistoryItem] = Field(default_factory=list)
    count: int = 0


class QuotaAllocation(BaseModel):
    labels: list[str] = Field(default_factory=list)
    values: list[int] = Field(default_factory=list)


class QuotaOverviewEnvelope(BaseModel):
    status: str = "error"
    allocation: QuotaAllocation = Field(default_factory=QuotaAllocation)
    tenant_count: int = 0


class QuotaTrendSeries(BaseModel):
    label: str
    #: 没采样的桶是 ``None`` —— 折线断开，不用 0 填充（0 会被读成用量归零）。
    data: list[int | None] = Field(default_factory=list)


class QuotaTrendEnvelope(BaseModel):
    status: str = "error"
    tenant_id: str = ""
    labels: list[str] = Field(default_factory=list)
    series: list[QuotaTrendSeries] = Field(default_factory=list)
    hours: int = 0
    sampled: bool = False
