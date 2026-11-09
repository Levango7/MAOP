"""Priority-queue dispatch: contention → enqueue → drain by priority (2026-11-09).

Background
----------
``acquire``/``release`` + ``AgentRouter._active_count`` and the whole
``dispatch_priority``/``drain_pending``/``PriorityTaskQueue`` machinery existed,
but **nothing in production ever called them** — ``set_priority_queue`` had zero
callers, so ``RoutingStrategy.PRIORITY`` was a name in an enum and nothing more.

These tests pin the behaviour that makes the queue real:

1. Contention (agent at ``max_concurrent``) enqueues instead of over-dispatching.
2. ``drain_pending`` pulls strictly in ``(priority, deadline, FIFO)`` order.
3. An **expired** task fails fast with ``EXIT_QUEUE_DEADLINE`` — it is *not*
   executed late and reported as success.
4. The queue has a hard cap; overflow is rejected loudly, never silently dropped
   (a silently dropped task leaves its caller waiting on a future that never
   settles).
5. ``_slot_reserved`` prevents the re-entrancy livelock: a drained task must not
   re-acquire (it already owns the slot) and must not re-enqueue.
6. Backward compatibility: an explicitly detached queue (``None``) keeps the
   degrade-and-execute semantics from 2026-11-08.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import patch

import pytest

from maop.core.agent.registry.agent_catalog import (
    AgentCapability,
    AgentCatalog,
    AgentDescriptor,
)
from maop.core.agent.router.agent_router import AgentRouter, set_agent_router
from maop.core.reliability.priority_queue import PriorityTask, PriorityTaskQueue
from maop.delegate.dispatcher import Dispatcher


def _solo_catalog(name: str = "solo", max_concurrent: int = 1) -> AgentCatalog:
    cat = AgentCatalog()
    cat.register(AgentDescriptor(
        name=name, cli=name,
        capabilities=[AgentCapability.CODE_GENERATION],
        healthy=True, max_concurrent=max_concurrent,
    ))
    return cat


def _dispatcher(catalog: AgentCatalog, queue: Any = "auto") -> Dispatcher:
    set_agent_router(AgentRouter(catalog=catalog))
    if queue == "auto":
        return Dispatcher()                     # default: mounts its own queue
    return Dispatcher(priority_queue=queue)      # explicit (incl. None)


async def _fake_inner(self: Any, agent: str, task: str, **kw: Any):
    """Stub the innermost execution layer — NOT dispatch().

    Stubbing ``dispatch`` would bypass the enqueue branch inside
    ``_dispatch_impl`` entirely and make every test pass vacuously.
    """
    from maop.core.reliability.error_schema import new_result
    from maop.delegate.models import DispatchResult

    await asyncio.sleep(0.01)
    return DispatchResult(result=new_result(agent=agent, task=task, stdout=f"done:{task}"))


@pytest.fixture(autouse=True)
def _reset_router():
    yield
    set_agent_router(None)


# ── 1) 挂载形态 ────────────────────────────────────────────────────────


def test_default_mounts_a_queue() -> None:
    """默认必须挂载：否则整套机制对生产仍是死代码。"""
    assert isinstance(Dispatcher()._priority_queue, PriorityTaskQueue)


def test_explicit_none_detaches_the_queue() -> None:
    """显式传 None = 关掉队列（哨兵区分"没传"与"显式关掉"）。"""
    assert Dispatcher(priority_queue=None)._priority_queue is None


def test_explicit_queue_is_used_as_is() -> None:
    q = PriorityTaskQueue()
    assert Dispatcher(priority_queue=q)._priority_queue is q


# ── 2) 竞争 → 入队 → 按优先级排空 ──────────────────────────────────────


@pytest.mark.asyncio
async def test_contention_enqueues_and_drains_in_priority_order() -> None:
    d = _dispatcher(_solo_catalog())
    executed: list[str] = []

    async def fake(self, agent, task, **kw):
        executed.append(task)
        return await _fake_inner(self, agent, task, **kw)

    # 占满唯一槽位
    assert d._acquire_agent_slot("solo") is True

    with patch.object(type(d), "_dispatch_impl_inner", fake):
        tasks = [
            asyncio.create_task(d.dispatch("solo", "low", priority=5)),
            asyncio.create_task(d.dispatch("solo", "high", priority=1)),
            asyncio.create_task(d.dispatch("solo", "mid", priority=3)),
        ]
        # 三个都该进入队列，一个都不该执行
        for _ in range(20):
            await asyncio.sleep(0.02)
            if len(d._priority_queue) == 3:
                break
        assert executed == [], "槽位已满却仍然直接执行了"
        assert len(d._priority_queue) == 3

        # 释放槽位 → 触发排空
        d._release_agent_slot("solo")
        await asyncio.wait_for(asyncio.gather(*tasks), timeout=5)

    assert executed == ["high", "mid", "low"], (
        f"排空顺序不符合 (priority, deadline, FIFO)：{executed}"
    )
    assert len(d._priority_queue) == 0


@pytest.mark.asyncio
async def test_no_contention_executes_directly() -> None:
    """有空闲槽位时行为与过去逐字一致——不入队。"""
    d = _dispatcher(_solo_catalog(max_concurrent=4))
    executed: list[str] = []

    async def fake(self, agent, task, **kw):
        executed.append(task)
        return await _fake_inner(self, agent, task, **kw)

    with patch.object(type(d), "_dispatch_impl_inner", fake):
        await asyncio.wait_for(d.dispatch("solo", "t1", priority=1), timeout=5)

    assert executed == ["t1"]
    assert len(d._priority_queue) == 0


# ── 3) 死线：过期即失败，不迟到执行 ────────────────────────────────────


@pytest.mark.asyncio
async def test_expired_task_fails_fast_on_enqueue() -> None:
    d = _dispatcher(_solo_catalog())
    assert d._acquire_agent_slot("solo") is True  # 制造竞争，迫进入队

    import time as _t
    past_ms = int(_t.time() * 1000) - 5000
    result = await asyncio.wait_for(
        d.dispatch("solo", "late", priority=1, deadline_ms=past_ms), timeout=5,
    )
    assert result.result.exit_code == -8  # EXIT_QUEUE_DEADLINE
    assert "deadline" in (result.result.error or "").lower()
    d._release_agent_slot("solo")


@pytest.mark.asyncio
async def test_expired_task_in_queue_is_not_executed() -> None:
    """队列里躺到过期的任务：排空时按失败结算，绝不白跑一遍。"""
    d = _dispatcher(_solo_catalog())
    q = d._priority_queue
    import time as _t

    fut: asyncio.Future = asyncio.get_running_loop().create_future()
    pt = PriorityTask(
        payload={"agent": "solo", "task": "stale", "future": fut},
        priority=1, deadline_ms=int(_t.time() * 1000) - 1000,
    )
    q.push(pt)

    drained = await asyncio.wait_for(d.drain_pending(limit=5), timeout=5)
    assert drained == 1
    result = await asyncio.wait_for(fut, timeout=5)
    assert result.result.exit_code == -8


# ── 4) 容量：满则响亮拒收，不静默丢弃 ──────────────────────────────────


@pytest.mark.asyncio
async def test_queue_full_rejects_loudly() -> None:
    from maop.delegate.dispatch_priority import QUEUE_MAX_LEN
    d = _dispatcher(_solo_catalog())
    q = d._priority_queue
    import time as _t

    far_future = int(_t.time() * 1000) + 600_000
    for i in range(QUEUE_MAX_LEN):
        q.push(PriorityTask(
            payload={"agent": "solo", "task": f"filler-{i}", "future": None},
            priority=3, deadline_ms=far_future,
        ))
    before = len(q)
    with pytest.raises(RuntimeError, match="queue full"):
        await asyncio.wait_for(
            d.dispatch_priority("solo", "overflow", deadline_ms=far_future),
            timeout=5,
        )
    assert len(q) == before, "拒收不应偷偷再入队"


# ── 5) 防重入活锁 ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_drained_task_does_not_reacquire_or_reenqueue() -> None:
    """排空路径带 _slot_reserved=True：不得再次 acquire、不得再次入队。

    这是活锁防线。若 drain 调 dispatch 时未传递该标记，inner 会再次
    acquire 失败 → 再次入队 → 排空 → …… 无限循环，且三个 future 永不结算
    （2026-11-09 实测到过这个形态：TypeError 未传递关键字参数）。
    """
    d = _dispatcher(_solo_catalog())
    executed: list[str] = []

    async def fake(self, agent, task, **kw):
        executed.append(task)
        return await _fake_inner(self, agent, task, **kw)

    d._acquire_agent_slot("solo")  # 占满

    fut: asyncio.Future = asyncio.get_running_loop().create_future()
    d._priority_queue.push(PriorityTask(
        payload={"agent": "solo", "task": "queued", "future": fut}, priority=1,
    ))
    d._release_agent_slot("solo")  # 释放并触发排空

    with patch.object(type(d), "_dispatch_impl_inner", fake):
        result = await asyncio.wait_for(fut, timeout=5)

    assert executed == ["queued"]
    assert result.result.stdout == "done:queued"
    assert len(d._priority_queue) == 0, "任务被执行后不该又回到队列"


@pytest.mark.asyncio
async def test_drain_yields_when_no_slot_and_pushes_back() -> None:
    """排空时拿不到槽位：原样推回并停止，既不丢弃也不降级执行。"""
    d = _dispatcher(_solo_catalog())
    d._acquire_agent_slot("solo")  # 占满

    fut: asyncio.Future = asyncio.get_running_loop().create_future()
    pt = PriorityTask(payload={"agent": "solo", "task": "waiting", "future": fut}, priority=2)
    d._priority_queue.push(pt)

    drained = await asyncio.wait_for(d.drain_pending(limit=3), timeout=5)
    assert drained == 0, "无槽位时不该声称已派发"
    assert len(d._priority_queue) == 1, "任务必须原样推回"
    assert not fut.done(), "任务还在等，future 不该提前结算"

    d._release_agent_slot("solo")


# ── 6) 向后兼容：显式关掉队列 = 11-08 的降级语义 ───────────────────────


@pytest.mark.asyncio
async def test_detached_queue_keeps_degrade_and_execute() -> None:
    d = _dispatcher(_solo_catalog(), queue=None)
    executed: list[str] = []

    async def fake(self, agent, task, **kw):
        executed.append(task)
        return await _fake_inner(self, agent, task, **kw)

    d._acquire_agent_slot("solo")  # 占满 → 但没有队列可入队
    with patch.object(type(d), "_dispatch_impl_inner", fake):
        await asyncio.wait_for(d.dispatch("solo", "over", priority=1), timeout=5)

    assert executed == ["over"], "关掉队列后应保持降级执行"
    assert d._priority_queue is None


# ── 7) dispatch_priority 直达入口 ──────────────────────────────────────


@pytest.mark.asyncio
async def test_dispatch_priority_without_queue_falls_back_to_direct() -> None:
    d = _dispatcher(_solo_catalog(), queue=None)
    executed: list[str] = []

    async def fake(self, agent, task, **kw):
        executed.append(task)
        return await _fake_inner(self, agent, task, **kw)

    with patch.object(type(d), "_dispatch_impl_inner", fake):
        await asyncio.wait_for(d.dispatch_priority("solo", "direct", priority=2), timeout=5)
    assert executed == ["direct"]
