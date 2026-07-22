"""Tests for Phase 6: concurrency — TaskPool, TaskQueue, SSEStreamer."""

import asyncio
import json

import pytest

from maop.concurrency import (
    Priority, TaskStatus, Task, TaskQueue, TaskPool, SSEStreamer,
)


# ═══════════════════════════════════════════════════════════════
# TaskQueue tests
# ═══════════════════════════════════════════════════════════════

class TestTaskQueue:
    """Test priority task queue."""

    def test_put_get(self):
        async def _test():
            q = TaskQueue()
            t = Task(name="job1", priority=Priority.NORMAL)
            await q.put(t)
            assert q.qsize() == 1
            got = await q.get()
            assert got.id == t.id
            assert q.empty()
        asyncio.run(_test())

    def test_priority_ordering(self):
        async def _test():
            q = TaskQueue()
            low = Task(name="low", priority=Priority.LOW)
            high = Task(name="high", priority=Priority.HIGH)
            normal = Task(name="normal", priority=Priority.NORMAL)
            await q.put(low)
            await q.put(high)
            await q.put(normal)
            # Should get high first
            first = await q.get()
            assert first.priority == Priority.HIGH
            second = await q.get()
            assert second.priority == Priority.NORMAL
            third = await q.get()
            assert third.priority == Priority.LOW
        asyncio.run(_test())

    def test_empty_queue(self):
        async def _test():
            q = TaskQueue()
            assert q.empty()
            assert q.qsize() == 0
        asyncio.run(_test())

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
        asyncio.run(_test())


# ═══════════════════════════════════════════════════════════════
# TaskPool tests
# ═══════════════════════════════════════════════════════════════

class TestTaskPool:
    """Test bounded async task pool."""

    def test_submit_and_wait(self):
        async def _test():
            pool = TaskPool(max_workers=2)
            await pool.start()

            async def my_executor(task):
                return f"result-{task.name}"

            task = Task(name="job1")
            tid = await pool.submit(task, my_executor)
            result = await pool.wait(tid, timeout=5)
            assert result == "result-job1"

            await pool.stop()
        asyncio.run(_test())

    def test_multiple_tasks(self):
        async def _test():
            pool = TaskPool(max_workers=4)
            await pool.start()

            async def my_executor(task):
                await asyncio.sleep(0.05)
                return task.name

            ids = []
            for i in range(5):
                t = Task(name=f"job{i}")
                tid = await pool.submit(t, my_executor)
                ids.append(tid)

            results = []
            for tid in ids:
                r = await pool.wait(tid, timeout=5)
                results.append(r)

            assert len(results) == 5
            assert pool.active_count == 0
            await pool.stop()
        asyncio.run(_test())

    def test_task_failure(self):
        async def _test():
            pool = TaskPool(max_workers=2)
            await pool.start()

            async def failing_executor(task):
                raise ValueError("deliberate error")

            task = Task(name="fail-job")
            tid = await pool.submit(task, failing_executor)

            with pytest.raises(ValueError, match="deliberate error"):
                await pool.wait(tid, timeout=5)

            t = pool.get_task(tid)
            assert t.status == TaskStatus.FAILED
            assert "deliberate error" in t.error
            await pool.stop()
        asyncio.run(_test())

    def test_concurrency_limit(self):
        async def _test():
            pool = TaskPool(max_workers=2)
            await pool.start()

            max_concurrent = 0
            current = 0

            async def slow_executor(task):
                nonlocal max_concurrent, current
                current += 1
                max_concurrent = max(max_concurrent, current)
                await asyncio.sleep(0.1)
                current -= 1
                return "done"

            ids = []
            for i in range(6):
                t = Task(name=f"job{i}")
                tid = await pool.submit(t, slow_executor)
                ids.append(tid)

            for tid in ids:
                await pool.wait(tid, timeout=10)

            # max_concurrent should not exceed max_workers
            assert max_concurrent <= 2
            await pool.stop()
        asyncio.run(_test())

    def test_get_task(self):
        async def _test():
            pool = TaskPool()
            await pool.start()

            async def exec_fn(task):
                return "ok"

            t = Task(name="lookup")
            tid = await pool.submit(t, exec_fn)
            found = pool.get_task(tid)
            assert found is not None
            assert found.name == "lookup"

            assert pool.get_task("nonexistent") is None
            await pool.stop()
        asyncio.run(_test())

    def test_all_tasks(self):
        async def _test():
            pool = TaskPool()
            await pool.start()

            async def exec_fn(task):
                await asyncio.sleep(0.01)
                return "ok"

            for i in range(3):
                await pool.submit(Task(name=f"t{i}"), exec_fn)

            # Wait a bit for completion
            await asyncio.sleep(0.1)
            all_t = pool.all_tasks()
            assert len(all_t) == 3
            await pool.stop()
        asyncio.run(_test())


