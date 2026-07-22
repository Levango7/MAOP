"""Tests for Phase 5: evolve engine + config hot-reload."""

import json
import tempfile
from pathlib import Path


from maop.evolve import (
    EvolveEngine, EvolutionStats,
    AgentStats, AgentKeyStats,
    Suggestion, _compute_stats, _generate_suggestions,
)
from maop.config.hot_reload import (
    ConfigHotReload, _file_hash,
)


# ═══════════════════════════════════════════════════════════════
# Evolve engine tests
# ═══════════════════════════════════════════════════════════════

class TestComputeStats:
    """Test stats computation from delegation history."""

    def test_empty_data(self):
        stats = _compute_stats([])
        assert stats.by_agent == []
        assert stats.by_key == []

    def test_single_entry(self):
        data = [{"agent": "claude", "routing_key": "chat", "result": {"exit_code": 0, "duration_ms": 100}}]
        stats = _compute_stats(data)
        assert len(stats.by_agent) == 1
        assert stats.by_agent[0].agent == "claude"
        assert stats.by_agent[0].total == 1
        assert stats.by_agent[0].success == 1
        assert stats.by_agent[0].rate == 100.0

    def test_multiple_agents(self):
        data = [
            {"agent": "claude", "routing_key": "chat", "result": {"exit_code": 0, "duration_ms": 100}},
            {"agent": "codex", "routing_key": "code", "result": {"exit_code": 1, "duration_ms": 200}},
            {"agent": "claude", "routing_key": "chat", "result": {"exit_code": 0, "duration_ms": 150}},
        ]
        stats = _compute_stats(data)
        assert len(stats.by_agent) == 2
        claude = next(a for a in stats.by_agent if a.agent == "claude")
        assert claude.total == 2
        assert claude.success == 2
        assert claude.rate == 100.0

    def test_by_routing_key(self):
        data = [
            {"agent": "claude", "routing_key": "chat", "result": {"exit_code": 0}},
            {"agent": "codex", "routing_key": "code", "result": {"exit_code": 1}},
        ]
        stats = _compute_stats(data)
        assert len(stats.by_key) == 2

    def test_avg_duration(self):
        data = [
            {"agent": "a", "routing_key": "k", "result": {"exit_code": 0, "duration_ms": 100}},
            {"agent": "a", "routing_key": "k", "result": {"exit_code": 0, "duration_ms": 200}},
        ]
        stats = _compute_stats(data)
        assert stats.by_agent[0].avg_duration_ms == 150


class TestGenerateSuggestions:
    """Test suggestion generation."""

    def test_no_suggestions_for_healthy(self):
        stats = EvolutionStats(
            by_agent=[AgentStats(agent="claude", total=10, success=9, rate=90.0)],
            by_key=[], by_agent_key=[],
        )
        suggestions = _generate_suggestions(stats, [])
        # No low-success or slow suggestions
        low_success = [s for s in suggestions if s.type == "agent_low_success"]
        assert len(low_success) == 0

    def test_low_success_suggestion(self):
        stats = EvolutionStats(
            by_agent=[AgentStats(agent="bad-agent", total=5, success=1, fail=4, rate=20.0)],
            by_key=[], by_agent_key=[],
        )
        suggestions = _generate_suggestions(stats, [])
        low = [s for s in suggestions if s.type == "agent_low_success"]
        assert len(low) == 1
        assert low[0].severity == "high"
        assert low[0].agent == "bad-agent"

    def test_slow_agent_suggestion(self):
        stats = EvolutionStats(
            by_agent=[AgentStats(agent="slow", total=3, success=3, rate=100.0, avg_duration_ms=120000)],
            by_key=[], by_agent_key=[],
        )
        suggestions = _generate_suggestions(stats, [])
        slow = [s for s in suggestions if s.type == "slow_agent"]
        assert len(slow) == 1
        assert slow[0].severity == "medium"

    def test_routing_mismatch_suggestion(self):
        stats = EvolutionStats(
            by_agent=[],
            by_key=[],
            by_agent_key=[AgentKeyStats(agent="codex", routing_key="docs", total=5, success=1, rate=20.0)],
        )
        suggestions = _generate_suggestions(stats, [])
        mismatch = [s for s in suggestions if s.type == "routing_mismatch"]
        assert len(mismatch) == 1
        assert mismatch[0].auto_applicable is True

    def test_empty_routing_key_suggestion(self):
        data = [
            {"agent": "a", "routing_key": "", "result": {"exit_code": 0}},
        ]
        stats = EvolutionStats(by_agent=[], by_key=[], by_agent_key=[])
        suggestions = _generate_suggestions(stats, data)
        empty = [s for s in suggestions if s.type == "empty_routing_key"]
        assert len(empty) == 1


