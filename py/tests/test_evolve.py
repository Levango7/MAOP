"""Tests for MAOP.evolve — Self-evolution engine: stats, suggestions, apply, promote."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from maop.evolve import (
    AgentStats,
    EvolutionStats,
    EvolveEngine,
    EvolveResult,
    Suggestion,
    SuggestionSeverity,
    _compute_stats,
    _generate_suggestions,
    _load_observability_data,
    _load_observability_data_from_db,
)

# ── Fixtures ──────────────────────────────────────────────────

def _delegation(agent: str, exit_code: int = 0, routing_key: str = "default",
                duration_ms: int = 1000) -> dict:
    return {
        "agent": agent,
        "routing_key": routing_key,
        "result": {"exit_code": exit_code, "duration_ms": duration_ms},
    }


@pytest.fixture
def evolve_root(tmp_path: Path) -> Path:
    """Create a temp MAOP root with logs/ and data/ dirs."""
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    return tmp_path


@pytest.fixture
def engine(evolve_root: Path) -> EvolveEngine:
    return EvolveEngine(root_dir=evolve_root)


def _write_delegations(root: Path, data: list[dict]) -> None:
    (root / "logs").mkdir(parents=True, exist_ok=True)
    (root / "logs" / "delegations.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


# ── Model tests ───────────────────────────────────────────────

class TestModels:
    def test_agent_stats_defaults(self):
        s = AgentStats()
        assert s.agent == ""
        assert s.total == 0
        assert s.rate == 0.0

    def test_suggestion_severity_values(self):
        assert SuggestionSeverity.HIGH == "high"
        assert SuggestionSeverity.LOW == "low"
        assert SuggestionSeverity.MEDIUM == "medium"

    def test_suggestion_auto_timestamp(self):
        s = Suggestion()
        assert s.timestamp != ""
        assert s.applied is False
        assert s.auto_applicable is False

    def test_evolution_stats_empty(self):
        es = EvolutionStats()
        assert es.by_agent == []
        assert es.by_key == []
        assert es.by_agent_key == []

    def test_evolve_result_defaults(self):
        r = EvolveResult()
        assert r.action == ""
        assert r.stats is None
        assert r.suggestions == []


# ── _load_observability_data ──────────────────────────────────

class TestLoadObservabilityData:
    def test_no_file_returns_empty(self, tmp_path: Path):
        assert _load_observability_data(tmp_path) == []

    def test_list_format(self, evolve_root: Path):
        data = [_delegation("claude"), _delegation("codex")]
        _write_delegations(evolve_root, data)
        result = _load_observability_data(evolve_root / "logs")
        assert len(result) == 2

    def test_single_dict_wrapped(self, evolve_root: Path):
        data = _delegation("claude")
        (evolve_root / "logs" / "delegations.json").write_text(
            json.dumps(data), encoding="utf-8"
        )
        result = _load_observability_data(evolve_root / "logs")
        assert len(result) == 1
        assert result[0]["agent"] == "claude"

    def test_corrupt_json_returns_empty(self, evolve_root: Path):
        (evolve_root / "logs" / "delegations.json").write_text(
            "not valid json {{{", encoding="utf-8"
        )
        assert _load_observability_data(evolve_root / "logs") == []


# ── _compute_stats ────────────────────────────────────────────

class TestComputeStats:
    def test_empty_data(self):
        stats = _compute_stats([])
        assert stats.by_agent == []
        assert stats.by_key == []

    def test_single_agent_success_rate(self):
        data = [
            _delegation("claude", exit_code=0),
            _delegation("claude", exit_code=0),
            _delegation("claude", exit_code=1),
        ]
        stats = _compute_stats(data)
        assert len(stats.by_agent) == 1
        a = stats.by_agent[0]
        assert a.agent == "claude"
        assert a.total == 3
        assert a.success == 2
        assert a.fail == 1
        assert a.rate == 66.7

    def test_multiple_agents(self):
        data = [
            _delegation("claude", exit_code=0),
            _delegation("codex", exit_code=1),
        ]
        stats = _compute_stats(data)
        agents = {s.agent for s in stats.by_agent}
        assert agents == {"claude", "codex"}

    def test_avg_duration(self):
        data = [
            _delegation("claude", duration_ms=2000),
            _delegation("claude", duration_ms=4000),
        ]
        stats = _compute_stats(data)
        assert stats.by_agent[0].avg_duration_ms == 3000

    def test_routing_key_stats(self):
        data = [
            _delegation("claude", routing_key="code"),
            _delegation("claude", routing_key="code", exit_code=1),
            _delegation("claude", routing_key="test"),
        ]
        stats = _compute_stats(data)
        keys = {k.routing_key: k for k in stats.by_key}
        assert keys["code"].total == 2
        assert keys["code"].success == 1
        assert keys["test"].total == 1

    def test_agent_key_stats(self):
        data = [
            _delegation("claude", routing_key="code", duration_ms=500),
            _delegation("claude", routing_key="code", duration_ms=1500),
        ]
        stats = _compute_stats(data)
        assert len(stats.by_agent_key) == 1
        ak = stats.by_agent_key[0]
        assert ak.agent == "claude"
        assert ak.routing_key == "code"
        assert ak.avg_duration_ms == 1000

    def test_unknown_agent_default(self):
        data = [{"routing_key": "x", "result": {"exit_code": 0}}]
        stats = _compute_stats(data)
        assert stats.by_agent[0].agent == "unknown"

    def test_zero_duration_not_counted(self):
        data = [_delegation("claude", duration_ms=0)]
        stats = _compute_stats(data)
        assert stats.by_agent[0].avg_duration_ms == 0


# ── _generate_suggestions ─────────────────────────────────────

class TestGenerateSuggestions:
    def test_no_suggestions_for_good_stats(self):
        data = [_delegation("claude", exit_code=0) for _ in range(5)]
        stats = _compute_stats(data)
        suggestions = _generate_suggestions(stats, data)
        assert len(suggestions) == 0

    def test_low_success_rate_suggestion(self):
        data = [_delegation("claude", exit_code=1) for _ in range(4)]
        data[0]["result"]["exit_code"] = 0  # 1 success out of 4 = 25%
        stats = _compute_stats(data)
        suggestions = _generate_suggestions(stats, data)
        types = [s.type for s in suggestions]
        assert "agent_low_success" in types

    def test_routing_mismatch_suggestion(self):
        data = [_delegation("claude", exit_code=1, routing_key="hard") for _ in range(4)]
        stats = _compute_stats(data)
        suggestions = _generate_suggestions(stats, data)
        types = [s.type for s in suggestions]
        assert "routing_mismatch" in types

    def test_slow_agent_suggestion(self):
        data = [_delegation("claude", exit_code=0, duration_ms=120000) for _ in range(2)]
        stats = _compute_stats(data)
        suggestions = _generate_suggestions(stats, data)
        types = [s.type for s in suggestions]
        assert "slow_agent" in types

    def test_empty_routing_key_suggestion(self):
        data = [{"agent": "claude", "routing_key": "", "result": {"exit_code": 0}}]
        stats = _compute_stats(data)
        suggestions = _generate_suggestions(stats, data)
        types = [s.type for s in suggestions]
        assert "empty_routing_key" in types

    def test_suggestion_ids_increment(self):
        data = [_delegation("claude", exit_code=1) for _ in range(5)]
        stats = _compute_stats(data)
        suggestions = _generate_suggestions(stats, data)
        if len(suggestions) >= 2:
            assert suggestions[0].id == "S000"
            assert suggestions[1].id == "S001"

    def test_auto_applicable_flags(self):
        data = [_delegation("claude", exit_code=1, routing_key="x") for _ in range(4)]
        stats = _compute_stats(data)
        suggestions = _generate_suggestions(stats, data)
        for s in suggestions:
            if s.type == "routing_mismatch":
                assert s.auto_applicable is True


# ── EvolveEngine ──────────────────────────────────────────────

class TestEvolveEngine:
    def test_init_default_root(self):
        eng = EvolveEngine()
        assert eng._root == Path.cwd()

    def test_init_custom_root(self, evolve_root: Path):
        eng = EvolveEngine(root_dir=evolve_root)
        assert eng._log_dir == evolve_root / "logs"
        assert eng._suggestions_file == evolve_root / "data" / "evolve-suggestions.json"

    def test_analyze_empty(self, engine: EvolveEngine):
        result = engine.analyze()
        assert result.action == "analyze"
        assert result.stats is not None
        assert result.stats.by_agent == []

    def test_analyze_with_data(self, engine: EvolveEngine, evolve_root: Path):
        _write_delegations(evolve_root, [_delegation("claude"), _delegation("codex", exit_code=1)])
        result = engine.analyze()
        assert len(result.stats.by_agent) == 2

    def test_suggest_saves_and_returns(self, engine: EvolveEngine, evolve_root: Path):
        data = [_delegation("claude", exit_code=1) for _ in range(5)]
        _write_delegations(evolve_root, data)
        result = engine.suggest()
        assert result.action == "suggest"
        assert len(result.suggestions) > 0
        assert engine._suggestions_file.exists()

    def test_suggest_no_data(self, engine: EvolveEngine):
        result = engine.suggest()
        assert result.action == "suggest"
        assert result.suggestions == []

    def test_apply_auto_applicable(self, engine: EvolveEngine, evolve_root: Path):
        data = [_delegation("claude", exit_code=1, routing_key="x") for _ in range(4)]
        _write_delegations(evolve_root, data)
        engine.suggest()
        # Find an auto-applicable suggestion
        suggestions = engine._load_suggestions()
        auto_s = next(s for s in suggestions if s.auto_applicable)
        result = engine.apply(auto_s.id)
        assert result.action == "apply"
        assert result.applied is not None
        assert result.applied.applied is True

    def test_apply_non_auto_applicable(self, engine: EvolveEngine, evolve_root: Path):
        data = [_delegation("claude", exit_code=1) for _ in range(5)]
        _write_delegations(evolve_root, data)
        engine.suggest()
        suggestions = engine._load_suggestions()
        non_auto = next(s for s in suggestions if not s.auto_applicable)
        result = engine.apply(non_auto.id)
        assert result.applied is not None
        assert result.applied.applied is False

    def test_apply_not_found(self, engine: EvolveEngine):
        result = engine.apply("NONEXIST")
        assert result.action == "apply"
        assert result.applied is None

    def test_promote(self, engine: EvolveEngine, evolve_root: Path):
        data = [_delegation("claude", exit_code=1, routing_key="x") for _ in range(4)]
        _write_delegations(evolve_root, data)
        engine.suggest()
        suggestions = engine._load_suggestions()
        auto_s = next(s for s in suggestions if s.auto_applicable)
        result = engine.promote(auto_s.id)
        assert result.action == "promote"
        assert result.applied is not None
        assert result.applied.applied is True

    def test_promote_not_found(self, engine: EvolveEngine):
        result = engine.promote("NOPE")
        assert result.action == "promote"
        assert result.applied is None

    def test_status(self, engine: EvolveEngine, evolve_root: Path):
        # Use failing delegations to generate suggestions
        data = [_delegation("claude", exit_code=1) for _ in range(5)]
        _write_delegations(evolve_root, data)
        engine.suggest()
        result = engine.status()
        assert result.action == "status"
        assert result.stats is not None
        assert len(result.suggestions) > 0

    def test_status_empty(self, engine: EvolveEngine):
        result = engine.status()
        assert result.action == "status"
        assert result.stats is not None
        assert result.suggestions == []

    def test_load_suggestions_missing_file(self, engine: EvolveEngine):
        assert engine._load_suggestions() == []

    def test_load_suggestions_corrupt(self, engine: EvolveEngine, evolve_root: Path):
        engine._suggestions_file.parent.mkdir(parents=True, exist_ok=True)
        engine._suggestions_file.write_text("corrupt", encoding="utf-8")
        assert engine._load_suggestions() == []


# ── _load_observability_data_from_db ──────────────────────────

def _write_delegations_to_db(root: Path, rows: list[dict]) -> Path:
    """Write delegation records directly into a SQLite maop.db."""
    import sqlite3
    db_path = root / "data" / "maop.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS delegations (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          timestamp TEXT,
          agent TEXT,
          task TEXT,
          routing_key TEXT,
          exit_code INT,
          stdout TEXT,
          stderr TEXT,
          duration_ms INT,
          trace_id TEXT
        )
    """)
    for r in rows:
        conn.execute(
            "INSERT INTO delegations (timestamp, agent, task, routing_key, exit_code, stdout, stderr, duration_ms, trace_id) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                r.get("timestamp", "2026-01-01T00:00:00Z"),
                r.get("agent", "unknown"),
                r.get("task", ""),
                r.get("routing_key", ""),
                r.get("exit_code", 0),
                r.get("stdout", ""),
                r.get("stderr", ""),
                r.get("duration_ms", 0),
                r.get("trace_id", ""),
            ),
        )
    conn.commit()
    conn.close()
    return db_path


