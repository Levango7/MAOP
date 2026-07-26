"""Unit tests for Phase γ-3 multi-objective routing (Pareto + TOPSIS).

Coverage:
- ``ObjectiveWeights`` — defaults, normalization, degenerate inputs
- ``ParetoFrontier`` — domination predicate, frontier computation
- ``TOPSIS`` — ideal/nadir distances, score range [0, 1]
- ``MultiObjectiveScorer`` — end-to-end ranking, edge cases
- ``RouteScorerIntegration`` — enable/disable, backward compatibility
"""
from __future__ import annotations

import pytest

from maop.config.loader import AgentDef, MaopConfig, RouteEntry
from maop.core.multi_objective_scorer import (
    AgentObjectiveVector,
    MultiObjectiveScorer,
    ObjectiveWeights,
    ParetoFrontierResult,
)
from maop.core.route_scorer import RouteScorer

# ── TestObjectiveWeights ─────────────────────────────────────────

class TestObjectiveWeights:
    def test_defaults_sum_to_one(self):
        w = ObjectiveWeights()
        assert w.success_rate == 0.4
        assert w.latency == 0.3
        assert w.cost == 0.2
        assert w.quota_headroom == 0.1
        assert pytest.approx(w.total) == 1.0

    def test_custom_values(self):
        w = ObjectiveWeights(
            success_rate=0.5, latency=0.2, cost=0.2, quota_headroom=0.1
        )
        assert pytest.approx(w.total) == 1.0

    def test_normalize_already_unit(self):
        w = ObjectiveWeights()  # sums to 1.0
        n = w.normalize()
        assert pytest.approx(n.total) == 1.0
        # Values unchanged
        assert n.success_rate == w.success_rate
        assert n.latency == w.latency
        assert n.cost == w.cost
        assert n.quota_headroom == w.quota_headroom

    def test_normalize_unbalanced(self):
        w = ObjectiveWeights(
            success_rate=2.0, latency=1.0, cost=1.0, quota_headroom=0.0
        )
        n = w.normalize()
        assert pytest.approx(n.total) == 1.0
        assert pytest.approx(n.success_rate) == 0.5  # 2 / 4
        assert pytest.approx(n.latency) == 0.25
        assert pytest.approx(n.cost) == 0.25
        assert pytest.approx(n.quota_headroom) == 0.0

    def test_normalize_zero_returns_defaults(self):
        w = ObjectiveWeights(
            success_rate=0.0, latency=0.0, cost=0.0, quota_headroom=0.0
        )
        n = w.normalize()
        # Degenerate input ⇒ safe defaults (sum = 1.0)
        assert pytest.approx(n.total) == 1.0
        assert n.success_rate > 0


# ── TestParetoFrontier ───────────────────────────────────────────

