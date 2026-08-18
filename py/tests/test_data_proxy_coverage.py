"""Coverage tests for dashboard/data_proxy.py + dashboard/routers/data.py
+ dashboard/routers/system.py (extended) + dashboard/routers/agents/ (extended package).

Uses isolated tmp_path + real instances where possible.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

# ── Data Proxy ──────────────────────────────────────────────────────

class TestDataProxy:
    def test_init(self, tmp_path):
        from maop.dashboard.data_proxy import DataProxy
        proxy = DataProxy(root_dir=str(tmp_path))
        assert proxy is not None

    def test_stats(self, tmp_path):
        from maop.dashboard.data_proxy import DataProxy
        proxy = DataProxy(root_dir=str(tmp_path))
        stats = proxy.stats()
        assert stats is not None

    def test_repr(self, tmp_path):
        from maop.dashboard.data_proxy import DataProxy
        proxy = DataProxy(root_dir=str(tmp_path))
        assert "DataProxy" in repr(proxy) or "data_proxy" in repr(proxy).lower()

    def test_report(self, tmp_path):
        from maop.dashboard.data_proxy import DataProxy
        proxy = DataProxy(root_dir=str(tmp_path))
        result = asyncio.run(proxy.report(hours=24))
        assert isinstance(result, dict)

    def test_agent_stats(self, tmp_path):
        from maop.dashboard.data_proxy import DataProxy
        proxy = DataProxy(root_dir=str(tmp_path))
        result = asyncio.run(proxy.agent_stats())
        assert isinstance(result, list)

    def test_live(self, tmp_path):
        from maop.dashboard.data_proxy import DataProxy
        proxy = DataProxy(root_dir=str(tmp_path))
        result = asyncio.run(proxy.live())
        assert isinstance(result, dict)

    def test_failures(self, tmp_path):
        from maop.dashboard.data_proxy import DataProxy
        proxy = DataProxy(root_dir=str(tmp_path))
        result = asyncio.run(proxy.failures(hours=24))
        assert isinstance(result, list)

    def test_timeseries(self, tmp_path):
        from maop.dashboard.data_proxy import DataProxy
        proxy = DataProxy(root_dir=str(tmp_path))
        result = asyncio.run(proxy.timeseries(hours=168))
        assert isinstance(result, list)

    def test_chain(self, tmp_path):
        from maop.dashboard.data_proxy import DataProxy
        proxy = DataProxy(root_dir=str(tmp_path))
        result = asyncio.run(proxy.chain())
        assert isinstance(result, list)

    def test_memory_stats(self, tmp_path):
        from maop.dashboard.data_proxy import DataProxy
        proxy = DataProxy(root_dir=str(tmp_path))
        result = asyncio.run(proxy.memory_stats())
        assert isinstance(result, dict)

    def test_guardrail_report(self, tmp_path):
        from maop.dashboard.data_proxy import DataProxy
        proxy = DataProxy(root_dir=str(tmp_path))
        result = asyncio.run(proxy.guardrail_report())
        assert isinstance(result, dict)

    def test_snapshot(self, tmp_path):
        from maop.dashboard.data_proxy import DataProxy
        proxy = DataProxy(root_dir=str(tmp_path))
        result = asyncio.run(proxy.snapshot())
        assert isinstance(result, dict)

    def test_queue_stats(self, tmp_path):
        from maop.dashboard.data_proxy import DataProxy
        proxy = DataProxy(root_dir=str(tmp_path))
        result = asyncio.run(proxy.queue_stats())
        assert isinstance(result, dict)

    def test_tools_stats(self, tmp_path):
        from maop.dashboard.data_proxy import DataProxy
        proxy = DataProxy(root_dir=str(tmp_path))
        result = asyncio.run(proxy.tools_stats())
        assert isinstance(result, dict)

    def test_tools_list(self, tmp_path):
        from maop.dashboard.data_proxy import DataProxy
        proxy = DataProxy(root_dir=str(tmp_path))
        result = asyncio.run(proxy.tools_list())
        assert isinstance(result, list)

    def test_sandbox_list(self, tmp_path):
        from maop.dashboard.data_proxy import DataProxy
        proxy = DataProxy(root_dir=str(tmp_path))
        result = asyncio.run(proxy.sandbox_list())
        assert isinstance(result, list)

    def test_human_pending(self, tmp_path):
        from maop.dashboard.data_proxy import DataProxy
        proxy = DataProxy(root_dir=str(tmp_path))
        result = asyncio.run(proxy.human_pending())
        assert isinstance(result, dict)

    def test_prompts_list(self, tmp_path):
        from maop.dashboard.data_proxy import DataProxy
        proxy = DataProxy(root_dir=str(tmp_path))
        result = asyncio.run(proxy.prompts_list())
        assert isinstance(result, dict)

    def test_coordination_report(self, tmp_path):
        from maop.dashboard.data_proxy import DataProxy
        proxy = DataProxy(root_dir=str(tmp_path))
        result = asyncio.run(proxy.coordination_report())
        assert isinstance(result, dict)

    def test_skills_list(self, tmp_path):
        from maop.dashboard.data_proxy import DataProxy
        proxy = DataProxy(root_dir=str(tmp_path))
        result = asyncio.run(proxy.skills_list())
        assert isinstance(result, list)

    def test_versions_check(self, tmp_path):
        from maop.dashboard.data_proxy import DataProxy
        proxy = DataProxy(root_dir=str(tmp_path))
        result = asyncio.run(proxy.versions_check())
        assert isinstance(result, dict)

    def test_providers_report(self, tmp_path):
        from maop.dashboard.data_proxy import DataProxy
        proxy = DataProxy(root_dir=str(tmp_path))
        result = asyncio.run(proxy.providers_report())
        assert isinstance(result, dict)

    def test_mcp_servers(self, tmp_path):
        from maop.dashboard.data_proxy import DataProxy
        proxy = DataProxy(root_dir=str(tmp_path))
        result = asyncio.run(proxy.mcp_servers())
        assert isinstance(result, list)

    def test_mcp_tools(self, tmp_path):
        from maop.dashboard.data_proxy import DataProxy
        proxy = DataProxy(root_dir=str(tmp_path))
        result = asyncio.run(proxy.mcp_tools())
        assert isinstance(result, list)

    def test_graph_nodes(self, tmp_path):
        from maop.dashboard.data_proxy import DataProxy
        proxy = DataProxy(root_dir=str(tmp_path))
        result = asyncio.run(proxy.graph_nodes())
        assert isinstance(result, list)

    def test_graph_edges(self, tmp_path):
        from maop.dashboard.data_proxy import DataProxy
        proxy = DataProxy(root_dir=str(tmp_path))
        result = asyncio.run(proxy.graph_edges())
        assert isinstance(result, list)

    def test_logs_get(self, tmp_path):
        from maop.dashboard.data_proxy import DataProxy
        proxy = DataProxy(root_dir=str(tmp_path))
        result = asyncio.run(proxy.logs_get(name="dashboard", limit=10))
        assert isinstance(result, list)

    def test_delegation_period_stats(self, tmp_path):
        from maop.dashboard.data_proxy import DataProxy
        proxy = DataProxy(root_dir=str(tmp_path))
        result = asyncio.run(proxy.delegation_period_stats())
        assert isinstance(result, dict)


# ── Data Proxy Sync helpers ─────────────────────────────────────────

class TestDataProxySync:
    def test_query_maop_sync(self, tmp_path):
        from maop.dashboard.data_proxy import DataProxy
        proxy = DataProxy(root_dir=str(tmp_path))
        result = proxy._query_maop_sync("SELECT 1 as v")
        assert isinstance(result, list)

    def test_query_memory_sync(self, tmp_path):
        from maop.dashboard.data_proxy import DataProxy
        proxy = DataProxy(root_dir=str(tmp_path))
        result = proxy._query_memory_sync("SELECT 1 as v")
        assert isinstance(result, list)

    def test_queue_stats_sync(self, tmp_path):
        from maop.dashboard.data_proxy import DataProxy
        proxy = DataProxy(root_dir=str(tmp_path))
        result = proxy._queue_stats_sync()
        assert isinstance(result, dict)

    def test_read_delegations_json(self, tmp_path):
        from maop.dashboard.data_proxy import DataProxy
        proxy = DataProxy(root_dir=str(tmp_path))
        result = proxy._read_delegations_json(limit=10)
        assert isinstance(result, list)

    def test_read_checker_logs(self, tmp_path):
        from maop.dashboard.data_proxy import DataProxy
        proxy = DataProxy(root_dir=str(tmp_path))
        result = proxy._read_checker_logs(limit=10)
        assert isinstance(result, list)


# ── Proxy Stats Model ──────────────────────────────────────────────

class TestProxyStats:
    def test_default(self):
        from maop.dashboard.data_proxy import ProxyStats
        stats = ProxyStats()
        assert stats.queries == 0
        assert stats.cache_hits == 0
        assert stats.total_latency_ms == 0.0

# --- Merged from test_data_proxy_coverage3.py ---

# ── Helpers ─────────────────────────────────────────────────────────


def _make_proxy(root: Path):
    from maop.dashboard.data_proxy import DataProxy
    return DataProxy(root_dir=str(root))


def _init_maop_db(root: Path) -> Path:
    """Initialize maop.db with the standard schema and return its path."""
    from maop.core.backends.data import MaopDatabase
    db = MaopDatabase(root / "data" / "maop.db")
    db.init()
    return root / "data" / "maop.db"


def _insert_rows(db_path: Path, table: str, rows: list[dict]) -> None:
    """Insert rows into a table by dict column names."""
    if not rows:
        return
    cols = list(rows[0].keys())
    placeholders = ",".join("?" * len(cols))
    sql = f"INSERT INTO {table} ({','.join(cols)}) VALUES ({placeholders})"
    with sqlite3.connect(str(db_path)) as conn:
        for r in rows:
            conn.execute(sql, tuple(r[c] for c in cols))
        conn.commit()


# ── _ensure_db_schema exception path (80-84) ────────────────────────


class TestEnsureDbSchemaError:
    def test_init_logs_when_schema_init_fails(self, tmp_path, caplog):
        """_ensure_db_schema should log warning when MaopDatabase.init raises."""
        with patch("maop.core.backends.data.MaopDatabase") as MockDb:
            MockDb.return_value.init.side_effect = RuntimeError("boom")
            with caplog.at_level("WARNING", logger="maop.dashboard.data_proxy"):
                proxy = _make_proxy(tmp_path)
            # Construction should not raise; warning logged instead.
            assert proxy is not None
            assert any("schema" in rec.message.lower() for rec in caplog.records)


# ── report() with data (158-167) ────────────────────────────────────


class TestReportWithData:
    def test_report_with_delegations(self, tmp_path):
        from datetime import datetime, timedelta, timezone
        now = datetime.now(timezone.utc)
        ts1 = (now - timedelta(hours=1)).isoformat()
        ts2 = (now - timedelta(hours=2)).isoformat()
        ts3 = (now - timedelta(hours=3)).isoformat()
        db_path = _init_maop_db(tmp_path)
        _insert_rows(db_path, "delegations", [
            {"timestamp": ts1, "agent": "claude",
             "task": "t1", "routing_key": "", "exit_code": 0,
             "stdout": "", "stderr": "", "duration_ms": 100, "trace_id": ""},
            {"timestamp": ts2, "agent": "claude",
             "task": "t2", "routing_key": "", "exit_code": 1,
             "stdout": "", "stderr": "", "duration_ms": 200, "trace_id": ""},
            {"timestamp": ts3, "agent": "codex",
             "task": "t3", "routing_key": "", "exit_code": 0,
             "stdout": "", "stderr": "", "duration_ms": 50, "trace_id": ""},
        ])
        proxy = _make_proxy(tmp_path)
        result = asyncio.run(proxy.report(hours=48))
        assert result["total_delegations"] == 3
        assert "claude" in result["by_agent"]
        assert "codex" in result["by_agent"]
        assert result["by_agent"]["claude"]["success"] == 1
        assert result["by_agent"]["claude"]["failure"] == 1
        assert 0 < result["success_rate"] <= 100


# ── agent_stats() with circuit_breaker_state (204-207, 233-234) ─────


class TestAgentStatsWithCB:
    def test_with_circuit_breaker_state(self, tmp_path):
        db_path = _init_maop_db(tmp_path)
        _insert_rows(db_path, "delegations", [
            {"timestamp": "2026-01-01T00:00:00+00:00", "agent": "claude",
             "task": "t", "routing_key": "", "exit_code": 0,
             "stdout": "", "stderr": "", "duration_ms": 10, "trace_id": ""},
        ])
        # Create the view/table the bridge queries
        with sqlite3.connect(str(db_path)) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS circuit_breaker_state "
                "(agent TEXT, state TEXT, failures INT, threshold INT)"
            )
            conn.execute(
                "INSERT INTO circuit_breaker_state VALUES ('claude', 'open', 7, 5)"
            )
            conn.commit()
        proxy = _make_proxy(tmp_path)
        result = asyncio.run(proxy.agent_stats())
        claude = next(r for r in result if r["agent"] == "claude")
        assert claude["circuit_breaker"] == "open"
        assert claude["failures"] == 7
        assert claude["threshold"] == 5

    def test_config_fallback_exception(self, tmp_path):
        """If ConfigLoader.load raises, the warning branch (233-234) runs."""
        db_path = _init_maop_db(tmp_path)
        _insert_rows(db_path, "delegations", [
            {"timestamp": "2026-01-01T00:00:00+00:00", "agent": "claude",
             "task": "t", "routing_key": "", "exit_code": 0,
             "stdout": "", "stderr": "", "duration_ms": 10, "trace_id": ""},
        ])
        proxy = _make_proxy(tmp_path)
        with patch("maop.config.loader.ConfigLoader") as MockLoader:
            MockLoader.return_value.load.side_effect = RuntimeError("cfg boom")
            result = asyncio.run(proxy.agent_stats())
        # Should still return the claude row from delegations
        assert any(r["agent"] == "claude" for r in result)


# ── live() exception sub-branches (275-276, 288-289, 297-298) ──────


class TestLiveExceptionBranches:
    def test_live_queue_stats_exception(self, tmp_path):
        _init_maop_db(tmp_path)
        proxy = _make_proxy(tmp_path)
        with patch.object(proxy, "_queue_stats_sync", side_effect=RuntimeError("q boom")):
            result = asyncio.run(proxy.live())
        # Should still return a dict; queue_depth falls back to 0
        assert result["queue_depth"] == 0

    def test_live_agent_stats_exception(self, tmp_path):
        _init_maop_db(tmp_path)
        proxy = _make_proxy(tmp_path)
        with patch.object(proxy, "agent_stats", side_effect=RuntimeError("a boom")):
            result = asyncio.run(proxy.live())
        assert result["agents"] == []

    def test_live_cost_tracker_exception(self, tmp_path):
        _init_maop_db(tmp_path)
        proxy = _make_proxy(tmp_path)
        with patch("maop.core.cost_tracker.CostTracker") as MockCT:
            MockCT.return_value.summary.side_effect = RuntimeError("ct boom")
            result = asyncio.run(proxy.live())
        assert result["cost_per_hour"] == 0.0


# ── delegation_period_stats timestamp parsing (374-414) ─────────────


class TestDelegationPeriodStatsParsing:
    def _write_log(self, root: Path, records: list) -> None:
        log_dir = root / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / "delegations.json").write_text(
            json.dumps(records), encoding="utf-8"
        )

    def test_no_log_returns_empty(self, tmp_path):
        proxy = _make_proxy(tmp_path)
        result = asyncio.run(proxy.delegation_period_stats())
        assert result["total"] == 0
        assert result["delegations_mom"] is None

    def test_z_suffix_timestamp(self, tmp_path):
        """Cover _parse_ts branch that converts 'Z' to '+00:00'."""
        from datetime import datetime, timezone
        now = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
        self._write_log(tmp_path, [
            {"timestamp": "2026-05-15T10:00:00Z", "exit_code": 0},
            {"timestamp": "2026-05-20T10:00:00Z", "exit_code": 1},
        ])
        proxy = _make_proxy(tmp_path)
        result = asyncio.run(proxy.delegation_period_stats(now=now))
        assert result["total"] >= 1

    def test_fractional_seconds_truncated(self, tmp_path):
        """Cover _parse_ts branch that truncates fractional seconds to 6 digits."""
        from datetime import datetime, timezone
        now = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
        self._write_log(tmp_path, [
            {"timestamp": "2026-05-15T10:00:00.123456789+00:00", "exit_code": 0},
        ])
        proxy = _make_proxy(tmp_path)
        result = asyncio.run(proxy.delegation_period_stats(now=now))
        assert result["total"] >= 1

    def test_unparseable_timestamp(self, tmp_path):
        """Cover _parse_ts branch that returns None on bad input."""
        from datetime import datetime, timezone
        now = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
        self._write_log(tmp_path, [
            {"timestamp": "not-a-timestamp", "exit_code": 0},
            {"timestamp": "", "exit_code": 0},
            {"timestamp": "2026-05-15T10:00:00+00:00", "exit_code": 0},
        ])
        proxy = _make_proxy(tmp_path)
        result = asyncio.run(proxy.delegation_period_stats(now=now))
        assert result["total"] == 1

    def test_exit_code_via_result_dict(self, tmp_path):
        """Cover _window branch that reads exit_code from rec['result']."""
        from datetime import datetime, timezone
        now = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
        self._write_log(tmp_path, [
            {"timestamp": "2026-05-15T10:00:00+00:00",
             "result": {"exit_code": 0}},
        ])
        proxy = _make_proxy(tmp_path)
        result = asyncio.run(proxy.delegation_period_stats(now=now))
        assert result["total"] == 1
        assert result["success_rate"] == 100.0

    def test_records_not_list_returns_empty(self, tmp_path):
        """Cover branch where delegations.json is not a list."""
        (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
        (tmp_path / "logs" / "delegations.json").write_text(
            json.dumps({"not": "a list"}), encoding="utf-8"
        )
        proxy = _make_proxy(tmp_path)
        result = asyncio.run(proxy.delegation_period_stats())
        assert result["total"] == 0

    def test_read_exception_returns_empty(self, tmp_path):
        """Cover branch where delegations.json read raises."""
        (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
        (tmp_path / "logs" / "delegations.json").write_text(
            "not valid json {{{", encoding="utf-8"
        )
        proxy = _make_proxy(tmp_path)
        result = asyncio.run(proxy.delegation_period_stats())
        assert result["total"] == 0

    def test_mom_and_yoy_with_prior_data(self, tmp_path):
        """Cover _pct branches where prev period has data."""
        from datetime import datetime, timezone
        now = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
        self._write_log(tmp_path, [
            # current 30d
            {"timestamp": "2026-05-15T10:00:00+00:00", "exit_code": 0},
            {"timestamp": "2026-05-16T10:00:00+00:00", "exit_code": 0},
            # prev 30d
            {"timestamp": "2026-04-15T10:00:00+00:00", "exit_code": 0},
            # current 365d (older)
            {"timestamp": "2025-12-01T10:00:00+00:00", "exit_code": 0},
            # prev 365d
            {"timestamp": "2024-12-01T10:00:00+00:00", "exit_code": 0},
        ])
        proxy = _make_proxy(tmp_path)
        result = asyncio.run(proxy.delegation_period_stats(now=now))
        assert result["delegations_mom"] is not None
        assert result["delegations_yoy"] is not None


# ── memory_stats episodic_count exception (470-471) ─────────────────


class TestMemoryStatsEpisodic:
    def test_episodic_table_missing(self, tmp_path):
        """If episodic_memory table doesn't exist, the except branch runs."""
        from maop.core.backends.data import MaopDatabase
        # Initialize memory.db via the standard memory schema
        mem_db = tmp_path / "data" / "maop.db"
        MaopDatabase(mem_db).init()
        # Create memory_entries etc. but NOT episodic_memory
        with sqlite3.connect(str(mem_db)) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS memory_entries "
                "(id INTEGER PRIMARY KEY, agent TEXT, topic TEXT, content TEXT)"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS memory_traces "
                "(id INTEGER PRIMARY KEY, trace_id TEXT, agent TEXT)"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS memory_trajectory "
                "(id INTEGER PRIMARY KEY)"
            )
            conn.execute(
                "INSERT INTO memory_entries (agent, topic, content) "
                "VALUES ('claude', 'bugs', 'fix')"
            )
            conn.commit()
        proxy = _make_proxy(tmp_path)
        result = asyncio.run(proxy.memory_stats())
        assert result["total_entries"] == 1
        assert result["total_episodic"] == 0


