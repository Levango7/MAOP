"""Response models for the cost-attribution router（M-X3 M0 端点切片）.

聚合形状与 ``core.cost_attribution.attribution_summary`` 的返回一一对应：
分桶键是动态的（agent/phase/model 名），所以维度映射用
``dict[str, DimensionBucket]``；桶内三键（cost_usd/calls/tokens）由
``tests/test_cost_attribution.py`` 的对齐断言钉死，这里只做线上形状。
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class DimensionBucket(BaseModel):
    cost_usd: float = 0.0
    calls: int = 0
    tokens: int = 0


class AttributionSummary(BaseModel):
    total: DimensionBucket = Field(default_factory=DimensionBucket)
    by_agent: dict[str, DimensionBucket] = Field(default_factory=dict)
    by_phase: dict[str, DimensionBucket] = Field(default_factory=dict)
    by_model: dict[str, DimensionBucket] = Field(default_factory=dict)


class CostBudgetAlert(BaseModel):
    status: str = "firing"
    labels: dict[str, str] = Field(default_factory=dict)
    annotations: dict[str, str] = Field(default_factory=dict)


class CostAttributionEnvelope(BaseModel):
    status: str = "ok"
    summary: AttributionSummary


class CostBudgetCheckEnvelope(BaseModel):
    status: str = "ok"
    alerts: list[CostBudgetAlert] = Field(default_factory=list)
