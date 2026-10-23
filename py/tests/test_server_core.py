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


# ── 路由遮蔽（静态路径被先注册的参数路径吃掉）───────────────────
def _leaf_routes(routes):
    """Flatten ``include_router`` wrappers into the leaves Starlette matches.

    FastAPI 0.141 keeps each ``include_router`` as a ``_IncludedRouter`` without
    a ``.path``, so ``app.routes`` alone hides the real matching order — which
    is exactly what a shadowing check needs.
    """
    leaves = []
    for route in routes:
        children = getattr(route, "routes", None)
        if children is None:
            inner = getattr(route, "original_router", None)
            children = getattr(inner, "routes", None) if inner is not None else None
        if children:
            leaves.extend(_leaf_routes(children))
        elif getattr(route, "path", None) and getattr(route, "methods", None):
            leaves.append(route)
    return leaves


def _http_methods(route) -> set[str]:
    return set(route.methods or ()) - {"HEAD", "OPTIONS"}


def test_no_static_route_is_shadowed_by_an_earlier_param_route():
    """A concrete path must be served by the route that declares it.

    Found by P1-b: ``GET /api/evolution/ab/list`` was served by
    ``/api/evolution/ab/{cycle_id}`` (that router was included first), so the
    dashboard's A/B experiment list could never be non-empty — and the OpenAPI
    schema still described the *right* shape, because both routes exist in the
    document. Only resolving the URL catches it.
    """
    from starlette.routing import Match

    leaves = _leaf_routes(_app.routes)
    assert len(leaves) > 300, f"flattening lost routes: {len(leaves)}"

    shadowed: list[str] = []
    for index, route in enumerate(leaves):
        if "{" in route.path:
            continue
        for method in sorted(_http_methods(route)):
            scope = {
                "type": "http",
                "path": route.path,
                "method": method,
                "headers": [],
                "query_string": b"",
                "root_path": "",
            }
            for earlier in leaves[:index]:
                match, _child = earlier.matches(scope)
                if match == Match.FULL and method in _http_methods(earlier):
                    if earlier.path != route.path:
                        shadowed.append(f"{method} {route.path} served by {earlier.path}")
                    break

    assert shadowed == []