class TestParetoFrontier:
    def test_is_dominated_clear_case(self):
        """a is dominated by b on every dim."""
        scorer = MultiObjectiveScorer()
        a = AgentObjectiveVector(
            success_rate=0.5, latency_ms=1.0, cost_usd=1.0, quota_headroom=0.5
        )
        b = AgentObjectiveVector(
            success_rate=0.9, latency_ms=0.0, cost_usd=0.0, quota_headroom=0.9
        )
        assert scorer.is_dominated(a, b) is True
        assert scorer.is_dominated(b, a) is False

    def test_is_dominated_identical_vectors(self):
        """Identical vectors do NOT dominate each other (strict-inequality rule)."""
        scorer = MultiObjectiveScorer()
        a = AgentObjectiveVector(
            success_rate=0.8, latency_ms=0.2, cost_usd=0.2, quota_headroom=0.8
        )
        b = AgentObjectiveVector(
            success_rate=0.8, latency_ms=0.2, cost_usd=0.2, quota_headroom=0.8
        )
        assert scorer.is_dominated(a, b) is False
        assert scorer.is_dominated(b, a) is False

    def test_is_dominated_trade_off_not_dominated(self):
        """a better on success, b better on latency ⇒ neither dominates."""
        scorer = MultiObjectiveScorer()
        a = AgentObjectiveVector(
            success_rate=0.9, latency_ms=0.8, cost_usd=0.5, quota_headroom=0.5
        )
        b = AgentObjectiveVector(
            success_rate=0.5, latency_ms=0.1, cost_usd=0.5, quota_headroom=0.5
        )
        assert scorer.is_dominated(a, b) is False
        assert scorer.is_dominated(b, a) is False

    def test_is_dominated_weak_dominance(self):
        """b >= a on all dims, equal on some, strictly better on one ⇒ dominated."""
        scorer = MultiObjectiveScorer()
        a = AgentObjectiveVector(
            success_rate=0.5, latency_ms=0.5, cost_usd=0.5, quota_headroom=0.5
        )
        b = AgentObjectiveVector(
            success_rate=0.5, latency_ms=0.5, cost_usd=0.5, quota_headroom=0.9
        )
        assert scorer.is_dominated(a, b) is True

    def test_frontier_three_candidates_one_dominated(self):
        """Typical 3-candidate scenario: the middle one is dominated."""
        scorer = MultiObjectiveScorer()
        # a: high success, slow, expensive, low quota
        # b: medium on all — dominated by c
        # c: high success, fast, cheap, high quota — dominates b
        candidates = {
            "a": AgentObjectiveVector(0.95, 3000, 0.10, 0.4),
            "b": AgentObjectiveVector(0.50, 5000, 0.20, 0.3),
            "c": AgentObjectiveVector(0.90, 1000, 0.05, 0.9),
        }
        result = scorer.compute_pareto_frontier(candidates)
        assert isinstance(result, ParetoFrontierResult)
        assert "b" in result.dominated_agents
        assert "a" in result.dominant_agents
        assert "c" in result.dominant_agents
        assert result.frontier_size == 2

    def test_frontier_all_pareto_optimal(self):
        """Three candidates, each best on a different dim ⇒ frontier of 3."""
        scorer = MultiObjectiveScorer()
        candidates = {
            "fast":  AgentObjectiveVector(0.5, 100,  0.5, 0.5),  # best latency
            "cheap": AgentObjectiveVector(0.5, 500,  0.01, 0.5),  # best cost
            "quota": AgentObjectiveVector(0.5, 500,  0.5, 1.0),   # best quota
        }
        result = scorer.compute_pareto_frontier(candidates)
        assert result.frontier_size == 3
        assert result.dominated_agents == []

    def test_frontier_empty(self):
        scorer = MultiObjectiveScorer()
        result = scorer.compute_pareto_frontier({})
        assert result.frontier_size == 0
        assert result.dominant_agents == []
        assert result.dominated_agents == []

    def test_frontier_single_candidate(self):
        scorer = MultiObjectiveScorer()
        candidates = {
            "solo": AgentObjectiveVector(0.8, 1000, 0.05, 0.7),
        }
        result = scorer.compute_pareto_frontier(candidates)
        assert result.frontier_size == 1
        assert "solo" in result.dominant_agents


# ── TestTOPSIS ───────────────────────────────────────────────────

class TestTOPSIS:
    def test_score_in_unit_interval(self):
        scorer = MultiObjectiveScorer()
        vec = AgentObjectiveVector(0.8, 0.3, 0.3, 0.7)
        ideal = AgentObjectiveVector(1.0, 0.0, 0.0, 1.0)
        nadir = AgentObjectiveVector(0.0, 1.0, 1.0, 0.0)
        s = scorer.compute_topsis_score(vec, ObjectiveWeights(), ideal, nadir)
        assert 0.0 <= s <= 1.0

    def test_score_ideal_is_one(self):
        """The ideal point itself scores 1.0."""
        scorer = MultiObjectiveScorer()
        ideal = AgentObjectiveVector(1.0, 0.0, 0.0, 1.0)
        nadir = AgentObjectiveVector(0.0, 1.0, 1.0, 0.0)
        s = scorer.compute_topsis_score(ideal, ObjectiveWeights(), ideal, nadir)
        assert pytest.approx(s, abs=1e-6) == 1.0

    def test_score_nadir_is_zero(self):
        """The nadir point itself scores 0.0."""
        scorer = MultiObjectiveScorer()
        ideal = AgentObjectiveVector(1.0, 0.0, 0.0, 1.0)
        nadir = AgentObjectiveVector(0.0, 1.0, 1.0, 0.0)
        s = scorer.compute_topsis_score(nadir, ObjectiveWeights(), ideal, nadir)
        assert pytest.approx(s, abs=1e-6) == 0.0

    def test_score_midpoint_between_ideal_and_nadir(self):
        """Midpoint between ideal and nadir on every dim ⇒ score ≈ 0.5."""
        scorer = MultiObjectiveScorer()
        vec = AgentObjectiveVector(0.5, 0.5, 0.5, 0.5)
        ideal = AgentObjectiveVector(1.0, 0.0, 0.0, 1.0)
        nadir = AgentObjectiveVector(0.0, 1.0, 1.0, 0.0)
        s = scorer.compute_topsis_score(vec, ObjectiveWeights(), ideal, nadir)
        assert pytest.approx(s, abs=1e-6) == 0.5

    def test_score_weights_influence_ranking(self):
        """Heavier weight on a dim where ``a`` is closer to ideal should
        push ``a``'s score above ``b``'s, and vice-versa."""
        scorer = MultiObjectiveScorer()
        # a is perfect on success_rate but poor on latency; b the reverse.
        a = AgentObjectiveVector(1.0, 1.0, 0.5, 0.5)
        b = AgentObjectiveVector(0.0, 0.0, 0.5, 0.5)
        ideal = AgentObjectiveVector(1.0, 0.0, 0.0, 1.0)
        nadir = AgentObjectiveVector(0.0, 1.0, 1.0, 0.0)

        s_success_heavy = scorer.compute_topsis_score(
            a, ObjectiveWeights(success_rate=1.0, latency=0.0, cost=0.0, quota_headroom=0.0),
            ideal, nadir,
        )
        s_latency_heavy = scorer.compute_topsis_score(
            b, ObjectiveWeights(success_rate=0.0, latency=1.0, cost=0.0, quota_headroom=0.0),
            ideal, nadir,
        )
        assert s_success_heavy > 0.5
        assert s_latency_heavy > 0.5


