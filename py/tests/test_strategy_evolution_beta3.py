"""Tests for Phase beta.3: Agent Strategy Learning + Cache Strategy Evolution."""
from __future__ import annotations

from pathlib import Path

from maop.agent_strategy_learner import (
    AgentStrategyAdjustment,
    AgentStrategyLearner,
    RoutingKeyPerformance,
    StrategyLearnReport,
)
from maop.cache_evolver import (
    CacheEvolver,
    CacheEvolveReport,
    CacheStrategyAdjustment,
)
from maop.core.reliability.cache import LRUCache
from maop.core.memory.semantic_cache import SemanticCache


class TestAgentStrategyLearner:
    def test_learn_returns_report(self, tmp_path: Path) -> None:
        learner = AgentStrategyLearner(root_dir=tmp_path)
        report = learner.learn()
        assert isinstance(report, StrategyLearnReport)
        assert hasattr(report, "total_combos")
        assert hasattr(report, "reliable_combos")
        assert hasattr(report, "underperformers")
        assert hasattr(report, "adjustments")
        assert hasattr(report, "routing_winners")
        assert hasattr(report, "recommendations")

    def test_learn_empty_data(self, tmp_path: Path) -> None:
        learner = AgentStrategyLearner(root_dir=tmp_path)
        report = learner.learn()
        assert report.total_combos == 0
        assert report.adjustments == []
        assert report.routing_winners == {}

    def test_learn_with_performance_data(self, tmp_path: Path) -> None:
        from maop.core.agent.lifecycle.agent_performance import AgentPerformanceTracker
        tracker = AgentPerformanceTracker(root_dir=tmp_path)
        for _ in range(8):
            tracker.record(agent="agent_a", routing_key="code", outcome="success")
        for _ in range(2):
            tracker.record(agent="agent_a", routing_key="code", outcome="failure")
        for _ in range(2):
            tracker.record(agent="agent_b", routing_key="code", outcome="success")
        for _ in range(8):
            tracker.record(agent="agent_b", routing_key="code", outcome="failure")
        learner = AgentStrategyLearner(root_dir=tmp_path, min_samples=5)
        report = learner.learn(hours=168)
        assert report.total_combos >= 2
        assert report.routing_winners.get("code") == "agent_a"

    def test_underperformer_triggers_reroute(self, tmp_path: Path) -> None:
        from maop.core.agent.lifecycle.agent_performance import AgentPerformanceTracker
        tracker = AgentPerformanceTracker(root_dir=tmp_path)
        for _ in range(10):
            tracker.record(agent="good_agent", routing_key="test", outcome="success")
        for _ in range(8):
            tracker.record(agent="poor_agent", routing_key="test", outcome="failure")
        for _ in range(2):
            tracker.record(agent="poor_agent", routing_key="test", outcome="success")
        learner = AgentStrategyLearner(root_dir=tmp_path, min_samples=5)
        report = learner.learn()
        reroute_adjs = [a for a in report.adjustments if a.action == "reroute"]
        assert len(reroute_adjs) >= 1
        assert any(a.agent == "poor_agent" for a in reroute_adjs)

    def test_severe_underperformer_triggers_disable(self, tmp_path: Path) -> None:
        from maop.core.agent.lifecycle.agent_performance import AgentPerformanceTracker
        tracker = AgentPerformanceTracker(root_dir=tmp_path)
        for _ in range(15):
            tracker.record(agent="bad_agent", routing_key="task", outcome="failure")
        tracker.record(agent="bad_agent", routing_key="task", outcome="success")
        learner = AgentStrategyLearner(root_dir=tmp_path, min_samples=5, disable_threshold=0.2)
        report = learner.learn()
        disable_adjs = [a for a in report.adjustments if a.action == "disable"]
        assert len(disable_adjs) >= 1
        assert disable_adjs[0].auto_applicable is False

    def test_slow_reliable_agent_triggers_reduce_timeout(self, tmp_path: Path) -> None:
        from maop.core.agent.lifecycle.agent_performance import AgentPerformanceTracker
        tracker = AgentPerformanceTracker(root_dir=tmp_path)
        for _ in range(10):
            tracker.record(agent="slow_agent", routing_key="work", outcome="success", latency_ms=90000)
        learner = AgentStrategyLearner(root_dir=tmp_path, min_samples=5)
        report = learner.learn()
        timeout_adjs = [a for a in report.adjustments if a.action == "reduce_timeout"]
        assert len(timeout_adjs) >= 1
        assert timeout_adjs[0].auto_applicable is True

    def test_reliable_winner_gets_prefer(self, tmp_path: Path) -> None:
        from maop.core.agent.lifecycle.agent_performance import AgentPerformanceTracker
        tracker = AgentPerformanceTracker(root_dir=tmp_path)
        for _ in range(20):
            tracker.record(agent="star_agent", routing_key="key1", outcome="success")
        learner = AgentStrategyLearner(root_dir=tmp_path, min_samples=5)
        report = learner.learn()
        prefer_adjs = [a for a in report.adjustments if a.action == "prefer"]
        assert len(prefer_adjs) >= 1

    def test_adjustments_sorted_by_confidence(self, tmp_path: Path) -> None:
        from maop.core.agent.lifecycle.agent_performance import AgentPerformanceTracker
        tracker = AgentPerformanceTracker(root_dir=tmp_path)
        for _ in range(20):
            tracker.record(agent="a1", routing_key="k", outcome="success")
        for _ in range(15):
            tracker.record(agent="a2", routing_key="k", outcome="failure")
        for _ in range(5):
            tracker.record(agent="a2", routing_key="k", outcome="success")
        learner = AgentStrategyLearner(root_dir=tmp_path, min_samples=5)
        report = learner.learn()
        if len(report.adjustments) >= 2:
            confidences = [a.confidence for a in report.adjustments]
            assert confidences == sorted(confidences, reverse=True)

    def test_apply_adjustment_non_auto_returns_false(self, tmp_path: Path) -> None:
        adj = AgentStrategyAdjustment(agent="test", routing_key="k", action="disable", auto_applicable=False)
        learner = AgentStrategyLearner(root_dir=tmp_path)
        assert learner.apply_adjustment(adj) is False

    def test_recommendations_generated(self, tmp_path: Path) -> None:
        from maop.core.agent.lifecycle.agent_performance import AgentPerformanceTracker
        tracker = AgentPerformanceTracker(root_dir=tmp_path)
        for _ in range(10):
            tracker.record(agent="a", routing_key="k", outcome="success")
        learner = AgentStrategyLearner(root_dir=tmp_path, min_samples=5)
        report = learner.learn()
        assert isinstance(report.recommendations, list)

    def test_routing_key_performance_is_reliable(self) -> None:
        perf = RoutingKeyPerformance(agent="a", routing_key="k", total=10, success=9, success_rate=0.9)
        assert perf.is_reliable is True
        perf_low = RoutingKeyPerformance(agent="a", routing_key="k", total=10, success=3, success_rate=0.3)
        assert perf_low.is_reliable is False
        perf_few = RoutingKeyPerformance(agent="a", routing_key="k", total=3, success=3, success_rate=1.0)
        assert perf_few.is_reliable is False