# ── guardrail_report yaml parsing (498-505) ─────────────────────────


class TestGuardrailReportYaml:
    def test_yaml_parsed(self, tmp_path):
        cfg_dir = tmp_path / "config"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        (cfg_dir / "guardrails.yaml").write_text(
            "rules:\n  - name: r1\n    action: block\n  - name: r2\n    action: warn\n",
            encoding="utf-8",
        )
        proxy = _make_proxy(tmp_path)
        result = asyncio.run(proxy.guardrail_report())
        assert result["total_rules"] == 2
        assert result["status"] == "active"

    def test_yaml_malformed(self, tmp_path):
        cfg_dir = tmp_path / "config"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        (cfg_dir / "guardrails.yaml").write_text(
            "not: valid: yaml: {{{", encoding="utf-8"
        )
        proxy = _make_proxy(tmp_path)
        result = asyncio.run(proxy.guardrail_report())
        # Falls back to no rules
        assert result["total_rules"] == 0
        assert result["status"] == "no_rules"


# ── snapshot() exception branches (528-554) ─────────────────────────


class TestSnapshotExceptions:
    def test_live_exception_returns_empty(self, tmp_path):
        _init_maop_db(tmp_path)
        proxy = _make_proxy(tmp_path)
        with patch.object(proxy, "live", side_effect=RuntimeError("live boom")):
            result = asyncio.run(proxy.snapshot())
        assert result == {}

    def test_queue_stats_exception_in_snapshot(self, tmp_path):
        _init_maop_db(tmp_path)
        proxy = _make_proxy(tmp_path)
        with patch.object(proxy, "_queue_stats_sync", side_effect=RuntimeError("q boom")):
            result = asyncio.run(proxy.snapshot())
        # queue_health falls back to 100
        assert result.get("queue_health_pct") == 100

    def test_memory_stats_exception_in_snapshot(self, tmp_path):
        _init_maop_db(tmp_path)
        proxy = _make_proxy(tmp_path)
        with patch.object(proxy, "memory_stats", side_effect=RuntimeError("m boom")):
            result = asyncio.run(proxy.snapshot())
        assert result.get("memory_usage_pct") == 0