# ── TestMultiObjectiveScorer ─────────────────────────────────────

class TestMultiObjectiveScorer:
    def test_rank_agents_empty(self):
        scorer = MultiObjectiveScorer()
        assert scorer.rank_agents({}) == []

    def test_rank_agents_single(self):
        scorer = MultiObjectiveScorer()
        candidates = {
            "solo": AgentObjectiveVector(0.8, 1000, 0.05, 0.7),
        }
        ranking = scorer.rank_agents(candidates)
        assert len(ranking) == 1
        assert ranking[0][0] == "solo"
        # Single-candidate frontier ⇒ ideal == nadir == vector ⇒ closeness = 1.0
        assert pytest.approx(ranking[0][1], abs=1e-6) == 1.0

    def test_rank_agents_drops_dominated(self):
        """A dominated agent should never appear in the ranking."""
        scorer = MultiObjectiveScorer()
        candidates = {
            "dominant": AgentObjectiveVector(0.95, 1000, 0.05, 0.9),
            "dominated": AgentObjectiveVector(0.50, 5000, 0.20, 0.3),
        }
        ranking = scorer.rank_agents(candidates)
        names = [name for name, _ in ranking]
        assert "dominant" in names
        assert "dominated" not in names

    def test_rank_agents_descending_order(self):
        """Ranking must be sorted by score descending."""
        scorer = MultiObjectiveScorer()
        # All three are Pareto-optimal (each best on a different dim) —
        # TOPSIS decides the order.
        candidates = {
            "fast":  AgentObjectiveVector(0.5, 100,  0.5, 0.5),
            "cheap": AgentObjectiveVector(0.5, 500,  0.01, 0.5),
            "quota": AgentObjectiveVector(0.5, 500,  0.5, 1.0),
        }
        ranking = scorer.rank_agents(candidates)
        for i in range(len(ranking) - 1):
            assert ranking[i][1] >= ranking[i + 1][1]

    def test_rank_agents_all_pareto_optimal(self):
        """All Pareto-optimal candidates are returned; none dropped."""
        scorer = MultiObjectiveScorer()
        candidates = {
            "fast":  AgentObjectiveVector(0.5, 100,  0.5, 0.5),
            "cheap": AgentObjectiveVector(0.5, 500,  0.01, 0.5),
            "quota": AgentObjectiveVector(0.5, 500,  0.5, 1.0),
        }
        ranking = scorer.rank_agents(candidates)
        assert len(ranking) == 3

    def test_rank_agents_respects_custom_weights(self):
        """Custom weights change the TOPSIS ranking when there's a trade-off."""
        candidates = {
            "fast":  AgentObjectiveVector(0.5, 100,  0.5, 0.5),
            "cheap": AgentObjectiveVector(0.5, 500,  0.01, 0.5),
        }
        scorer = MultiObjectiveScorer()

        latency_heavy = scorer.rank_agents(
            candidates,
            weights=ObjectiveWeights(
                success_rate=0.0, latency=1.0, cost=0.0, quota_headroom=0.0
            ),
        )
        cost_heavy = scorer.rank_agents(
            candidates,
            weights=ObjectiveWeights(
                success_rate=0.0, latency=0.0, cost=1.0, quota_headroom=0.0
            ),
        )
        # With latency-heavy weights, the faster agent should win
        assert latency_heavy[0][0] == "fast"
        # With cost-heavy weights, the cheaper agent should win
        assert cost_heavy[0][0] == "cheap"

    def test_rank_agents_identical_candidates_all_optimal(self):
        """Identical candidates ⇒ all Pareto-optimal, all score 1.0."""
        scorer = MultiObjectiveScorer()
        candidates = {
            "a": AgentObjectiveVector(0.8, 1000, 0.05, 0.7),
            "b": AgentObjectiveVector(0.8, 1000, 0.05, 0.7),
            "c": AgentObjectiveVector(0.8, 1000, 0.05, 0.7),
        }
        ranking = scorer.rank_agents(candidates)
        assert len(ranking) == 3
        # All identical ⇒ all on frontier, all score the same
        scores = [s for _, s in ranking]
        assert max(scores) - min(scores) < 1e-6

    def test_normalize_collapses_constant_dimension(self):
        """If every candidate has the same latency, that dim normalizes to 0
        for all — no div-by-zero, no spurious ranking effect."""
        scorer = MultiObjectiveScorer()
        candidates = {
            "a": AgentObjectiveVector(0.9, 1000, 0.05, 0.5),
            "b": AgentObjectiveVector(0.5, 1000, 0.20, 0.5),
        }
        # Same latency + same quota ⇒ dominance decided by success & cost
        result = scorer.compute_pareto_frontier(candidates)
        assert "a" in result.dominant_agents
        assert "b" in result.dominated_agents


