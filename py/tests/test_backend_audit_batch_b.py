"""Tests for backend-audit batch B — leaks, visibility, and hot paths.

Covers the 2026-10-09 audit findings outside the distributed
path (PR #71):

- :class:`~maop.core.reliability.worker_pool.WorkerPool`
  retained one ``_tasks``/``_futures`` entry per submitted
  task forever (unbounded growth). Finished entries are now
  retained up to a cap, evicted oldest-first; running tasks
  are never evicted. ``stop()`` also cancels live task
  handles so a stopped pool stops executing.
- :meth:`maop.engine.Engine._run_single` leaked the
  per-run spawn queue whenever the run was cancelled or
  paused mid-layer.
- ``PreemptableWorkerPool`` auto-created its durable
  checkpoint store inside ``contextlib.suppress(Exception)``
  — the silent degradation that hid a broken import for a
  whole release. It now logs an error.
- ``safe_eval`` re-parsed the expression on every call
  (``ast.parse`` is the hot path); the template resolver
  was O(context × template) and re-expanded inserted text
  (second-order injection).
- A broken CONDITION expression silently evaluated as
  ``false``; the license key was logged and raised in
  plaintext.
"""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from maop.core.agent.auth.harness_auth import _mask_key
from maop.core.reliability.worker_pool import (
    _COMPLETED_TASK_RETENTION,
    WorkerPool,
)
from maop.engine import Engine, StepStatus, StepType, WorkflowStep
from maop.engine_utils import _parse_expr, _resolve_template, safe_eval


async def _never_run(task, workdir="", skip_verify=False, agent=""):
    """Stands in for a long MaopLoop.run: never resolves."""
    await asyncio.Event().wait()


# ── WorkerPool bookkeeping ──────────────────────────────

class TestWorkerPoolRetention:
    @pytest.mark.asyncio
    async def test_finished_tasks_stay_queryable(self) -> None:
        """Completed tasks remain gettable (API compatibility)."""
        pool = WorkerPool(max_workers=2, root_dir="")
        await pool.start()
        try:
            with patch("maop.maop_loop.MaopLoop") as mock_loop_cls:
                mock_loop = MagicMock()

                async def _run(task, workdir="", skip_verify=False, agent=""):
                    return f"done:{task}"

                mock_loop.run = AsyncMock(side_effect=_run)
                mock_loop_cls.return_value = mock_loop
                tid = await pool.submit("task one")
                await pool.wait(tid, timeout=5)
                wt = pool.get_task(tid)
                assert wt is not None
                assert wt.status == "success"
        finally:
            await pool.stop()

    @pytest.mark.asyncio
    async def test_finished_entries_evicted_oldest_first(self) -> None:
        """Beyond the cap, the oldest finished entries go first."""
        pool = WorkerPool(max_workers=2, root_dir="")
        await pool.start()
        try:
            with patch("maop.maop_loop.MaopLoop") as mock_loop_cls:
                mock_loop = MagicMock()

                async def _run(task, workdir="", skip_verify=False, agent=""):
                    return "ok"

                mock_loop.run = AsyncMock(side_effect=_run)
                mock_loop_cls.return_value = mock_loop
                ids = [await pool.submit(f"task {i}") for i in range(3)]
                for tid in ids:
                    await pool.wait(tid, timeout=5)
                # Retention is capped: only the newest entry stays.
                with patch(
                    "maop.core.reliability.worker_pool."
                    "_COMPLETED_TASK_RETENTION",
                    1,
                ):
                    # Simulate one more completion to trigger eviction.
                    extra = await pool.submit("task extra")
                    await pool.wait(extra, timeout=5)
                    assert len(pool._tasks) <= 1
                    assert pool.get_task(extra) is not None
                    assert pool.get_task(ids[0]) is None
        finally:
            await pool.stop()

    @pytest.mark.asyncio
    async def test_running_tasks_never_evicted(self) -> None:
        """A long-running task keeps its entry even past the cap."""
        pool = WorkerPool(max_workers=2, root_dir="")
        await pool.start()
        try:
            with patch("maop.maop_loop.MaopLoop") as mock_loop_cls:
                mock_loop = MagicMock()

                async def _run(task, workdir="", skip_verify=False, agent=""):
                    if task == "long runner":
                        await asyncio.Event().wait()
                    return "ok"

                mock_loop.run = AsyncMock(side_effect=_run)
                mock_loop_cls.return_value = mock_loop
                runner = await pool.submit("long runner")
                await asyncio.sleep(0.05)
                assert pool.get_task(runner) is not None
                # Fill the retention window with finished tasks.
                for _ in range(_COMPLETED_TASK_RETENTION + 2):
                    filler = await pool.submit("filler")
                    await pool.wait(filler, timeout=5)
                # The running task survived the evictions.
                assert pool.get_task(runner) is not None
        finally:
            await pool.stop()

    @pytest.mark.asyncio
    async def test_stop_cancels_live_handles(self) -> None:
        """stop() cancels running coroutines instead of abandoning them."""
        pool = WorkerPool(max_workers=1, root_dir="")
        await pool.start()
        try:
            with patch("maop.maop_loop.MaopLoop") as mock_loop_cls:
                mock_loop = MagicMock()
                mock_loop.run = AsyncMock(side_effect=_never_run)
                mock_loop_cls.return_value = mock_loop
                tid = await pool.submit("long task")
                await asyncio.sleep(0.05)  # let it start running
                handle = pool._task_handles[tid]
                await pool.stop()
                # Cancellation is delivered asynchronously: wait
                # for the coroutine to process it (it resolves
                # the future and flips the status on the way out).
                await asyncio.gather(handle, return_exceptions=True)
                wt = pool.get_task(tid)
                assert wt is not None
                assert wt.status == "cancelled"
                with pytest.raises(asyncio.CancelledError):
                    await pool.wait(tid, timeout=5)
        finally:
            await pool.stop()


