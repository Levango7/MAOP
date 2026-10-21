"""Tests for the append-only run_events ledger (M1 run review)."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from maop.core import run_events as ev


@pytest.fixture
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "runs.db"
    monkeypatch.setattr(ev, "get_db_path", lambda *a, **k: path)
    return path


def _backdate(db: Path, *, trace_id: str, stamp: str) -> None:
    """Age an event. ``record_run_event`` always stamps "now", so window and
    retention behaviour can only be pinned by rewriting the stored timestamp."""
    with sqlite3.connect(str(db)) as conn:
        conn.execute(
            "UPDATE run_events SET timestamp = ? WHERE trace_id = ?",
            (stamp, trace_id),
        )
        conn.commit()


def test_record_and_fetch_preserves_order(db: Path) -> None:
    assert ev.record_run_event("t1", "run.started", payload={"task": "重构 auth"})
    assert ev.record_run_event("t1", "run.step", agent="claude", step=1,
                               status="ok", duration_ms=120, payload={"stdout": "hi"})
    assert ev.record_run_event("t1", "run.finished", agent="claude",
                               duration_ms=900, payload={"success": True})

    events = ev.fetch_run_events("t1")
    assert [e["kind"] for e in events] == ["run.started", "run.step", "run.finished"]
    assert [e["seq"] for e in events] == [1, 2, 3]
    assert events[1]["agent"] == "claude"
    assert events[1]["payload"]["stdout"] == "hi"
    assert events[2]["duration_ms"] == 900


def test_long_payload_strings_are_clipped(db: Path) -> None:
    ev.record_run_event("t2", "run.step", payload={"stdout": "x" * (ev.MAX_TEXT * 3)})
    stored = ev.fetch_run_events("t2")[0]["payload"]["stdout"]
    assert len(stored) < ev.MAX_TEXT + 60
    assert "截断" in stored


def test_missing_trace_id_is_refused(db: Path) -> None:
    assert ev.record_run_event("", "run.started") is False
    assert ev.fetch_run_events("nothing-stored") == []


def test_storage_failure_does_not_raise(db: Path, monkeypatch: pytest.MonkeyPatch,
                                        capsys: pytest.CaptureFixture[str]) -> None:
    def _boom(*args: Any, **kwargs: Any):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(ev, "sqlite_connect", _boom)
    assert ev.record_run_event("t3", "run.started") is False
    assert ev.fetch_run_events("t3") == []
    assert ev.fetch_recent_runs() == []


def test_fetch_recent_runs_summarises_newest_first(db: Path) -> None:
    ev.record_run_event("old", "run.started", payload={"task": "旧任务"})
    ev.record_run_event("old", "run.finished", agent="codex", duration_ms=10,
                        payload={"success": False})
    ev.record_run_event("new", "run.started", payload={"task": "新任务"})

    runs = ev.fetch_recent_runs(limit=5)
    assert [r["trace_id"] for r in runs] == ["new", "old"]
    assert runs[0]["status"] == "incomplete"
    assert runs[0]["task"] == "新任务"
    assert runs[1]["status"] == "finished"
    assert runs[1]["agent"] == "codex"
    assert runs[1]["success"] is False
    assert runs[1]["duration_ms"] == 10


@pytest.mark.asyncio
async def test_loop_run_writes_timeline(tmp_path: Path) -> None:
    """A real ``MaopLoop.run`` must leave a queryable per-run timeline.

    The agent config is empty on purpose: execution fails fast, but the
    ledger still has to record what was attempted — that is exactly what
    "can I review this run later" depends on. ``MAOP_DATA_DIR`` is set by
    the session conftest, so the rows land in this test's own DB.
    """
    from maop.maop_loop import LoopConfig, MaopLoop

    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "agents.yaml").write_text("agents: {}\n", encoding="utf-8")
    (tmp_path / "config" / "models.yaml").write_text("models: {}\n", encoding="utf-8")
    (tmp_path / "data").mkdir()

    lc = LoopConfig(
        enable_semantic_analyze=False, enable_parallel=False,
        enable_load_balancer=False, enable_result_cache=False,
        enable_metrics=False, enable_timeseries=False, enable_evolve=False,
        enable_dream=False, enable_cache_guard=False,
        iterative_max_attempts=1, iterative_backoff_ms=0, retry_backoff_ms=0,
        default_timeout_s=5,
    )
    result = await MaopLoop(root_dir=str(tmp_path), loop_config=lc).run(
        task="写一句中文问候", workdir=str(tmp_path)
    )

    events = ev.fetch_run_events(result.trace_id)
    kinds = [e["kind"] for e in events]
    assert kinds[0] == "run.started"
    assert kinds[-1] == "run.finished"
    assert "run.plan" in kinds
    assert "run.step" in kinds, f"execution attempt missing: {kinds}"
    assert [e["seq"] for e in events] == list(range(1, len(events) + 1))

    started = events[0]["payload"]
    assert started["task"] == "写一句中文问候"
    finished = events[-1]["payload"]
    assert finished["success"] == result.success
    assert finished["task"] == "写一句中文问候"

    runs = ev.fetch_recent_runs(limit=3)
    assert runs[0]["trace_id"] == result.trace_id
    assert runs[0]["task"] == "写一句中文问候"


# ── rollups: the bridge reads statistics from this table ────────────


def test_agent_rollup_counts_steps_only(db: Path) -> None:
    ev.record_run_event("r1", "run.started", agent="claude")
    ev.record_run_event("r1", "run.step", agent="claude", status="ok", duration_ms=100)
    ev.record_run_event("r1", "run.step", agent="claude", status="failed", duration_ms=200)
    ev.record_run_event("r2", "run.step", agent="codex", status="ok", duration_ms=50)
    ev.record_run_event("r2", "run.verify", agent="codex", status="passed")

    rollup = {row["agent"]: row for row in ev.agent_rollup()}
    assert set(rollup) == {"claude", "codex"}
    assert (rollup["claude"]["total"], rollup["claude"]["successes"]) == (2, 1)
    assert rollup["claude"]["success_rate"] == 0.5
    assert rollup["claude"]["avg_duration_ms"] == 150
    assert (rollup["codex"]["total"], rollup["codex"]["successes"]) == (1, 1)


def test_agent_rollup_ignores_events_without_agent(db: Path) -> None:
    # An unattributed attempt must not invent a nameless bucket.
    ev.record_run_event("r3", "run.step", agent="", status="ok")
    assert ev.agent_rollup() == []
    assert ev.recent_steps() == []


def test_agent_rollup_window_is_not_vacuous(db: Path) -> None:
    from datetime import datetime, timezone

    ev.record_run_event("r4", "run.step", agent="claude", status="ok")
    _backdate(db, trace_id="r4", stamp="2000-01-01T00:00:00+00:00")
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    assert ev.agent_rollup(now) == []
    assert [r["agent"] for r in ev.agent_rollup("1999-01-01T00:00:00+00:00")] == ["claude"]
    assert [r["agent"] for r in ev.agent_rollup()] == ["claude"]


def test_recent_steps_flattens_payload_for_the_live_panel(db: Path) -> None:
    ev.record_run_event(
        "r5", "run.step", agent="claude", status="failed", duration_ms=77,
        payload={"task": "跑测试", "exit_code": 2},
    )
    rows = ev.recent_steps()
    assert len(rows) == 1
    assert rows[0]["agent"] == "claude"
    assert rows[0]["task"] == "跑测试"
    assert rows[0]["exit_code"] == 2
    assert rows[0]["duration_ms"] == 77
    assert rows[0]["status"] == "failed"
    assert rows[0]["trace_id"] == "r5"


def test_recent_steps_newest_first_and_limited(db: Path) -> None:
    for index in range(3):
        ev.record_run_event(f"t{index}", "run.step", agent="claude", status="ok",
                            payload={"task": f"任务{index}", "exit_code": 0})
    assert [r["task"] for r in ev.recent_steps(limit=2)] == ["任务2", "任务1"]


def test_failed_steps_keeps_error_text_and_drops_successes(db: Path) -> None:
    ev.record_run_event("r6", "run.step", agent="claude", status="ok",
                        payload={"task": "成的", "exit_code": 0})
    ev.record_run_event("r6", "run.step", agent="claude", status="failed",
                        payload={"task": "崩的", "exit_code": 3, "error": "适配器超时"})
    rows = ev.failed_steps()
    assert [r["task"] for r in rows] == ["崩的"]
    assert rows[0]["error"] == "适配器超时"
    assert rows[0]["exit_code"] == 3
    assert rows[0]["status"] == "failed"


def test_hourly_rollup_buckets_steps_and_ignores_other_kinds(db: Path) -> None:
    ev.record_run_event("h1", "run.step", agent="claude", status="ok", duration_ms=100)
    ev.record_run_event("h2", "run.step", agent="claude", status="failed", duration_ms=300)
    ev.record_run_event("h3", "run.verify", agent="claude", status="passed")
    _backdate(db, trace_id="h2", stamp="2026-10-19T05:07:11+00:00")

    buckets = ev.hourly_rollup("2026-01-01T00:00:00+00:00")
    by_hour = {b["hour"]: b for b in buckets}
    assert by_hour["2026-10-19T05:00:00"] == {
        "hour": "2026-10-19T05:00:00", "total": 1, "successes": 0, "avg_duration_ms": 300,
    }
    assert sum(b["total"] for b in buckets) == 2
    assert sum(b["successes"] for b in buckets) == 1


def test_hourly_rollup_window_excludes_older_hours(db: Path) -> None:
    ev.record_run_event("h4", "run.step", agent="claude", status="ok")
    _backdate(db, trace_id="h4", stamp="2020-05-05T05:05:05+00:00")
    assert ev.hourly_rollup("2021-01-01T00:00:00+00:00") == []
    assert len(ev.hourly_rollup("2019-01-01T00:00:00+00:00")) == 1


# ── retention: append-only must still be bounded ────────────────────


def test_prune_removes_only_rows_outside_the_window(db: Path) -> None:
    ev.record_run_event("fresh", "run.step", agent="claude", status="ok")
    ev.record_run_event("stale", "run.step", agent="claude", status="ok")
    _backdate(db, trace_id="stale", stamp="2000-01-01T00:00:00+00:00")

    assert ev.prune_run_events(older_than_days=30) == 1
    assert len(ev.fetch_run_events("fresh")) == 1
    assert ev.fetch_run_events("stale") == []


def test_prune_clamps_a_zero_window_instead_of_wiping_the_ledger(db: Path) -> None:
    ev.record_run_event("r6", "run.step", agent="claude", status="ok")
    assert ev.prune_run_events(older_than_days=0) == 0
    assert len(ev.fetch_run_events("r6")) == 1


def test_rollups_and_prune_degrade_to_empty_on_storage_errors(
    db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(*args: Any, **kwargs: Any):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(ev, "sqlite_connect", _boom)
    assert ev.agent_rollup() == []
    assert ev.recent_steps() == []
    assert ev.failed_steps() == []
    assert ev.hourly_rollup("2020-01-01T00:00:00+00:00") == []
    assert ev.prune_run_events() == 0
