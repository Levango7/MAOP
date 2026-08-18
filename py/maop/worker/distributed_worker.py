"""MAOP Distributed Worker — Consumes tasks from Redis Streams and executes them.

F1-01 (分布式执行): a worker process that registers with the
:class:`~maop.core.scheduling.worker_pool.WorkerRegistry`, consumes DAG
node tasks from the Redis Streams task queue, executes them, and posts
results back to the run's results stream.

Lifecycle
---------
1. **Register** — on startup the worker calls
   :meth:`WorkerRegistry.register` with its host, concurrency, and
   capability tags.
2. **Heartbeat loop** — a background task refreshes the heartbeat every
   ``heartbeat_interval`` seconds (default 5s).
3. **Consumer loop** — the worker reads from the ``maop:sched:tasks``
   stream's ``maop_workers`` consumer group, up to ``concurrency`` tasks
   at a time. Each task is executed by the configured ``executor``
   callable (default: a no-op that echoes the payload). Results are
   posted via :meth:`DistributedScheduler.post_result` and the task is
   XACKed.
4. **Shutdown** — on SIGINT/SIGTERM the worker stops consuming, drains
   in-flight tasks, unregisters, and exits.

Executor
--------
The ``executor`` parameter is a callable
``async (node_id, payload, affinity) -> (status, output, error)``. The
default :func:`default_executor` simply echoes the payload — useful for
testing and as a placeholder. Production deployments inject an executor
that runs the actual MAOP step logic (e.g. via
:class:`~maop.engine.Engine` or :class:`~maop.maop_loop.MaopLoop`).
"""

from __future__ import annotations

import asyncio
import json
import logging
import signal
import socket
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from maop.core.scheduling.distributed_scheduler import (
    _F_AFFINITY,
    _F_NODE_ID,
    _F_PAYLOAD,
    _F_RUN_ID,
    DistributedScheduler,
    SchedulingError,
)
from maop.core.scheduling.worker_pool import WorkerRegistry

if TYPE_CHECKING:
    import redis  # noqa: F401

logger = logging.getLogger(__name__)

# Default consumer group name (must match the scheduler's group).
_DEFAULT_GROUP = "maop_workers"
# Stream field for the consumer name (used by xreadgroup).
_F_CONSUMER = "consumer"


@dataclass
class WorkerConfig:
    """Configuration for a :class:`DistributedWorker`.

    Attributes
    ----------
    redis_url : str
        Redis connection URL (e.g. ``"redis://localhost:6379/0"``).
    concurrency : int
        Maximum concurrent task executions.
    capabilities : set[str]
        Affinity tags this worker advertises.
    heartbeat_interval : float
        Seconds between heartbeat refreshes.
    poll_timeout_ms : int
        Milliseconds to block on xreadgroup when no tasks are available.
    worker_id : str
        Optional explicit id (auto-generated when empty).
    """

    redis_url: str = "redis://localhost:6379/0"
    concurrency: int = 4
    capabilities: set[str] = field(default_factory=set)
    heartbeat_interval: float = 5.0
    poll_timeout_ms: int = 1000
    worker_id: str = ""


@dataclass
class TaskResult:
    """Result of executing a single task.

    Attributes
    ----------
    node_id : str
        The DAG node that was executed.
    status : str
        ``"success"`` | ``"failed"``.
    output : Any
        The executor's output (JSON-serialisable).
    error : str
        Error message on failure (empty on success).
    duration_ms : int
        Execution duration in milliseconds.
    """

    node_id: str
    status: str = "success"
    output: Any = None
    error: str = ""
    duration_ms: int = 0


# Type alias for the executor callable.
ExecutorFn = Callable[
    [str, dict[str, Any], set[str]],
    Awaitable[TaskResult],
]


async def default_executor(
    node_id: str,
    payload: dict[str, Any],
    affinity: set[str],
) -> TaskResult:
    """Default no-op executor: echoes the payload as output.

    Useful for testing and as a placeholder. Production deployments
    inject an executor that runs the actual MAOP step logic.
    """
    return TaskResult(
        node_id=node_id,
        status="success",
        output={"echo": payload, "node_id": node_id},
        duration_ms=0,
    )


def _decode(v: Any) -> Any:
    """Decode a Redis bytes-or-str value to str."""
    if isinstance(v, bytes):
        return v.decode()
    return v


