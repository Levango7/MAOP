"""F-8-1 nail: the dashboard startup path must create the reliability-owned
tables it queries directly.

On a fresh SQLite DB, _ensure_db_schema only ran MaopDatabase's DDL, so
circuit_breaker_state / queue_messages / queue_dead_letters /
queue_idempotent were missing. agent_stats()/live() query them directly and
_query_maop_sync swallowed the sqlite error as [], making every breaker
column report "closed" (config fallback), open_circuit_breakers stay [] and
queue_depth stay 0.
"""
from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

from maop.core.backends.db_utils import get_db_path


def _write_agent_config(tmp_path) -> None:
    """One agent in the project config so agent_stats lists it.

    CircuitBreaker seeds breaker rows for DEFAULT_AGENTS (repo-bundled
    fallback), so the name must exist there too — "claude" does.
    """
    cfg_dir = Path(str(tmp_path)) / "config"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "agents.yaml").write_text("agents:\n  claude: {}\n", encoding="utf-8")


def _tables(db_path) -> set[str]:
    con = sqlite3.connect(str(db_path))
    try:
        return {
            r[0]
            for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    finally:
        con.close()


def _seed_open_breaker(agent: str) -> None:
    con = sqlite3.connect(str(get_db_path()))
    try:
        cur = con.execute(
            "UPDATE circuit_breaker_state SET state = 'open', failures = 7 "
            "WHERE agent = ?",
            (agent,),
        )
        assert cur.rowcount == 1, f"breaker row for {agent!r} not seeded"
        con.commit()
    finally:
        con.close()


def _enqueue_probe() -> None:
    con = sqlite3.connect(str(get_db_path("queue")))
    try:
        con.execute(
            "INSERT INTO queue_messages (id, topic, payload, status, "
            "enqueued_at, visible_at) "
            "VALUES ('f81-probe', 'f81-topic', '{}', 'pending', 0.0, 0.0)",
        )
        con.commit()
    finally:
        con.close()


class TestReliabilitySchemaAtStartup:
    def test_four_tables_created_on_fresh_db(self, tmp_path):
        from maop.dashboard.data_proxy import DataProxy

        DataProxy(root_dir=str(tmp_path))
        assert "circuit_breaker_state" in _tables(get_db_path())
        for name in ("queue_messages", "queue_dead_letters", "queue_idempotent"):
            assert name in _tables(get_db_path("queue"))

    def test_agent_stats_reflects_breaker_table(self, tmp_path):
        from maop.dashboard.data_proxy import DataProxy

        _write_agent_config(tmp_path)
        proxy = DataProxy(root_dir=str(tmp_path))
        agent = asyncio.run(proxy.agent_stats())[0]["agent"]
        before = next(r for r in asyncio.run(proxy.agent_stats()) if r["agent"] == agent)
        assert before["circuit_breaker"] == "closed"
        _seed_open_breaker(agent)
        stats = asyncio.run(proxy.agent_stats())
        row = next(r for r in stats if r["agent"] == agent)
        assert row["circuit_breaker"] == "open"

    def test_live_reports_open_breakers_and_queue_depth(self, tmp_path):
        from maop.dashboard.data_proxy import DataProxy

        _write_agent_config(tmp_path)
        proxy = DataProxy(root_dir=str(tmp_path))
        agent = asyncio.run(proxy.agent_stats())[0]["agent"]
        _seed_open_breaker(agent)
        _enqueue_probe()
        live = asyncio.run(proxy.live())
        assert live["open_circuit_breakers"][0]["agent"] == agent
        assert live["queue_depth"] >= 1
