"""Coverage tests for maop.core.worker_pool — lifecycle, submit/wait, stats, CPU."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from maop.core.reliability.worker_pool import (
    PoolStats,
    WorkerPool,
    WorkerStatus,
    WorkerTask,
    get_worker_pool,
)


class TestWorkerPoolLifecycle:
    @pytest.mark.asyncio
    async def test_start_and_stop(self):
        pool = WorkerPool(max_workers=2)
        await pool.start()
        assert pool.is_running is True
        await pool.stop()
        assert pool.is_running is False

    @pytest.mark.asyncio
    async def test_stop_cancels_pending_futures(self):
        pool = WorkerPool(max_workers=2)
        await pool.start()
        # Create a future manually to simulate pending task
        fut = asyncio.get_running_loop().create_future()
        pool._futures["fake-id"] = fut
        await pool.stop()
        assert fut.cancelled() or fut.done()

    @pytest.mark.asyncio
    async def test_stop_resets_worker_status(self):
        pool = WorkerPool(max_workers=3)
        await pool.start()
        pool._worker_status[0] = WorkerStatus.RUNNING
        await pool.stop()
        assert all(s == WorkerStatus.STOPPED for s in pool._worker_status.values())


class TestWorkerPoolSubmitWait:
    @pytest.mark.asyncio
    async def test_wait_nonexistent_raises_keyerror(self):
        pool = WorkerPool(max_workers=1)
        await pool.start()
        try:
            with pytest.raises(KeyError, match="not found"):
                await pool.wait("nonexistent-id")
        finally:
            await pool.stop()

    @pytest.mark.asyncio
    async def test_submit_returns_task_id(self):
        pool = WorkerPool(max_workers=1, root_dir="")
        await pool.start()
        try:
            with patch("maop.maop_loop.MaopLoop") as mock_loop_cls:
                mock_loop = MagicMock()
                mock_loop.run = AsyncMock(return_value="result")
                mock_loop_cls.return_value = mock_loop
                task_id = await pool.submit("do task")
                assert isinstance(task_id, str)
                result = await pool.wait(task_id, timeout=5)
                assert result == "result"
        finally:
            await pool.stop()

    @pytest.mark.asyncio
    async def test_submit_with_agent_name(self):
        pool = WorkerPool(max_workers=1, root_dir="")
        await pool.start()
        try:
            with patch("maop.maop_loop.MaopLoop") as mock_loop_cls:
                mock_loop = MagicMock()
                mock_loop.run = AsyncMock(return_value="ok")
                mock_loop_cls.return_value = mock_loop
                task_id = await pool.submit("task", agent_name="claude")
                result = await pool.wait(task_id, timeout=5)
                assert result == "ok"
                # Verify agent_name was forwarded
                mock_loop.run.assert_awaited_once()
                assert mock_loop.run.call_args.kwargs.get("agent") == "claude"
        finally:
            await pool.stop()

    @pytest.mark.asyncio
    async def test_task_exception_propagates(self):
        pool = WorkerPool(max_workers=1, root_dir="")
        await pool.start()
        try:
            with patch("maop.maop_loop.MaopLoop") as mock_loop_cls:
                mock_loop = MagicMock()
                mock_loop.run = AsyncMock(side_effect=RuntimeError("task failed"))
                mock_loop_cls.return_value = mock_loop
                task_id = await pool.submit("task")
                with pytest.raises(RuntimeError, match="task failed"):
                    await pool.wait(task_id, timeout=5)
        finally:
            await pool.stop()

    @pytest.mark.asyncio
    async def test_run_all_empty(self):
        pool = WorkerPool(max_workers=2, root_dir="")
        await pool.start()
        try:
            results = await pool.run_all([])
            assert results == []
        finally:
            await pool.stop()

    @pytest.mark.asyncio
    async def test_run_all_multiple(self):
        pool = WorkerPool(max_workers=2, root_dir="")
        await pool.start()
        try:
            with patch("maop.maop_loop.MaopLoop") as mock_loop_cls:
                mock_loop = MagicMock()
                mock_loop.run = AsyncMock(return_value="done")
                mock_loop_cls.return_value = mock_loop
                results = await pool.run_all(["task1", "task2", "task3"])
                assert results == ["done", "done", "done"]
        finally:
            await pool.stop()


class TestWorkerPoolStats:
    @pytest.mark.asyncio
    async def test_stats_initial(self):
        pool = WorkerPool(max_workers=4, max_cpu_workers=2)
        await pool.start()
        try:
            stats = pool.stats()
            assert stats.total_workers == 4
            assert stats.active_workers == 0
            assert stats.idle_workers == 4
            assert stats.completed_tasks == 0
        finally:
            await pool.stop()

    @pytest.mark.asyncio
    async def test_get_task(self):
        pool = WorkerPool(max_workers=1, root_dir="")
        await pool.start()
        try:
            with patch("maop.maop_loop.MaopLoop") as mock_loop_cls:
                mock_loop = MagicMock()
                mock_loop.run = AsyncMock(return_value="ok")
                mock_loop_cls.return_value = mock_loop
                task_id = await pool.submit("task")
                await pool.wait(task_id, timeout=5)
                task = pool.get_task(task_id)
                assert task is not None
                assert task.id == task_id
                assert pool.get_task("nonexistent") is None
        finally:
            await pool.stop()

    @pytest.mark.asyncio
    async def test_all_tasks(self):
        pool = WorkerPool(max_workers=2, root_dir="")
        await pool.start()
        try:
            with patch("maop.maop_loop.MaopLoop") as mock_loop_cls:
                mock_loop = MagicMock()
                mock_loop.run = AsyncMock(return_value="ok")
                mock_loop_cls.return_value = mock_loop
                tid1 = await pool.submit("t1")
                tid2 = await pool.submit("t2")
                await pool.wait(tid1, timeout=5)
                await pool.wait(tid2, timeout=5)
                tasks = pool.all_tasks()
                assert len(tasks) == 2
        finally:
            await pool.stop()


class TestWorkerPoolCpu:
    @pytest.mark.asyncio
    async def test_run_cpu(self):
        pool = WorkerPool(max_workers=1, max_cpu_workers=1)
        await pool.start()
        try:
            # Use a module-level picklable function (abs is builtin)
            result = await pool.run_cpu(abs, -7)
            assert result == 7
        finally:
            await pool.stop()


class TestWorkerPoolRepr:
    def test_repr(self):
        pool = WorkerPool(max_workers=4, max_cpu_workers=2)
        r = repr(pool)
        assert "io=4" in r
        assert "cpu=2" in r


class TestGetWorkerPool:
    def test_singleton(self):
        # Reset singleton
        import maop.core.reliability.worker_pool as wp_mod
        wp_mod._global_pool = None
        pool1 = get_worker_pool(max_workers=2)
        pool2 = get_worker_pool(max_workers=4)  # args ignored after first call
        assert pool1 is pool2
        wp_mod._global_pool = None  # cleanup


class TestWorkerTaskModel:
    def test_defaults(self):
        t = WorkerTask(description="test")
        assert t.status == "pending"
        assert t.worker_id == -1
        assert len(t.id) == 12

    def test_custom(self):
        t = WorkerTask(id="custom-id", description="x", status="running")
        assert t.id == "custom-id"
        assert t.status == "running"


class TestPoolStatsModel:
    def test_defaults(self):
        s = PoolStats()
        assert s.total_workers == 0
        assert s.active_workers == 0
        assert s.completed_tasks == 0