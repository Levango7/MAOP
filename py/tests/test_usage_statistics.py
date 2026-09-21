"""UsageStatistics 白盒测试.

覆盖：调用记录 / 统计聚合 / 排名 / 智能推荐 / 成本分析 / 持久化 / 线程安全。
每个测试使用 tmp_path 隔离 SQLite，不触碰真实 DB。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any  # noqa: F401

import pytest

from maop.core.agent.ops.usage_statistics import (
    AgentRecommendation,  # noqa: F401
    AgentStats,  # noqa: F401
    CostAnalysis,  # noqa: F401
    UsageStatistics,
)

# ── Mock Catalog（模拟 AgentCatalog，用于推荐测试）──────────────


@dataclass
class _MockCapability:
    """模拟 AgentCapability 枚举."""

    value: str


@dataclass
class _MockDescriptor:
    """模拟 AgentDescriptor."""

    name: str
    capabilities: list[_MockCapability] = field(default_factory=list)
    billing_model: _MockCapability = field(
        default_factory=lambda: _MockCapability("blackbox"),
    )


class _MockCatalog:
    """模拟 AgentCatalog，仅需 get(name) 方法."""

    def __init__(self) -> None:
        self._agents: dict[str, _MockDescriptor] = {}

    def register(self, desc: _MockDescriptor) -> None:
        self._agents[desc.name] = desc

    def get(self, name: str) -> _MockDescriptor | None:
        return self._agents.get(name)


# ── Fixtures ─────────────────────────────────────────────────────


@pytest.fixture
def stats_mgr(tmp_path: Path) -> UsageStatistics:
    """使用隔离 tmp_path DB 的全新 UsageStatistics。"""
    return UsageStatistics(db_path=tmp_path / "usage.db")


@pytest.fixture
def stats_mgr_with_catalog(
    tmp_path: Path,
) -> tuple[UsageStatistics, _MockCatalog]:
    """使用隔离 tmp_path DB + MockCatalog 的 UsageStatistics。"""
    catalog = _MockCatalog()
    catalog.register(_MockDescriptor(
        name="agent-free",
        capabilities=[_MockCapability("code_generation"), _MockCapability("tool_use")],
        billing_model=_MockCapability("free"),
    ))
    catalog.register(_MockDescriptor(
        name="agent-paid",
        capabilities=[_MockCapability("code_generation")],
        billing_model=_MockCapability("per_token"),
    ))
    catalog.register(_MockDescriptor(
        name="agent-sub",
        capabilities=[_MockCapability("chat")],
        billing_model=_MockCapability("subscription"),
    ))
    mgr = UsageStatistics(db_path=tmp_path / "usage.db", catalog=catalog)
    return mgr, catalog


# ── 1. 调用记录 ──────────────────────────────────────────────────


class TestRecordCall:
    """record_call 的基本行为与参数校验。"""

    def test_record_call_basic(self, stats_mgr: UsageStatistics) -> None:
        """记录一次调用后应能查询到统计。"""
        stats_mgr.record_call("agent-a", success=True, duration_s=1.0, cost=0.01)
        stats_obj = stats_mgr.get_agent_stats("agent-a", period="all")
        assert stats_obj.total_calls == 1
        assert stats_obj.success_count == 1
        assert stats_obj.fail_count == 0

    def test_record_call_with_optional_fields(self, stats_mgr: UsageStatistics) -> None:
        """记录带 tokens 和 task_type 的调用。"""
        stats_mgr.record_call(
            "agent-a", success=True, duration_s=2.0, cost=0.05,
            tokens=500, task_type="code_generation",
        )
        stats_obj = stats_mgr.get_agent_stats("agent-a", period="all")
        assert stats_obj.total_tokens == 500
        assert stats_obj.avg_duration_s == pytest.approx(2.0)

    def test_record_call_negative_duration_raises(
        self, stats_mgr: UsageStatistics,
    ) -> None:
        """负耗时应抛出 ValueError。"""
        with pytest.raises(ValueError, match="duration_s"):
            stats_mgr.record_call("agent-a", success=True, duration_s=-1.0, cost=0.0)

    def test_record_call_negative_cost_raises(
        self, stats_mgr: UsageStatistics,
    ) -> None:
        """负成本应抛出 ValueError。"""
        with pytest.raises(ValueError, match="cost"):
            stats_mgr.record_call("agent-a", success=True, duration_s=1.0, cost=-0.01)


# ── 2. 统计聚合 ──────────────────────────────────────────────────


class TestGetStats:
    """get_agent_stats / get_all_stats 的统计聚合。"""

    def test_get_agent_stats_no_records(self, stats_mgr: UsageStatistics) -> None:
        """无记录时返回零值统计。"""
        stats_obj = stats_mgr.get_agent_stats("nonexistent", period="all")
        assert stats_obj.total_calls == 0
        assert stats_obj.success_rate == 0.0
        assert stats_obj.total_cost == 0.0

    def test_get_agent_stats_mixed_success(self, stats_mgr: UsageStatistics) -> None:
        """混合成功/失败记录的统计。"""
        stats_mgr.record_call("agent-a", success=True, duration_s=1.0, cost=0.01)
        stats_mgr.record_call("agent-a", success=True, duration_s=2.0, cost=0.02)
        stats_mgr.record_call("agent-a", success=False, duration_s=0.5, cost=0.0)

        stats_obj = stats_mgr.get_agent_stats("agent-a", period="all")
        assert stats_obj.total_calls == 3
        assert stats_obj.success_count == 2
        assert stats_obj.fail_count == 1
        assert stats_obj.success_rate == pytest.approx(2 / 3)
        assert stats_obj.total_cost == pytest.approx(0.03)
        assert stats_obj.avg_cost_per_call == pytest.approx(0.01)

    def test_get_all_stats_multiple_agents(
        self, stats_mgr: UsageStatistics,
    ) -> None:
        """多个 Agent 的全量统计。"""
        stats_mgr.record_call("agent-a", success=True, duration_s=1.0, cost=0.01)
        stats_mgr.record_call("agent-b", success=False, duration_s=2.0, cost=0.05)
        stats_mgr.record_call("agent-c", success=True, duration_s=0.5, cost=0.0)

        all_stats = stats_mgr.get_all_stats(period="all")
        names = [s.agent_name for s in all_stats]
        assert names == ["agent-a", "agent-b", "agent-c"]
        assert all(s.total_calls == 1 for s in all_stats)


# ── 3. 排名 ──────────────────────────────────────────────────────


class TestRanking:
    """get_ranking 按不同指标排序。"""

    def test_ranking_by_success_rate(self, stats_mgr: UsageStatistics) -> None:
        """按成功率降序排名。"""
        # agent-a: 100% 成功率
        stats_mgr.record_call("agent-a", success=True, duration_s=1.0, cost=0.01)
        # agent-b: 50% 成功率
        stats_mgr.record_call("agent-b", success=True, duration_s=1.0, cost=0.01)
        stats_mgr.record_call("agent-b", success=False, duration_s=1.0, cost=0.01)

        ranking = stats_mgr.get_ranking(metric="success_rate")
        assert ranking[0].agent_name == "agent-a"
        assert ranking[1].agent_name == "agent-b"
        assert ranking[0].success_rate > ranking[1].success_rate

    def test_ranking_by_avg_duration(self, stats_mgr: UsageStatistics) -> None:
        """按平均耗时升序排名（越快越好）。"""
        stats_mgr.record_call("fast", success=True, duration_s=0.1, cost=0.01)
        stats_mgr.record_call("slow", success=True, duration_s=5.0, cost=0.01)

        ranking = stats_mgr.get_ranking(metric="avg_duration_s")
        assert ranking[0].agent_name == "fast"
        assert ranking[1].agent_name == "slow"


# ── 4. 智能推荐 ──────────────────────────────────────────────────


class TestRecommend:
    """recommend_agent 智能推荐。"""

    def test_recommend_empty_stats(self, stats_mgr: UsageStatistics) -> None:
        """无任何调用记录时返回空列表。"""
        assert stats_mgr.recommend_agent() == []

    def test_recommend_basic_ordering(
        self, stats_mgr_with_catalog: tuple[UsageStatistics, _MockCatalog],
    ) -> None:
        """高成功率 Agent 应排在前面。"""
        mgr, _ = stats_mgr_with_catalog
        # agent-free: 100% 成功率，免费
        mgr.record_call("agent-free", success=True, duration_s=0.5, cost=0.0)
        # agent-paid: 50% 成功率，收费
        mgr.record_call("agent-paid", success=True, duration_s=2.0, cost=0.05)
        mgr.record_call("agent-paid", success=False, duration_s=2.0, cost=0.05)

        recs = mgr.recommend_agent()
        assert len(recs) == 2
        assert recs[0].agent_name == "agent-free"
        assert recs[0].score > recs[1].score

    def test_recommend_prefer_free(
        self, stats_mgr_with_catalog: tuple[UsageStatistics, _MockCatalog],
    ) -> None:
        """prefer_free=True 时免费 Agent 应获得加分。"""
        mgr, _ = stats_mgr_with_catalog
        # 两个 Agent 统计数据相同
        mgr.record_call("agent-free", success=True, duration_s=1.0, cost=0.01)
        mgr.record_call("agent-paid", success=True, duration_s=1.0, cost=0.01)

        recs_free = mgr.recommend_agent(prefer_free=True)
        recs_no_free = mgr.recommend_agent(prefer_free=False)

        # prefer_free=True 时 agent-free 应排第一
        assert recs_free[0].agent_name == "agent-free"
        # prefer_free=False 时两者评分应相同（统计数据一致）
        assert recs_no_free[0].score == pytest.approx(recs_no_free[1].score)

    def test_recommend_capability_match(
        self, stats_mgr_with_catalog: tuple[UsageStatistics, _MockCatalog],
    ) -> None:
        """能力匹配应影响推荐评分。"""
        mgr, _ = stats_mgr_with_catalog
        # agent-free 有 code_generation + tool_use
        # agent-paid 有 code_generation
        # agent-sub 有 chat
        mgr.record_call("agent-free", success=True, duration_s=1.0, cost=0.01)
        mgr.record_call("agent-paid", success=True, duration_s=1.0, cost=0.01)
        mgr.record_call("agent-sub", success=True, duration_s=1.0, cost=0.01)

        # 要求 code_generation + tool_use
        recs = mgr.recommend_agent(
            capabilities=["code_generation", "tool_use"],
            prefer_free=False,
        )
        # agent-free 匹配 2/2=1.0，agent-paid 匹配 1/2=0.5，agent-sub 匹配 0/2=0.0
        scores_by_name = {r.agent_name: r.score for r in recs}
        assert scores_by_name["agent-free"] > scores_by_name["agent-paid"]
        assert scores_by_name["agent-paid"] > scores_by_name["agent-sub"]

    def test_recommend_score_in_range(
        self, stats_mgr_with_catalog: tuple[UsageStatistics, _MockCatalog],
    ) -> None:
        """推荐评分应在 0-1 范围内。"""
        mgr, _ = stats_mgr_with_catalog
        mgr.record_call("agent-free", success=True, duration_s=1.0, cost=0.01)
        mgr.record_call("agent-paid", success=False, duration_s=10.0, cost=1.0)

        recs = mgr.recommend_agent()
        for rec in recs:
            assert 0.0 <= rec.score <= 1.0
            assert rec.reason != ""
            assert rec.estimated_cost >= 0.0


# ── 5. 成本分析 ──────────────────────────────────────────────────


class TestCostAnalysis:
    """get_cost_analysis 成本分析。"""

    def test_cost_analysis_basic(self, stats_mgr: UsageStatistics) -> None:
        """基本成本分析。"""
        stats_mgr.record_call("cheap", success=True, duration_s=1.0, cost=0.01)
        stats_mgr.record_call("expensive", success=True, duration_s=1.0, cost=0.50)

        analysis = stats_mgr.get_cost_analysis(period="all")
        assert analysis.total_cost == pytest.approx(0.51)
        assert analysis.total_calls == 2
        assert analysis.avg_cost_per_call == pytest.approx(0.255)
        assert analysis.most_expensive == "expensive"
        assert analysis.cheapest == "cheap"
        assert analysis.by_agent["cheap"] == pytest.approx(0.01)
        assert analysis.by_agent["expensive"] == pytest.approx(0.50)

    def test_cost_analysis_empty(self, stats_mgr: UsageStatistics) -> None:
        """无数据时返回零值成本分析。"""
        analysis = stats_mgr.get_cost_analysis(period="all")
        assert analysis.total_cost == 0.0
        assert analysis.total_calls == 0
        assert analysis.most_expensive == ""
        assert analysis.cheapest == ""


# ── 6. 持久化与线程安全 ─────────────────────────────────────────


class TestPersistenceAndConcurrency:
    """持久化与线程安全。"""

    def test_persistence_across_instances(self, tmp_path: Path) -> None:
        """新实例应能加载旧实例写入的数据。"""
        db_path = tmp_path / "usage.db"
        mgr1 = UsageStatistics(db_path=db_path)
        mgr1.record_call("agent-a", success=True, duration_s=1.0, cost=0.01)

        mgr2 = UsageStatistics(db_path=db_path)
        stats_obj = mgr2.get_agent_stats("agent-a", period="all")
        assert stats_obj.total_calls == 1
        assert stats_obj.success_count == 1

    def test_thread_safety(self, stats_mgr: UsageStatistics) -> None:
        """多线程并发记录不应丢失数据。"""
        num_threads = 10
        calls_per_thread = 50
        errors: list[Exception] = []

        def worker() -> None:
            try:
                for i in range(calls_per_thread):
                    stats_mgr.record_call(
                        f"agent-{threading.current_thread().name}",
                        success=True,
                        duration_s=0.1,
                        cost=0.001,
                    )
            except Exception as exc:
                errors.append(exc)

        threads = [
            threading.Thread(target=worker, name=f"t{i}")
            for i in range(num_threads)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        all_stats = stats_mgr.get_all_stats(period="all")
        total = sum(s.total_calls for s in all_stats)
        assert total == num_threads * calls_per_thread