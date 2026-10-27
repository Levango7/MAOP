"""HTTP contracts for ``/api/analysis/*`` — the 6 endpoints the dashboard calls.

The Analysis page read a vocabulary the API never sent. The KPI card asked
for ``success_rate`` while the producer writes ``success_rate_pct``; the
resource cards asked for flat ``cpu_pct`` / ``memory_mb`` / ``db_in_use`` /
``cache_hit_rate`` while the producer nests them under ``live`` / ``db_pool``
/ ``cache``; the cost table asked for ``dimension`` and ``value`` while the
producer writes ``key`` and ``cost_usd``; the slow-endpoint table asked for
``name`` / ``latency_ms`` while the producer writes ``metric`` /
``avg_seconds``. Every one of those reads as ``undefined``, so the page
rendered "0", "—", an empty pie chart and an empty slow-endpoint table while
the API was returning correct numbers — and no schema said so, because every
one of the six endpoints answered ``dict[str, Any]``.

Two directions are asserted here, and each is bound to the other:

* the producer's payload keys survive the envelope — pydantic drops unknown
  extras by default, so a key the envelope forgot would vanish from the wire
  without an error;
* the keys the view reads are the keys the producer writes — pinned per
  endpoint as a (producer path, view expression) pair, so renaming on either
  side alone turns this file red.

The producers are exercised for real; only the backends behind them are
faked (cost tracker, audit log, psutil, metrics). A mock of the producer
would only re-state the assumption under test.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from maop.core.cost_tracker import CostEntry, CostSummary
from maop.dashboard.routers import analysis as analysis_router
from maop.dashboard.routers import state
from maop.dashboard.routers.analysis_schemas import (
    AgentEfficiencyEnvelope,
    BottlenecksEnvelope,
    CostBreakdownEnvelope,
    ResourceUtilizationEnvelope,
    SummaryEnvelope,
    TaskTrendsEnvelope,
)
from maop.dashboard.services import data_service

REPO_ROOT = Path(__file__).resolve().parents[2]
ANALYSIS_VIEW = REPO_ROOT / "dashboard-enterprise" / "src" / "views" / "Analysis.vue"

CONTRACT_ROUTES: list[tuple[str, str]] = [
    ("GET", "/api/analysis/agent-efficiency"),
    ("GET", "/api/analysis/task-trends"),
    ("GET", "/api/analysis/resource-utilization"),
    ("GET", "/api/analysis/cost-breakdown"),
    ("GET", "/api/analysis/performance-bottlenecks"),
    ("GET", "/api/analysis/summary"),
]

ENVELOPE_BY_ROUTE: dict[str, Any] = {
    "/api/analysis/agent-efficiency": AgentEfficiencyEnvelope,
    "/api/analysis/task-trends": TaskTrendsEnvelope,
    "/api/analysis/resource-utilization": ResourceUtilizationEnvelope,
    "/api/analysis/cost-breakdown": CostBreakdownEnvelope,
    "/api/analysis/performance-bottlenecks": BottlenecksEnvelope,
    "/api/analysis/summary": SummaryEnvelope,
}

#: (route, path inside ``data``, expression inside Analysis.vue).
#:
#: The two halves must agree: the path must resolve in the producer's real
#: payload, and the expression must still be in the view. Renaming a field on
#: one side only breaks one of the two assertions.
VIEW_CONTRACT: list[tuple[str, str, str]] = [
    # ── summary KPI cards ──
    ("/api/analysis/summary", "total_tasks", "summaryData.total_tasks"),
    ("/api/analysis/summary", "success_rate_pct", "summaryData.success_rate_pct"),
    ("/api/analysis/summary", "total_cost_usd", "summaryData.total_cost_usd"),
    ("/api/analysis/summary", "avg_latency_ms", "summaryData.avg_latency_ms"),
    ("/api/analysis/summary", "active_agents", "summaryData.active_agents"),
    ("/api/analysis/summary", "total_tokens", "summaryData.total_tokens"),
    # ── agent efficiency table ──
    ("/api/analysis/agent-efficiency", "agents[].agent", "key: 'agent'"),
    ("/api/analysis/agent-efficiency", "agents[].total_tasks", "key: 'total_tasks'"),
    ("/api/analysis/agent-efficiency", "agents[].success_rate_pct", "a.success_rate_pct"),
    ("/api/analysis/agent-efficiency", "agents[].tokens", "key: 'tokens'"),
    ("/api/analysis/agent-efficiency", "agents[].cost_usd", "a.cost_usd"),
    ("/api/analysis/agent-efficiency", "agents[].avg_latency_ms", "a.avg_latency_ms"),
    ("/api/analysis/agent-efficiency", "agents[].tasks_per_usd", "key: 'tasks_per_usd'"),
    ("/api/analysis/agent-efficiency", "agents[].circuit_breaker", "a.circuit_breaker"),
    # ── task trends ──
    ("/api/analysis/task-trends", "series[].bucket", "key: 'bucket'"),
    ("/api/analysis/task-trends", "series[].success", "key: 'success'"),
    ("/api/analysis/task-trends", "series[].failure", "key: 'failure'"),
    ("/api/analysis/task-trends", "series[].timeout", "key: 'timeout'"),
    ("/api/analysis/task-trends", "series[].total", "key: 'total'"),
    ("/api/analysis/task-trends", "series[].success_rate", "s.success_rate"),
    # ── resource utilization ──
    ("/api/analysis/resource-utilization", "live.cpu_percent", "live?.cpu_percent"),
    ("/api/analysis/resource-utilization", "live.memory_used_mb", "live?.memory_used_mb"),
    ("/api/analysis/resource-utilization", "db_pool.in_use", "db_pool?.in_use"),
    ("/api/analysis/resource-utilization", "db_pool.max_size", "db_pool?.max_size"),
    ("/api/analysis/resource-utilization", "cache.hit_rate", "cache?.hit_rate"),
    ("/api/analysis/resource-utilization", "series[].cpu_percent", "r.cpu_percent"),
    # ── cost breakdown ──
    ("/api/analysis/cost-breakdown", "breakdown[].key", "it.key"),
    ("/api/analysis/cost-breakdown", "breakdown[].cost_usd", "it.cost_usd"),
    ("/api/analysis/cost-breakdown", "breakdown[].tokens", "it.tokens"),
    ("/api/analysis/cost-breakdown", "breakdown[].calls", "it.calls"),
    ("/api/analysis/cost-breakdown", "total_cost_usd", "costData.value.total_cost_usd"),
    # ── performance bottlenecks ──
    ("/api/analysis/performance-bottlenecks", "slowest_endpoints[].metric", "e.metric"),
    ("/api/analysis/performance-bottlenecks", "slowest_endpoints[].avg_seconds", "e.avg_seconds"),
    ("/api/analysis/performance-bottlenecks", "expensive_agents[].agent", "e.agent"),
    ("/api/analysis/performance-bottlenecks", "expensive_agents[].cost_usd", "e.cost_usd"),
    ("/api/analysis/performance-bottlenecks", "top_memory_consumers[].name", "e.name"),
    ("/api/analysis/performance-bottlenecks", "top_memory_consumers[].memory_mb", "e.memory_mb"),
]


# ── faked backends ────────────────────────────────────────────────


class _FakeTracker:
    """A cost tracker whose cost order and latency order disagree.

    ``chatty`` is slow and cheap; ``pricey`` is fast and expensive. A ranking
    by latency therefore cannot be mistaken for a ranking by cost — which is
    exactly the confusion this batch removes (the section is titled "Most
    Expensive Agents" and renders a cost column).
    """

    def __init__(self) -> None:
        self.entries = [
            CostEntry(
                agent="chatty", model="m1", total_tokens=1000, cost_usd=0.01,
                latency_ms=9000, created_at="2026-10-20T10:00:00+00:00",
            ),
            CostEntry(
                agent="pricey", model="m1", total_tokens=10, cost_usd=5.0,
                latency_ms=10, created_at="2026-10-21T10:00:00+00:00",
            ),
        ]

    def get_entries(self, **_kw: Any) -> list[CostEntry]:
        return list(self.entries)

    def summary(self, **_kw: Any) -> CostSummary:
        return CostSummary(
            total_prompt_tokens=1010,
            total_completion_tokens=0,
            total_tokens=1010,
            total_cost_usd=5.01,
            total_calls=2,
            avg_latency_ms=4505.0,
            by_agent={
                "chatty": {"tokens": 1000, "cost": 0.01, "calls": 1},
                "pricey": {"tokens": 10, "cost": 5.0, "calls": 1},
            },
            by_model={"m1": {"tokens": 1010, "cost": 5.01, "calls": 2}},
            by_session={"s1": {"tokens": 1010, "cost": 5.01, "calls": 2}},
        )


class _FakeBridge:
    async def agent_stats(self) -> list[dict[str, Any]]:
        return [
            {
                "agent": "chatty", "total_delegations": 10, "successes": 9,
                "success_rate": 90.0, "circuit_breaker": "closed", "failures": 1,
            },
            {
                "agent": "pricey", "total_delegations": 4, "successes": 3,
                "success_rate": 75.0, "circuit_breaker": "open", "failures": 1,
            },
        ]


class _FakeMemoryInfo:
    rss = 256 * 1024 * 1024


class _FakeProcess:
    def __init__(self) -> None:
        self.info = {
            "pid": 4242,
            "name": "worker-1.exe",
            "memory_info": _FakeMemoryInfo(),
            "cpu_percent": 3.5,
        }


class _FakeVirtualMemory:
    percent = 55.0
    used = 512 * 1024 * 1024
    total = 1024 * 1024 * 1024


class _FakePsutil:
    def cpu_percent(self, interval: float | None = None) -> float:
        return 42.0

    def virtual_memory(self) -> _FakeVirtualMemory:
        return _FakeVirtualMemory()

    def process_iter(self, attrs: Any = None) -> list[_FakeProcess]:
        return [_FakeProcess()]


class _FakeTimeSeriesStore:
    def __init__(self, db_path: Any = None) -> None:
        self.db_path = db_path

    def query(self, q: Any) -> list[dict[str, Any]]:
        return [
            {"timestamp": 1_767_225_600.0, "metric": q.metric, "value": 12.5},
            {"timestamp": 1_767_225_900.0, "metric": q.metric, "value": 30.0},
        ]


class _FakeMetrics:
    def to_json(self) -> dict[str, Any]:
        return {
            "http.request.duration": {"type": "histogram", "count": 4, "sum": 2.0},
            "http.requests": {"type": "counter", "count": 9},
        }


@pytest.fixture
def analysis_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Real producers, faked backends."""
    monkeypatch.setattr(analysis_router, "require_admin", lambda request: None)
    monkeypatch.setattr(state, "get_bridge", lambda: _FakeBridge())
    monkeypatch.setattr(data_service, "_get_cost_tracker", lambda: _FakeTracker())
    monkeypatch.setattr(data_service, "_safe_psutil", lambda: _FakePsutil())
    monkeypatch.setattr(
        data_service, "_get_db_pool_stats",
        lambda: {"max_size": 8, "in_use": 3, "available": 5},
    )
    monkeypatch.setattr(
        data_service, "_get_cache_stats",
        lambda: {
            "hits": 9, "misses": 1, "evictions": 0,
            "size": 10, "max_size": 100, "hit_rate": 0.9,
        },
    )
    now = time.time()
    monkeypatch.setattr(
        data_service, "_get_audit_events",
        lambda limit=5000: [
            {"timestamp": now - 3600, "action": "task.run", "level": "info", "detail": {}},
            {"timestamp": now - 1800, "action": "task.run", "level": "error", "detail": {}},
        ],
    )
    monkeypatch.setattr(
        "maop.core.monitoring.timeseries.TimeSeriesStore", _FakeTimeSeriesStore,
    )
    monkeypatch.setattr("maop.core.monitoring.monitoring.metrics", _FakeMetrics())


@pytest.fixture
def client(analysis_env: None) -> TestClient:
    app = FastAPI()
    app.include_router(analysis_router.router)
    return TestClient(app)


def _view_source() -> str:
    """The view source with optional chaining flattened, so an expression
    like ``live?.cpu_percent`` can be matched against ``live.cpu_percent``."""
    return ANALYSIS_VIEW.read_text(encoding="utf-8").replace("?.", ".")


def _resolve(payload: dict[str, Any], path: str) -> Any:
    """Walk a dotted path; ``a[].b`` descends into the first list element."""
    current: Any = payload
    for part in path.split("."):
        is_list = part.endswith("[]")
        key = part[:-2] if is_list else part
        current = current[key]
        if is_list:
            assert current, f"empty list at {path}"
            current = current[0]
    return current


# ── the routes themselves ─────────────────────────────────────────


def test_all_contract_routes_declare_a_response_model(client: TestClient) -> None:
    schema = client.app.openapi()
    problems: list[str] = []
    for method, template in CONTRACT_ROUTES:
        op = (schema["paths"].get(template) or {}).get(method.lower())
        if op is None:
            problems.append(f"MISSING {method} {template}")
            continue
        body = ((op.get("responses") or {}).get("200", {}).get("content") or {}).get(
            "application/json", {},
        ).get("schema") or {}
        if "$ref" not in body and "properties" not in body:
            problems.append(f"UNTYPED {method} {template}")
    assert problems == []


def test_envelope_defaults_are_the_error_branch_defaults() -> None:
    """A missing ``status`` must not default to ``"ok"``."""
    for model in ENVELOPE_BY_ROUTE.values():
        assert model.model_fields["status"].default == "error", model.__name__


def test_every_response_carries_the_three_part_envelope(client: TestClient) -> None:
    for _method, route in CONTRACT_ROUTES:
        body = client.get(route).json()
        assert body["status"] == "ok", route
        assert set(body) == {"status", "data", "meta"}, route
        assert set(body["meta"]) == {"date_from", "date_to", "generated_at", "note"}, route


def test_the_producer_payload_survives_the_envelope(client: TestClient) -> None:
    """Nothing the producer writes may be dropped on the way out."""
    for _method, route in CONTRACT_ROUTES:
        body = client.get(route).json()
        model = ENVELOPE_BY_ROUTE[route]
        declared = set(model.model_fields["data"].annotation.model_fields)
        produced = set(body["data"])
        assert produced <= declared, (
            f"{route}: the envelope drops {sorted(produced - declared)}"
        )


# ── producer ↔ view, bound in both directions ─────────────────────


def test_the_view_reads_the_keys_the_producer_writes(client: TestClient) -> None:
    view_source = _view_source()
    for route, path, expression in VIEW_CONTRACT:
        body = client.get(route).json()
        value = _resolve(body["data"], path)
        assert value is not None, f"{route}: {path} is missing from the payload"
        assert expression.replace("?.", ".") in view_source, (
            f"{route}: Analysis.vue no longer reads {expression!r} "
            f"(the producer still sends {path!r})"
        )


def test_the_view_still_mentions_every_field_it_reads(client: TestClient) -> None:
    """The reverse bound: a field the view reads must exist in the payload."""
    view_source = _view_source()
    for route, path, expression in VIEW_CONTRACT:
        assert expression.replace("?.", ".") in view_source, f"{route}: Analysis.vue lost {expression!r}"


# ── the producer must not lie ─────────────────────────────────────


def test_expensive_agents_are_ranked_by_cost(client: TestClient) -> None:
    """The section is titled "Most Expensive Agents" and renders a cost column.

    It used to carry a *latency* ranking under a different key, so the table
    was permanently empty.
    """
    data = client.get("/api/analysis/performance-bottlenecks").json()["data"]
    assert "slowest_agents" not in data, "the latency ranking is still in the payload"
    rows = data["expensive_agents"]
    assert [r["agent"] for r in rows] == ["pricey", "chatty"], "not ranked by cost"
    assert rows[0]["cost_usd"] == 5.0
    assert rows[0]["calls"] == 1
    assert rows[0]["tokens"] == 10


def test_slow_endpoints_report_the_metric_and_its_mean(client: TestClient) -> None:
    rows = client.get("/api/analysis/performance-bottlenecks").json()["data"]["slowest_endpoints"]
    assert rows, "the fake metrics collector reports one histogram"
    row = rows[0]
    assert row["metric"] == "http.request.duration"
    assert row["count"] == 4
    assert row["avg_seconds"] == 0.5


def test_memory_consumers_are_listed(client: TestClient) -> None:
    rows = client.get("/api/analysis/performance-bottlenecks").json()["data"]["top_memory_consumers"]
    assert rows and rows[0]["name"] == "worker-1.exe"
    assert rows[0]["memory_mb"] == 256.0


def test_the_resource_chart_series_is_chart_ready(client: TestClient) -> None:
    """The view draws one CPU line — it needs a flat series to draw it."""
    data = client.get("/api/analysis/resource-utilization").json()["data"]
    assert data["series"], "no chart-ready CPU series"
    point = data["series"][0]
    assert set(point) == {"timestamp", "cpu_percent"}
    assert point["timestamp"] == 1_767_225_600.0
    assert point["cpu_percent"] == 12.5


def test_the_resource_cards_report_the_live_sample(client: TestClient) -> None:
    live = client.get("/api/analysis/resource-utilization").json()["data"]["live"]
    assert live["cpu_percent"] == 42.0
    assert live["memory_percent"] == 55.0
    assert live["memory_used_mb"] == 512.0
    assert live["memory_total_mb"] == 1024.0


def test_the_summary_reports_the_token_total_the_kpi_card_shows(client: TestClient) -> None:
    data = client.get("/api/analysis/summary").json()["data"]
    assert data["total_tokens"] == 1010
    assert data["total_cost_usd"] == 5.01
    assert data["total_calls"] == 2
    assert data["avg_latency_ms"] == 4505.0


def test_the_summary_success_rate_is_a_percentage(client: TestClient) -> None:
    """``success_rate_pct`` is 0-100 — the view renders it as a percentage."""
    data = client.get("/api/analysis/summary").json()["data"]
    assert data["success_rate_pct"] == 85.7
    assert 0.0 <= data["success_rate_pct"] <= 100.0


def test_task_trends_bucket_success_and_failure(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """分桶契约：同一 UTC 日的一成一败必须落进**一个**桶，且**不看真实时钟**。

    以前这条不带参数请求（走 ``_parse_date_range`` 的"最近 24h"回退），事件时间戳用
    夹具里的 ``now-3600`` / ``now-1800`` —— 两个都是真实时钟的函数，于是 **UTC 午夜后
    一小时内跑就是两个桶**：本机（UTC+8）实测红，CI 因为一天内跑完而看不见这个红。
    "要等午夜才能复现"本身就是这条用例的缺陷。所以这里不冻时钟、也不把期望值 1 改成
    2 凑绿，而是整条改用**字面日**：显式给出覆盖该日的区间 + 该日内的两个固定时刻。
    任何时刻跑都是同一份输入 ⇒ 恒为 1 个桶。
    """
    day = "2026-10-27"
    day_start = datetime(2026, 10, 27, tzinfo=timezone.utc).timestamp()
    monkeypatch.setattr(
        data_service, "_get_audit_events",
        lambda limit=5000: [
            {
                "timestamp": day_start + 9 * 3600,
                "action": "task.run", "level": "info", "detail": {},
            },
            {
                "timestamp": day_start + 8 * 3600 + 1800,
                "action": "task.run", "level": "error", "detail": {},
            },
        ],
    )

    url = f"/api/analysis/task-trends?date_from={day}T00:00:00Z&date_to={day}T23:59:59Z"
    data = client.get(url).json()["data"]
    assert data["granularity"] == "day"
    assert data["bucket_count"] == len(data["series"]) == 1
    bucket = data["series"][0]
    assert bucket["bucket"] == day
    assert bucket["success"] == 1
    assert bucket["failure"] == 1
    assert bucket["timeout"] == 0
    assert bucket["total"] == 2
    assert bucket["success_rate"] == 0.5


def test_cost_breakdown_groups_by_agent_with_costs(client: TestClient) -> None:
    data = client.get("/api/analysis/cost-breakdown").json()["data"]
    assert data["group_by"] == "agent"
    rows = {r["key"]: r for r in data["breakdown"]}
    assert set(rows) == {"chatty", "pricey"}
    assert rows["pricey"]["cost_usd"] == 5.0
    assert rows["pricey"]["calls"] == 1
    assert rows["pricey"]["tokens"] == 10
    assert data["total_cost_usd"] == 5.01
    assert data["trend"], "the per-day cost trend is empty"