# ── _queue_stats_sync exception path (578-582) ──────────────────────


class TestQueueStatsSyncException:
    def test_queue_db_missing_tables(self, tmp_path):
        """If queue.db has no queue_messages table, the except branch runs."""
        # Don't initialize queue.db — pool will create empty db
        proxy = _make_proxy(tmp_path)
        result = proxy._queue_stats_sync()
        assert result == {"pending": 0, "processing": 0, "dead_letters": 0}


# ── tools/sandbox/human/prompts exception branches (608-677) ────────


class TestToolsAndManagersException:
    def test_tools_stats_exception(self, tmp_path):
        proxy = _make_proxy(tmp_path)
        with patch("maop.core.agent.tools.tool_manager.ToolManager") as MockTM:
            MockTM.return_value.stats.side_effect = RuntimeError("tm boom")
            result = asyncio.run(proxy.tools_stats())
        assert result == {"total": 0, "enabled": 0, "disabled": 0, "total_calls": 0}

    def test_tools_list_exception(self, tmp_path):
        proxy = _make_proxy(tmp_path)
        with patch("maop.core.agent.tools.tool_manager.ToolManager") as MockTM:
            MockTM.return_value.list.side_effect = RuntimeError("tm boom")
            result = asyncio.run(proxy.tools_list())
        assert result == []

    def test_sandbox_list_exception(self, tmp_path):
        proxy = _make_proxy(tmp_path)
        with patch("maop.core.security.sandbox.SandboxManager") as MockSM:
            MockSM.return_value.list_all.side_effect = RuntimeError("sm boom")
            result = asyncio.run(proxy.sandbox_list())
        assert result == []

    def test_human_pending_exception(self, tmp_path):
        proxy = _make_proxy(tmp_path)
        with patch("maop.core.agent.delegation.human_proxy.HumanProxy") as MockHP:
            MockHP.return_value.pending.side_effect = RuntimeError("hp boom")
            result = asyncio.run(proxy.human_pending())
        assert result == {"pending": [], "stats": {}}

    def test_prompts_list_exception(self, tmp_path):
        proxy = _make_proxy(tmp_path)
        with patch("maop.prompt_manager.PromptManager") as MockPM:
            MockPM.return_value.list_templates.side_effect = RuntimeError("pm boom")
            result = asyncio.run(proxy.prompts_list())
        assert result == {"templates": [], "stats": {}}