class TestLoadObservabilityDataFromDB:
    def test_no_db_returns_empty(self, tmp_path: Path):
        assert _load_observability_data_from_db(tmp_path / "nonexistent.db") == []

    def test_reads_from_db(self, evolve_root: Path):
        _write_delegations_to_db(evolve_root, [
            {"agent": "claude", "routing_key": "code", "exit_code": 0, "duration_ms": 1500},
            {"agent": "codex", "routing_key": "chat", "exit_code": 1, "duration_ms": 3000},
        ])
        data = _load_observability_data_from_db(evolve_root / "data" / "maop.db")
        assert len(data) == 2
        # ORDER BY id DESC — newest first
        agents = {d["agent"] for d in data}
        assert agents == {"claude", "codex"}
        claude_row = next(d for d in data if d["agent"] == "claude")
        assert claude_row["routing_key"] == "code"
        assert claude_row["result"]["exit_code"] == 0
        assert claude_row["result"]["duration_ms"] == 1500

    def test_null_exit_code_becomes_minus_one(self, evolve_root: Path):
        import sqlite3
        db_path = evolve_root / "data" / "maop.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE delegations (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT, agent TEXT, task TEXT, routing_key TEXT, exit_code INT, stdout TEXT, stderr TEXT, duration_ms INT, trace_id TEXT)")
        conn.execute("INSERT INTO delegations (agent, exit_code, duration_ms) VALUES ('claude', NULL, NULL)")
        conn.commit()
        conn.close()
        data = _load_observability_data_from_db(db_path)
        assert len(data) == 1
        assert data[0]["result"]["exit_code"] == -1
        assert data[0]["result"]["duration_ms"] == 0

    def test_null_agent_becomes_unknown(self, evolve_root: Path):
        import sqlite3
        db_path = evolve_root / "data" / "maop.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE delegations (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT, agent TEXT, task TEXT, routing_key TEXT, exit_code INT, stdout TEXT, stderr TEXT, duration_ms INT, trace_id TEXT)")
        conn.execute("INSERT INTO delegations (agent, exit_code) VALUES (NULL, 0)")
        conn.commit()
        conn.close()
        data = _load_observability_data_from_db(db_path)
        assert data[0]["agent"] == "unknown"

    def test_corrupt_db_returns_empty(self, evolve_root: Path):
        db_path = evolve_root / "data" / "maop.db"
        db_path.write_text("not a database", encoding="utf-8")
        assert _load_observability_data_from_db(db_path) == []


