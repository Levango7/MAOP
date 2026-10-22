"""Core-path smoke tests for dashboard server creation and routing."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from maop.dashboard.server import app as _app


def test_app_is_valid_fastapi():
    """Module-level app is a valid FastAPI instance."""
    assert _app is not None
    assert hasattr(_app, "router")


def test_routes_registered():
    """At least a handful of routes are registered."""
    paths = [r.path for r in _app.routes if hasattr(r, "path")]
    assert len(paths) > 3, f"Expected >3 routes, got {len(paths)}"


def test_lifespan_defined():
    """Lifespan handler is accessible."""
    assert hasattr(_app.router, "lifespan_context") or hasattr(_app.router, "lifespan")


# ── 关停期排空（lifespan 的 finally 走的就是它）──────────────────
import asyncio

import pytest

from maop.dashboard.server import drain_cancelled


@pytest.mark.asyncio
async def test_drain_cancels_and_awaits_a_pending_task():
    """一个正在 await 的任务：取消要真的落地，函数还要等它结束。

    只 cancel 不 await 的话，事件循环在关停之后才收尾，日志里会冒出
    "Task was destroyed but it is pending"，而且 MCP 传输的清理顺序不可控。
    """
    started = asyncio.Event()
    forever = asyncio.Event()

    async def _busy():
        started.set()
        await forever.wait()

    task = asyncio.create_task(_busy())
    await started.wait()
    assert await drain_cancelled(task) == 1
    assert task.done() and task.cancelled()


@pytest.mark.asyncio
async def test_drain_skips_none_and_finished_tasks():
    async def _quick():
        return 1

    done_task = asyncio.create_task(_quick())
    await done_task
    assert await drain_cancelled(None, done_task) == 0
    assert not done_task.cancelled()


@pytest.mark.asyncio
async def test_drain_handles_several_tasks_at_once():
    async def _wait_forever():
        await asyncio.Event().wait()

    a = asyncio.create_task(_wait_forever())
    b = asyncio.create_task(_wait_forever())
    await asyncio.sleep(0)  # 让两个任务真的开始运行，否则 done() 判定不可靠
    assert await drain_cancelled(a, b) == 2
    assert a.cancelled() and b.cancelled()