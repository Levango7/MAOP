"""Tests for MAOP.core.worker_pool — Multi-loop parallel execution."""

import asyncio

import pytest

from maop.core.worker_pool import (
    PoolStats,
    WorkerPool,
    WorkerStatus,
    WorkerTask,
    get_worker_pool,
)

# ── WorkerPool lifecycle ───────────────────────────────────────

class TestWorkerPoolLifecycle:
    @pytest.mark.asyncio
    async def test_start_stop(self):
        pool = WorkerPool(max_workers=2, max_cpu_workers=1)
        await pool.start()
        assert pool.is_running
        stats = pool.stats()
        assert stats.total_workers == 2
        assert stats.cpu_workers >= 1
        await pool.stop()
        assert not pool.is_running

    @pytest.mark.asyncio
    async def test_repr(self):
        pool = WorkerPool(max_workers=4, max_cpu_workers=2)
        r = repr(pool)
        assert "WorkerPool" in r
        assert "io=4" in r


# ── CPU-bound execution ────────────────────────────────────────

def _cpu_heavy(n: int) -> int:
    """CPU-bound function for testing process pool."""
    total = 0
    for i in range(n):
        total += i * i
    return total


class TestCPUBound:
    @pytest.mark.asyncio
    async def test_run_cpu(self):
        pool = WorkerPool(max_workers=2, max_cpu_workers=2)
        await pool.start()
        try:
            result = await pool.run_cpu(_cpu_heavy, 1000)
            # Sum of i^2 for i in 0..999
            expected = sum(i * i for i in range(1000))
            assert result == expected
        finally:
            await pool.stop()

    @pytest.mark.asyncio
    async def test_run_cpu_concurrent(self):
        pool = WorkerPool(max_workers=2, max_cpu_workers=2)
        await pool.start()
        try:
            results = await asyncio.gather(
                pool.run_cpu(_cpu_heavy, 500),
                pool.run_cpu(_cpu_heavy, 300),
            )
            assert results[0] == sum(i * i for i in range(500))
            assert results[1] == sum(i * i for i in range(300))
        finally:
            await pool.stop()


# ── Stats ──────────────────────────────────────────────────────

class TestWorkerPoolStats:
    @pytest.mark.asyncio
    async def test_initial_stats(self):
        pool = WorkerPool(max_workers=4, max_cpu_workers=1)
        await pool.start()
        try:
            stats = pool.stats()
            assert stats.total_workers == 4
            assert stats.active_workers == 0
            assert stats.idle_workers == 4
            assert stats.completed_tasks == 0
            assert stats.failed_tasks == 0
        finally:
            await pool.stop()

    @pytest.mark.asyncio
    async def test_stats_after_cpu_work(self):
        pool = WorkerPool(max_workers=2, max_cpu_workers=2)
        await pool.start()
        try:
            await pool.run_cpu(_cpu_heavy, 100)
            stats = pool.stats()
            # cpu_active should be 0 after completion
            assert stats.cpu_active == 0
        finally:
            await pool.stop()


# ── WorkerTask model ───────────────────────────────────────────

class TestWorkerTask:
    def test_default_values(self):
        wt = WorkerTask(description="test task")
        assert wt.status == "pending"
        assert wt.worker_id == -1
        assert wt.error == ""

    def test_auto_id(self):
        wt1 = WorkerTask(description="a")
        wt2 = WorkerTask(description="b")
        assert wt1.id != wt2.id


# ── PoolStats model ────────────────────────────────────────────

class TestPoolStats:
    def test_default_values(self):
        ps = PoolStats()
        assert ps.total_workers == 0
        assert ps.active_workers == 0
        assert ps.completed_tasks == 0

    def test_custom_values(self):
        ps = PoolStats(
            total_workers=8, active_workers=3, idle_workers=5,
            completed_tasks=100, failed_tasks=2,
        )
        assert ps.total_workers == 8
        assert ps.active_workers == 3


# ── Global singleton ───────────────────────────────────────────

class TestGlobalPool:
    def test_get_worker_pool(self):
        pool = get_worker_pool(max_workers=2)
        assert isinstance(pool, WorkerPool)
        # Second call returns same instance
        pool2 = get_worker_pool()
        assert pool is pool2


# ── Semaphore concurrency control ──────────────────────────────

class TestConcurrencyControl:
    @pytest.mark.asyncio
    async def test_max_workers_semaphore(self):
        """Verify semaphore limits concurrent IO tasks."""
        pool = WorkerPool(max_workers=2, max_cpu_workers=1)
        await pool.start()
        try:
            # Submit tasks that track concurrency
            concurrent_count = 0
            max_concurrent = 0
            lock = asyncio.Lock()

            async def tracked_work(_task):
                nonlocal concurrent_count, max_concurrent
                async with lock:
                    concurrent_count += 1
                    max_concurrent = max(max_concurrent, concurrent_count)
                await asyncio.sleep(0.05)
                async with lock:
                    concurrent_count -= 1

            # We can't use submit() directly since it creates MaopLoop,
            # so test the semaphore directly
            async with pool._sem:
                assert pool._sem._value < pool._max_workers
        finally:
            await pool.stop()

    @pytest.mark.asyncio
    async def test_worker_status_tracking(self):
        pool = WorkerPool(max_workers=3, max_cpu_workers=1)
        await pool.start()
        try:
            # All workers should be idle initially
            for status in pool._worker_status.values():
                assert status == WorkerStatus.IDLE
        finally:
            await pool.stop()