# ── coordination_report exception branches (699-709) ───────────────


class TestCoordinationReportExceptions:
    def test_inner_config_exception(self, tmp_path):
        _init_maop_db(tmp_path)
        proxy = _make_proxy(tmp_path)
        with patch("maop.config.loader.ConfigLoader") as MockLoader:
            MockLoader.return_value.load.side_effect = RuntimeError("cfg boom")
            result = asyncio.run(proxy.coordination_report())
        assert result["teams"] == []

    def test_outer_exception(self, tmp_path):
        proxy = _make_proxy(tmp_path)
        with patch.object(proxy, "queue_stats", side_effect=RuntimeError("q boom")):
            result = asyncio.run(proxy.coordination_report())
        assert result == {"queue": {}, "active_agents": [], "teams": []}


# ── skills_list exception branch (722-726) ──────────────────────────


class TestSkillsListException:
    def test_skills_list_exception(self, tmp_path):
        proxy = _make_proxy(tmp_path)
        with patch("maop.core.agent.tools.tool_manager.ToolManager") as MockTM:
            MockTM.return_value.list.side_effect = RuntimeError("tm boom")
            result = asyncio.run(proxy.skills_list())
        assert result == []

    def test_skills_list_with_skill_category(self, tmp_path):
        """Cover the branch where a cat_group has category 'skill'."""
        proxy = _make_proxy(tmp_path)
        with patch("maop.core.agent.tools.tool_manager.ToolManager") as MockTM:
            MockTM.return_value.list.return_value = [
                {"category": "skill", "tools": [{"name": "skill1"}]},
                {"category": "other", "tools": [{"name": "other1"}]},
            ]
            result = asyncio.run(proxy.skills_list())
        assert result == [{"name": "skill1"}]


