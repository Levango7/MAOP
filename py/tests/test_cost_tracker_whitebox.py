"""White-box tests for CostTracker — token usage & budget alerting.

Covers record/summary/budget/persistence paths against the SQLite-backed
implementation in maop.core.cost_tracker. Each test relies on the
session-scoped MAOP_DATA_DIR isolation provided by conftest.py so no
real production DB is touched.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from maop.core.monitoring.cost_tracker import CostTracker


# ── Fixtures ─────────────────────────────────────────────────────


@pytest.fixture
def tracker(tmp_path: Path) -> CostTracker:
    """A fresh CostTracker rooted in the isolated tmp data dir."""
    return CostTracker(root_dir=tmp_path)


# ── 1. Single record ─────────────────────────────────────────────


def test_record_single_call_returns_correct_cost(tracker: CostTracker) -> None:
    """Recording one LLM call returns a CostEntry with auto-calculated cost."""
    entry = tracker.record(
        session_id="s1", agent="coder", model="gpt-4o-mini",
        prompt_tokens=1000, completion_tokens=500, latency_ms=120,
    )
    # gpt-4o-mini: 0.15/1M prompt, 0.60/1M completion
    expected = round((1000 / 1e6) * 0.15 + (500 / 1e6) * 0.60, 6)
    assert entry.cost_usd == pytest.approx(expected)
    assert entry.total_tokens == 1500
    assert entry.agent == "coder"
    assert entry.id.startswith("cost-")


# ── 2. Accumulation ──────────────────────────────────────────────


def test_summary_accumulates_multiple_calls(tracker: CostTracker) -> None:
    """summary() aggregates token totals and cost across multiple calls."""
    for _ in range(3):
        tracker.record(
            agent="coder", model="gpt-4o-mini",
            prompt_tokens=1000, completion_tokens=500,
        )
    s = tracker.summary()
    assert s.total_calls == 3
    assert s.total_prompt_tokens == 3000
    assert s.total_completion_tokens == 1500
    assert s.total_tokens == 4500
    assert s.total_cost_usd > 0.0


# ── 3. Group by agent ────────────────────────────────────────────


def test_summary_groups_by_agent(tracker: CostTracker) -> None:
    """by_agent breakdown keeps per-agent token/cost stats separate."""
    tracker.record(agent="coder", model="gpt-4o-mini", prompt_tokens=100, completion_tokens=0)
    tracker.record(agent="reviewer", model="gpt-4o-mini", prompt_tokens=200, completion_tokens=0)
    s = tracker.summary()
    assert set(s.by_agent) == {"coder", "reviewer"}
    assert s.by_agent["coder"]["tokens"] == 100
    assert s.by_agent["reviewer"]["tokens"] == 200


# ── 4. Group by model ────────────────────────────────────────────


def test_summary_groups_by_model(tracker: CostTracker) -> None:
    """by_model breakdown separates tokens/cost per model."""
    tracker.record(agent="a", model="gpt-4o-mini", prompt_tokens=100, completion_tokens=0)
    tracker.record(agent="a", model="deepseek-chat", prompt_tokens=300, completion_tokens=0)
    s = tracker.summary()
    assert set(s.by_model) == {"gpt-4o-mini", "deepseek-chat"}
    assert s.by_model["gpt-4o-mini"]["calls"] == 1
    assert s.by_model["deepseek-chat"]["tokens"] == 300


# ── 5. Budget threshold alert ────────────────────────────────────


def test_budget_status_flags_over_budget(tmp_path: Path) -> None:
    """daily_over_budget flips True once spend exceeds daily_limit_usd."""
    t = CostTracker(root_dir=tmp_path, daily_limit_usd=0.001)
    # gpt-4o: 2.50/1M prompt → 10000 prompt tokens = $0.025 > $0.001
    t.record(agent="a", model="gpt-4o", prompt_tokens=10000, completion_tokens=0)
    status = t.budget_status()
    assert status.daily_over_budget is True
    assert status.daily_spent_usd > status.daily_limit_usd


# ── 6. Reset to zero ─────────────────────────────────────────────


def test_reset_by_recreating_tracker_clears_data(tmp_path: Path) -> None:
    """CostTracker has no reset(); rebuilding against an empty DB yields zero."""
    t = CostTracker(root_dir=tmp_path)
    t.record(agent="a", model="gpt-4o-mini", prompt_tokens=500, completion_tokens=0)
    assert t.summary().total_calls == 1
    # Wipe the underlying SQLite file then rebuild → fresh state.

    t2 = CostTracker(root_dir=tmp_path)
    db_file2 = t2._db_path
    # Same path both times (unified DB); delete & reinit to simulate reset.
    db_file2.unlink(missing_ok=True)
    t3 = CostTracker(root_dir=tmp_path)
    assert t3.summary().total_calls == 0
    assert t3.summary().total_cost_usd == 0.0


# ── 7. Persistence ───────────────────────────────────────────────


def test_persistence_reload_preserves_entries(tracker: CostTracker) -> None:
    """A new CostTracker instance over the same DB sees prior entries."""
    tracker.record(
        session_id="s1", agent="coder", model="gpt-4o-mini",
        prompt_tokens=800, completion_tokens=200, latency_ms=42,
        metadata={"task": "codegen"},
    )
    db_path = tracker._db_path
    reloaded = CostTracker(root_dir=db_path.parent.parent)  # same data dir
    entries = reloaded.get_entries(agent="coder")
    assert len(entries) == 1
    e = entries[0]
    assert e.prompt_tokens == 800
    assert e.completion_tokens == 200
    assert e.metadata == {"task": "codegen"}
    # Round-trip via raw SQLite to prove the row is durable on disk.
    with sqlite3.connect(str(db_path)) as conn:
        row = conn.execute("SELECT metadata FROM cost_entries LIMIT 1").fetchone()
    assert json.loads(row[0]) == {"task": "codegen"}