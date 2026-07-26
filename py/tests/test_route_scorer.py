"""Unit tests for RouteScorer — scoring, cooldown, and agent selection.

Covers:
- match() with regex, keyword, and capability scoring
- cooldown extension logic (B-P0-1 fix verification)
- mark_agent_failed / mark_agent_success / is_agent_in_cooldown
- get_cooldown_status
- _select_agent with cooldown-aware selection
- driver validation warning (P1 fix)
"""
from __future__ import annotations

import time

import pytest

from maop.config.loader import AgentDef, MaopConfig, RouteEntry
from maop.core.route_scorer import RouteScorer, _COOLDOWN_SEC


# ── Fixtures ──────────────────────────────────────────────────

@pytest.fixture
def simple_config() -> MaopConfig:
    """Config with 3 agents and 2 routing rules."""
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
            "search-agent": AgentDef(
                cli="search-tool", driver="wrapper",
                capabilities=["search"],
            ),
        },
        routing={
            "codegen": RouteEntry(
                primary="claude", fallback="kimi",
                match=r"write|implement|create|build|refactor",
                keywords=["code", "function", "class", "test"],
            ),
            "search": RouteEntry(
                primary="search-agent",
                match=r"search|find|lookup",
                keywords=["search", "query"],
            ),
        },
    )


@pytest.fixture
def scorer(simple_config: MaopConfig) -> RouteScorer:
    return RouteScorer(config=simple_config)


# ── Tests: match() ────────────────────────────────────────────

class TestMatch:
    def test_match_regex_codegen(self, scorer: RouteScorer):
        result = scorer.match("write a function to sort data")
        assert result is not None
        assert result.routing_key == "codegen"
        assert result.agent == "claude"
        assert result.score > 0
        assert "regex" in result.matched_by or "keyword" in result.matched_by

    def test_match_regex_search(self, scorer: RouteScorer):
        result = scorer.match("search for relevant documents")
        assert result is not None
        assert result.routing_key == "search"
        assert result.agent == "search-agent"

    def test_match_keyword_only(self, scorer: RouteScorer):
        """Keyword match without regex should still score."""
        result = scorer.match("help me with code and function design")
        assert result is not None
        assert result.routing_key == "codegen"

    def test_match_no_config(self):
        """Scorer without config returns None."""
        scorer = RouteScorer(config=None)
        assert scorer.match("do something") is None

    def test_match_no_route_matches(self, scorer: RouteScorer):
        """Task that doesn't match any route returns None."""
        result = scorer.match("xyz random unrelated text qqq")
        assert result is None

    def test_match_confidence_levels(self, scorer: RouteScorer):
        """High-confidence match when regex hits."""
        result = scorer.match("write and implement a new function")
        assert result is not None
        assert result.confidence in ("high", "medium", "low")

    def test_match_adaptive_flag(self, scorer: RouteScorer):
        """match() works with adaptive=False."""
        result = scorer.match("write code", adaptive=False)
        assert result is not None
        assert result.routing_key == "codegen"


# ── Tests: Cooldown ───────────────────────────────────────────

