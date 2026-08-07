"""White-box tests for RoutingDecisionStore — scheduling audit trail.

Covers record/query/ordering/recent-N/persistence against the SQLite
store. The store exposes query_by_trace and query_recent but no
agent/time-range filter, so those are implemented via query_recent
followed by client-side filtering — a legitimate white-box approach.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

import pytest

from maop.core.routing.routing_decision import RoutingDecisionRecord, RoutingDecisionStore


def _make_decision(
    *,
    trace_id: str = "t1",
    stage: str = "route_scorer",
    agent: str = "coder",
    timestamp: float | None = None,
    explanation: str = "picked coder",
) -> RoutingDecisionRecord:
    """Build a minimal RoutingDecisionRecord for tests."""
    return RoutingDecisionRecord(
        trace_id=trace_id,
        span_id="",
        parent_span_id=None,
        timestamp=timestamp if timestamp is not None else time.time(),
        stage=stage,
        input_summary={"routing_key": "codegen"},
        output_summary={"selected_agent": agent, "score": 0.85},
        explanation=explanation,
        duration_ms=1.2,
        attributes={"decision_mode": "weighted_sum"},
    )


@pytest.fixture
def store(tmp_path: Path) -> RoutingDecisionStore:
    """A fresh RoutingDecisionStore backed by an isolated temp DB file."""
    return RoutingDecisionStore(db_path=tmp_path / "rd.db")


# ── 1. Record & field integrity ─────────────────────────────────


def test_record_preserves_all_fields(store: RoutingDecisionStore) -> None:
    """record() persists every field; query_by_trace returns it intact."""
    d = _make_decision(explanation="full field check")
    rid = store.record(d)
    assert rid > 0
    chain = store.query_by_trace(d.trace_id)
    assert len(chain) == 1
    got = chain[0]
    assert got.trace_id == d.trace_id
    assert got.stage == "route_scorer"
    assert got.output_summary == {"selected_agent": "coder", "score": 0.85}
    assert got.input_summary == {"routing_key": "codegen"}
    assert got.explanation == "full field check"
    assert got.duration_ms == 1.2
    assert got.attributes == {"decision_mode": "weighted_sum"}


# ── 2. Filter by agent (client-side) ────────────────────────────


def test_filter_by_agent(store: RoutingDecisionStore) -> None:
    """Decisions are filtered by selected_agent in output_summary."""
    store.record(_make_decision(agent="coder", trace_id="t1"))
    store.record(_make_decision(agent="reviewer", trace_id="t2"))
    coder = [
        d for d in store.query_recent(limit=100)
        if d.output_summary.get("selected_agent") == "coder"
    ]
    assert len(coder) == 1
    assert coder[0].trace_id == "t1"


# ── 3. Filter by time range (client-side) ───────────────────────


def test_filter_by_time_range(store: RoutingDecisionStore) -> None:
    """Decisions within a [start, end] window are selected client-side."""
    t0 = time.time()
    store.record(_make_decision(trace_id="old", timestamp=t0 - 100))
    store.record(_make_decision(trace_id="mid", timestamp=t0 - 10))
    store.record(_make_decision(trace_id="new", timestamp=t0))
    window = [d for d in store.query_recent(limit=100) if t0 - 50 <= d.timestamp <= t0 + 1]
    assert {d.trace_id for d in window} == {"mid", "new"}


# ── 4. Recent N ─────────────────────────────────────────────────


def test_query_recent_limit(store: RoutingDecisionStore) -> None:
    """query_recent(limit=N) returns at most N newest decisions."""
    base = time.time()
    for i in range(5):
        store.record(_make_decision(trace_id=f"t{i}", timestamp=base + i))
    recent = store.query_recent(limit=3)
    assert len(recent) == 3
    # newest-first ordering → t4, t3, t2
    assert [d.trace_id for d in recent] == ["t4", "t3", "t2"]


# ── 5. Ordering ─────────────────────────────────────────────────


def test_query_recent_orders_newest_first(store: RoutingDecisionStore) -> None:
    """query_recent returns decisions ordered by timestamp DESC."""
    base = time.time()
    for i in range(3):
        store.record(_make_decision(trace_id=f"t{i}", timestamp=base + i * 10))
    recent = store.query_recent(limit=10)
    timestamps = [d.timestamp for d in recent]
    assert timestamps == sorted(timestamps, reverse=True)


# ── 6. Empty query ──────────────────────────────────────────────


def test_empty_queries(store: RoutingDecisionStore) -> None:
    """Empty store and empty trace_id both yield empty results."""
    assert store.query_by_trace("") == []
    assert store.query_recent(limit=10) == []
    assert store.count() == 0


# ── 7. Persistence ──────────────────────────────────────────────


def test_persistence_reload_preserves_decisions(store: RoutingDecisionStore) -> None:
    """A new RoutingDecisionStore over the same DB file sees prior decisions."""
    store.record(_make_decision(trace_id="persist_t", explanation="keep me"))
    db_path = store._db_path
    reloaded = RoutingDecisionStore(db_path=db_path)
    chain = reloaded.query_by_trace("persist_t")
    assert len(chain) == 1
    assert chain[0].explanation == "keep me"
    # Prove durability on disk via raw SQLite.
    with sqlite3.connect(str(db_path)) as conn:
        row = conn.execute("SELECT output_summary FROM routing_decisions LIMIT 1").fetchone()
    assert json.loads(row[0])["selected_agent"] == "coder"