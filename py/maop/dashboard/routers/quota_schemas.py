"""MAOP Dashboard — Agent 额度分桶 API 的请求/响应模型.

请求体一律 ``extra="forbid"``：Pydantic v2 默认静默丢弃未声明字段，前端若
多发了一个后端不认的键，额度会被"写成默认值"而不是报错——那是静默改数据。

响应模型复用领域模型 ``ConsumeResult``（``core/agent/billing/quota_bucket.py``）：
只有领域层知道一次消耗实际从哪个桶扣了多少。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from maop.core.agent.billing.quota_bucket import BUCKET_NAMES, ConsumeResult

# 请求体里可用的桶名：三桶 + auto（按 免费 → 预付 → 后付 顺序扣减）
CONSUME_BUCKET_NAMES = ("auto", *BUCKET_NAMES)

# 可设置额度上限的桶（auto 不行——"上限"没有顺序语义）
SETTABLE_BUCKET_NAMES = BUCKET_NAMES


class QuotaBucketView(BaseModel):
    """单个额度桶的视图。"""

    total: int = 0
    used: int = 0
    remaining: int = 0


class QuotaView(BaseModel):
    """Agent 三桶额度视图。"""

    free: QuotaBucketView = Field(default_factory=QuotaBucketView)
    prepaid: QuotaBucketView = Field(default_factory=QuotaBucketView)
    postpaid: QuotaBucketView = Field(default_factory=QuotaBucketView)
    reset_period: str = ""
    last_reset: str = ""


class QuotaGetEnvelope(BaseModel):
    """GET /api/quota/{agent_name} 响应。"""

    status: str = "error"
    agent_name: str = ""
    quota: QuotaView = Field(default_factory=QuotaView)
    error: str = ""


class QuotaSetRequest(BaseModel):
    """PUT /api/quota/{agent_name} 请求体。"""

    model_config = ConfigDict(extra="forbid")

    bucket: str = Field(default="prepaid", description="设置哪个桶: free/prepaid/postpaid")
    total: int = Field(default=0, ge=0, description="额度上限")


class QuotaSetEnvelope(BaseModel):
    """PUT /api/quota/{agent_name} 响应。"""

    status: str = "error"
    agent_name: str = ""
    quota: QuotaView = Field(default_factory=QuotaView)
    error: str = ""


class QuotaConsumeRequest(BaseModel):
    """POST /api/quota/{agent_name}/consume 请求体。"""

    model_config = ConfigDict(extra="forbid")

    amount: int = Field(default=0, ge=0, description="消耗量")
    bucket: str = Field(default="auto", description="从哪个桶扣: free/prepaid/postpaid/auto")


class QuotaConsumeEnvelope(BaseModel):
    """POST /api/quota/{agent_name}/consume 响应。"""

    status: str = "error"
    agent_name: str = ""
    result: ConsumeResult = Field(default_factory=ConsumeResult)
    error: str = ""


class QuotaRefundRequest(BaseModel):
    """POST /api/quota/{agent_name}/refund 请求体。"""

    model_config = ConfigDict(extra="forbid")

    amount: int = Field(default=0, ge=0, description="退还量")
    bucket: str = Field(default="auto", description="退还到哪个桶: free/prepaid/postpaid/auto")


class QuotaRefundEnvelope(BaseModel):
    """POST /api/quota/{agent_name}/refund 响应。"""

    status: str = "error"
    agent_name: str = ""
    error: str = ""


class QuotaResetRequest(BaseModel):
    """POST /api/quota/{agent_name}/reset 请求体。"""

    model_config = ConfigDict(extra="forbid")

    bucket: str = Field(default="all", description="重置哪个桶: free/prepaid/postpaid/all")


class QuotaResetEnvelope(BaseModel):
    """POST /api/quota/{agent_name}/reset 响应。"""

    status: str = "error"
    agent_name: str = ""
    error: str = ""


class QuotaRemainingEnvelope(BaseModel):
    """GET /api/quota/{agent_name}/remaining 响应。"""

    status: str = "error"
    agent_name: str = ""
    remaining: dict[str, int] = Field(default_factory=dict)
    total: int = 0
    error: str = ""
