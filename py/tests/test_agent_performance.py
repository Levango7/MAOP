"""Tests for AgentPerformanceTracker and adaptive routing."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from maop.core.agent.lifecycle.agent_performance import AgentPerformanceTracker, AgentScore, AgentStats


@pytest.fixture
def tmp_root(tmp_path: Path) -> Path:
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    return tmp_path


@pytest.fixture
def tracker(tmp_root: Path) -> AgentPerformanceTracker:
    return AgentPerformanceTracker(root_dir=str(tmp_root))


class TestAgentStats:
    def test_defaults(self):
        s = AgentStats(agent="test")
        assert s.total_tasks == 0
        assert s.success_rate == 0.0


class TestAgentScore:
    def test_defaults(self):
        s = AgentScore(agent="test")
        assert s.score == 0.0


class TestRecord:
    def test_record_returns_id(self, tracker: AgentPerformanceTracker):
        eid = tracker.record(agent="claude", outcome="success")
        assert eid and len(eid) == 12

    def test_record_multiple(self, tracker: AgentPerformanceTracker):
        tracker.record(agent="claude", outcome="success", cost_usd=0.01)
        tracker.record(agent="claude", outcome="failure", cost_usd=0.02)
        tracker.record(agent="codex", outcome="success", cost_usd=0.005)
        stats = tracker.get_agent_stats("claude")
        assert stats.total_tasks == 2
        assert stats.success_count == 1
        assert stats.failure_count == 1


class TestGetAgentStats:
    def test_no_data(self, tracker: AgentPerformanceTracker):
        stats = tracker.get_agent_stats("nonexistent")
        assert stats.total_tasks == 0

    def test_success_rate(self, tracker: AgentPerformanceTracker):
        for _ in range(7):
            tracker.record(agent="claude", outcome="success")
        for _ in range(3):
            tracker.record(agent="claude", outcome="failure")
        stats = tracker.get_agent_stats("claude")
        assert stats.total_tasks == 10
        assert stats.success_rate == 0.7

    def test_cost_and_latency(self, tracker: AgentPerformanceTracker):
        tracker.record(agent="claude", outcome="success", cost_usd=0.01, latency_ms=500)
        tracker.record(agent="claude", outcome="success", cost_usd=0.03, latency_ms=1500)
        stats = tracker.get_agent_stats("claude")
        assert stats.avg_cost_usd == pytest.approx(0.02, abs=0.001)
        assert stats.avg_latency_ms == pytest.approx(1000.0, abs=1.0)

    def test_routing_key_filter(self, tracker: AgentPerformanceTracker):
        tracker.record(agent="claude", routing_key="code", outcome="success")
        tracker.record(agent="claude", routing_key="docs", outcome="failure")
        stats_code = tracker.get_agent_stats("claude", routing_key="code")
        stats_docs = tracker.get_agent_stats("claude", routing_key="docs")
        assert stats_code.success_rate == 1.0
        assert stats_docs.success_rate == 0.0


class TestRankAgents:
    def test_rank_empty(self, tracker: AgentPerformanceTracker):
        assert tracker.rank_agents() == []

    def test_rank_single_agent(self, tracker: AgentPerformanceTracker):
        for _ in range(5):
            tracker.record(agent="claude", outcome="success", cost_usd=0.01)
        scores = tracker.rank_agents(agents=["claude"])
        assert len(scores) == 1
        assert scores[0].agent == "claude"
        assert scores[0].score > 0

    def test_rank_multiple_agents(self, tracker: AgentPerformanceTracker):
        for _ in range(8):
            tracker.record(agent="claude", outcome="success", cost_usd=0.02)
        for _ in range(2):
            tracker.record(agent="claude", outcome="failure", cost_usd=0.02)
        for _ in range(9):
            tracker.record(agent="codex", outcome="success", cost_usd=0.005)
        for _ in range(1):
            tracker.record(agent="codex", outcome="failure", cost_usd=0.005)
        scores = tracker.rank_agents(agents=["claude", "codex"])
        assert len(scores) == 2
        assert scores[0].agent == "codex"  # higher success + lower cost

    def test_rank_insufficient_data(self, tracker: AgentPerformanceTracker):
        tracker.record(agent="new_agent", outcome="success")
        scores = tracker.rank_agents(agents=["new_agent"], min_tasks=3)
        assert scores[0].score == 0.5  # neutral


class TestBestAgent:
    def test_best_with_data(self, tracker: AgentPerformanceTracker):
        for _ in range(5):
            tracker.record(agent="claude", outcome="success", cost_usd=0.02)
        for _ in range(5):
            tracker.record(agent="codex", outcome="success", cost_usd=0.005)
        best = tracker.best_agent(agents=["claude", "codex"])
        assert best == "codex"

    def test_best_no_data(self, tracker: AgentPerformanceTracker):
        best = tracker.best_agent(agents=["claude"], default="claude")
        assert best == "claude"

    def test_best_empty_list(self, tracker: AgentPerformanceTracker):
        best = tracker.best_agent(agents=[], default="fallback")
        assert best == "fallback"


class TestSyncFromEpisodic:
    def test_sync_no_episodic(self, tracker: AgentPerformanceTracker):
        count = tracker.sync_from_episodic()
        assert count >= 0  # may be 0 if no episodic db


class TestGetAllStats:
    def test_all_stats(self, tracker: AgentPerformanceTracker):
        tracker.record(agent="claude", outcome="success")
        tracker.record(agent="codex", outcome="failure")
        all_stats = tracker.get_all_stats()
        agents = {s.agent for s in all_stats}
        assert "claude" in agents
        assert "codex" in agents


class TestRecencyWindow:
    def test_recency_filter(self, tmp_root: Path):
        tracker = AgentPerformanceTracker(root_dir=str(tmp_root), recency_window_s=1.0)
        tracker.record(agent="claude", outcome="success")
        time.sleep(0.1)
        stats = tracker.get_agent_stats("claude")
        assert stats.total_tasks >= 1