class TestEvolveEngineSQLiteSource:
    """Verify EvolveEngine reads from SQLite first, JSON as fallback."""

    def test_db_takes_priority_over_json(self, evolve_root: Path):
        # Write both JSON and DB with different data
        _write_delegations(evolve_root, [_delegation("json_agent", exit_code=0)])
        _write_delegations_to_db(evolve_root, [
            {"agent": "db_agent", "routing_key": "code", "exit_code": 0, "duration_ms": 100},
        ])
        eng = EvolveEngine(root_dir=evolve_root)
        result = eng.analyze()
        agents = {s.agent for s in result.stats.by_agent}
        assert "db_agent" in agents
        assert "json_agent" not in agents

    def test_json_fallback_when_db_empty(self, evolve_root: Path):
        _write_delegations(evolve_root, [_delegation("claude"), _delegation("codex", exit_code=1)])
        eng = EvolveEngine(root_dir=evolve_root)
        result = eng.analyze()
        assert len(result.stats.by_agent) == 2

    def test_json_fallback_when_no_db(self, evolve_root: Path):
        _write_delegations(evolve_root, [_delegation("claude", exit_code=0)])
        eng = EvolveEngine(root_dir=evolve_root)
        result = eng.analyze()
        assert len(result.stats.by_agent) == 1
        assert result.stats.by_agent[0].agent == "claude"

    def test_suggest_with_db_data(self, evolve_root: Path):
        _write_delegations_to_db(evolve_root, [
            {"agent": "claude", "routing_key": "code", "exit_code": 1, "duration_ms": 100},
        ] * 5)
        eng = EvolveEngine(root_dir=evolve_root)
        result = eng.suggest()
        assert len(result.suggestions) > 0

    def test_status_with_db_data(self, evolve_root: Path):
        _write_delegations_to_db(evolve_root, [
            {"agent": "claude", "routing_key": "code", "exit_code": 1, "duration_ms": 100},
        ] * 5)
        eng = EvolveEngine(root_dir=evolve_root)
        eng.suggest()
        result = eng.status()
        assert result.stats is not None
        assert len(result.stats.by_agent) == 1
        assert len(result.suggestions) > 0


