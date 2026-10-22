"""The job registry has to forget finished work.

``state.active_jobs`` kept every job ever run — each entry carries the captured
stdout/stderr and, for subprocess jobs, the process handle. Nothing in the tree
ever popped an entry, so a dashboard left running for weeks was accumulating a
history file in RAM while calling it "active jobs".
"""

from __future__ import annotations

import ast
import time
from pathlib import Path

import pytest

from maop.dashboard.routers import state

DASHBOARD = Path(state.__file__).parent.parent


@pytest.fixture(autouse=True)
def clean_registry():
    saved = dict(state.active_jobs)
    state.active_jobs.clear()
    yield
    state.active_jobs.clear()
    state.active_jobs.update(saved)


def job(status: str, start: str | None = None) -> dict[str, object]:
    return {
        "action": "test", "status": status,
        "start": start or time.strftime("%Y-%m-%dT%H:%M:%S"),
        "stdout": "x" * 32,
    }


class TestRetention:
    def test_register_inserts(self) -> None:
        state.register_job("j1", job("running"))
        assert state.active_jobs["j1"]["status"] == "running"

    def test_finished_jobs_are_capped(self) -> None:
        for i in range(state.JOB_RETENTION_MAX + 60):
            state.register_job(
                f"j{i}", job("completed", start=f"2026-01-01T00:{i // 60:02d}:{i % 60:02d}"),
            )
        assert len(state.active_jobs) == state.JOB_RETENTION_MAX

    def test_eviction_removes_oldest_finished_first(self) -> None:
        for i in range(state.JOB_RETENTION_MAX + 5):
            state.register_job(f"j{i}", job("failed", start=f"2026-01-01T00:00:{i % 10:02d}"))
        assert "j0" not in state.active_jobs
        assert f"j{state.JOB_RETENTION_MAX + 4}" in state.active_jobs

    def test_running_jobs_are_never_evicted(self) -> None:
        """The cap is allowed to be exceeded rather than drop live work."""
        live = [f"r{i}" for i in range(state.JOB_RETENTION_MAX + 10)]
        for key in live:
            state.register_job(key, job("running"))
        assert len(state.active_jobs) == len(live)
        assert all(k in state.active_jobs for k in live)

    def test_prune_reports_how_many_it_dropped(self) -> None:
        for i in range(10):
            state.register_job(f"f{i}", job("cancelled"))
        assert state.prune_active_jobs(max_entries=4) == 6
        assert len(state.active_jobs) == 4

    def test_prune_is_a_noop_below_the_ceiling(self) -> None:
        state.register_job("one", job("done"))
        assert state.prune_active_jobs() == 0


class TestStatusVocabulary:
    """A terminal status that is not in TERMINAL_JOB_STATUSES leaks forever,
    silently — the entry simply never becomes eligible for eviction."""

    def test_every_status_written_into_the_registry_is_known(self) -> None:
        written: set[str] = set()
        for path in sorted(DASHBOARD.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                callee = ast.unparse(node.func)
                if not callee.endswith("register_job"):
                    continue  # covers both `register_job(...)` and `state.register_job(...)`
                for value in ast.walk(node):
                    if not isinstance(value, ast.Dict):
                        continue
                    keys = [ast.unparse(k) for k in value.keys if k is not None]
                    if "'status'" not in keys:
                        continue
                    slot = value.values[keys.index("'status'")]
                    if isinstance(slot, ast.Constant) and isinstance(slot.value, str):
                        written.add(slot.value)
        assert written, "no register_job status literals found — scanner is blind"
        unknown = written - state.TERMINAL_JOB_STATUSES - {"running", "pending"}
        assert not unknown, (
            f"statuses written into active_jobs that can never be evicted: {sorted(unknown)}"
        )