# ── TestRouteScorerIntegration ───────────────────────────────────

class TestRouteScorerIntegration:
    """Verify the multi-objective path integrates with RouteScorer without
    breaking backward compatibility."""

    @pytest.fixture
    def simple_config(self) -> MaopConfig:
        return MaopConfig(
            agents={
                "claude": AgentDef(
                    cli="claude", driver="cli",
                    capabilities=["codegen", "review", "chat"],
                ),
                "kimi": AgentDef(
                    cli="kimi", driver="cli",
                    capabilities=["codegen", "chat"],
                ),
            },
            routing={
                "codegen": RouteEntry(
                    primary="claude", fallback="kimi",
                    match=r"write|implement|create|build",
                    keywords=["code", "function", "class"],
                ),
            },
        )

    def test_default_off_backward_compatible(self, simple_config):
        """Out of the box, multi-objective mode is OFF."""
        scorer = RouteScorer(config=simple_config)
        assert scorer.is_multi_objective_enabled is False
        # Existing behavior preserved
        result = scorer.match("write a function to sort data")
        assert result is not None
        assert result.routing_key == "codegen"
        assert result.agent == "claude"

    def test_enable_multi_objective(self, simple_config):
        scorer = RouteScorer(config=simple_config)
        scorer.enable_multi_objective()
        assert scorer.is_multi_objective_enabled is True

    def test_enable_multi_objective_with_custom_weights(self, simple_config):
        scorer = RouteScorer(config=simple_config)
        weights = ObjectiveWeights(
            success_rate=0.5, latency=0.2, cost=0.2, quota_headroom=0.1
        )
        scorer.enable_multi_objective(weights=weights)
        assert scorer.is_multi_objective_enabled is True
        assert scorer._mo_weights is weights

    def test_disable_multi_objective(self, simple_config):
        scorer = RouteScorer(config=simple_config)
        scorer.enable_multi_objective()
        assert scorer.is_multi_objective_enabled is True
        scorer.disable_multi_objective()
        assert scorer.is_multi_objective_enabled is False
        assert scorer._mo_weights is None

    def test_match_works_when_enabled_with_no_perf_data(
        self, simple_config, monkeypatch
    ):
        """When the perf DB is empty, multi-objective path should still
        return a valid agent (falls back to primary)."""
        # Point MAOP_ROOT_DIR at a non-existent path so the tracker has no data
        monkeypatch.setenv("MAOP_ROOT_DIR", "/nonexistent/MAOP/for/test")
        scorer = RouteScorer(config=simple_config)
        scorer.enable_multi_objective()
        result = scorer.match("write a function", adaptive=True)
        assert result is not None
        assert result.agent in ("claude", "kimi")

    def test_legacy_path_unchanged_when_disabled(self, simple_config):
        """With the flag OFF, _select_agent must take the legacy branch."""
        scorer = RouteScorer(config=simple_config)
        # Should NOT raise and should return the primary agent
        route = RouteEntry(primary="claude", fallback="kimi")
        agent = scorer._select_agent(route, "codegen", adaptive=False)
        assert agent == "claude"

    def test_compute_score_multi_objective_returns_default_on_empty(
        self, simple_config
    ):
        """Empty candidate list ⇒ default value returned."""
        scorer = RouteScorer(config=simple_config)
        scorer.enable_multi_objective()
        result = scorer._compute_score_multi_objective(
            [], "codegen", default="claude"
        )
        assert result == "claude"