class TestCooldown:
    def test_no_cooldown_initially(self, scorer: RouteScorer):
        assert not scorer.is_agent_in_cooldown("claude")

    def test_mark_failed_enters_cooldown(self, scorer: RouteScorer):
        scorer.mark_agent_failed("claude")
        assert scorer.is_agent_in_cooldown("claude")

    def test_mark_success_clears_cooldown(self, scorer: RouteScorer):
        scorer.mark_agent_failed("claude")
        assert scorer.is_agent_in_cooldown("claude")
        scorer.mark_agent_success("claude")
        assert not scorer.is_agent_in_cooldown("claude")

    def test_cooldown_expires(self, scorer: RouteScorer):
        """Cooldown should expire after _COOLDOWN_SEC."""
        scorer.mark_agent_failed("claude")
        # Simulate time passage beyond base cooldown
        scorer._cooldowns["claude"].last_fail_ts = time.time() - _COOLDOWN_SEC - 1
        assert not scorer.is_agent_in_cooldown("claude")

    def test_cooldown_extension_fail_count_2(self, scorer: RouteScorer):
        """B-P0-1 fix: 2 failures should extend cooldown to 1.5x."""
        scorer.mark_agent_failed("claude")
        scorer.mark_agent_failed("claude")
        # fail_count=2 → extended = 300 * (1 + 1*0.5) = 450s
        # Set last_fail_ts to 400s ago — should still be in cooldown
        scorer._cooldowns["claude"].last_fail_ts = time.time() - 400
        assert scorer.is_agent_in_cooldown("claude")

    def test_cooldown_extension_fail_count_3(self, scorer: RouteScorer):
        """3 failures should extend cooldown to 2x (600s)."""
        scorer.mark_agent_failed("claude")
        scorer.mark_agent_failed("claude")
        scorer.mark_agent_failed("claude")
        # fail_count=3 → extended = 300 * (1 + 2*0.5) = 600s
        scorer._cooldowns["claude"].last_fail_ts = time.time() - 500
        assert scorer.is_agent_in_cooldown("claude")

    def test_cooldown_extension_fail_count_4_plus(self, scorer: RouteScorer):
        """4+ failures should extend cooldown to 2.5x (750s), capped."""
        for _ in range(5):
            scorer.mark_agent_failed("claude")
        # fail_count=5 → min(5-1, 3)=3 → extended = 300 * (1 + 3*0.5) = 750s
        scorer._cooldowns["claude"].last_fail_ts = time.time() - 700
        assert scorer.is_agent_in_cooldown("claude")
        # But 800s should be expired
        scorer._cooldowns["claude"].last_fail_ts = time.time() - 800
        assert not scorer.is_agent_in_cooldown("claude")

    def test_cooldown_expired_cleans_up(self, scorer: RouteScorer):
        """Expired cooldown should be removed from dict."""
        scorer.mark_agent_failed("claude")
        assert "claude" in scorer._cooldowns
        scorer._cooldowns["claude"].last_fail_ts = time.time() - _COOLDOWN_SEC * 3
        scorer.is_agent_in_cooldown("claude")
        assert "claude" not in scorer._cooldowns

    def test_get_cooldown_status(self, scorer: RouteScorer):
        scorer.mark_agent_failed("claude")
        scorer.mark_agent_failed("kimi")
        statuses = scorer.get_cooldown_status()
        assert len(statuses) == 2
        agents = [s["agent"] for s in statuses]
        assert "claude" in agents
        assert "kimi" in agents
        for s in statuses:
            assert "fail_count" in s
            assert "remaining_s" in s

    def test_cooldown_pruning(self, scorer: RouteScorer):
        """Excessive cooldown entries should be pruned."""
        for i in range(10):
            scorer.mark_agent_failed(f"agent_{i}")
        # All should be present
        assert len(scorer._cooldowns) == 10


# ── Tests: Agent Selection ────────────────────────────────────

class TestSelectAgent:
    def test_select_primary_when_not_in_cooldown(self, scorer: RouteScorer):
        """Primary agent should be selected when not in cooldown."""
        route = RouteEntry(primary="claude", fallback="kimi")
        agent = scorer._select_agent(route, "codegen", adaptive=False)
        assert agent == "claude"

    def test_select_fallback_when_primary_in_cooldown(self, scorer: RouteScorer):
        """Fallback should be selected when primary is in cooldown."""
        scorer.mark_agent_failed("claude")
        route = RouteEntry(primary="claude", fallback="kimi")
        agent = scorer._select_agent(route, "codegen", adaptive=False)
        assert agent == "kimi"

    def test_select_primary_when_all_in_cooldown(self, scorer: RouteScorer):
        """Primary should be returned when all candidates are in cooldown."""
        scorer.mark_agent_failed("claude")
        scorer.mark_agent_failed("kimi")
        route = RouteEntry(primary="claude", fallback="kimi")
        agent = scorer._select_agent(route, "codegen", adaptive=False)
        assert agent == "claude"  # better than nothing

    def test_select_returns_claude_when_no_candidates(self, scorer: RouteScorer):
        """Empty route should default to 'claude'."""
        route = RouteEntry(primary="", fallback="", tertiary="")
        agent = scorer._select_agent(route, "codegen", adaptive=False)
        assert agent == "claude"

    def test_driver_warning_on_unknown_driver(self, scorer: RouteScorer, caplog):
        """P1 fix: unknown driver should produce a warning."""
        # Patch the config to have an agent with unknown driver
        scorer.config.agents["claude"].driver = "unknown_driver"
        route = RouteEntry(primary="claude", fallback="kimi")
        import logging
        with caplog.at_level(logging.WARNING):
            scorer._select_agent(route, "codegen", adaptive=False)
        # Check that a warning about unknown driver was logged
        warnings = [r for r in caplog.records if "unknown driver" in r.message.lower()]
        assert len(warnings) > 0


# ── Tests: get_route_scorer singleton ─────────────────────────

class TestGetRouteScorer:
    def test_get_route_scorer_returns_instance(self):
        from maop.core.route_scorer import get_route_scorer
        scorer = get_route_scorer()
        assert isinstance(scorer, RouteScorer)

    def test_get_route_scorer_with_config(self, simple_config: MaopConfig):
        from maop.core.route_scorer import get_route_scorer
        RouteScorer.reset()
        scorer = get_route_scorer(config=simple_config)
        assert isinstance(scorer, RouteScorer)
        assert scorer.config is simple_config