# --- Merged from test_evolve_coverage3.py ---

def _make_engine(tmp_path: Path) -> EvolveEngine:
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    return EvolveEngine(root_dir=tmp_path)


def _gen_suggestions(engine: EvolveEngine, tmp_path: Path) -> list[Suggestion]:
    """Generate suggestions by writing failing delegations and calling suggest()."""
    data = [_delegation("claude", exit_code=1, routing_key="x") for _ in range(4)]
    _write_delegations(tmp_path, data)
    engine.suggest()
    return engine._load_suggestions()

# ── suggest() merge with existing applied (332, 336-337) ────────────


class TestSuggestMerge:
    def test_preserves_applied_state(self, tmp_path):
        engine = _make_engine(tmp_path)
        # First round: generate and apply an auto-applicable suggestion
        suggestions = _gen_suggestions(engine, tmp_path)
        auto_s = next(s for s in suggestions if s.auto_applicable)
        engine.apply(auto_s.id)
        # Second round: suggest again — should preserve applied state
        result = engine.suggest()
        applied = [s for s in result.suggestions if s.applied]
        assert len(applied) >= 1

    def test_merges_existing_not_in_new(self, tmp_path):
        """Cover branch where existing suggestion id not in new_ids is appended."""
        engine = _make_engine(tmp_path)
        # Pre-write a suggestion file with a suggestion that won't be regenerated
        engine._suggestions_file.parent.mkdir(parents=True, exist_ok=True)
        custom = Suggestion(
            id="CUSTOM_999", type="custom", severity="low",
            detail="custom", suggestion="custom", auto_applicable=False,
        )
        engine._save_suggestions([custom])
        # Generate new suggestions — custom should be preserved
        _gen_suggestions(engine, tmp_path)
        result = engine.suggest()
        ids = {s.id for s in result.suggestions}
        assert "CUSTOM_999" in ids


# ── apply() branches (347, 356-358, 365-370) ────────────────────────


class TestApplyBranches:
    def test_apply_already_applied(self, tmp_path):
        """Cover branch where s.applied is True (347)."""
        engine = _make_engine(tmp_path)
        suggestions = _gen_suggestions(engine, tmp_path)
        auto_s = next(s for s in suggestions if s.auto_applicable)
        # Apply once
        engine.apply(auto_s.id)
        # Apply again — should hit the already-applied branch
        result = engine.apply(auto_s.id)
        assert result.applied is not None
        assert result.applied.applied is True

    def test_apply_config_mutator_success(self, tmp_path):
        """Cover branch where ConfigMutator succeeds (356-358)."""
        engine = _make_engine(tmp_path)
        suggestions = _gen_suggestions(engine, tmp_path)
        auto_s = next(s for s in suggestions if s.auto_applicable)
        with patch("maop.core.reliability.config_mutator.ConfigMutator") as MockMut:
            mock_result = MagicMock()
            mock_result.applied = True
            mock_result.error = None
            MockMut.return_value.apply_suggestion.return_value = mock_result
            result = engine.apply(auto_s.id)
        assert result.applied is not None
        assert result.applied.applied is True

    def test_apply_config_mutator_failure_fallback(self, tmp_path):
        """Cover branch where ConfigMutator fails and falls back to direct (365-370)."""
        engine = _make_engine(tmp_path)
        suggestions = _gen_suggestions(engine, tmp_path)
        auto_s = next(s for s in suggestions if s.auto_applicable)
        with patch("maop.core.reliability.config_mutator.ConfigMutator") as MockMut:
            mock_result = MagicMock()
            mock_result.applied = False
            mock_result.error = "mutator failed"
            MockMut.return_value.apply_suggestion.return_value = mock_result
            result = engine.apply(auto_s.id)
        assert result.applied is not None
        assert result.applied.applied is True

    def test_apply_config_mutator_exception_fallback(self, tmp_path):
        """Cover branch where ConfigMutator raises and falls back (365-370)."""
        engine = _make_engine(tmp_path)
        suggestions = _gen_suggestions(engine, tmp_path)
        auto_s = next(s for s in suggestions if s.auto_applicable)
        with patch("maop.core.reliability.config_mutator.ConfigMutator") as MockMut:
            MockMut.return_value.apply_suggestion.side_effect = RuntimeError("boom")
            result = engine.apply(auto_s.id)
        assert result.applied is not None
        assert result.applied.applied is True


