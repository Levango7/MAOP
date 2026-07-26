"""Tests for MAOP.core.dynamic_router - Dynamic agent routing by health + performance."""

from __future__ import annotations

import json

import pytest

from maop.core.dynamic_router import AgentScore, DynamicRouter


@pytest.fixture
def project_root(tmp_path):
    """Create a minimal MAOP project structure for testing."""
    # config/agents.yaml
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "agents.yaml").write_text(
        """
routing:
  codegen:
    primary: claude
    fallback: cursor
    tertiary: kilo
  chat:
    primary: openclaw
    fallback: mimo
  review:
    primary: qoder
    fallback: kimi
""", encoding="utf-8"
    )
    # data/ dir
    (tmp_path / "data").mkdir()
    return tmp_path


class TestAgentScore:
    def test_create(self):
        s = AgentScore(agent="claude", score=0.9, success_rate=0.95, speed=0.85)
        assert s.agent == "claude"
        assert s.score == 0.9

    def test_defaults(self):
        s = AgentScore(agent="x", score=0.5, success_rate=0.5, speed=0.5)
        assert s.success_rate == 0.5


class TestDynamicRouter:
    def test_route_returns_scores(self, project_root):
        router = DynamicRouter(project_root)
        scores = router.route(refresh=True)
        assert "codegen" in scores
        assert "chat" in scores
        assert "review" in scores
        # Each routing key has a list of AgentScore
        for agent_scores in scores.values():
            assert isinstance(agent_scores, list)
            for s in agent_scores:
                assert isinstance(s, AgentScore)
                assert 0 <= s.score <= 1

    def test_route_sorted_by_score_desc(self, project_root):
        router = DynamicRouter(project_root)
        scores = router.route(refresh=True)
        for agent_scores in scores.values():
            for i in range(len(agent_scores) - 1):
                assert agent_scores[i].score >= agent_scores[i + 1].score

    def test_best_agent(self, project_root):
        router = DynamicRouter(project_root)
        best = router.best_agent("codegen")
        assert best is not None
        assert isinstance(best, str)

    def test_best_agent_missing_key(self, project_root):
        router = DynamicRouter(project_root)
        assert router.best_agent("nonexistent") is None

    def test_route_for_key(self, project_root):
        router = DynamicRouter(project_root)
        scores = router.route_for_key("chat")
        assert len(scores) == 2  # openclaw, mimo
        agents = [s.agent for s in scores]
        assert "openclaw" in agents
        assert "mimo" in agents

    def test_cache(self, project_root):
        router = DynamicRouter(project_root, cache_ttl=60)
        # First call computes
        scores1 = router.route(refresh=True)
        # Second call uses cache
        scores2 = router.route(refresh=False)
        assert len(scores1) == len(scores2)

    def test_with_health_data(self, project_root):
        """Test that health data affects scoring."""
        # Create health file
        logs_dir = project_root / "src" / "logs"
        logs_dir.mkdir(parents=True)
        (logs_dir / "healthcheck_latest.json").write_text(
            json.dumps([
                {"agent": "claude", "status": "alive", "ms": 5000},
                {"agent": "cursor", "status": "dead", "ms": 0},
            ]), encoding="utf-8"
        )

        router = DynamicRouter(project_root)
        scores = router.route(refresh=True)
        codegen_scores = {s.agent: s for s in scores["codegen"]}

        # Claude is alive and fast -> high score
        assert codegen_scores["claude"].score > 0.5
        # Cursor is dead -> very low score
        assert codegen_scores["cursor"].score < 0.1

    def test_with_delegation_history(self, project_root):
        """Test that delegation history affects scoring."""
        logs_dir = project_root / "logs"
        logs_dir.mkdir(parents=True)
        delegations = [
            {"agent": "claude", "result": {"exit_code": 0, "duration_ms": 3000}},
            {"agent": "claude", "result": {"exit_code": 0, "duration_ms": 4000}},
            {"agent": "claude", "result": {"exit_code": 1, "duration_ms": 5000}},
            {"agent": "cursor", "result": {"exit_code": 0, "duration_ms": 20000}},
            {"agent": "cursor", "result": {"exit_code": 1, "duration_ms": 25000}},
        ]
        (logs_dir / "delegations.json").write_text(
            json.dumps(delegations), encoding="utf-8"
        )

        router = DynamicRouter(project_root)
        scores = router.route(refresh=True)
        codegen_scores = {s.agent: s for s in scores["codegen"]}

        # Claude: 2/3 success = 0.6667, fast avg ~3.5s
        # Cursor: 1/2 success = 0.5, slow avg ~22.5s
        assert codegen_scores["claude"].success_rate > codegen_scores["cursor"].success_rate
        assert codegen_scores["claude"].speed > codegen_scores["cursor"].speed
        assert codegen_scores["claude"].score > codegen_scores["cursor"].score

    def test_no_config_file(self, tmp_path):
        """Router handles missing config gracefully."""
        (tmp_path / "data").mkdir()
        router = DynamicRouter(tmp_path)
        scores = router.route(refresh=True)
        assert scores == {}

    def test_cache_file_written(self, project_root):
        """Verify cache file is written to data/."""
        router = DynamicRouter(project_root)
        router.route(refresh=True)
        cache_file = project_root / "data" / "dynamic-routing-cache.json"
        assert cache_file.exists()
        cached = json.loads(cache_file.read_text(encoding="utf-8"))
        assert "codegen" in cached
