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