# ── promote() non-auto-applicable (380) ─────────────────────────────


class TestPromoteNonAuto:
    def test_promote_non_auto_applicable(self, tmp_path):
        """Cover branch where suggestion is not auto_applicable (380)."""
        engine = _make_engine(tmp_path)
        # Generate a non-auto-applicable suggestion
        data = [_delegation("claude", exit_code=1) for _ in range(5)]
        _write_delegations(tmp_path, data)
        engine.suggest()
        suggestions = engine._load_suggestions()
        non_auto = next(s for s in suggestions if not s.auto_applicable)
        result = engine.promote(non_auto.id)
        assert result.applied is not None
        assert result.applied.applied is False


# ── _apply_to_agents_yaml branches (391-444) ────────────────────────


class TestApplyToAgentsYaml:
    def test_no_yaml_module(self, tmp_path):
        """Cover yaml ImportError branch (391-393)."""
        engine = _make_engine(tmp_path)
        s = Suggestion(id="S1", type="slow_agent", agent="claude")
        with patch.dict("sys.modules", {"yaml": None}):
            engine._apply_to_agents_yaml(s)  # should not raise

    def test_no_agents_yaml_file(self, tmp_path):
        """Cover branch where agents.yaml doesn't exist (396-398)."""
        engine = _make_engine(tmp_path)
        s = Suggestion(id="S1", type="slow_agent", agent="claude")
        # No config/agents.yaml created
        engine._apply_to_agents_yaml(s)

    def test_agents_yaml_read_exception(self, tmp_path):
        """Cover branch where agents.yaml read fails (400-405)."""
        engine = _make_engine(tmp_path)
        cfg_dir = tmp_path / "config"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        # Write invalid YAML
        (cfg_dir / "agents.yaml").write_text("not: valid: yaml: {{{", encoding="utf-8")
        s = Suggestion(id="S1", type="slow_agent", agent="claude")
        engine._apply_to_agents_yaml(s)

    def test_agent_not_in_yaml(self, tmp_path):
        """Cover branch where agent not found in agents.yaml (407-411)."""
        engine = _make_engine(tmp_path)
        cfg_dir = tmp_path / "config"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        (cfg_dir / "agents.yaml").write_text(
            "agents:\n  other_agent:\n    timeout_s: 60\n", encoding="utf-8"
        )
        s = Suggestion(id="S1", type="slow_agent", agent="claude")
        engine._apply_to_agents_yaml(s)

    def test_slow_agent_updates_timeout(self, tmp_path):
        """Cover slow_agent branch that updates timeout_s (414-416)."""
        engine = _make_engine(tmp_path)
        cfg_dir = tmp_path / "config"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        (cfg_dir / "agents.yaml").write_text(
            "agents:\n  claude:\n    timeout_s: 60\n", encoding="utf-8"
        )
        s = Suggestion(id="S1", type="slow_agent", agent="claude")
        engine._apply_to_agents_yaml(s)
        # Verify timeout was increased
        import yaml
        data = yaml.safe_load((cfg_dir / "agents.yaml").read_text(encoding="utf-8"))
        assert data["agents"]["claude"]["timeout_s"] == 90  # 60 * 1.5

    def test_routing_mismatch_returns_early(self, tmp_path):
        """Cover routing_mismatch branch that returns early (417-420)."""
        engine = _make_engine(tmp_path)
        cfg_dir = tmp_path / "config"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        (cfg_dir / "agents.yaml").write_text(
            "agents:\n  claude:\n    timeout_s: 60\n", encoding="utf-8"
        )
        s = Suggestion(id="S1", type="routing_mismatch", agent="claude")
        engine._apply_to_agents_yaml(s)
        # timeout should NOT be changed
        import yaml
        data = yaml.safe_load((cfg_dir / "agents.yaml").read_text(encoding="utf-8"))
        assert data["agents"]["claude"]["timeout_s"] == 60

    def test_write_exception_restores_backup(self, tmp_path):
        """Cover branch where write fails and backup is restored (436-444)."""
        engine = _make_engine(tmp_path)
        cfg_dir = tmp_path / "config"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        original_content = "agents:\n  claude:\n    timeout_s: 60\n"
        (cfg_dir / "agents.yaml").write_text(original_content, encoding="utf-8")
        s = Suggestion(id="S1", type="slow_agent", agent="claude")
        # Make safe_write_text fail
        with patch("maop.core.reliability.safe_writer.safe_write_text", side_effect=RuntimeError("write boom")):
            engine._apply_to_agents_yaml(s)
        # Original content should be restored from backup
        assert (cfg_dir / "agents.yaml").read_text(encoding="utf-8") == original_content


# ── _save_suggestions exception (460-462) ───────────────────────────