# ── Engine spawn-queue cleanup ──────────────────────────

class TestEngineSpawnCleanup:
    @pytest.mark.asyncio
    async def test_spawn_queue_dropped_on_normal_exit(self) -> None:
        engine = Engine()
        step = WorkflowStep(id="s1", type=StepType.TERMINAL, task="done")
        await engine.run(steps=[step], trace_id="trace-ok")
        assert "trace-ok" not in engine._spawns

    @pytest.mark.asyncio
    async def test_spawn_queue_dropped_on_cancellation(self) -> None:
        released = asyncio.Event()

        async def slow_executor(step, context, workdir, trace_id):
            await released.wait()
            return SimpleNamespaceOutput()

        engine = Engine(step_executor=slow_executor)
        step = WorkflowStep(id="s1", type=StepType.AGENT, task="slow")
        run_task = asyncio.ensure_future(
            engine.run(steps=[step], trace_id="trace-cancel"),
        )
        await asyncio.sleep(0.05)  # let the step start
        run_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await run_task
        released.set()
        assert "trace-cancel" not in engine._spawns


class SimpleNamespaceOutput:
    """Minimal stand-in for an executor result."""

    output = "ok"
    exit_code = 0


# ── Template resolution ─────────────────────────────────

class TestResolveTemplate:
    def test_second_order_values_not_reexpanded(self) -> None:
        """A value containing a placeholder is inserted literally."""
        result = _resolve_template(
            "{{ a }}", {"a": "{{ b }}", "b": "X"},
        )
        assert result == "{{ b }}"

    def test_unknown_key_left_untouched(self) -> None:
        assert _resolve_template("{{ missing }}", {}) == "{{ missing }}"

    def test_key_with_dashes_resolved(self) -> None:
        assert (
            _resolve_template("{{ my-step }}", {"my-step": "v"}) == "v"
        )

    def test_multiple_placeholders_single_pass(self) -> None:
        result = _resolve_template(
            "{{ a }} and {{ b }}", {"a": "1", "b": "2"},
        )
        assert result == "1 and 2"


# ── safe_eval parse cache ───────────────────────────────

class TestSafeEvalCache:
    def test_parse_is_cached(self) -> None:
        before = _parse_expr.cache_info().hits
        safe_eval("1 + 1", {})
        safe_eval("1 + 1", {})
        assert _parse_expr.cache_info().hits >= before + 1

    def test_eval_still_correct(self) -> None:
        assert safe_eval("2 * 3", {}) == 6


# ── CONDITION failure visibility ────────────────────────

class TestConditionVisibility:
    @pytest.mark.asyncio
    async def test_broken_expr_logs_warning(self, caplog) -> None:
        engine = Engine()
        step = WorkflowStep(
            id="cond", type=StepType.CONDITION,
            params={"expr": "1 +"},  # SyntaxError
        )
        with caplog.at_level(logging.WARNING):
            result = await engine.run(steps=[step], trace_id="t-cond")
        assert any(
            "failed to evaluate" in rec.message for rec in caplog.records
        )
        # A broken expression is treated as not-passed (SKIPPED).
        assert result.steps[0].status == StepStatus.SKIPPED


# ── License key masking ─────────────────────────────────

class TestMaskKey:
    def test_long_key_masked(self) -> None:
        assert _mask_key("sk-abcdef1234567890") == "sk-a****90"

    def test_short_key_all_stars(self) -> None:
        assert _mask_key("abc") == "***"

    def test_empty_key(self) -> None:
        assert _mask_key("") == ""
