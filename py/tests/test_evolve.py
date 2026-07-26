"""Tests for MAOP.evolve — Self-evolution engine: stats, suggestions, apply, promote."""

from __future__ import annotations

import json
from pathlib import Path

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