class DistributedWorker:
    """A distributed worker that consumes and executes tasks from Redis Streams.

    Parameters
    ----------
    redis_client : Any
        A ``redis.Redis`` (or ``fakeredis.FakeRedis``) client.
    scheduler : DistributedScheduler | None
        Scheduler instance (used for ``post_result``). When ``None``, a
        scheduler backed by the same Redis client is created.
    config : WorkerConfig | None
        Worker configuration. When ``None``, defaults are used.
    executor : ExecutorFn | None
        Async callable that executes a task. When ``None``, the
        :func:`default_executor` is used.
    """

    def __init__(
        self,
        redis_client: Any,
        *,
        scheduler: DistributedScheduler | None = None,
        config: WorkerConfig | None = None,
        executor: ExecutorFn | None = None,
    ) -> None:
        self._redis = redis_client
        self._config = config or WorkerConfig()
        self._scheduler = scheduler or DistributedScheduler(redis_client)
        self._registry: WorkerRegistry = self._scheduler.registry
        self._executor: ExecutorFn = executor or default_executor
        self._worker_id: str = ""
        self._consumer_name: str = ""
        self._running = False
        self._in_flight: set[str] = set()
        self._stop_event = asyncio.Event()
        # Background tasks.
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._consumer_task: asyncio.Task[None] | None = None

    # ── Lifecycle ────────────────────────────────────────────────

    async def start(self) -> str:
        """Register the worker and start heartbeat + consumer loops.

        Returns the assigned ``worker_id``.
        """
        if self._running:
            return self._worker_id
        self._worker_id = self._registry.register(
            host=socket.gethostname(),
            concurrency=self._config.concurrency,
            capabilities=self._config.capabilities,
            worker_id=self._config.worker_id,
        )
        self._consumer_name = f"consumer-{self._worker_id}"
        self._running = True
        self._stop_event.clear()
        # Start background loops.
        self._heartbeat_task = asyncio.ensure_future(self._heartbeat_loop())
        self._consumer_task = asyncio.ensure_future(self._consumer_loop())
        logger.info(
            "[dist-worker] %s started (concurrency=%d, caps=%s)",
            self._worker_id, self._config.concurrency, sorted(self._config.capabilities),
        )
        return self._worker_id

    async def stop(self) -> None:
        """Stop consuming, drain in-flight tasks, and unregister."""
        if not self._running:
            return
        self._running = False
        self._stop_event.set()
        # Cancel background loops.
        for task in (self._heartbeat_task, self._consumer_task):
            if task is not None and not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception) as exc:
                    logger.debug("distributed_worker: await stopped task raised: %s", exc)
        # Wait briefly for in-flight tasks to drain (best-effort).
        if self._in_flight:
            logger.info(
                "[dist-worker] %s draining %d in-flight tasks",
                self._worker_id, len(self._in_flight),
            )
            # Give in-flight tasks up to 5s to complete.
            deadline = time.monotonic() + 5.0
            while self._in_flight and time.monotonic() < deadline:
                await asyncio.sleep(0.1)
        # Unregister (graceful).
        self._registry.unregister(self._worker_id)
        logger.info("[dist-worker] %s stopped", self._worker_id)

    # ── Heartbeat loop ───────────────────────────────────────────

    async def _heartbeat_loop(self) -> None:
        """Refresh the worker's heartbeat periodically."""
        interval = self._config.heartbeat_interval
        while not self._stop_event.is_set():
            try:
                self._registry.heartbeat(self._worker_id)
            except Exception as exc:
                logger.warning("[dist-worker] %s heartbeat failed: %s", self._worker_id, exc)
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=interval)
            except asyncio.TimeoutError:
                continue
            else:
                return

    # ── Consumer loop ────────────────────────────────────────────

    async def _consumer_loop(self) -> None:
        """Consume tasks from the stream and execute them."""
        stream = self._scheduler.task_stream
        group = _DEFAULT_GROUP
        # Concurrency is enforced by a semaphore.
        sem = asyncio.Semaphore(self._config.concurrency)
        # Poll interval derived from poll_timeout_ms (converted to seconds).
        # We use non-blocking xreadgroup (block=None) and sleep between
        # polls to avoid blocking the asyncio event loop (fakeredis is
        # synchronous and would stall the loop if we used a blocking
        # read with a timeout).
        poll_interval = max(0.01, self._config.poll_timeout_ms / 1000.0)
        while not self._stop_event.is_set():
            try:
                # Read up to (concurrency - in_flight) tasks (non-blocking).
                available = self._config.concurrency - len(self._in_flight)
                if available <= 0:
                    await asyncio.sleep(0.05)
                    continue
                entries = self._redis.xreadgroup(
                    group,
                    self._consumer_name,
                    {stream: ">"},
                    count=available,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.debug("[dist-worker] %s xreadgroup error: %s", self._worker_id, exc)
                await asyncio.sleep(0.5)
                continue
            if not entries:
                # No new tasks; yield to the event loop before retrying.
                if self._stop_event.is_set():
                    return
                await asyncio.sleep(poll_interval)
                continue
            for _stream, msgs in entries:
                for msg_id, fields in msgs:
                    asyncio.ensure_future(
                        self._handle_task(stream, group, msg_id, fields, sem),
                    )

    async def _handle_task(
        self,
        stream: str,
        group: str,
        msg_id: Any,
        fields: dict[Any, Any],
        sem: asyncio.Semaphore,
    ) -> None:
        """Execute a single task and post its result."""
        msg_id_str = _decode(msg_id)
        node_id = _decode(fields.get(_F_NODE_ID.encode(), fields.get(_F_NODE_ID, b"")))
        run_id = _decode(fields.get(_F_RUN_ID.encode(), fields.get(_F_RUN_ID, b"")))
        affinity_raw = _decode(
            fields.get(_F_AFFINITY.encode(), fields.get(_F_AFFINITY, b"")),
        )
        affinity = {t for t in affinity_raw.split(",") if t}
        payload_raw = _decode(fields.get(_F_PAYLOAD.encode(), fields.get(_F_PAYLOAD, b"")))
        try:
            payload: dict[str, Any] = json.loads(payload_raw) if payload_raw else {}
        except (json.JSONDecodeError, TypeError):
            payload = {"raw": payload_raw}

        self._in_flight.add(msg_id_str)
        self._registry.assign_task(self._worker_id, msg_id_str)
        async with sem:
            start = time.monotonic()
            try:
                result = await self._executor(node_id, payload, affinity)
                status = result.status
                output = result.output
                error = result.error
            except Exception as exc:
                status = "failed"
                output = None
                error = f"{type(exc).__name__}: {exc}"
                logger.warning(
                    "[dist-worker] %s task %s (node %s) failed: %s",
                    self._worker_id, msg_id_str, node_id, exc,
                )
            duration_ms = int((time.monotonic() - start) * 1000)
        # Post result to the run's results stream.
        try:
            self._scheduler.post_result(
                run_id,
                node_id,
                status=status,
                output=output,
                error=error,
                worker_id=self._worker_id,
                duration_ms=duration_ms,
            )
        except Exception as exc:
            logger.error(
                "[dist-worker] %s failed to post result for node %s: %s",
                self._worker_id, node_id, exc,
            )
        # ACK the task so it is not redelivered.
        try:
            self._redis.xack(stream, group, msg_id)
        except Exception as exc:
            logger.debug("[dist-worker] %s xack error: %s", self._worker_id, exc)
        self._in_flight.discard(msg_id_str)
        self._registry.complete_task(self._worker_id, msg_id_str)

    # ── Introspection ────────────────────────────────────────────

    @property
    def worker_id(self) -> str:
        return self._worker_id

    @property
    def is_running(self) -> bool:
        return self._running

    def in_flight_count(self) -> int:
        """Return the number of tasks currently being executed."""
        return len(self._in_flight)

    def __repr__(self) -> str:
        return (
            f"DistributedWorker(id={self._worker_id!r}, "
            f"concurrency={self._config.concurrency}, running={self._running})"
        )


# ── CLI entry point ──────────────────────────────────────────────────

def run_worker(
    redis_url: str = "redis://localhost:6379/0",
    *,
    concurrency: int = 4,
    capabilities: set[str] | None = None,
    heartbeat_interval: float = 5.0,
    executor: ExecutorFn | None = None,
) -> None:
    """Start a distributed worker and run until SIGINT/SIGTERM.

    This is the entry point invoked by ``maop worker start``. It creates
    a Redis client, builds a :class:`DistributedWorker`, and runs its
    consumer loop until interrupted.
    """
    import redis as _redis_mod

    client = _redis_mod.from_url(redis_url)
    # Verify connection.
    try:
        client.ping()
    except Exception as exc:
        raise SchedulingError(f"Cannot connect to Redis at {redis_url}: {exc}") from exc

    config = WorkerConfig(
        redis_url=redis_url,
        concurrency=concurrency,
        capabilities=capabilities or set(),
        heartbeat_interval=heartbeat_interval,
    )
    worker = DistributedWorker(client, config=config, executor=executor)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    # Signal handlers for graceful shutdown.
    def _signal_handler(signum: int, frame: object) -> None:
        logger.info("[dist-worker] received signal %s, shutting down...", signum)
        loop.call_soon_threadsafe(
            lambda: asyncio.ensure_future(worker.stop()),
        )

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _signal_handler)
        except (ValueError, OSError):
            # Not in main thread (e.g. under test) — skip.
            pass

    try:
        loop.run_until_complete(worker.start())
        # Run until stop_event is set (via signal handler) or Ctrl+C.
        loop.run_until_complete(_wait_for_stop(worker))
    except KeyboardInterrupt:
        pass
    finally:
        loop.run_until_complete(worker.stop())
        loop.close()
        try:
            client.close()
        except Exception:
            logger.debug('swallowed exception', exc_info=True)


async def _wait_for_stop(worker: DistributedWorker) -> None:
    """Block until the worker's stop event is set (via signal handler)."""
    # Poll is_running; the signal handler calls worker.stop() which sets it False.
    while worker.is_running:
        await asyncio.sleep(0.5)