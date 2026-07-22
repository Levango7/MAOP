"""MAOP Concurrency — Async task pool with priority queue and streaming SSE.

Provides:
  - TaskPool: Bounded async task pool with priority scheduling
  - SSEStreamer: Server-Sent Events streaming for live progress
  - TaskQueue: Priority-based task queue with backpressure
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from enum import Enum, IntEnum
from typing import Any, AsyncIterator, Callable

from pydantic import BaseModel, Field


# ── Task priority ─────────────────────────────────────────────

class Priority(IntEnum):
    CRITICAL = 0
    HIGH = 1
    NORMAL = 2
    LOW = 3
    BACKGROUND = 4


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    CANCELLED = "cancelled"


# ── Task model ────────────────────────────────────────────────

class Task(BaseModel):
    """A unit of work in the task pool."""
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    name: str = ""
    priority: int = Priority.NORMAL
    status: str = TaskStatus.PENDING
    result: Any = None
    error: str = ""
    created_at: float = Field(default_factory=time.time)
    started_at: float = 0.0
    finished_at: float = 0.0
    progress: float = 0.0  # 0.0 to 1.0
    metadata: dict[str, Any] = Field(default_factory=dict)


# ── Priority Queue ────────────────────────────────────────────

class TaskQueue:
    """Priority-based async task queue with backpressure.

    Usage::

        queue = TaskQueue(max_size=100)
        await queue.put(task)
        task = await queue.get()
    """

    def __init__(self, max_size: int = 0) -> None:
        self._max_size = max_size
        self._queue: list[Task] = []
        self._not_empty = asyncio.Event()
        self._not_full = asyncio.Event()
        self._not_full.set()
        self._lock = asyncio.Lock()

    async def put(self, task: Task) -> None:
        """Add a task to the queue (sorted by priority)."""
        while True:
            async with self._lock:
                if self._max_size == 0 or len(self._queue) < self._max_size:
                    self._queue.append(task)
                    self._queue.sort(key=lambda t: t.priority)
                    self._not_empty.set()
                    return
                # Queue full — clear not_full before releasing lock
                self._not_full.clear()
            # Wait outside lock to avoid deadlock
            await self._not_full.wait()

    async def get(self) -> Task:
        """Get the highest-priority task."""
        while True:
            async with self._lock:
                if self._queue:
                    task = self._queue.pop(0)
                    if self._max_size > 0:
                        self._not_full.set()
                    return task
                # Queue empty — clear not_empty before releasing lock
                self._not_empty.clear()
            # Wait outside lock to avoid deadlock
            await self._not_empty.wait()

    def qsize(self) -> int:
        return len(self._queue)

    def empty(self) -> bool:
        return len(self._queue) == 0


# ── Task Pool ─────────────────────────────────────────────────

class TaskPool:
    """Bounded async task pool with priority scheduling.

    Usage::

        pool = TaskPool(max_workers=4)

        async def my_work(task):
            await asyncio.sleep(1)
            return "done"

        task = Task(name="job1", priority=Priority.HIGH)
        await pool.submit(task, my_work)
        result = await pool.wait(task.id)
    """

    def __init__(self, max_workers: int = 4, max_queue_size: int = 100) -> None:
        self._max_workers = max_workers
        self._queue = TaskQueue(max_size=max_queue_size)
        self._tasks: dict[str, Task] = {}
        self._results: dict[str, Any] = {}
        self._futures: dict[str, asyncio.Future] = {}
        self._executors: dict[str, Callable[[Task], Any]] = {}
        self._workers: list[asyncio.Task] = []
        self._running = False
        self._active_count = 0

    @property
    def active_count(self) -> int:
        return self._active_count

    @property
    def pending_count(self) -> int:
        return self._queue.qsize()

    async def start(self) -> None:
        """Start the worker pool."""
        if self._running:
            return
        self._running = True
        for _ in range(self._max_workers):
            self._workers.append(asyncio.ensure_future(self._worker()))

    async def stop(self) -> None:
        """Stop the worker pool."""
        self._running = False
        for w in self._workers:
            if not w.done():
                w.cancel()
        if self._workers:
            await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()
        for fut in self._futures.values():
            if not fut.done():
                fut.cancel()

    async def _worker(self) -> None:
        """Worker loop: consume tasks from priority queue and execute."""
        while self._running:
            try:
                task = await self._queue.get()
            except asyncio.CancelledError:
                break
            executor = self._executors.get(task.id)
            if executor is None:
                continue
            await self._execute(task, executor)

    async def submit(
        self,
        task: Task,
        executor: Callable[[Task], Any],
    ) -> str:
        """Submit a task for execution.

        Parameters
        ----------
        task : Task
            Task descriptor.
        executor : Callable[[Task], Any]
            Async function to execute.

        Returns
        -------
        str
            Task ID.
        """
        self._tasks[task.id] = task
        self._futures[task.id] = asyncio.get_running_loop().create_future()
        self._executors[task.id] = executor
        if not self._running:
            await self.start()
        await self._queue.put(task)
        return task.id

    async def _execute(self, task: Task, executor: Callable[[Task], Any]) -> None:
        """Execute a task."""
        task.status = TaskStatus.RUNNING
        task.started_at = time.time()
        self._active_count += 1

        try:
            result = await executor(task)
            task.status = TaskStatus.SUCCESS
            task.result = result
            self._results[task.id] = result
            if not self._futures[task.id].done():
                self._futures[task.id].set_result(result)
        except asyncio.CancelledError:
            task.status = TaskStatus.CANCELLED
            if not self._futures[task.id].done():
                self._futures[task.id].cancel()
        except Exception as exc:
            task.status = TaskStatus.FAILED
            task.error = str(exc)
            if not self._futures[task.id].done():
                self._futures[task.id].set_exception(exc)
        finally:
            task.finished_at = time.time()
            self._active_count -= 1

    async def wait(self, task_id: str, timeout: float = 0) -> Any:
        """Wait for a task to complete and return its result."""
        fut = self._futures.get(task_id)
        if fut is None:
            raise KeyError(f"Task {task_id} not found")
        if timeout > 0:
            return await asyncio.wait_for(fut, timeout=timeout)
        return await fut

    def get_task(self, task_id: str) -> Task | None:
        return self._tasks.get(task_id)

    def all_tasks(self) -> list[Task]:
        return list(self._tasks.values())


# ── SSE Streamer ──────────────────────────────────────────────

class SSEEvent(BaseModel):
    """A single Server-Sent Event."""
    event: str = "message"
    data: str = ""
    id: str = ""
    retry: int = 0


class SSEStreamer:
    """Server-Sent Events streamer for live progress updates.

    Usage::

        streamer = SSEStreamer()
        streamer.send(event="progress", data="50%")
        async for chunk in streamer.stream():
            yield chunk
    """

    def __init__(self, max_buffer: int = 100) -> None:
        self._queue: asyncio.Queue[SSEEvent | None] = asyncio.Queue(maxsize=max_buffer)
        self._event_count = 0

    def send(self, event: str = "message", data: str = "", event_id: str = "") -> None:
        """Send an SSE event (non-blocking)."""
        sse = SSEEvent(event=event, data=data, id=event_id or str(self._event_count))
        self._event_count += 1
        try:
            self._queue.put_nowait(sse)
        except asyncio.QueueFull:
            pass  # Drop oldest under backpressure

    def send_json(self, event: str = "message", **data: Any) -> None:
        """Send a JSON-encoded SSE event."""
        self.send(event=event, data=json.dumps(data, ensure_ascii=False, default=str))

    def close(self) -> None:
        """Signal end of stream."""
        try:
            self._queue.put_nowait(None)
        except asyncio.QueueFull:
            pass

    async def stream(self) -> AsyncIterator[str]:
        """Yield SSE-formatted chunks."""
        while True:
            sse = await self._queue.get()
            if sse is None:
                yield "event: close\ndata: \n\n"
                break
            lines = []
            if sse.event:
                lines.append(f"event: {sse.event}")
            if sse.data:
                lines.append(f"data: {sse.data}")
            if sse.id:
                lines.append(f"id: {sse.id}")
            if sse.retry:
                lines.append(f"retry: {sse.retry}")
            lines.append("")
            lines.append("")
            yield "\n".join(lines)

    @property
    def event_count(self) -> int:
        return self._event_count


# ── Token-level Streaming (P1-3) ──────────────────────────────

class TokenStreamer:
    """Token-by-token streaming for LLM inference output.

    Unlike SSEStreamer which sends whole events, TokenStreamer pushes
    individual tokens as they are generated, enabling real-time
    progressive rendering in the client.

    Usage::

        streamer = TokenStreamer()
        # In LLM callback:
        streamer.push_token("Hello")
        streamer.push_token(" world")
        streamer.end()

        # In FastAPI endpoint:
        async for chunk in streamer.token_stream():
            yield chunk
    """

    def __init__(self, max_buffer: int = 1000) -> None:
        self._queue: asyncio.Queue[str | None] = asyncio.Queue(maxsize=max_buffer)
        self._token_count = 0
        self._total_chars = 0
        self._started_at: float = 0.0
        self._ended = False

    def push_token(self, token: str) -> None:
        """Push a single token into the stream (non-blocking)."""
        if self._token_count == 0:
            self._started_at = time.perf_counter()
        self._token_count += 1
        self._total_chars += len(token)
        try:
            self._queue.put_nowait(token)
        except asyncio.QueueFull:
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                self._queue.put_nowait(token)
            except asyncio.QueueFull:
                pass

    def push_tokens(self, tokens: list[str]) -> None:
        """Push multiple tokens at once."""
        for t in tokens:
            self.push_token(t)

    def end(self) -> None:
        """Signal end of token stream."""
        self._ended = True
        try:
            self._queue.put_nowait(None)
        except asyncio.QueueFull:
            pass

    async def token_stream(self) -> AsyncIterator[str]:
        """Yield individual tokens as SSE data chunks."""
        while True:
            token = await self._queue.get()
            if token is None:
                yield "event: token_end\ndata: \n\n"
                break
            yield f"event: token\ndata: {json.dumps(token, ensure_ascii=False)}\n\n"

    async def text_stream(self) -> AsyncIterator[str]:
        """Yield raw text tokens (without SSE framing)."""
        while True:
            token = await self._queue.get()
            if token is None:
                break
            yield token

    @property
    def token_count(self) -> int:
        return self._token_count

    @property
    def total_chars(self) -> int:
        return self._total_chars

    @property
    def tokens_per_second(self) -> float:
        """Current throughput in tokens/second."""
        if self._started_at == 0 or self._token_count == 0:
            return 0.0
        elapsed = time.perf_counter() - self._started_at
        return self._token_count / elapsed if elapsed > 0 else 0.0

    @property
    def is_ended(self) -> bool:
        return self._ended