class TestSaveSuggestionsException:
    def test_save_exception_logged(self, tmp_path, caplog):
        """Cover branch where _save_suggestions fails (460-462)."""
        engine = _make_engine(tmp_path)
        # Make the suggestions file path unwritable by pointing it at a directory
        engine._suggestions_file = tmp_path / "data"  # this is a directory
        with caplog.at_level("WARNING"):
            engine._save_suggestions([Suggestion(id="S1")])
        # Should have logged a warning
        assert any("save" in rec.message.lower() for rec in caplog.records)


# ── auto_evolve fallback to legacy (498-500) ────────────────────────


class TestAutoEvolveFallback:
    def test_evolution_loop_failure_falls_back(self, tmp_path):
        """Cover branch where EvolutionLoop raises and falls back to legacy (498-500)."""
        engine = _make_engine(tmp_path)
        with patch("maop.core.evolution.evolution_loop.EvolutionLoop") as MockLoop:
            MockLoop.return_value.run_full_evolution.side_effect = RuntimeError("loop boom")
            result = engine.auto_evolve(hours=24)
        # Should fall back to legacy and return a dict
        assert isinstance(result, dict)
        assert "analysis_report" in result or "new_suggestions" in result

    def test_auto_evolve_success(self, tmp_path):
        """Cover auto_evolve success path (485-497)."""
        engine = _make_engine(tmp_path)
        with patch("maop.core.evolution.evolution_loop.EvolutionLoop") as MockLoop:
            MockLoop.return_value.run_full_evolution.return_value = {
                "loop_report": {"status": "ok"},
                "total_suggestions": 3,
                "strategy_learning": {"total_combos": 5},
            }
            result = engine.auto_evolve(hours=24)
        assert result["new_suggestions"] == 3
        assert "analysis_report" in result


# ── _auto_evolve_legacy body (504-633) ──────────────────────────────


