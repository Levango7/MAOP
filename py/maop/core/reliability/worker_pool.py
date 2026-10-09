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
import threading
import time
import uuid
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from enum import Enum
from functools import partial
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from maop.core.reliability.pipeline_checkpoint import (
        PipelineCheckpoint,
    )

logger = logging.getLogger(__name__)


# ── Models ──────────────────────────────────────────────────────

class WorkerStatus(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    STOPPED = "stopped"


class TaskPreemptedError(RuntimeError):
    """A running task was cancelled to admit a higher-priority one.

    Raised into the task's result future by :meth:`WorkerPool.cancel`
    when called with ``preempt=True``. Priority-aware callers
    (:class:`~maop.core.reliability.preemptable_worker_pool.
    PreemptableWorkerPool`) catch it to distinguish a preemption
    (re-enqueue the task under its original token) from a plain
    cancellation or a real failure.
    """


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
        checkpoint: PipelineCheckpoint | None = None,
    ) -> None:
        self._max_workers = max(1, max_workers)
        # Default CPU workers = min(2, cpu_count - 1)
        if max_cpu_workers <= 0:
            max_cpu_workers = max(1, min(2, (os.cpu_count() or 4) - 1))
        self._max_cpu_workers = max_cpu_workers
        self._root_dir = root_dir
        # Phase γ-3: optional durable execution records. When set, every
        # executed task gets a pipeline-checkpoint run (started/completed/
        # failed + attempt counter), which is the wiring that makes true
        # preemption safe: a cancelled task leaves a durable "running"
        # step that pending_steps() reports for retry, and repeated
        # preemption-retry cycles are visible via the attempts counter.
        # Mid-task (phase-level) resume inside MaopLoop.run remains a
        # separate, future enhancement — the task itself is re-executed
        # in full by the re-enqueueing scheduler.
        self._checkpoint = checkpoint

        # IO-bound: asyncio semaphore
        self._sem = asyncio.Semaphore(self._max_workers)
        self._tasks: dict[str, WorkerTask] = {}
        self._futures: dict[str, asyncio.Future] = {}
        # Phase γ-3: live asyncio task handles (submit returns before the
        # coroutine finishes) and the set of task ids cancelled for
        # preemption, so the CancelledError handler can tell a preemption
        # apart from a plain cancellation (stop()).
        self._task_handles: dict[str, asyncio.Task] = {}
        self._preempt_ids: set[str] = set()
        self._completed = 0
        self._failed = 0

        # CPU-bound: process pool (lazy init)
        self._cpu_pool: ProcessPoolExecutor | None = None
        self._cpu_active = 0

        # Worker tracking
        self._worker_status: dict[int, WorkerStatus] = dict.fromkeys(range(self._max_workers), WorkerStatus.IDLE)

        self._running = False

        # H-2 fix: 预初始化 _shared_loop = None，并用锁保护懒创建，
        # 消除多协程并发到达懒初始化点的 TOCTOU 竞态。
        # （来源：coding-pattern/python-shared-dict-cache-concurrency-audit-fix-playbook）
        self._shared_loop: Any = None
        self._loop_lock = threading.Lock()
        # H-3/H-4 fix: 用锁保护 _completed/_failed/_cpu_active 计数器更新，
        # 避免多线程并发下的读-改-写竞态。临界区极短（单个 +=），
        # 不会阻塞事件循环。
        self._counter_lock = threading.Lock()

    @property
    def semaphore(self) -> asyncio.Semaphore:
        """P2-2 fix: 公共只读访问 IO 并发信号量。

        原代码 loop_executor.py 通过 getattr(self._worker_pool, "_sem", None)
        访问私有属性，违反封装。暴露公共 property 供外部获取信号量。
        """
        return self._sem

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

        handle = asyncio.ensure_future(
            self._run_task(wt, task, workdir, skip_verify, agent_name)
        )
        # Phase γ-3: keep the asyncio handle so cancel() can preempt a
        # running task (the result future alone cannot cancel the
        # coroutine).
        self._task_handles[wt.id] = handle
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

        Phase γ-3: the try/except now wraps the *semaphore acquisition
        too*, so a cancel() that lands while the task is still waiting
        for a slot resolves the result future instead of leaving it (and
        the completion watcher) hanging. Cancellation inside the body is
        routed through the same handler: a preemption (task id present
        in ``_preempt_ids``) resolves the future with
        :class:`TaskPreemptedError`; any other cancellation resolves it
        as a plain cancellation.
        """
        worktree_info = None
        actual_workdir = workdir
        worker_id = -1
        ckpt_run_id: str | None = None
        try:
            async with self._sem:
                # Find an idle worker slot
                worker_id = self._find_idle_worker()
                if worker_id >= 0:
                    self._worker_status[worker_id] = WorkerStatus.RUNNING
                wt.worker_id = worker_id
                wt.status = "running"
                wt.started_at = time.time()
                ckpt_run_id = self._checkpoint_begin(wt, task)

                try:
                    # Create isolated worktree if root_dir is a git repo
                    if self._root_dir and not workdir:
                        try:
                            from maop.core.agent.memory_ctx.worktree import WorktreeManager
                            wt_mgr = WorktreeManager(root_dir=self._root_dir or ".")
                            worktree_info = wt_mgr.create_root(task_id=wt.id)  # type: ignore
                            actual_workdir = str(worktree_info)
                        except Exception as e:
                            logger.debug("ignored: %s", e, exc_info=True)

                    from maop.maop_loop import MaopLoop
                    # P2-2 fix: reuse shared MaopLoop to avoid re-opening 5 SQLite
                    # connections per task (was causing connection exhaustion)
                    # H-2 fix: 双检锁保护 _shared_loop 懒创建，消除 TOCTOU 竞态。
                    # （来源：coding-pattern/python-shared-dict-cache-concurrency-audit-fix-playbook）
                    if self._shared_loop is None:
                        with self._loop_lock:
                            if self._shared_loop is None:
                                self._shared_loop = MaopLoop(root_dir=self._root_dir)
                    loop = self._shared_loop
                    result = await loop.run(
                        task=task, workdir=actual_workdir, skip_verify=skip_verify,
                        agent=agent_name,
                    )
                    wt.result = result
                    wt.status = "success"
                    # H-3 fix: 锁内更新 _completed 计数器
                    with self._counter_lock:
                        self._completed += 1
                    self._checkpoint_end(ckpt_run_id, task, ok=True)
                    if not self._futures[wt.id].done():
                        self._futures[wt.id].set_result(result)
                except Exception as exc:
                    wt.status = "failed"
                    wt.error = str(exc)
                    # H-3 fix: 锁内更新 _failed 计数器
                    with self._counter_lock:
                        self._failed += 1
                    self._checkpoint_end(ckpt_run_id, task, ok=False, error=str(exc))
                    if not self._futures[wt.id].done():
                        self._futures[wt.id].set_exception(exc)
                    logger.warning("Worker %d task failed: %s", worker_id, exc)
        except asyncio.CancelledError:
            if wt.id in self._preempt_ids:
                self._preempt_ids.discard(wt.id)
                wt.status = "preempted"
                if not self._futures[wt.id].done():
                    self._futures[wt.id].set_exception(
                        TaskPreemptedError(
                            f"task {wt.id} preempted by a higher-priority task"
                        )
                    )
                logger.info(
                    "[worker-pool] task %s preempted (status=preempted, "
                    "re-enqueue expected)", wt.id,
                )
            else:
                wt.status = "cancelled"
                if not self._futures[wt.id].done():
                    self._futures[wt.id].cancel()
        finally:
            wt.finished_at = time.time()
            if worker_id >= 0:
                self._worker_status[worker_id] = WorkerStatus.IDLE
            # Clean up worktree after task completion
            if worktree_info:
                try:
                    from maop.core.agent.memory_ctx.worktree import WorktreeManager
                    wt_mgr = WorktreeManager(root_dir=self._root_dir or ".")
                    wt_mgr.cleanup(worktree_info)  # type: ignore
                except Exception as e:
                    logger.debug("ignored: %s", e, exc_info=True)
            self._task_handles.pop(wt.id, None)

    def cancel(self, task_id: str, *, preempt: bool = False) -> bool:
        """Cancel a live task via its asyncio handle.

        Parameters
        ----------
        task_id : str
            The id returned by :meth:`submit`.
        preempt : bool
            When ``True``, the task is recorded as *preempted*: its
            result future resolves with :class:`TaskPreemptedError`
            instead of a bare cancellation, so a priority-aware caller
            can re-enqueue it. The task id must not already be finished.

        Returns
        -------
        bool
            ``True`` if a live task was found and cancelled; ``False``
            for unknown or already-finished tasks (no-op).
        """
        handle = self._task_handles.get(task_id)
        if handle is None or handle.done():
            return False
        if preempt:
            self._preempt_ids.add(task_id)
        handle.cancel()
        return True

    # ── Checkpoint wiring (Phase γ-3) ───────────────────────

    def _checkpoint_begin(self, wt: WorkerTask, task: str) -> str | None:
        """Open a checkpoint run for a starting task (best-effort).

        The task description is the single step; failures here are
        logged and swallowed — checkpointing must never break task
        execution.
        """
        if self._checkpoint is None:
            return None
        try:
            run_id = self._checkpoint.start_run(
                "worker_pool",
                steps=[task],
                variables={"task_id": wt.id, "description": task},
            )
            self._checkpoint.start_step(run_id, task)
            return run_id
        except Exception as e:
            logger.debug("[worker-pool] checkpoint begin failed: %s", e)
            return None

    def _checkpoint_end(
        self,
        run_id: str | None,
        step: str,
        *,
        ok: bool,
        error: str = "",
    ) -> None:
        """Close a checkpoint run's step (best-effort).

        On preemption neither branch runs: the step stays ``running``
        in the durable record, which ``pending_steps()`` reports for
        retry — that is the resumable state true preemption relies on.
        """
        if self._checkpoint is None or run_id is None:
            return
        try:
            if ok:
                self._checkpoint.complete_step(run_id, step, output="")
            else:
                self._checkpoint.fail_step(run_id, step, error=error)
        except Exception as e:
            logger.debug("[worker-pool] checkpoint end failed: %s", e)

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

        # H-4 fix: 锁内更新 _cpu_active 计数器
        with self._counter_lock:
            self._cpu_active += 1
        try:
            loop = asyncio.get_running_loop()
            # Use functools.partial for pickle compatibility
            fn = partial(func, *args, **kwargs)
            result = await loop.run_in_executor(self._cpu_pool, fn)
            return result
        finally:
            with self._counter_lock:
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
# H-1 fix: 保护单例创建的锁，避免多线程并发时创建多个实例
# （来源：coding-pattern/python-shared-dict-cache-concurrency-audit-fix-playbook）
_pool_lock = threading.Lock()


def get_worker_pool(
    max_workers: int = 4,
    max_cpu_workers: int = 0,
    root_dir: str | None = None,
) -> WorkerPool:
    """Get or create the global worker pool singleton.

    H-1 fix: 使用 threading.Lock + 双检锁（DCLP）保护单例创建，
    避免多线程并发调用时创建多个 WorkerPool 实例。
    """
    global _global_pool
    if _global_pool is None:
        with _pool_lock:
            # 双检锁：持锁后再次检查，防止等待期间已被其他线程初始化
            if _global_pool is None:
                _global_pool = WorkerPool(
                    max_workers=max_workers,
                    max_cpu_workers=max_cpu_workers,
                    root_dir=root_dir,
                )
    return _global_pool
