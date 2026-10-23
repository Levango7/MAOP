"""Response models for ``/api/analysis/*`` — the 6 endpoints the dashboard calls.

The producer is :mod:`maop.dashboard.services.data_service`; every field below
is spelled the way that module writes it. The audit trail for that vocabulary
is the mismatch this batch fixes: the view read ``success_rate`` where the
producer writes ``success_rate_pct``, ``cpu_pct`` where the producer nests
``live.cpu_percent``, ``dimension`` where the producer writes ``key`` — so the
KPI cards, the resource cards, the cost table and the pie chart rendered
"0", "—" and nothing at all while the API was returning the right numbers.

Note on the ``status`` defaults: the success path always writes
``"status": "ok"``. The error path does not reach this model at all —
``@handle_api_errors`` without an ``error_value`` turns exceptions into a
``JSONResponse``, which bypasses ``response_model`` validation. The default
is ``"error"`` so a dict that ever arrives without a ``status`` is labelled
as a failure rather than a success.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class AnalysisMeta(BaseModel):
    """The standard meta block every analysis response carries."""

    date_from: str = ""
    date_to: str = ""
    generated_at: str = ""
    note: str = ""


# ── 1. Agent efficiency ───────────────────────────────────────────


class AgentEfficiencyItem(BaseModel):
    """One row of the agent-efficiency table."""

    agent: str = ""
    total_tasks: int = 0
    successes: int = 0
    failures: int = 0
    success_rate_pct: float = 0.0
    circuit_breaker: str = "unknown"
    tokens: int = 0
    cost_usd: float = 0.0
    calls: int = 0
    tasks_per_usd: float = 0.0
    avg_latency_ms: float = 0.0


class AgentEfficiencyData(BaseModel):
    agents: list[AgentEfficiencyItem] = Field(default_factory=list)
    total_cost_usd: float = 0.0
    total_calls: int = 0


class AgentEfficiencyEnvelope(BaseModel):
    status: str = "error"
    data: AgentEfficiencyData = Field(default_factory=AgentEfficiencyData)
    meta: AnalysisMeta = Field(default_factory=AnalysisMeta)


# ── 2. Task trends ─────────────────────────────────────────────────


class TrendPoint(BaseModel):
    """One time bucket of the task trends chart."""

    bucket: str = ""
    success: int = 0
    failure: int = 0
    timeout: int = 0
    total: int = 0
    success_rate: float = 0.0
    failure_rate: float = 0.0
    timeout_rate: float = 0.0


class TaskTrendsData(BaseModel):
    granularity: str = "day"
    series: list[TrendPoint] = Field(default_factory=list)
    bucket_count: int = 0


class TaskTrendsEnvelope(BaseModel):
    status: str = "error"
    data: TaskTrendsData = Field(default_factory=TaskTrendsData)
    meta: AnalysisMeta = Field(default_factory=AnalysisMeta)


# ── 3. Resource utilization ────────────────────────────────────────


class ResourceLive(BaseModel):
    """Live psutil sample — every field is ``None`` when psutil is missing."""

    cpu_percent: float | None = None
    memory_percent: float | None = None
    memory_used_mb: float | None = None
    memory_total_mb: float | None = None


class ResourcePoint(BaseModel):
    """One point of the chart-ready CPU series."""

    timestamp: float = 0.0
    cpu_percent: float = 0.0


class DbPoolStats(BaseModel):
    max_size: int = 0
    in_use: int = 0
    available: int = 0


class CacheStats(BaseModel):
    hits: int = 0
    misses: int = 0
    evictions: int = 0
    size: int = 0
    max_size: int = 0
    hit_rate: float = 0.0


class ResourceUtilizationData(BaseModel):
    live: ResourceLive = Field(default_factory=ResourceLive)
    history: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    series: list[ResourcePoint] = Field(default_factory=list)
    db_pool: DbPoolStats = Field(default_factory=DbPoolStats)
    cache: CacheStats = Field(default_factory=CacheStats)


class ResourceUtilizationEnvelope(BaseModel):
    status: str = "error"
    data: ResourceUtilizationData = Field(default_factory=ResourceUtilizationData)
    meta: AnalysisMeta = Field(default_factory=AnalysisMeta)


# ── 4. Cost breakdown ──────────────────────────────────────────────


class CostItem(BaseModel):
    """One group of the cost breakdown (``key`` is the group name)."""

    key: str = ""
    cost_usd: float = 0.0
    tokens: int = 0
    calls: int = 0
    avg_cost_per_call: float = 0.0


class CostTrendPoint(BaseModel):
    date: str = ""
    cost_usd: float = 0.0


class CostBreakdownData(BaseModel):
    group_by: str = "agent"
    breakdown: list[CostItem] = Field(default_factory=list)
    trend: list[CostTrendPoint] = Field(default_factory=list)
    total_cost_usd: float = 0.0
    total_tokens: int = 0
    total_calls: int = 0


class CostBreakdownEnvelope(BaseModel):
    status: str = "error"
    data: CostBreakdownData = Field(default_factory=CostBreakdownData)
    meta: AnalysisMeta = Field(default_factory=AnalysisMeta)


# ── 5. Performance bottlenecks ─────────────────────────────────────


class SlowEndpoint(BaseModel):
    """A slow histogram metric — ``avg_seconds`` is the mean duration."""

    metric: str = ""
    count: int = 0
    sum_seconds: float = 0.0
    avg_seconds: float = 0.0


class ExpensiveAgent(BaseModel):
    """An agent ranked by spend."""

    agent: str = ""
    calls: int = 0
    cost_usd: float = 0.0
    tokens: int = 0


class MemoryConsumer(BaseModel):
    pid: int = 0
    name: str = ""
    memory_mb: float = 0.0
    cpu_percent: float = 0.0


class BottlenecksData(BaseModel):
    slowest_endpoints: list[SlowEndpoint] = Field(default_factory=list)
    expensive_agents: list[ExpensiveAgent] = Field(default_factory=list)
    top_memory_consumers: list[MemoryConsumer] = Field(default_factory=list)


class BottlenecksEnvelope(BaseModel):
    status: str = "error"
    data: BottlenecksData = Field(default_factory=BottlenecksData)
    meta: AnalysisMeta = Field(default_factory=AnalysisMeta)


# ── 6. Summary ─────────────────────────────────────────────────────


class SparkPoint(BaseModel):
    date: str = ""
    count: int = 0


class SummaryData(BaseModel):
    date_range: str = "7d"
    total_tasks: int = 0
    success_rate_pct: float = 0.0
    avg_latency_ms: float = 0.0
    total_cost_usd: float = 0.0
    total_calls: int = 0
    total_tokens: int = 0
    active_agents: int = 0
    sparkline: list[SparkPoint] = Field(default_factory=list)
    observability: dict[str, Any] = Field(default_factory=dict)


class SummaryEnvelope(BaseModel):
    status: str = "error"
    data: SummaryData = Field(default_factory=SummaryData)
    meta: AnalysisMeta = Field(default_factory=AnalysisMeta)


__all__ = [
    "AgentEfficiencyData",
    "AgentEfficiencyEnvelope",
    "AgentEfficiencyItem",
    "AnalysisMeta",
    "BottlenecksData",
    "BottlenecksEnvelope",
    "CacheStats",
    "CostBreakdownData",
    "CostBreakdownEnvelope",
    "CostItem",
    "CostTrendPoint",
    "DbPoolStats",
    "ExpensiveAgent",
    "MemoryConsumer",
    "ResourceLive",
    "ResourcePoint",
    "ResourceUtilizationData",
    "ResourceUtilizationEnvelope",
    "SlowEndpoint",
    "SparkPoint",
    "SummaryData",
    "SummaryEnvelope",
    "TaskTrendsData",
    "TaskTrendsEnvelope",
    "TrendPoint",
]