# ── versions_check ImportError (735-736) ────────────────────────────


class TestVersionsCheckImportError:
    def test_import_error_branch(self, tmp_path):
        proxy = _make_proxy(tmp_path)
        with patch.dict("sys.modules", {"maop": None}):
            result = asyncio.run(proxy.versions_check())
        # Should fall back to "unknown"
        assert result["MAOP_VERSION"] == "unknown"
        assert result["ps_bridge_active"] is False


# ── providers_report exception branch (757-759) ────────────────────


class TestProvidersReportException:
    def test_agent_stats_exception(self, tmp_path):
        proxy = _make_proxy(tmp_path)
        with patch.object(proxy, "agent_stats", side_effect=RuntimeError("a boom")):
            result = asyncio.run(proxy.providers_report())
        assert result == {"agents": [], "total": 0, "available": 0}


# ── mcp_servers/mcp_tools yaml parsing (774-795) ────────────────────


class TestMcpYamlParsing:
    def _write_mcp_config(self, root: Path, content: str) -> None:
        cfg_dir = root / "config"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        (cfg_dir / "mcp_servers.yaml").write_text(content, encoding="utf-8")

    def test_mcp_servers_yaml_parsed(self, tmp_path):
        self._write_mcp_config(tmp_path, "servers:\n  - name: s1\n  - name: s2\n")
        proxy = _make_proxy(tmp_path)
        result = asyncio.run(proxy.mcp_servers())
        assert len(result) == 2

    def test_mcp_servers_yaml_malformed(self, tmp_path):
        self._write_mcp_config(tmp_path, "not: valid: yaml: {{{")
        proxy = _make_proxy(tmp_path)
        result = asyncio.run(proxy.mcp_servers())
        assert result == []

    def test_mcp_tools_yaml_parsed(self, tmp_path):
        self._write_mcp_config(
            tmp_path,
            "servers:\n  - name: s1\n    tools:\n      - t1\n      - t2\n",
        )
        proxy = _make_proxy(tmp_path)
        result = asyncio.run(proxy.mcp_tools())
        assert result == ["t1", "t2"]

    def test_mcp_tools_yaml_malformed(self, tmp_path):
        self._write_mcp_config(tmp_path, "not: valid: yaml: {{{")
        proxy = _make_proxy(tmp_path)
        result = asyncio.run(proxy.mcp_tools())
        assert result == []


