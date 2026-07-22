"""Tests for MAOP.concurrency — TaskPool, TaskQueue, SSEStreamer, TokenStreamer."""

from __future__ import annotations

import asyncio

import pytest

from maop.concurrency import (
    Priority,
    SSEEvent,
    SSEStreamer,
    Task,
    TaskPool,
    TaskQueue,
    TaskStatus,
    TokenStreamer,
)


# ── Helpers ───────────────────────────────────────────────────

def _run(coro):
    """Run a coroutine to completion in a fresh event loop."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ── Enum/Model tests ──────────────────────────────────────────

class TestEnums:
    def test_priority_ordering(self):
        assert Priority.CRITICAL < Priority.HIGH < Priority.NORMAL < Priority.LOW < Priority.BACKGROUND

    def test_task_status_values(self):
        assert TaskStatus.PENDING == "pending"
        assert TaskStatus.SUCCESS == "success"
        assert TaskStatus.FAILED == "failed"

    def test_task_defaults(self):
        t = Task()
        assert t.priority == Priority.NORMAL
        assert t.status == TaskStatus.PENDING
        assert t.progress == 0.0
        assert len(t.id) == 12

    def test_sse_event_defaults(self):
        e = SSEEvent()
        assert e.event == "message"
        assert e.retry == 0


# ── TaskQueue ─────────────────────────────────────────────────

class TestTaskQueue:
    def test_put_and_get(self):
        async def _test():
            q = TaskQueue()
            t = Task(name="job1")
            await q.put(t)
            assert q.qsize() == 1
            assert not q.empty()
            got = await q.get()
            assert got.id == t.id
            assert q.empty()
        _run(_test())

    def test_priority_ordering(self):
        async def _test():
            q = TaskQueue()
            low = Task(name="low", priority=Priority.LOW)
            high = Task(name="high", priority=Priority.HIGH)
            normal = Task(name="normal", priority=Priority.NORMAL)
            await q.put(low)
            await q.put(high)
            await q.put(normal)
            first = await q.get()
            second = await q.get()
            third = await q.get()
            assert first.name == "high"
            assert second.name == "normal"
            assert third.name == "low"
        _run(_test())

    def test_empty_queue(self):
        q = TaskQueue()
        assert q.empty() is True
        assert q.qsize() == 0

    def test_qsize_after_puts(self):
        async def _test():
            q = TaskQueue()
            for i in range(5):
                await q.put(Task(name=f"job{i}"))
            assert q.qsize() == 5
        _run(_test())

    def test_max_size_backpressure(self):
        async def _test():
            q = TaskQueue(max_size=2)
            await q.put(Task(name="a"))
            await q.put(Task(name="b"))
            assert q.qsize() == 2
            # Getting one should allow another put
            await q.get()
            await q.put(Task(name="c"))
            assert q.qsize() == 2
        _run(_test())


# ── TaskPool ──────────────────────────────────────────────────

class TestTaskPool:
    def test_init_defaults(self):
        pool = TaskPool()
        assert pool.active_count == 0
        assert pool.pending_count == 0

    def test_submit_and_wait(self):
        async def _test():
            pool = TaskPool(max_workers=2)

            async def executor(task):
                await asyncio.sleep(0.01)
                return f"result_{task.name}"

            task = Task(name="job1")
            tid = await pool.submit(task, executor)
            result = await pool.wait(tid, timeout=5)
            assert result == "result_job1"
            await pool.stop()
        _run(_test())

    def test_submit_priority_queue_order(self):
        """High-priority tasks are dequeued before low-priority tasks."""
        async def _test():
            pool = TaskPool(max_workers=1)
            gate = asyncio.Event()
            execution_order: list[str] = []

            async def executor(task):
                await gate.wait()
                await asyncio.sleep(0.01)
                execution_order.append(task.name)
                return task.name

            t_low = Task(name="low", priority=Priority.LOW)
            t_high = Task(name="high", priority=Priority.HIGH)
            t_normal = Task(name="normal", priority=Priority.NORMAL)

            await pool.submit(t_low, executor)
            await asyncio.sleep(0.02)  # Worker picks up t_low, blocks on gate
            await pool.submit(t_high, executor)
            await pool.submit(t_normal, executor)

            # Queue should have high before normal (low is running)
            assert pool._queue.qsize() == 2
            assert pool._queue._queue[0].name == "high"
            assert pool._queue._queue[1].name == "normal"

            gate.set()
            await pool.wait(t_low.id, timeout=5)
            await pool.wait(t_high.id, timeout=5)
            await pool.wait(t_normal.id, timeout=5)
            await pool.stop()

            # Execution order: low (first, blocked), then high (priority), then normal
            assert execution_order == ["low", "high", "normal"]
        _run(_test())

    def test_wait_nonexistent_raises(self):
        async def _test():
            pool = TaskPool()
            with pytest.raises(KeyError):
                await pool.wait("nonexistent")
        _run(_test())

    def test_get_task(self):
        async def _test():
            pool = TaskPool()

            async def executor(task):
                return "ok"

            task = Task(name="job1")
            await pool.submit(task, executor)
            got = pool.get_task(task.id)
            assert got is not None
            assert got.name == "job1"
            assert pool.get_task("nope") is None
            await pool.stop()
        _run(_test())

    def test_all_tasks(self):
        async def _test():
            pool = TaskPool()

            async def executor(task):
                return "ok"

            t1 = Task(name="a")
            t2 = Task(name="b")
            await pool.submit(t1, executor)
            await pool.submit(t2, executor)
            all_t = pool.all_tasks()
            assert len(all_t) == 2
            await pool.stop()
        _run(_test())

    def test_executor_exception(self):
        async def _test():
            pool = TaskPool()

            async def executor(task):
                raise ValueError("boom")

            task = Task(name="err")
            await pool.submit(task, executor)
            with pytest.raises(ValueError, match="boom"):
                await pool.wait(task.id, timeout=5)
            await pool.stop()
        _run(_test())

    def test_start_stop(self):
        async def _test():
            pool = TaskPool()
            await pool.start()
            assert pool._running is True
            await pool.stop()
            assert pool._running is False
        _run(_test())

    def test_wait_with_timeout_exceeded(self):
        async def _test():
            pool = TaskPool()

            async def executor(task):
                await asyncio.sleep(10)
                return "done"

            task = Task(name="slow")
            await pool.submit(task, executor)
            with pytest.raises(asyncio.TimeoutError):
                await pool.wait(task.id, timeout=0.05)
            await pool.stop()
        _run(_test())


# ── SSEStreamer ───────────────────────────────────────────────

class TestSSEStreamer:
    def test_send_increments_count(self):
        streamer = SSEStreamer()
        streamer.send(event="progress", data="50%")
        streamer.send(event="progress", data="100%")
        assert streamer.event_count == 2

    def test_send_json(self):
        streamer = SSEStreamer()
        streamer.send_json(event="data", key1="val1", key2=42)
        assert streamer.event_count == 1

    def test_stream_yields_events(self):
        async def _test():
            streamer = SSEStreamer()
            streamer.send(event="progress", data="50%")
            streamer.close()
            chunks = []
            async for chunk in streamer.stream():
                chunks.append(chunk)
            assert len(chunks) == 2
            assert "event: progress" in chunks[0]
            assert "data: 50%" in chunks[0]
            assert "event: close" in chunks[1]
        _run(_test())

    def test_stream_with_id_and_retry(self):
        async def _test():
            streamer = SSEStreamer()
            sse = SSEEvent(event="msg", data="hello", id="abc", retry=5000)
            streamer._queue.put_nowait(sse)
            streamer.close()
            chunks = []
            async for chunk in streamer.stream():
                chunks.append(chunk)
            assert "id: abc" in chunks[0]
            assert "retry: 5000" in chunks[0]
        _run(_test())

    def test_close_signals_end(self):
        async def _test():
            streamer = SSEStreamer()
            streamer.close()
            chunks = []
            async for chunk in streamer.stream():
                chunks.append(chunk)
            assert len(chunks) == 1
            assert "close" in chunks[0]
        _run(_test())


# ── TokenStreamer ─────────────────────────────────────────────

class TestTokenStreamer:
    def test_push_token_increments_count(self):
        ts = TokenStreamer()
        ts.push_token("Hello")
        ts.push_token(" world")
        assert ts.token_count == 2
        assert ts.total_chars == 11

    def test_push_tokens_batch(self):
        ts = TokenStreamer()
        ts.push_tokens(["a", "b", "c"])
        assert ts.token_count == 3
        assert ts.total_chars == 3

    def test_end_sets_flag(self):
        ts = TokenStreamer()
        ts.push_token("x")
        ts.end()
        assert ts.is_ended is True

    def test_token_stream(self):
        async def _test():
            ts = TokenStreamer()
            ts.push_token("Hello")
            ts.push_token(" world")
            ts.end()
            chunks = []
            async for chunk in ts.token_stream():
                chunks.append(chunk)
            assert len(chunks) == 3
            assert "event: token" in chunks[0]
            assert "Hello" in chunks[0]
            assert "event: token_end" in chunks[2]
        _run(_test())

    def test_text_stream(self):
        async def _test():
            ts = TokenStreamer()
            ts.push_tokens(["foo", "bar", "baz"])
            ts.end()
            tokens = []
            async for tok in ts.text_stream():
                tokens.append(tok)
            assert tokens == ["foo", "bar", "baz"]
        _run(_test())

    def test_tokens_per_second_zero_initially(self):
        ts = TokenStreamer()
        assert ts.tokens_per_second == 0.0

    def test_tokens_per_second_positive(self):
        ts = TokenStreamer()
        ts.push_token("hello")
        assert ts.tokens_per_second > 0

    def test_buffer_overflow_drops_oldest(self):
        ts = TokenStreamer(max_buffer=2)
        ts.push_token("a")
        ts.push_token("b")
        ts.push_token("c")  # should drop "a"
        assert ts.token_count == 3
        assert ts.total_chars == 3