class TestEvolveEngine:
    """Test EvolveEngine with temp directories."""

    def test_analyze_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = EvolveEngine(root_dir=tmp)
            result = engine.analyze()
            assert result.action == "analyze"
            assert result.stats is not None

    def test_suggest_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = EvolveEngine(root_dir=tmp)
            result = engine.suggest()
            assert result.action == "suggest"
            assert isinstance(result.suggestions, list)

    def test_suggest_with_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp) / "logs"
            log_dir.mkdir()
            data = [
                {"agent": "bad", "routing_key": "k", "result": {"exit_code": 1, "duration_ms": 100}},
                {"agent": "bad", "routing_key": "k", "result": {"exit_code": 1, "duration_ms": 100}},
                {"agent": "bad", "routing_key": "k", "result": {"exit_code": 1, "duration_ms": 100}},
            ]
            (log_dir / "delegations.json").write_text(json.dumps(data), encoding="utf-8")

            engine = EvolveEngine(root_dir=tmp)
            result = engine.suggest()
            assert len(result.suggestions) > 0
            assert result.suggestions[0].type == "agent_low_success"

    def test_apply_nonexistent(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = EvolveEngine(root_dir=tmp)
            result = engine.apply(suggestion_id="S999")
            assert result.applied is None

    def test_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = EvolveEngine(root_dir=tmp)
            result = engine.status()
            assert result.action == "status"

    def test_apply_auto_applicable(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = EvolveEngine(root_dir=tmp)
            # Pre-save a suggestion
            s = Suggestion(id="S000", type="slow_agent", auto_applicable=True)
            data_dir = Path(tmp) / "data"
            data_dir.mkdir()
            (data_dir / "evolve-suggestions.json").write_text(
                json.dumps([s.model_dump()]), encoding="utf-8"
            )
            result = engine.apply(suggestion_id="S000")
            assert result.applied is not None
            assert result.applied.applied is True


# ═══════════════════════════════════════════════════════════════
# Config hot-reload tests
# ═══════════════════════════════════════════════════════════════

class TestFileHash:
    """Test file hash computation."""

    def test_existing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            fpath = Path(tmp) / "test.yaml"
            fpath.write_text("test: value\n", encoding="utf-8")
            h = _file_hash(fpath)
            assert h is not None
            assert len(h) == 32  # MD5 hex

    def test_nonexistent_file(self):
        h = _file_hash(Path("/nonexistent/file.yaml"))
        assert h is None


class TestConfigHotReload:
    """Test config hot-reload watcher."""

    def test_initial_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp) / "config"
            config_dir.mkdir()
            (config_dir / "agents.yaml").write_text("agents: {}\n", encoding="utf-8")

            watcher = ConfigHotReload(root_dir=tmp)
            assert not watcher.state.running
            assert watcher.state.reload_count == 0

    def test_check_no_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp) / "config"
            config_dir.mkdir()
            (config_dir / "agents.yaml").write_text("agents: {}\n", encoding="utf-8")

            watcher = ConfigHotReload(root_dir=tmp)
            changed = watcher.check_once()
            assert changed == []

    def test_detect_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp) / "config"
            config_dir.mkdir()
            agents_file = config_dir / "agents.yaml"
            agents_file.write_text("agents: {}\n", encoding="utf-8")

            watcher = ConfigHotReload(root_dir=tmp)
            # No changes initially
            assert watcher.check_once() == []

            # Modify file
            agents_file.write_text("agents: {claude: {}}\n", encoding="utf-8")
            changed = watcher.check_once()
            assert len(changed) == 1

            # No change on second check
            assert watcher.check_once() == []

    def test_force_reload(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp) / "config"
            config_dir.mkdir()
            (config_dir / "agents.yaml").write_text("agents: {}\n", encoding="utf-8")

            watcher = ConfigHotReload(root_dir=tmp)
            watcher.force_reload()
            assert watcher.state.reload_count == 1

    def test_watching_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp) / "config"
            config_dir.mkdir()
            (config_dir / "agents.yaml").write_text("agents: {}\n", encoding="utf-8")

            watcher = ConfigHotReload(root_dir=tmp)
            assert len(watcher.state.watching) == 3  # agents, rules, models

    def test_start_stop(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp) / "config"
            config_dir.mkdir()
            (config_dir / "agents.yaml").write_text("agents: {}\n", encoding="utf-8")

            watcher = ConfigHotReload(root_dir=tmp, poll_interval_s=0.1)
            # start() needs an event loop; test it in async context
            import asyncio
            async def _test():
                watcher.start()
                assert watcher.state.running
                await asyncio.sleep(0.05)
                await watcher.stop()
                assert not watcher.state.running
            asyncio.run(_test())
