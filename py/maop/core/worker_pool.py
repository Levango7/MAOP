"""MAOP Worker Pool — Multi-loop parallel execution with CPU isolation.

Enables MaopLoop to run multiple tasks concurrently across:
  - IO-bound tasks: asyncio event loop (existing TaskPool)
  - CPU-bound tasks: ProcessPoolExecutor (evolve stats, memory search)

Usage::

    pool = WorkerPool(max_workers=4, max_cpu_workers=2)

    # Run multiple tasks in parallel
    results = await pool.run_all([
        "Add input validation",
        "Fix timeout bug",
        "Update README",
    ])

    # Or submit individually
    task_id = await pool.submit("Refactor auth module")
    result = await pool.wait(task_id)

    # CPU-bound work (offloaded to process pool)
    stats = await pool.run_cpu(compute_heavy_stats, data)
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from enum import Enum
from functools import partial
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ── Models ──────────────────────────────────────────────────────

class WorkerStatus(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    STOPPED = "stopped"


class WorkerTask(BaseModel):
    """A task submitted to the worker pool."""
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    description: str = ""
    status: str = "pending"
    worker_id: int = -1
    submitted_at: float = Field(default_factory=time.time)
    started_at: float = 0.0
    finished_at: float = 0.0
    result: Any = None
    error: str = ""


class PoolStats(BaseModel):
    """Worker pool statistics."""
    total_workers: int = 0
    active_workers: int = 0
    idle_workers: int = 0
    pending_tasks: int = 0
    completed_tasks: int = 0
    failed_tasks: int = 0
    cpu_workers: int = 0
    cpu_active: int = 0


# ── Worker Pool ────────────────────────────────────────────────

class WorkerPool:
    """Multi-loop parallel execution with CPU isolation.

    Parameters
    ----------
    max_workers : int
        Maximum concurrent IO-bound tasks (MaopLoop instances).
    max_cpu_workers : int
        Maximum CPU-bound worker processes.
    root_dir : str | None
        MAOP project root (passed to each MaopLoop instance).
    """

    def __init__(
        self,
        max_workers: int = 4,
        max_cpu_workers: int = 0,
        root_dir: str | None = None,
    ) -> None:
        self._max_workers = max(1, max_workers)
        # Default CPU workers = min(2, cpu_count - 1)
        if max_cpu_workers <= 0:
            max_cpu_workers = max(1, min(2, (os.cpu_count() or 4) - 1))
        self._max_cpu_workers = max_cpu_workers
        self._root_dir = root_dir

        # IO-bound: asyncio semaphore
        self._sem = asyncio.Semaphore(self._max_workers)
        self._tasks: dict[str, WorkerTask] = {}
        self._futures: dict[str, asyncio.Future] = {}
        self._completed = 0
        self._failed = 0

        # CPU-bound: process pool (lazy init)
        self._cpu_pool: ProcessPoolExecutor | None = None
        self._cpu_active = 0

        # Worker tracking
        self._worker_status: dict[int, WorkerStatus] = dict.fromkeys(range(self._max_workers), WorkerStatus.IDLE)

        self._running = False

    # ── Lifecycle ─────────────────────────────────────────────

    async def start(self) -> None:
        """Start the worker pool."""
        self._running = True
        self._cpu_pool = ProcessPoolExecutor(max_workers=self._max_cpu_workers)
        logger.info(
            "WorkerPool started: io_workers=%d, cpu_workers=%d",
            self._max_workers, self._max_cpu_workers,
        )

    async def stop(self) -> None:
        """Stop the worker pool and release resources."""
        self._running = False

        # Cancel pending futures
        for fut in self._futures.values():
            if not fut.done():
                fut.cancel()

        # Shutdown CPU pool
        if self._cpu_pool is not None:
            self._cpu_pool.shutdown(wait=False)
            self._cpu_pool = None

        # Reset worker status
        for wid in self._worker_status:
            self._worker_status[wid] = WorkerStatus.STOPPED

        logger.info("WorkerPool stopped")

    # ── IO-bound task execution ───────────────────────────────

    async def submit(
        self,
        task: str,
        *,
        workdir: str = "",
        skip_verify: bool = False,
        priority: int = 0,
        agent_name: str = "",
    ) -> str:
        """Submit a single task for parallel execution.

        Returns task ID for later retrieval via wait().

        F6a (2026-07-22, Phase F): ``agent_name`` lets the caller
        (typically A2A ``dispatch_task``) pin the executing agent for
        this task. When non-empty, it is forwarded to
        ``MaopLoop.run(agent=...)`` which makes ``_phase_plan`` skip
        its own agent selection (plan_result + load_balancer) and use
        the explicit agent. When empty (default), the existing
        plan-based selection is preserved. See ADR-013.
        """
        wt = WorkerTask(description=task)
        self._tasks[wt.id] = wt
        self._futures[wt.id] = asyncio.get_running_loop().create_future()

        asyncio.ensure_future(self._run_task(wt, task, workdir, skip_verify, agent_name))
        return wt.id

    async def _run_task(
        self,
        wt: WorkerTask,
        task: str,
        workdir: str,
        skip_verify: bool,
        agent_name: str = "",
    ) -> None:
        """Execute a single task with semaphore-controlled concurrency.

        F6a (2026-07-22, Phase F): ``agent_name`` is forwarded to
        ``MaopLoop.run(agent=...)`` so the loop uses the explicitly
        pinned agent instead of selecting one via _phase_plan.
        """
        async with self._sem:
            # Find an idle worker slot
            worker_id = self._find_idle_worker()
            if worker_id >= 0:
                self._worker_status[worker_id] = WorkerStatus.RUNNING
            wt.worker_id = worker_id
            wt.status = "running"
            wt.started_at = time.time()

            worktree_info = None
            actual_workdir = workdir
            try:
                # Create isolated worktree if root_dir is a git repo
                if self._root_dir and not workdir:
                    try:
                        from maop.core.worktree import WorktreeManager
                        wt_mgr = WorktreeManager(root_dir=self._root_dir or ".")
                        worktree_info = wt_mgr.create_root(task_id=wt.id)  # type: ignore[call-arg]
                        actual_workdir = str(worktree_info)
                    except Exception:
                        pass

                from maop.maop_loop import MaopLoop
                # P2-2 fix: reuse shared MaopLoop to avoid re-opening 5 SQLite
                # connections per task (was causing connection exhaustion)
                if not hasattr(self, '_shared_loop') or self._shared_loop is None:
                    self._shared_loop = MaopLoop(root_dir=self._root_dir)
                loop = self._shared_loop
                result = await loop.run(
                    task=task, workdir=actual_workdir, skip_verify=skip_verify,
                    agent=agent_name,
                )
                wt.result = result
                wt.status = "success"
                self._completed += 1
                if not self._futures[wt.id].done():
                    self._futures[wt.id].set_result(result)
            except asyncio.CancelledError:
                wt.status = "cancelled"
                if not self._futures[wt.id].done():
                    self._futures[wt.id].cancel()
            except Exception as exc:
                wt.status = "failed"
                wt.error = str(exc)
                self._failed += 1
                if not self._futures[wt.id].done():
                    self._futures[wt.id].set_exception(exc)
                logger.warning("Worker %d task failed: %s", worker_id, exc)
            finally:
                wt.finished_at = time.time()
                if worker_id >= 0:
                    self._worker_status[worker_id] = WorkerStatus.IDLE
                # Clean up worktree after task completion
                if worktree_info:
                    try:
                        from maop.core.worktree import WorktreeManager
                        wt_mgr = WorktreeManager(root_dir=self._root_dir or ".")
                        wt_mgr.cleanup(worktree_info)  # type: ignore[attr-defined]
                    except Exception:
                        pass

    def _find_idle_worker(self) -> int:
        """Find an idle worker slot."""
        for wid, status in self._worker_status.items():
            if status == WorkerStatus.IDLE:
                return wid
        return -1

    async def wait(self, task_id: str, timeout: float = 0) -> Any:
        """Wait for a submitted task to complete."""
        fut = self._futures.get(task_id)
        if fut is None:
            raise KeyError(f"Task {task_id} not found")
        if timeout > 0:
            return await asyncio.wait_for(fut, timeout=timeout)
        return await fut

    async def run_all(
        self,
        tasks: list[str],
        *,
        workdir: str = "",
        skip_verify: bool = False,
        agent_name: str = "",
    ) -> list[Any]:
        """Run multiple tasks in parallel, return all results.

        Submits all tasks and waits for completion.

        F6a (2026-07-22, Phase F): ``agent_name`` pins the executing
        agent for every task in the batch (forwarded to ``submit``).
        """
        task_ids = []
        for task in tasks:
            tid = await self.submit(
                task, workdir=workdir, skip_verify=skip_verify, agent_name=agent_name,
            )
            task_ids.append(tid)

        results = []
        for tid in task_ids:
            try:
                result = await self.wait(tid)
                results.append(result)
            except Exception as exc:
                results.append(exc)
        return results

    # ── CPU-bound task execution ──────────────────────────────

    async def run_cpu(
        self,
        func: Callable[..., Any],
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """Run a CPU-bound function in the process pool.

        Use for: evolve statistics, memory full-scan, heavy computation.

        Parameters
        ----------
        func : Callable
            A picklable function to execute in a subprocess.
        *args, **kwargs
            Arguments passed to func.
        """
        if self._cpu_pool is None:
            self._cpu_pool = ProcessPoolExecutor(max_workers=self._max_cpu_workers)

        self._cpu_active += 1
        try:
            loop = asyncio.get_running_loop()
            # Use functools.partial for pickle compatibility
            fn = partial(func, *args, **kwargs)
            result = await loop.run_in_executor(self._cpu_pool, fn)
            return result
        finally:
            self._cpu_active -= 1

    # ── Query ─────────────────────────────────────────────────

    def get_task(self, task_id: str) -> WorkerTask | None:
        """Get task by ID."""
        return self._tasks.get(task_id)

    def all_tasks(self) -> list[WorkerTask]:
        """Get all tasks."""
        return list(self._tasks.values())

    def stats(self) -> PoolStats:
        """Get pool statistics."""
        active = sum(
            1 for s in self._worker_status.values()
            if s == WorkerStatus.RUNNING
        )
        pending = sum(
            1 for t in self._tasks.values()
            if t.status == "pending"
        )
        return PoolStats(
            total_workers=self._max_workers,
            active_workers=active,
            idle_workers=self._max_workers - active,
            pending_tasks=pending,
            completed_tasks=self._completed,
            failed_tasks=self._failed,
            cpu_workers=self._max_cpu_workers,
            cpu_active=self._cpu_active,
        )

    @property
    def is_running(self) -> bool:
        return self._running

    def __repr__(self) -> str:
        return (
            f"WorkerPool(io={self._max_workers}, cpu={self._max_cpu_workers}, "
            f"active={sum(1 for s in self._worker_status.values() if s == WorkerStatus.RUNNING)})"
        )


# ── Global pool singleton ──────────────────────────────────────

_global_pool: WorkerPool | None = None


def get_worker_pool(
    max_workers: int = 4,
    max_cpu_workers: int = 0,
    root_dir: str | None = None,
) -> WorkerPool:
    """Get or create the global worker pool singleton."""
    global _global_pool
    if _global_pool is None:
        _global_pool = WorkerPool(
            max_workers=max_workers,
            max_cpu_workers=max_cpu_workers,
            root_dir=root_dir,
        )
    return _global_pool
