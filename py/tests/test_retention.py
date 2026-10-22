"""Tests for maop.core.retention — the central append-only table ceiling."""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from maop.core import retention
from maop.core.retention import (
    EPOCH_S,
    ISO,
    POLICIES,
    RetentionPolicy,
    prune_all,
    prune_policy,
)

MAOP = Path(retention.__file__).parent.parent

NOW = datetime(2026, 10, 22, 12, 0, 0, tzinfo=timezone.utc)
NOW_EPOCH = NOW.timestamp()


def iso(days_ago: float) -> str:
    return (NOW - timedelta(days=days_ago)).isoformat()


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    return tmp_path / "retention.db"


def make(db: Path, ddl: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db)
    conn.execute(ddl)
    conn.commit()
    return conn


def rows(db: Path, table: str) -> list[tuple]:
    with sqlite3.connect(db) as conn:
        return conn.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()


def policy(table: str = "demo_log", col: str = "created_at", kind: str = ISO,
           days: int = 30, max_rows: int | None = None) -> RetentionPolicy:
    return RetentionPolicy(table, col, kind, "unified", days, max_rows)


class TestPolicies:
    def test_covers_the_tables_that_had_no_delete_path(self) -> None:
        """The registry is the point — a new unbounded table must be added here."""
        assert {p.table for p in POLICIES} == {
            "error_log", "hook_logs", "usage_calls", "cost_entries", "breaker_events",
            "change_log", "config_snapshots", "health_log", "routing_audit_logs",
            "memory_traces", "consolidation_log", "subagent_transcripts",
            "protocol_messages", "agent_performance",
        }

    def test_every_policy_table_is_created_and_written_in_the_tree(self) -> None:
        """Drift guard: a policy naming a table nobody creates would prune nothing
        forever and still report success."""
        sources = {f: f.read_text(encoding="utf-8", errors="replace")
                   for f in MAOP.rglob("*.py") if "__pycache__" not in f.parts}
        for p in POLICIES:
            created = [txt for txt in sources.values()
                       if re.search(rf"CREATE TABLE (?:IF NOT EXISTS )?[`\"']?{p.table}\b",
                                    txt, re.IGNORECASE)]
            assert created, f"{p.table}: no CREATE TABLE anywhere"
            with_column = [txt for txt in created
                           if re.search(rf"[`\"']?{p.time_col}[`\"']?\s+\w", txt)]
            assert with_column, f"{p.table}: time column {p.time_col} not in any DDL"
            written = any(re.search(rf"INSERT (?:OR \w+ )?INTO [`\"']?{p.table}\b", txt, re.IGNORECASE)
                          for txt in sources.values())
            assert written, f"{p.table}: nothing inserts into it — policy is dead weight"

    def test_identifiers_validate(self) -> None:
        from maop.core.backends.db_utils import validate_identifier

        for p in POLICIES:
            assert validate_identifier(p.table) == p.table
            assert validate_identifier(p.time_col) == p.time_col

    def test_windows_are_generous_enough_to_keep_a_story(self) -> None:
        """Cost/usage feed billing; snapshots feed rollback. Both must not age out
        inside a month."""
        by_table = {p.table: p for p in POLICIES}
        assert by_table["cost_entries"].days >= 90
        assert by_table["usage_calls"].days >= 90
        assert by_table["config_snapshots"].days == 0
        assert by_table["config_snapshots"].max_rows is not None

    def test_bad_policy_is_rejected_at_import(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(retention, "POLICIES", (
            RetentionPolicy("t", "c", "bogus_kind", "unified", 1),))
        with pytest.raises(ValueError, match="unknown time kind"):
            retention._validate_policies()

    def test_unknown_db_key_is_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(retention, "POLICIES", (
            RetentionPolicy("t", "c", ISO, "no_such_db", 1),))
        with pytest.raises(ValueError, match="unknown retention db key"):
            retention._validate_policies()

    def test_cap_only_policy_needs_a_cap(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(retention, "POLICIES", (
            RetentionPolicy("t", "c", ISO, "unified", 0, None),))
        with pytest.raises(ValueError, match="cap-only policy needs max_rows"):
            retention._validate_policies()


class TestIsoPruning:
    def test_drops_expired_keeps_recent(self, db: Path) -> None:
        conn = make(db, "CREATE TABLE demo_log (id INTEGER PRIMARY KEY, created_at TEXT NOT NULL)")
        for ts in (iso(400), iso(45), iso(10), iso(1)):
            conn.execute("INSERT INTO demo_log (created_at) VALUES (?)", (ts,))
        conn.commit()
        conn.close()

        assert prune_policy(policy(), db_path=db, now=NOW_EPOCH) == 2
        left = [r[1] for r in rows(db, "demo_log")]
        assert left == [iso(10), iso(1)]

    def test_empty_timestamp_is_not_pruned_as_infinitely_old(self, db: Path) -> None:
        conn = make(db, "CREATE TABLE demo_log (id INTEGER PRIMARY KEY, created_at TEXT NOT NULL)")
        conn.execute("INSERT INTO demo_log (created_at) VALUES ('')")
        conn.execute("INSERT INTO demo_log (created_at) VALUES (?)", (iso(400),))
        conn.commit()
        conn.close()

        assert prune_policy(policy(), db_path=db, now=NOW_EPOCH) == 1
        assert [r[1] for r in rows(db, "demo_log")] == [""]


class TestEpochPruning:
    def test_drops_expired_keeps_recent(self, db: Path) -> None:
        conn = make(db, "CREATE TABLE demo_log (id INTEGER PRIMARY KEY, created_at REAL NOT NULL)")
        for ts in (NOW_EPOCH - 400 * 86400, NOW_EPOCH - 86400, NOW_EPOCH):
            conn.execute("INSERT INTO demo_log (created_at) VALUES (?)", (ts,))
        conn.commit()
        conn.close()

        assert prune_policy(policy(kind=EPOCH_S), db_path=db, now=NOW_EPOCH) == 1
        assert len(rows(db, "demo_log")) == 2

    def test_kind_mismatch_cannot_wipe_the_table(self, db: Path) -> None:
        """SQLite says every number is less than every string.

        An ISO policy pointed at an epoch column must delete *nothing* — the
        typeof() guard is what turns a policy typo into a no-op instead of a
        data-loss incident.
        """
        conn = make(db, "CREATE TABLE demo_log (id INTEGER PRIMARY KEY, created_at REAL NOT NULL)")
        for ts in (NOW_EPOCH - 900 * 86400, NOW_EPOCH - 86400):
            conn.execute("INSERT INTO demo_log (created_at) VALUES (?)", (ts,))
        conn.commit()
        conn.close()

        assert prune_policy(policy(kind=ISO), db_path=db, now=NOW_EPOCH) == 0
        assert len(rows(db, "demo_log")) == 2

    def test_null_timestamp_is_skipped(self, db: Path) -> None:
        conn = make(db, "CREATE TABLE demo_log (id INTEGER PRIMARY KEY, created_at TEXT)")
        conn.execute("INSERT INTO demo_log (created_at) VALUES (NULL)")
        conn.execute("INSERT INTO demo_log (created_at) VALUES (?)", (iso(400),))
        conn.commit()
        conn.close()

        assert prune_policy(policy(), db_path=db, now=NOW_EPOCH) == 1
        assert len(rows(db, "demo_log")) == 1


class TestRowCap:
    def test_cap_only_policy_never_ages_by_clock(self, db: Path) -> None:
        conn = make(db, "CREATE TABLE demo_log (id INTEGER PRIMARY KEY, created_at TEXT NOT NULL)")
        for ts in (iso(4000), iso(3000), iso(1)):
            conn.execute("INSERT INTO demo_log (created_at) VALUES (?)", (ts,))
        conn.commit()
        conn.close()

        deleted = prune_policy(policy(days=0, max_rows=2), db_path=db, now=NOW_EPOCH)
        assert deleted == 1
        assert [r[1] for r in rows(db, "demo_log")] == [iso(3000), iso(1)]

    def test_cap_applies_after_expiry(self, db: Path) -> None:
        conn = make(db, "CREATE TABLE demo_log (id INTEGER PRIMARY KEY, created_at TEXT NOT NULL)")
        for ts in (iso(400), iso(2), iso(1), iso(0.5)):
            conn.execute("INSERT INTO demo_log (created_at) VALUES (?)", (ts,))
        conn.commit()
        conn.close()

        deleted = prune_policy(policy(days=30, max_rows=2), db_path=db, now=NOW_EPOCH)
        assert deleted == 2  # 1 expired + 1 surplus
        assert [r[1] for r in rows(db, "demo_log")] == [iso(1), iso(0.5)]

    def test_cap_is_noop_when_under_limit(self, db: Path) -> None:
        conn = make(db, "CREATE TABLE demo_log (id INTEGER PRIMARY KEY, created_at TEXT NOT NULL)")
        conn.execute("INSERT INTO demo_log (created_at) VALUES (?)", (iso(1),))
        conn.commit()
        conn.close()
        assert prune_policy(policy(max_rows=100), db_path=db, now=NOW_EPOCH) == 0


class TestFailureModes:
    def test_missing_table_is_skipped(self, db: Path) -> None:
        make(db, "CREATE TABLE other (id INTEGER PRIMARY KEY)").close()
        assert prune_policy(policy(table="demo_log"), db_path=db, now=NOW_EPOCH) == 0

    def test_missing_file_is_skipped_not_fatal(self, tmp_path: Path) -> None:
        assert prune_policy(policy(), db_path=tmp_path / "nope" / "x.db",
                            now=NOW_EPOCH) == 0

    def test_prune_all_returns_every_policy_and_never_raises(self, tmp_path: Path) -> None:
        fake = tmp_path / "all.db"
        make(fake, "CREATE TABLE error_log (id INTEGER PRIMARY KEY, timestamp TEXT NOT NULL)").close()
        paths = {p.db: fake for p in POLICIES}
        out = prune_all(db_paths=paths, now=NOW_EPOCH)
        assert set(out) == {p.table for p in POLICIES}
        assert out["error_log"] == 0

    def test_prune_all_rejects_duplicate_table(self, monkeypatch: pytest.MonkeyPatch) -> None:
        dupe = RetentionPolicy("dup", "c", ISO, "unified", 1)
        monkeypatch.setattr(retention, "POLICIES", (dupe, dupe))
        with pytest.raises(ValueError, match="duplicate retention policy"):
            prune_all()

    def test_summarise_reports_only_touched_tables(self) -> None:
        summary = retention.summarize({"error_log": 5, "hook_logs": 0})
        assert summary["retention_tables_checked"] == 2
        assert summary["retention_rows_pruned"] == 5
        assert summary["retention_by_table"] == {"error_log": 5}


class TestInjectionGuard:
    def test_table_name_cannot_carry_sql(self, db: Path) -> None:
        """A policy is a constant, but the guard is what keeps it that way."""
        evil = RetentionPolicy("x; DROP TABLE demo_log", "created_at", ISO, "unified", 1)
        with pytest.raises(ValueError, match="Invalid SQL"):
            retention.validate_identifier(evil.table, "retention table")

    def test_cutoff_uses_binding_not_interpolation(self, db: Path) -> None:
        conn = make(db, "CREATE TABLE demo_log (id INTEGER PRIMARY KEY, created_at TEXT NOT NULL)")
        conn.execute("INSERT INTO demo_log (created_at) VALUES (?)", ("' OR 1=1 --",))
        conn.commit()
        conn.close()
        prune_policy(policy(), db_path=db, now=NOW_EPOCH)
        # The literal string is not a valid timestamp ⇒ it sorts below the cutoff
        # and is treated as expired; the point is nothing else was deleted or
        # executed, and the row count drops by exactly one.
        assert rows(db, "demo_log") == []