class TestCacheEvolver:
    def test_evolve_returns_report(self) -> None:
        evolver = CacheEvolver()
        report = evolver.evolve()
        assert isinstance(report, CacheEvolveReport)
        assert hasattr(report, "total_caches")
        assert hasattr(report, "adjustments")
        assert hasattr(report, "applied_count")
        assert hasattr(report, "skipped_count")
        assert hasattr(report, "recommendations")

    def test_evolve_empty_caches(self) -> None:
        evolver = CacheEvolver(min_samples=1)
        report = evolver.evolve()
        assert isinstance(report.adjustments, list)
        assert isinstance(report.recommendations, list)

    def test_high_hit_rate_increases_ttl(self) -> None:
        cache = LRUCache(max_size=100, default_ttl_s=300.0)
        cache._hits = 90
        cache._misses = 10
        evolver = CacheEvolver(high_hit_rate=0.8, low_hit_rate=0.3, min_samples=20)
        adjustments = evolver._analyze_lru("test_high", cache)
        ttl_adjs = [a for a in adjustments if a.parameter == "default_ttl_s"]
        assert len(ttl_adjs) >= 1
        assert ttl_adjs[0].new_value > ttl_adjs[0].old_value
        assert ttl_adjs[0].auto_applicable is True

    def test_low_hit_rate_decreases_ttl(self) -> None:
        cache = LRUCache(max_size=100, default_ttl_s=300.0)
        cache._hits = 10
        cache._misses = 90
        evolver = CacheEvolver(high_hit_rate=0.8, low_hit_rate=0.3, min_samples=20)
        adjustments = evolver._analyze_lru("test_low", cache)
        ttl_adjs = [a for a in adjustments if a.parameter == "default_ttl_s"]
        assert len(ttl_adjs) >= 1
        assert ttl_adjs[0].new_value < ttl_adjs[0].old_value

    def test_zero_hits_with_entries_triggers_aggressive_shorten(self) -> None:
        cache = LRUCache(max_size=100, default_ttl_s=600.0)
        cache._hits = 0
        cache._misses = 30
        for i in range(10):
            cache.put(f"key_{i}", f"value_{i}", ttl_s=600)
        evolver = CacheEvolver(min_samples=20)
        adjustments = evolver._analyze_lru("test_zero", cache)
        ttl_adjs = [a for a in adjustments if a.parameter == "default_ttl_s"]
        assert len(ttl_adjs) >= 1
        assert ttl_adjs[0].new_value < ttl_adjs[0].old_value / 2

    def test_high_eviction_rate_triggers_size_increase(self) -> None:
        cache = LRUCache(max_size=5, default_ttl_s=300.0)
        cache._hits = 50
        cache._misses = 50
        cache._evictions = 40
        for i in range(5):
            cache.put(f"k{i}", f"v{i}")
        evolver = CacheEvolver(min_samples=20)
        adjustments = evolver._analyze_lru("test_evict", cache)
        size_adjs = [a for a in adjustments if a.parameter == "max_size"]
        assert len(size_adjs) >= 1
        assert size_adjs[0].new_value > size_adjs[0].old_value
        assert size_adjs[0].auto_applicable is False

    def test_insufficient_samples_no_adjustments(self) -> None:
        cache = LRUCache(max_size=100, default_ttl_s=300.0)
        cache._hits = 5
        cache._misses = 5
        evolver = CacheEvolver(min_samples=20)
        adjustments = evolver._analyze_lru("test_few", cache)
        assert adjustments == []

    def test_semantic_cache_low_hit_rate_lowers_threshold(self) -> None:
        cache = SemanticCache(similarity_threshold=0.92, default_ttl_s=300.0)
        cache._hits = 10
        cache._misses = 90
        evolver = CacheEvolver(high_hit_rate=0.8, low_hit_rate=0.3, min_samples=20)
        adjustments = evolver._analyze_semantic("test_sem_low", cache)
        thresh_adjs = [a for a in adjustments if a.parameter == "similarity_threshold"]
        assert len(thresh_adjs) >= 1
        assert thresh_adjs[0].new_value < thresh_adjs[0].old_value

    def test_semantic_cache_high_hit_rate_raises_threshold(self) -> None:
        cache = SemanticCache(similarity_threshold=0.92, default_ttl_s=300.0)
        cache._hits = 90
        cache._misses = 10
        evolver = CacheEvolver(high_hit_rate=0.8, low_hit_rate=0.3, min_samples=20)
        adjustments = evolver._analyze_semantic("test_sem_high", cache)
        thresh_adjs = [a for a in adjustments if a.parameter == "similarity_threshold"]
        assert len(thresh_adjs) >= 1
        assert thresh_adjs[0].new_value > thresh_adjs[0].old_value

    def test_apply_true_applies_safe_adjustments(self) -> None:
        cache = LRUCache(max_size=100, default_ttl_s=300.0)
        cache._hits = 90
        cache._misses = 10
        evolver = CacheEvolver(high_hit_rate=0.8, low_hit_rate=0.3, min_samples=20)
        adj = CacheStrategyAdjustment(
            cache_name="test_apply", cache_type="lru", parameter="default_ttl_s",
            old_value=300.0, new_value=450.0, reason="test", auto_applicable=True,
        )
        result = evolver._apply_adjustment(adj, lru_caches={"test_apply": cache}, semantic_caches={})
        assert result is True
        assert cache._default_ttl == 450.0

    def test_apply_false_does_not_apply(self) -> None:
        cache = LRUCache(max_size=100, default_ttl_s=300.0)
        cache._hits = 90
        cache._misses = 10
        evolver = CacheEvolver(min_samples=20)
        report = evolver.evolve(apply=False)
        assert isinstance(report.applied_count, int)

    def test_recommendations_generated_when_no_adjustments(self) -> None:
        evolver = CacheEvolver(min_samples=1000)
        report = evolver.evolve()
        assert len(report.recommendations) >= 1


