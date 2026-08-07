"""White-box tests for ErrorLedger — structured error tracking.

Covers record/find/hotspot/prune/persistence against the SQLite-backed
implementation. ErrorLedger has no agent field or prune() method, so
agent filtering and pruning are exercised via the private _db_connect
helper (white-box) — this is intentional for a white-box suite.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

import pytest

from maop.core.reliability.error_ledger import ErrorLedger


@pytest.fixture
def ledger(tmp_path: Path) -> ErrorLedger:
    """A fresh ErrorLedger rooted in the isolated tmp data dir."""
    return ErrorLedger(root_dir=tmp_path)


# ── 1. Record & query ────────────────────────────────────────────


def test_record_returns_queryable_id(ledger: ErrorLedger) -> None:
    """record() persists an error findable via find_by_pattern."""
    eid = ledger.record(
        error_type="tool_error", context="git push",
        trigger={"tool": "git"}, output="fatal: not a repo",
        pattern="git_repo_missing",
    )
    assert isinstance(eid, str) and eid
    hits = ledger.find_by_pattern("git_repo_missing")
    assert len(hits) == 1
    assert hits[0].error_type == "tool_error"
    assert hits[0].trigger == {"tool": "git"}


# ── 2. Filter by agent (via trigger) ─────────────────────────────


def test_filter_by_agent_via_trigger(ledger: ErrorLedger) -> None:
    """Agent info lives in trigger; white-box SQL filters by it."""
    ledger.record(error_type="x", trigger={"agent": "coder"}, pattern="p1")
    ledger.record(error_type="x", trigger={"agent": "reviewer"}, pattern="p2")
    with ledger._db_connect() as conn:
        rows = conn.execute(
            "SELECT pattern FROM error_ledger WHERE json_extract(trigger,'$.agent')=?",
            ("coder",),
        ).fetchall()
    assert [r[0] for r in rows] == ["p1"]


# ── 3. Filter by error_type ──────────────────────────────────────


def test_filter_by_error_type(ledger: ErrorLedger) -> None:
    """White-box SQL filter on error_type returns only matching rows."""
    ledger.record(error_type="tool_error", pattern="pa")
    ledger.record(error_type="llm_error", pattern="pb")
    with ledger._db_connect() as conn:
        rows = conn.execute(
            "SELECT pattern FROM error_ledger WHERE error_type=?",
            ("llm_error",),
        ).fetchall()
    assert [r[0] for r in rows] == ["pb"]


# ── 4. Pattern aggregation ───────────────────────────────────────


def test_hotspots_aggregate_recurrence(ledger: ErrorLedger) -> None:
    """get_hotspots sums recurrence per pattern, sorted by count desc."""
    for _ in range(3):
        ledger.record(error_type="t", pattern="hot")
    ledger.record(error_type="t", pattern="cold")
    hotspots = ledger.get_hotspots()
    assert hotspots[0].pattern == "hot"
    assert hotspots[0].count == 3
    assert hotspots[1].pattern == "cold"
    assert hotspots[1].count == 1


# ── 5. Prune old errors ──────────────────────────────────────────


def test_prune_old_errors_via_sql(ledger: ErrorLedger) -> None:
    """ErrorLedger has no prune(); white-box DELETE removes rows older than cutoff."""
    ledger.record(error_type="t", pattern="recent")
    # Insert a back-dated row directly.
    with ledger._db_connect() as conn:
        conn.execute(
            """INSERT INTO error_ledger
               (id, error_type, pattern, recurrence, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            ("old1", "t", "old", 1, time.time() - 86400 * 30),
        )
    cutoff = time.time() - 86400 * 7
    with ledger._db_connect() as conn:
        cur = conn.execute("DELETE FROM error_ledger WHERE created_at < ?", (cutoff,))
        removed = cur.rowcount
    assert removed == 1
    assert {h.pattern for h in ledger.get_hotspots()} == {"recent"}


# ── 6. Empty query ───────────────────────────────────────────────


def test_find_by_pattern_empty(ledger: ErrorLedger) -> None:
    """find_by_pattern on a ledger with no matches returns an empty list."""
    assert ledger.find_by_pattern("nonexistent") == []
    assert ledger.get_hotspots() == []


# ── 7. Persistence ───────────────────────────────────────────────


def test_persistence_reload_preserves_errors(ledger: ErrorLedger) -> None:
    """A new ErrorLedger over the same DB sees previously recorded errors."""
    ledger.record(
        error_type="tool_error", context="ctx",
        trigger={"k": "v"}, output="out", pattern="persist_p",
    )
    db_path = ledger._db_path
    reloaded = ErrorLedger(root_dir=db_path.parent.parent)
    hits = reloaded.find_by_pattern("persist_p")
    assert len(hits) == 1
    assert hits[0].context == "ctx"
    assert hits[0].trigger == {"k": "v"}
    # Prove durability on disk via raw SQLite.
    with sqlite3.connect(str(db_path)) as conn:
        row = conn.execute("SELECT trigger FROM error_ledger LIMIT 1").fetchone()
    assert json.loads(row[0]) == {"k": "v"}