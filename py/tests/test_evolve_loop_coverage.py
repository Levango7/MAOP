"""Coverage tests for evolve.py + maop_loop.py + core/evolution_loop.py
+ core/react_loop.py + core/llm_provider.py.

Uses isolated tmp_path + real instances where possible.
"""
from __future__ import annotations



# ── Evolve ─────────────────────────────────────────────────────────

class TestEvolveEngine:
    def test_init(self, tmp_path):
        from maop.evolve import EvolveEngine
        engine = EvolveEngine(root_dir=str(tmp_path))
        assert engine is not None

    def test_analyze_empty(self, tmp_path):
        from maop.evolve import EvolveEngine
        engine = EvolveEngine(root_dir=str(tmp_path))
        result = engine.analyze()
        assert result is not None
        assert result.action == "analyze"

    def test_suggest_empty(self, tmp_path):
        from maop.evolve import EvolveEngine
        engine = EvolveEngine(root_dir=str(tmp_path))
        result = engine.suggest()
        assert result is not None
        assert result.action == "suggest"

    def test_apply_nonexistent(self, tmp_path):
        from maop.evolve import EvolveEngine
        engine = EvolveEngine(root_dir=str(tmp_path))
        result = engine.apply(suggestion_id="nonexistent")
        assert result is not None
        assert result.action == "apply"

    def test_promote_nonexistent(self, tmp_path):
        from maop.evolve import EvolveEngine
        engine = EvolveEngine(root_dir=str(tmp_path))
        result = engine.promote(suggestion_id="nonexistent")
        assert result is not None
        assert result.action == "promote"


class TestEvolveFunctions:
    def test_load_observability_data_from_db_nonexistent(self, tmp_path):
        from pathlib import Path
        from maop.evolve import _load_observability_data_from_db
        result = _load_observability_data_from_db(Path(tmp_path / "nonexistent.db"))
        assert result == []

    def test_load_observability_data_nonexistent(self, tmp_path):
        from pathlib import Path
        from maop.evolve import _load_observability_data
        result = _load_observability_data(Path(tmp_path))
        assert result == []

    def test_compute_stats_empty(self):
        from maop.evolve import _compute_stats, EvolutionStats
        result = _compute_stats([])
        assert isinstance(result, EvolutionStats)

    def test_compute_stats_with_data(self):
        from maop.evolve import _compute_stats
        data = [
            {"agent": "a1", "routing_key": "k1", "result": {"exit_code": 0, "duration_ms": 100}},
            {"agent": "a1", "routing_key": "k1", "result": {"exit_code": 1, "duration_ms": 200}},
            {"agent": "a2", "routing_key": "k2", "result": {"exit_code": 0, "duration_ms": 50}},
        ]
        result = _compute_stats(data)
        assert result is not None

    def test_generate_suggestions_empty(self):
        from maop.evolve import _generate_suggestions, EvolutionStats
        stats = EvolutionStats()
        result = _generate_suggestions(stats, [])
        assert isinstance(result, list)


class TestEvolveModels:
    def test_agent_stats(self):
        from maop.evolve import AgentStats
        s = AgentStats(agent="a", total=10, success=8, fail=2, rate=80.0, avg_duration_ms=100)
        assert s.agent == "a"

    def test_routing_key_stats(self):
        from maop.evolve import RoutingKeyStats
        s = RoutingKeyStats(routing_key="k", total=5, success=3, rate=60.0)
        assert s.routing_key == "k"

    def test_evolution_stats(self):
        from maop.evolve import EvolutionStats
        s = EvolutionStats()
        assert s is not None

    def test_suggestion(self):
        from maop.evolve import Suggestion
        s = Suggestion(id="S001", description="test", severity="medium")
        assert s.id == "S001"

    def test_evolve_result(self):
        from maop.evolve import EvolveResult
        r = EvolveResult(action="test")
        assert r.action == "test"


# ── Maop Loop ──────────────────────────────────────────────────────

class TestMaopLoop:
    def test_init(self, tmp_path):
        from maop.maop_loop import MaopLoop
        loop = MaopLoop(root_dir=str(tmp_path))
        assert loop is not None


# ── Evolution Loop ─────────────────────────────────────────────────

class TestEvolutionLoop:
    def test_module_import(self):
        import maop.core.evolution.evolution_loop
        assert maop.core.evolution_loop is not None


# ── React Loop ─────────────────────────────────────────────────────

class TestReactLoop:
    def test_module_import(self):
        import maop.core.agent.llm_chat.react_loop
        assert maop.core.react_loop is not None


# ── LLM Provider ───────────────────────────────────────────────────

class TestLLMProvider:
    def test_module_import(self):
        import maop.core.agent.llm_chat.llm_provider
        assert maop.core.llm_provider is not None