# ── _read_delegations_json branches (852-858) ───────────────────────


class TestReadDelegationsJson:
    def test_no_file_returns_empty(self, tmp_path):
        proxy = _make_proxy(tmp_path)
        assert proxy._read_delegations_json(limit=10) == []

    def test_malformed_returns_empty(self, tmp_path):
        (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
        (tmp_path / "logs" / "delegations.json").write_text(
            "not valid json {{{", encoding="utf-8"
        )
        proxy = _make_proxy(tmp_path)
        assert proxy._read_delegations_json(limit=10) == []

    def test_not_list_returns_empty(self, tmp_path):
        (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
        (tmp_path / "logs" / "delegations.json").write_text(
            json.dumps({"not": "a list"}), encoding="utf-8"
        )
        proxy = _make_proxy(tmp_path)
        assert proxy._read_delegations_json(limit=10) == []

    def test_limit_zero_returns_all(self, tmp_path):
        (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
        data = [{"i": i} for i in range(5)]
        (tmp_path / "logs" / "delegations.json").write_text(
            json.dumps(data), encoding="utf-8"
        )
        proxy = _make_proxy(tmp_path)
        assert len(proxy._read_delegations_json(limit=0)) == 5


# ── _read_checker_logs branches (876-896) ───────────────────────────


class TestReadCheckerLogs:
    def test_no_dir_returns_empty(self, tmp_path):
        proxy = _make_proxy(tmp_path)
        assert proxy._read_checker_logs(limit=10) == []

    def test_with_matching_and_non_matching_lines(self, tmp_path):
        log_dir = tmp_path / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        # Matching line + non-matching line
        (log_dir / "checker_2026-01-01.log").write_text(
            "[2026-01-01 10:00:00] [claude] INFO: did thing\n"
            "random line without pattern\n"
            "[2026-01-01 10:01:00.123] [codex] ERROR: failed\n",
            encoding="utf-8",
        )
        proxy = _make_proxy(tmp_path)
        result = proxy._read_checker_logs(limit=10)
        assert len(result) == 3
        assert result[0]["agent"] == "claude"
        assert result[0]["level"] == "info"
        assert result[1]["agent"] == "checker"  # non-matching falls back
        assert result[2]["agent"] == "codex"
        assert result[2]["level"] == "error"

    def test_limit_truncates(self, tmp_path):
        log_dir = tmp_path / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        lines = "\n".join(
            f"[2026-01-01 10:0{i}:00] [agent{i}] INFO: msg{i}" for i in range(5)
        )
        (log_dir / "checker_2026-01-01.log").write_text(lines + "\n", encoding="utf-8")
        proxy = _make_proxy(tmp_path)
        result = proxy._read_checker_logs(limit=2)
        assert len(result) == 2

    def test_unreadable_file_skipped(self, tmp_path):
        """Cover the 'failed to read' debug branch (876-878)."""
        log_dir = tmp_path / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        # Create a file that will fail to read — use a directory with .log suffix
        bad = log_dir / "checker_2026-01-01.log"
        bad.mkdir()  # it's a directory, read_text will fail
        (log_dir / "checker_2026-01-02.log").write_text(
            "[2026-01-02 10:00:00] [claude] INFO: ok\n", encoding="utf-8"
        )
        proxy = _make_proxy(tmp_path)
        result = proxy._read_checker_logs(limit=10)
        # The directory file is skipped; the valid file is read
        assert len(result) == 1
        assert result[0]["agent"] == "claude"

    def test_limit_zero_returns_all(self, tmp_path):
        log_dir = tmp_path / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / "checker_2026-01-01.log").write_text(
            "[2026-01-01 10:00:00] [claude] INFO: did thing\n"
            "[2026-01-01 10:01:00] [codex] INFO: did other\n",
            encoding="utf-8",
        )
        proxy = _make_proxy(tmp_path)
        result = proxy._read_checker_logs(limit=0)
        assert len(result) == 2


# ── logs_get routing (delegations / checker) ────────────────────────


class TestLogsGetRouting:
    def test_logs_get_delegations(self, tmp_path):
        (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
        data = [{"agent": "claude", "exit_code": 0}]
        (tmp_path / "logs" / "delegations.json").write_text(
            json.dumps(data), encoding="utf-8"
        )
        proxy = _make_proxy(tmp_path)
        result = asyncio.run(proxy.logs_get(name="delegations", limit=10))
        assert len(result) == 1

    def test_logs_get_checker(self, tmp_path):
        log_dir = tmp_path / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / "checker_2026-01-01.log").write_text(
            "[2026-01-01 10:00:00] [claude] INFO: did thing\n", encoding="utf-8"
        )
        proxy = _make_proxy(tmp_path)
        result = asyncio.run(proxy.logs_get(name="checker", limit=10))
        assert len(result) == 1


# ── Success-path branches for sandbox/human/prompts/coordination/providers ──


class TestSuccessPaths:
    def test_sandbox_list_success(self, tmp_path):
        from unittest.mock import MagicMock
        proxy = _make_proxy(tmp_path)
        sb = MagicMock()
        sb.list_all.return_value = [MagicMock(model_dump=lambda: {"id": "sb1"})]
        proxy._sandbox_mgr = sb
        result = asyncio.run(proxy.sandbox_list())
        assert result == [{"id": "sb1"}]

    def test_human_pending_success(self, tmp_path):
        from unittest.mock import MagicMock
        proxy = _make_proxy(tmp_path)
        hp = MagicMock()
        hp.pending.return_value = [MagicMock(model_dump=lambda: {"id": "r1"})]
        hp.stats.return_value = {"total": 1}
        proxy._human_proxy = hp
        result = asyncio.run(proxy.human_pending())
        assert result["pending"] == [{"id": "r1"}]
        assert result["stats"] == {"total": 1}

    def test_prompts_list_success(self, tmp_path):
        from unittest.mock import MagicMock
        proxy = _make_proxy(tmp_path)
        with patch("maop.prompt_manager.PromptManager") as MockPM:
            MockPM.return_value.list_templates.return_value = [
                MagicMock(model_dump=lambda: {"name": "t1"})
            ]
            MockPM.return_value.stats.return_value = {"total": 1}
            result = asyncio.run(proxy.prompts_list())
        assert result["templates"] == [{"name": "t1"}]
        assert result["stats"] == {"total": 1}

    def test_coordination_report_with_config(self, tmp_path):
        """Cover the success path where ConfigLoader returns agents with groups."""
        from unittest.mock import MagicMock
        _init_maop_db(tmp_path)
        proxy = _make_proxy(tmp_path)
        # Mock ConfigLoader to return agents with .group attribute
        with patch("maop.config.loader.ConfigLoader") as MockLoader:
            mock_cfg = MockLoader.return_value.load.return_value
            agent_a = MagicMock(group="team1")
            agent_b = MagicMock(group="team1")
            agent_c = MagicMock(group="team2")
            mock_cfg.agents = {"a": agent_a, "b": agent_b, "c": agent_c}
            result = asyncio.run(proxy.coordination_report())
        teams = {t["team"]: t for t in result["teams"]}
        assert "team1" in teams
        assert "team2" in teams
        assert teams["team1"]["count"] == 2

    def test_providers_report_success(self, tmp_path):
        _init_maop_db(tmp_path)
        proxy = _make_proxy(tmp_path)
        # agent_stats returns [] by default (no delegations) — covers success path
        result = asyncio.run(proxy.providers_report())
        assert result["total"] == 0
        assert result["available"] == 0


# ── agent_stats config fallback adding unseen agents (222-224) ──────


class TestAgentStatsConfigFallback:
    def test_config_adds_unseen_agents(self, tmp_path):
        from unittest.mock import MagicMock
        db_path = _init_maop_db(tmp_path)
        _insert_rows(db_path, "delegations", [
            {"timestamp": "2026-01-01T00:00:00+00:00", "agent": "claude",
             "task": "t", "routing_key": "", "exit_code": 0,
             "stdout": "", "stderr": "", "duration_ms": 10, "trace_id": ""},
        ])
        proxy = _make_proxy(tmp_path)
        with patch("maop.config.loader.ConfigLoader") as MockLoader:
            mock_cfg = MockLoader.return_value.load.return_value
            # 'codex' is in config but not in delegations
            mock_cfg.agents = {"claude": MagicMock(), "codex": MagicMock()}
            result = asyncio.run(proxy.agent_stats())
        agents = {r["agent"] for r in result}
        assert "claude" in agents
        assert "codex" in agents  # added by config fallback
        codex = next(r for r in result if r["agent"] == "codex")
        assert codex["total_delegations"] == 0
        assert codex["circuit_breaker"] == "closed"


# ── _window with non-dict records (414) ─────────────────────────────


class TestDelegationPeriodStatsNonDict:
    def test_non_dict_records_skipped(self, tmp_path):
        """Cover _window branch where rec is not a dict."""
        from datetime import datetime, timezone
        now = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
        log_dir = tmp_path / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / "delegations.json").write_text(
            json.dumps([
                "not a dict",
                42,
                {"timestamp": "2026-05-15T10:00:00+00:00", "exit_code": 0},
            ]),
            encoding="utf-8",
        )
        proxy = _make_proxy(tmp_path)
        result = asyncio.run(proxy.delegation_period_stats(now=now))
        assert result["total"] == 1


# ── _queue_stats_sync success path (578-582) ────────────────────────


class TestQueueStatsSyncSuccess:
    def test_with_queue_tables(self, tmp_path):
        """Cover the success path where queue_messages/dead_letters exist."""
        from maop.core.backends.db_utils import get_db_path
        db_path = get_db_path("queue")
        db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(str(db_path)) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS queue_messages "
                "(id INTEGER PRIMARY KEY, status TEXT)"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS queue_dead_letters "
                "(id INTEGER PRIMARY KEY)"
            )
            conn.execute("INSERT INTO queue_messages (status) VALUES ('pending')")
            conn.execute("INSERT INTO queue_messages (status) VALUES ('pending')")
            conn.execute("INSERT INTO queue_messages (status) VALUES ('processing')")
            conn.execute("INSERT INTO queue_dead_letters (id) VALUES (1)")
            conn.commit()
        proxy = _make_proxy(tmp_path)
        result = proxy._queue_stats_sync()
        assert result["pending"] == 2
        assert result["processing"] == 1
        assert result["dead_letters"] == 1