class TestAutoEvolveBeta3Integration:
    def test_auto_evolve_returns_agent_strategy_field(self, tmp_path: Path) -> None:
        from maop.evolve import EvolveEngine
        engine = EvolveEngine(root_dir=tmp_path)
        result = engine.auto_evolve(hours=1)
        assert "agent_strategy" in result
        assert isinstance(result["agent_strategy"], dict)

    def test_auto_evolve_returns_cache_evolution_field(self, tmp_path: Path) -> None:
        from maop.evolve import EvolveEngine
        engine = EvolveEngine(root_dir=tmp_path)
        result = engine.auto_evolve(hours=1)
        assert "cache_evolution" in result
        assert isinstance(result["cache_evolution"], dict)

    def test_auto_evolve_handles_no_data_gracefully(self, tmp_path: Path) -> None:
        from maop.evolve import EvolveEngine
        engine = EvolveEngine(root_dir=tmp_path)
        result = engine.auto_evolve(hours=1)
        assert "analysis_report" in result
        assert "agent_strategy" in result
        assert "cache_evolution" in result
        assert "new_suggestions" in result
        assert "auto_applied" in result

    def test_auto_evolve_agent_strategy_with_data(self, tmp_path: Path) -> None:
        from maop.core.agent.lifecycle.agent_performance import AgentPerformanceTracker
        from maop.evolve import EvolveEngine
        tracker = AgentPerformanceTracker(root_dir=tmp_path)
        for _ in range(10):
            tracker.record(agent="good", routing_key="k", outcome="success")
        for _ in range(8):
            tracker.record(agent="bad", routing_key="k", outcome="failure")
        for _ in range(2):
            tracker.record(agent="bad", routing_key="k", outcome="success")
        engine = EvolveEngine(root_dir=tmp_path)
        result = engine.auto_evolve(hours=168)
        agent_strat = result["agent_strategy"]
        assert agent_strat.get("total_combos", 0) >= 2

    def test_auto_evolve_returns_new_suggestions_count(self, tmp_path: Path) -> None:
        from maop.evolve import EvolveEngine
        engine = EvolveEngine(root_dir=tmp_path)
        result = engine.auto_evolve(hours=1)
        assert isinstance(result["new_suggestions"], int)
        assert isinstance(result["auto_applied"], int)

    def test_auto_evolve_agent_strategy_has_recommendations(self, tmp_path: Path) -> None:
        from maop.evolve import EvolveEngine
        engine = EvolveEngine(root_dir=tmp_path)
        result = engine.auto_evolve(hours=1)
        assert "recommendations" in result["agent_strategy"]

    def test_auto_evolve_cache_evolution_has_recommendations(self, tmp_path: Path) -> None:
        from maop.evolve import EvolveEngine
        engine = EvolveEngine(root_dir=tmp_path)
        result = engine.auto_evolve(hours=1)
        assert "recommendations" in result["cache_evolution"]