# ═══════════════════════════════════════════════════════════════
# SSEStreamer tests
# ═══════════════════════════════════════════════════════════════

class TestSSEStreamer:
    """Test Server-Sent Events streamer."""

    def test_send_and_stream(self):
        async def _test():
            streamer = SSEStreamer()
            streamer.send(event="progress", data="50%")

            # Read one event
            chunks = []
            async for chunk in streamer.stream():
                chunks.append(chunk)
                streamer.close()  # Signal end after first event
                break

            assert len(chunks) == 1
            assert "event: progress" in chunks[0]
            assert "data: 50%" in chunks[0]
        asyncio.run(_test())

    def test_send_json(self):
        async def _test():
            streamer = SSEStreamer()
            streamer.send_json(event="status", task="job1", progress=0.5)

            chunks = []
            async for chunk in streamer.stream():
                chunks.append(chunk)
                streamer.close()
                break

            assert len(chunks) == 1
            assert "event: status" in chunks[0]
            # Data should be JSON
            for line in chunks[0].split("\n"):
                if line.startswith("data: "):
                    data = json.loads(line[6:])
                    assert data["task"] == "job1"
                    assert data["progress"] == 0.5
        asyncio.run(_test())

    def test_close(self):
        async def _test():
            streamer = SSEStreamer()
            streamer.close()

            chunks = []
            async for chunk in streamer.stream():
                chunks.append(chunk)

            assert len(chunks) == 1
            assert "event: close" in chunks[0]
        asyncio.run(_test())

    def test_multiple_events(self):
        async def _test():
            streamer = SSEStreamer()
            streamer.send(event="start", data="begin")
            streamer.send(event="progress", data="50%")
            streamer.send(event="done", data="end")
            streamer.close()

            events = []
            async for chunk in streamer.stream():
                events.append(chunk)

            # 3 data events + 1 close event
            assert len(events) == 4
            assert "event: start" in events[0]
            assert "event: progress" in events[1]
            assert "event: done" in events[2]
            assert "event: close" in events[3]
        asyncio.run(_test())

    def test_event_count(self):
        streamer = SSEStreamer()
        assert streamer.event_count == 0
        streamer.send(event="test", data="1")
        assert streamer.event_count == 1
        streamer.send(event="test", data="2")
        assert streamer.event_count == 2


# ═══════════════════════════════════════════════════════════════
# Task model tests
# ═══════════════════════════════════════════════════════════════

class TestTask:
    """Test Task model."""

    def test_defaults(self):
        t = Task(name="test")
        assert t.status == TaskStatus.PENDING
        assert t.priority == Priority.NORMAL
        assert t.progress == 0.0
        assert t.id  # Auto-generated

    def test_with_priority(self):
        t = Task(name="urgent", priority=Priority.CRITICAL)
        assert t.priority == Priority.CRITICAL

    def test_metadata(self):
        t = Task(name="meta", metadata={"key": "value"})
        assert t.metadata["key"] == "value"