class TestAutoEvolveLegacy:
    def test_legacy_with_empty_data(self, tmp_path):
        """Cover _auto_evolve_legacy with no delegation data."""
        engine = _make_engine(tmp_path)
        result = engine._auto_evolve_legacy(hours=24)
        assert isinstance(result, dict)
        assert "analysis_report" in result
        assert "agent_strategy" in result
        assert "cache_evolution" in result

    def test_legacy_with_cost_drivers(self, tmp_path):
        """Cover cost-driven suggestion branch (512-521)."""
        engine = _make_engine(tmp_path)
        with patch("maop.history_analyzer.HistoryAnalyzer") as MockHA:
            mock_report = MockHA.return_value.analyze.return_value
            mock_report.period_hours = 24
            mock_report.total_loops = 100
            mock_report.success_rate = 80.0
            mock_report.failure_clusters = []
            mock_report.bottlenecks = []
            mock_report.cost_drivers = [MagicMock(
                dimension="model", dimension_value="gpt4",
                total_cost=10.0,
            )]
            mock_report.recommendations = []
            # Also mock the strategy learner and cache evolver to avoid side effects
            with patch("maop.agent_strategy_learner.AgentStrategyLearner") as MockSL:
                MockSL.return_value.learn.return_value = MagicMock(
                    total_combos=0, reliable_combos=0, underperformers=0,
                    adjustments=[], routing_winners=[], recommendations=[],
                )
                with patch("maop.cache_evolver.CacheEvolver") as MockCE:
                    MockCE.return_value.evolve.return_value = MagicMock(
                        total_caches=0, adjustments=[], applied_count=0,
                        skipped_count=0, recommendations=[],
                    )
                    result = engine._auto_evolve_legacy(hours=24)
        assert result["new_suggestions"] >= 1

    def test_legacy_with_failure_clusters(self, tmp_path):
        """Cover failure-driven suggestion branch (523-533)."""
        engine = _make_engine(tmp_path)
        with patch("maop.history_analyzer.HistoryAnalyzer") as MockHA:
            mock_report = MockHA.return_value.analyze.return_value
            mock_report.period_hours = 24
            mock_report.total_loops = 100
            mock_report.success_rate = 80.0
            mock_report.failure_clusters = [MagicMock(
                count=5, pattern="timeout", root_cause_hypothesis="increase timeout",
            )]
            mock_report.bottlenecks = []
            mock_report.cost_drivers = []
            mock_report.recommendations = []
            with patch("maop.agent_strategy_learner.AgentStrategyLearner") as MockSL:
                MockSL.return_value.learn.return_value = MagicMock(
                    total_combos=0, reliable_combos=0, underperformers=0,
                    adjustments=[], routing_winners=[], recommendations=[],
                )
                with patch("maop.cache_evolver.CacheEvolver") as MockCE:
                    MockCE.return_value.evolve.return_value = MagicMock(
                        total_caches=0, adjustments=[], applied_count=0,
                        skipped_count=0, recommendations=[],
                    )
                    result = engine._auto_evolve_legacy(hours=24)
        assert result["new_suggestions"] >= 1

    def test_legacy_with_bottlenecks(self, tmp_path):
        """Cover bottleneck-driven suggestion branch (535-545)."""
        engine = _make_engine(tmp_path)
        with patch("maop.history_analyzer.HistoryAnalyzer") as MockHA:
            mock_report = MockHA.return_value.analyze.return_value
            mock_report.period_hours = 24
            mock_report.total_loops = 100
            mock_report.success_rate = 80.0
            mock_report.failure_clusters = []
            mock_report.bottlenecks = [MagicMock(
                component="db", avg_duration_ms=60000, impact_score=0.8,
            )]
            mock_report.cost_drivers = []
            mock_report.recommendations = []
            with patch("maop.agent_strategy_learner.AgentStrategyLearner") as MockSL:
                MockSL.return_value.learn.return_value = MagicMock(
                    total_combos=0, reliable_combos=0, underperformers=0,
                    adjustments=[], routing_winners=[], recommendations=[],
                )
                with patch("maop.cache_evolver.CacheEvolver") as MockCE:
                    MockCE.return_value.evolve.return_value = MagicMock(
                        total_caches=0, adjustments=[], applied_count=0,
                        skipped_count=0, recommendations=[],
                    )
                    result = engine._auto_evolve_legacy(hours=24)
        assert result["new_suggestions"] >= 1

    def test_legacy_agent_strategy_with_adjustments(self, tmp_path):
        """Cover agent strategy learning with adjustments (547-583)."""
        engine = _make_engine(tmp_path)
        adj = MagicMock(
            agent="claude", routing_key="code", action="disable",
            reason="underperforming", suggested_alternative="codex",
            auto_applicable=True,
        )
        with patch("maop.history_analyzer.HistoryAnalyzer") as MockHA:
            mock_report = MockHA.return_value.analyze.return_value
            mock_report.period_hours = 24
            mock_report.total_loops = 100
            mock_report.success_rate = 80.0
            mock_report.failure_clusters = []
            mock_report.bottlenecks = []
            mock_report.cost_drivers = []
            mock_report.recommendations = []
            with patch("maop.agent_strategy_learner.AgentStrategyLearner") as MockSL:
                MockSL.return_value.learn.return_value = MagicMock(
                    total_combos=5, reliable_combos=3, underperformers=2,
                    adjustments=[adj], routing_winners=[], recommendations=[],
                )
                with patch("maop.cache_evolver.CacheEvolver") as MockCE:
                    MockCE.return_value.evolve.return_value = MagicMock(
                        total_caches=0, adjustments=[], applied_count=0,
                        skipped_count=0, recommendations=[],
                    )
                    result = engine._auto_evolve_legacy(hours=24)
        assert result["agent_strategy"]["adjustments_count"] == 1

    def test_legacy_agent_strategy_apply_exception(self, tmp_path):
        """Cover branch where apply_adjustment raises (578-581)."""
        engine = _make_engine(tmp_path)
        adj = MagicMock(
            agent="claude", routing_key="code", action="disable",
            reason="underperforming", suggested_alternative="codex",
            auto_applicable=True,
        )
        with patch("maop.history_analyzer.HistoryAnalyzer") as MockHA:
            mock_report = MockHA.return_value.analyze.return_value
            mock_report.period_hours = 24
            mock_report.total_loops = 100
            mock_report.success_rate = 80.0
            mock_report.failure_clusters = []
            mock_report.bottlenecks = []
            mock_report.cost_drivers = []
            mock_report.recommendations = []
            with patch("maop.agent_strategy_learner.AgentStrategyLearner") as MockSL:
                MockSL.return_value.learn.return_value = MagicMock(
                    total_combos=5, reliable_combos=3, underperformers=2,
                    adjustments=[adj], routing_winners=[], recommendations=[],
                )
                MockSL.return_value.apply_adjustment.side_effect = RuntimeError("apply boom")
                with patch("maop.cache_evolver.CacheEvolver") as MockCE:
                    MockCE.return_value.evolve.return_value = MagicMock(
                        total_caches=0, adjustments=[], applied_count=0,
                        skipped_count=0, recommendations=[],
                    )
                    result = engine._auto_evolve_legacy(hours=24)
        # Should not raise; suggestion still generated
        assert result["new_suggestions"] >= 1

    def test_legacy_agent_strategy_learn_exception(self, tmp_path):
        """Cover branch where AgentStrategyLearner.learn raises (582-583)."""
        engine = _make_engine(tmp_path)
        with patch("maop.history_analyzer.HistoryAnalyzer") as MockHA:
            mock_report = MockHA.return_value.analyze.return_value
            mock_report.period_hours = 24
            mock_report.total_loops = 100
            mock_report.success_rate = 80.0
            mock_report.failure_clusters = []
            mock_report.bottlenecks = []
            mock_report.cost_drivers = []
            mock_report.recommendations = []
            with patch("maop.agent_strategy_learner.AgentStrategyLearner") as MockSL:
                MockSL.return_value.learn.side_effect = RuntimeError("learn boom")
                with patch("maop.cache_evolver.CacheEvolver") as MockCE:
                    MockCE.return_value.evolve.return_value = MagicMock(
                        total_caches=0, adjustments=[], applied_count=0,
                        skipped_count=0, recommendations=[],
                    )
                    result = engine._auto_evolve_legacy(hours=24)
        assert "agent_strategy" in result

    def test_legacy_cache_evolve_with_adjustments(self, tmp_path):
        """Cover cache evolution with adjustments (585-608)."""
        engine = _make_engine(tmp_path)
        cadj = MagicMock(
            cache_name="llm_cache", parameter="max_size",
            old_value=100, new_value=200, reason="hit rate low",
            auto_applicable=True, applied=True,
        )
        with patch("maop.history_analyzer.HistoryAnalyzer") as MockHA:
            mock_report = MockHA.return_value.analyze.return_value
            mock_report.period_hours = 24
            mock_report.total_loops = 100
            mock_report.success_rate = 80.0
            mock_report.failure_clusters = []
            mock_report.bottlenecks = []
            mock_report.cost_drivers = []
            mock_report.recommendations = []
            with patch("maop.agent_strategy_learner.AgentStrategyLearner") as MockSL:
                MockSL.return_value.learn.return_value = MagicMock(
                    total_combos=0, reliable_combos=0, underperformers=0,
                    adjustments=[], routing_winners=[], recommendations=[],
                )
                with patch("maop.cache_evolver.CacheEvolver") as MockCE:
                    MockCE.return_value.evolve.return_value = MagicMock(
                        total_caches=3, adjustments=[cadj], applied_count=1,
                        skipped_count=0, recommendations=[],
                    )
                    result = engine._auto_evolve_legacy(hours=24)
        assert result["cache_evolution"]["total_caches"] == 3

    def test_legacy_cache_evolve_exception(self, tmp_path):
        """Cover branch where CacheEvolver.evolve raises (609-610)."""
        engine = _make_engine(tmp_path)
        with patch("maop.history_analyzer.HistoryAnalyzer") as MockHA:
            mock_report = MockHA.return_value.analyze.return_value
            mock_report.period_hours = 24
            mock_report.total_loops = 100
            mock_report.success_rate = 80.0
            mock_report.failure_clusters = []
            mock_report.bottlenecks = []
            mock_report.cost_drivers = []
            mock_report.recommendations = []
            with patch("maop.agent_strategy_learner.AgentStrategyLearner") as MockSL:
                MockSL.return_value.learn.return_value = MagicMock(
                    total_combos=0, reliable_combos=0, underperformers=0,
                    adjustments=[], routing_winners=[], recommendations=[],
                )
                with patch("maop.cache_evolver.CacheEvolver") as MockCE:
                    MockCE.return_value.evolve.side_effect = RuntimeError("cache boom")
                    result = engine._auto_evolve_legacy(hours=24)
        assert "cache_evolution" in result

    def test_legacy_auto_apply_suggestion(self, tmp_path):
        """Cover branch where auto_applicable suggestion is auto-applied (623-631)."""
        engine = _make_engine(tmp_path)
        # Create an auto-applicable suggestion that engine.apply can find
        auto_s = Suggestion(
            id="auto_apply_test", type="routing_mismatch", severity="high",
            agent="claude", routing_key="x", auto_applicable=True,
        )
        engine._save_suggestions([auto_s])
        with patch("maop.history_analyzer.HistoryAnalyzer") as MockHA:
            mock_report = MockHA.return_value.analyze.return_value
            mock_report.period_hours = 24
            mock_report.total_loops = 100
            mock_report.success_rate = 80.0
            mock_report.failure_clusters = []
            mock_report.bottlenecks = []
            mock_report.cost_drivers = []
            mock_report.recommendations = []
            with patch("maop.agent_strategy_learner.AgentStrategyLearner") as MockSL:
                MockSL.return_value.learn.return_value = MagicMock(
                    total_combos=0, reliable_combos=0, underperformers=0,
                    adjustments=[], routing_winners=[], recommendations=[],
                )
                with patch("maop.cache_evolver.CacheEvolver") as MockCE:
                    MockCE.return_value.evolve.return_value = MagicMock(
                        total_caches=0, adjustments=[], applied_count=0,
                        skipped_count=0, recommendations=[],
                    )
                    # Add a new auto-applicable suggestion to trigger auto-apply
                    Suggestion(
                        id="new_auto_1", type="routing_mismatch", severity="high",
                        agent="claude", routing_key="y", auto_applicable=True,
                    )
                    # Patch _generate_suggestions via _compute_stats path is complex;
                    # instead, directly inject via patching _load_data + _compute_stats
                    with patch.object(engine, "_load_suggestions", return_value=[auto_s]):
                        result = engine._auto_evolve_legacy(hours=24)
        assert "auto_applied" in result

    def test_legacy_auto_apply_exception(self, tmp_path):
        """Cover branch where auto-apply raises (630-631)."""
        engine = _make_engine(tmp_path)
        auto_s = Suggestion(
            id="auto_apply_fail", type="routing_mismatch", severity="high",
            agent="claude", routing_key="x", auto_applicable=True,
        )
        engine._save_suggestions([auto_s])
        with patch("maop.history_analyzer.HistoryAnalyzer") as MockHA:
            mock_report = MockHA.return_value.analyze.return_value
            mock_report.period_hours = 24
            mock_report.total_loops = 100
            mock_report.success_rate = 80.0
            mock_report.failure_clusters = []
            mock_report.bottlenecks = []
            mock_report.cost_drivers = []
            mock_report.recommendations = []
            with patch("maop.agent_strategy_learner.AgentStrategyLearner") as MockSL:
                MockSL.return_value.learn.return_value = MagicMock(
                    total_combos=0, reliable_combos=0, underperformers=0,
                    adjustments=[], routing_winners=[], recommendations=[],
                )
                with patch("maop.cache_evolver.CacheEvolver") as MockCE:
                    MockCE.return_value.evolve.return_value = MagicMock(
                        total_caches=0, adjustments=[], applied_count=0,
                        skipped_count=0, recommendations=[],
                    )
                    with patch.object(engine, "apply", side_effect=RuntimeError("apply boom")):
                        with patch.object(engine, "_load_suggestions", return_value=[auto_s]):
                            result = engine._auto_evolve_legacy(hours=24)
        assert "auto_applied" in result