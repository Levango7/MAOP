"""Tests for maop.core.agent_evolution — agent self-evolution engine."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from maop.core.agent.evolution.agent_evolution import (
    AgentEvolution,
    EvolutionResult,
    EvolutionSuggestion,
    HIGH_LATENCY_THRESHOLD_MS,
)


@pytest.fixture
def evolution(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AgentEvolution:
    """Create an AgentEvolution with an isolated temp database."""
    db_path = tmp_path / "agent_evolution.db"

    def fake_get_db_path(module_name: str = "", *, legacy_fallback: str = "") -> Path:
        return db_path

    monkeypatch.setattr("maop.core.agent.memory_ctx.agent_memory.get_db_path", fake_get_db_path)
    return AgentEvolution(root_dir=tmp_path)


# ── Dataclass tests ──────────────────────────────────────────────


class TestEvolutionSuggestion:
    def test_defaults(self) -> None:
        s = EvolutionSuggestion()
        assert s.category == ""
        assert s.priority == "low"
        assert s.changes == {}
        assert s.confidence == 0.0

    def test_custom_values(self) -> None:
        s = EvolutionSuggestion(
            category="performance", priority="high",
            description="Slow", action="auto_applied",
            changes={"key": "val"}, confidence=0.9,
        )
        assert s.category == "performance"
        assert s.confidence == 0.9


class TestEvolutionResult:
    def test_defaults(self) -> None:
        r = EvolutionResult()
        assert r.agent_name == ""
        assert r.suggestions == []
        assert r.auto_applied == []
        assert r.errors == []

    def test_model_dump(self) -> None:
        s = EvolutionSuggestion(category="performance", priority="high")
        r = EvolutionResult(
            agent_name="claude", suggestions=[s],
            summary="test",
        )
        d = r.model_dump()
        assert d["agent_name"] == "claude"
        assert len(d["suggestions"]) == 1
        assert d["suggestions"][0]["category"] == "performance"
        assert d["summary"] == "test"


# ── _analyze_performance ─────────────────────────────────────────


class TestAnalyzePerformance:
    def test_empty_performances(self, evolution: AgentEvolution) -> None:
        result = evolution._analyze_performance("claude", [])
        assert result == []

    def test_no_latency_data(self, evolution: AgentEvolution) -> None:
        perfs = [{"content": {"success": True}}]
        result = evolution._analyze_performance("claude", perfs)
        assert result == []

    def test_high_avg_latency(self, evolution: AgentEvolution) -> None:
        perfs = [
            {"content": {"latency_ms": HIGH_LATENCY_THRESHOLD_MS + 5000}},
            {"content": {"latency_ms": HIGH_LATENCY_THRESHOLD_MS + 3000}},
        ]
        result = evolution._analyze_performance("claude", perfs)
        assert len(result) == 1
        assert result[0].category == "performance"
        assert result[0].priority == "high"
        assert result[0].action == "manual_required"

    def test_high_peak_latency_only(self, evolution: AgentEvolution) -> None:
        perfs = [
            {"content": {"latency_ms": 1000}},
            {"content": {"latency_ms": HIGH_LATENCY_THRESHOLD_MS + 5000}},
        ]
        result = evolution._analyze_performance("claude", perfs)
        assert len(result) == 1
        assert result[0].priority == "medium"
        assert result[0].action == "monitoring"

    def test_normal_latency_no_suggestion(self, evolution: AgentEvolution) -> None:
        perfs = [
            {"content": {"latency_ms": 100}},
            {"content": {"latency_ms": 200}},
        ]
        result = evolution._analyze_performance("claude", perfs)
        assert result == []


# ── _analyze_reliability ─────────────────────────────────────────


class TestAnalyzeReliability:
    def test_empty_data(self, evolution: AgentEvolution) -> None:
        result = evolution._analyze_reliability("claude", [], [])
        assert result == []

    def test_high_failure_rate(self, evolution: AgentEvolution) -> None:
        perfs = [
            {"content": {"success": False}},
            {"content": {"success": False}},
            {"content": {"success": True}},
        ]
        result = evolution._analyze_reliability("claude", [], perfs)
        assert len(result) >= 1
        reliability_s = [s for s in result if s.category == "reliability"]
        assert any(s.priority == "high" for s in reliability_s)

    def test_normal_failure_rate(self, evolution: AgentEvolution) -> None:
        perfs = [
            {"content": {"success": True}},
            {"content": {"success": True}},
            {"content": {"success": True}},
            {"content": {"success": False}},
        ]
        result = evolution._analyze_reliability("claude", [], perfs)
        high_priority = [s for s in result if s.priority == "high"]
        assert high_priority == []

    def test_many_error_patterns(self, evolution: AgentEvolution) -> None:
        errors = [{"content": {"err": f"e{i}"}} for i in range(5)]
        perfs = [{"content": {"success": True}}]
        result = evolution._analyze_reliability("claude", errors, perfs)
        assert any(s.action == "manual_required" for s in result)


# ── _analyze_capabilities ────────────────────────────────────────


class TestAnalyzeCapabilities:
    def test_empty_interactions(self, evolution: AgentEvolution) -> None:
        result = evolution._analyze_capabilities("claude", [])
        assert result == []

    def test_with_interactions(self, evolution: AgentEvolution) -> None:
        interactions = [
            {"content": {"task_type": "code_generation"}},
            {"content": {"task_type": "code_generation"}},
            {"content": {"task_type": "code_generation"}},
        ]
        result = evolution._analyze_capabilities("claude", interactions)
        assert isinstance(result, list)


# ── _analyze_preferences ─────────────────────────────────────────


class TestAnalyzePreferences:
    def test_empty_preferences(self, evolution: AgentEvolution) -> None:
        result = evolution._analyze_preferences("claude", [])
        assert result == []

    def test_with_preferences(self, evolution: AgentEvolution) -> None:
        prefs = [
            {"content": {"param": "temperature", "value": 0.7}},
        ]
        result = evolution._analyze_preferences("claude", prefs)
        assert isinstance(result, list)


# ── _analyze_error_lessons ───────────────────────────────────────


class TestAnalyzeErrorLessons:
    def test_empty_data(self, evolution: AgentEvolution) -> None:
        result = evolution._analyze_error_lessons("claude", [], [])
        assert result == []

    def test_with_lessons(self, evolution: AgentEvolution) -> None:
        lessons = [{"content": {"error": "timeout", "solution": "increase timeout"}}]
        errors = [{"content": {"error": "timeout"}}]
        result = evolution._analyze_error_lessons("claude", lessons, errors)
        assert isinstance(result, list)


# ── get_status ───────────────────────────────────────────────────


class TestGetStatus:
    def test_new_agent_status(self, evolution: AgentEvolution) -> None:
        status = evolution.get_status("new-agent")
        assert status["agent_name"] == "new-agent"
        assert status["total_memories"] == 0
        assert status["evolution_count"] == 0
        assert status["last_evolution"] is None
        assert status["ready_for_evolution"] is False

    def test_status_after_storing_memories(
        self, evolution: AgentEvolution
    ) -> None:
        for i in range(15):
            evolution._memory.store(
                "claude", "interaction", {"idx": i},
            )
        status = evolution.get_status("claude")
        assert status["total_memories"] == 15
        assert status["ready_for_evolution"] is True

    def test_status_with_evolution_history(
        self, evolution: AgentEvolution
    ) -> None:
        evolution._memory.record_evolution(
            "claude", "config_change", "test change", {"key": "val"},
        )
        status = evolution.get_status("claude")
        assert status["evolution_count"] == 1
        assert status["last_evolution"] is not None
        assert len(status["recent_evolution_history"]) == 1


# ── evolve (legacy fallback) ─────────────────────────────────────


class TestEvolveLegacy:
    @pytest.mark.asyncio
    async def test_evolve_no_memory(self, evolution: AgentEvolution) -> None:
        """Evolve on an agent with no memory should return empty result."""
        result = await evolution._evolve_legacy("new-agent")
        assert result.agent_name == "new-agent"
        assert isinstance(result.suggestions, list)
        assert "Generated" in result.summary

    @pytest.mark.asyncio
    async def test_evolve_with_performance_data(
        self, evolution: AgentEvolution
    ) -> None:
        """Evolve with high latency data should generate performance suggestion."""
        for _ in range(3):
            evolution._memory.store(
                "claude", "performance",
                {"latency_ms": HIGH_LATENCY_THRESHOLD_MS + 5000, "success": True},
            )
        result = await evolution._evolve_legacy("claude")
        assert any(s.category == "performance" for s in result.suggestions)

    @pytest.mark.asyncio
    async def test_evolve_records_history(
        self, evolution: AgentEvolution
    ) -> None:
        await evolution._evolve_legacy("claude")
        history = evolution._memory.get_evolution_history("claude")
        assert len(history) == 1
        assert history[0]["evolution_type"] == "full_analysis"


# ── evolve (EvolutionLoop delegation) ────────────────────────────


class TestEvolveDelegation:
    @pytest.mark.asyncio
    async def test_evolve_falls_back_on_loop_error(
        self, evolution: AgentEvolution
    ) -> None:
        """If EvolutionLoop raises, evolve falls back to legacy."""
        with patch("maop.core.evolution.evolution_loop.EvolutionLoop") as MockLoop:
            MockLoop.return_value.evolve_agent.side_effect = RuntimeError("fail")
            result = await evolution.evolve("claude")
            assert result.agent_name == "claude"
            assert isinstance(result, EvolutionResult)

    @pytest.mark.asyncio
    async def test_evolve_with_loop_success(
        self, evolution: AgentEvolution
    ) -> None:
        """Evolve with a working EvolutionLoop returns converted suggestions."""
        with patch("maop.core.evolution.evolution_loop.EvolutionLoop") as MockLoop:
            MockLoop.return_value.evolve_agent.return_value = {
                "suggestions": [
                    {
                        "category": "performance",
                        "severity": "HIGH",
                        "description": "Too slow",
                        "mutation_type": "auto_applied",
                        "mutation_params": {"timeout": 60},
                    }
                ],
                "auto_applied": [],
                "summary": "Found 1 issue",
            }
            result = await evolution.evolve("claude")
            assert len(result.suggestions) == 1
            assert result.suggestions[0].category == "performance"
            assert result.suggestions[0].priority == "high"
            assert result.summary == "Found 1 issue"