# ── graph_nodes/graph_edges with data (801-807, 811-817) ────────────


class TestGraphNodesEdges:
    def test_graph_nodes_with_data(self, tmp_path):
        """Cover graph_nodes success path with memory_entries data."""
        from maop.core.backends.data import MaopDatabase
        MaopDatabase(tmp_path / "data" / "maop.db").init()
        with sqlite3.connect(str(tmp_path / "data" / "maop.db")) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS memory_entries "
                "(id INTEGER PRIMARY KEY, agent TEXT, topic TEXT, content TEXT)"
            )
            conn.execute(
                "INSERT INTO memory_entries (agent, topic, content) "
                "VALUES ('claude', 't', 'c')"
            )
            conn.commit()
        proxy = _make_proxy(tmp_path)
        result = asyncio.run(proxy.graph_nodes())
        assert len(result) == 1
        assert result[0]["label"] == "claude"

    def test_graph_edges_with_data(self, tmp_path):
        """Cover graph_edges success path with memory_traces data."""
        from maop.core.backends.data import MaopDatabase
        MaopDatabase(tmp_path / "data" / "maop.db").init()
        with sqlite3.connect(str(tmp_path / "data" / "maop.db")) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS memory_traces "
                "(id INTEGER PRIMARY KEY, trace_id TEXT, agent TEXT)"
            )
            conn.execute(
                "INSERT INTO memory_traces (trace_id, agent) VALUES ('tr1', 'claude')"
            )
            conn.commit()
        proxy = _make_proxy(tmp_path)
        result = asyncio.run(proxy.graph_edges())
        assert len(result) == 1
        assert result[0]["source"] == "tr1"