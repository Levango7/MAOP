"""Priority-queue dispatch mixin for :class:`Dispatcher`.

Split out from ``dispatch_core.py`` for maintainability (mixin pattern).
Houses the Phase γ-2 priority-aware dispatch helpers:

- :meth:`dispatch_priority` — enqueue a dispatch request with priority
- :meth:`drain_pending` — execute queued requests in priority order
- :meth:`_record_soft_preemption_for_dispatch` — soft-preemption counter

These methods access ``self`` attributes (``_priority_queue``, ``_sla``,
``dispatch``) provided by the :class:`Dispatcher` host class, so the mixin
is purely a structural split — no logic changes.

The logger name is pinned to ``maop.delegate.dispatcher`` to preserve log
output exactly as before the split.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

logger = logging.getLogger("maop.delegate.dispatcher")

#: 队列长度硬上限。超过即**拒收并响亮失败**，而不是无限堆积——
#: 静默丢弃会让调用方永远等一个不会来的 future（2026-11-09）。
QUEUE_MAX_LEN = 1000

#: 死线已过时的任务返回的 exit_code（与 -2 not found / -4 guardrail /
#: -7 unhealthy-drained 同一族语义）。
EXIT_QUEUE_DEADLINE = -8

#: 队列已满时返回的 exit_code。
EXIT_QUEUE_FULL = -9

_M_QUEUE_DEPTH = "maop_dispatch_queue_depth"
_M_QUEUE_ENQUEUED = "maop_dispatch_queue_enqueued_total"
_M_QUEUE_DRAINED = "maop_dispatch_queue_drained_total"
_M_QUEUE_DEADLINE = "maop_dispatch_queue_deadline_total"
_M_QUEUE_REJECTED = "maop_dispatch_queue_rejected_total"

_metrics: Any = None


def _get_metrics() -> Any:
    """Lazily grab the observability metrics singleton (None-safe)."""
    global _metrics
    if _metrics is None:
        try:
            from maop.core.observability.metrics import get_metrics
            _metrics = get_metrics()
        except (ImportError, AttributeError, TypeError, ValueError):  # metrics 不得打倒 dispatch
            return None
    return _metrics


def _now_ms() -> int:
    return int(time.time() * 1000)


def _expired(pt: Any) -> bool:
    """任务是否已过死线（无死线的任务永不过期）。"""
    deadline = getattr(pt, "deadline_ms", None)
    return deadline is not None and _now_ms() > int(deadline)


class DispatchPriorityMixin:
    """Phase γ-2/γ-4 priority-queue dispatch helpers (mixin for Dispatcher).

    Expects the host class to provide:
        - ``self._priority_queue``  — optional priority task queue
        - ``self.dispatch(...)``    — synchronous dispatch coroutine
        - ``self._sla``             — SLAMonitor instance
        - ``self._acquire_agent_slot`` / ``self._release_agent_slot``
          (2026-11-09 起：队列项由 drain 负责申请/释放并发槽位)
    """

    async def dispatch_priority(
        self,
        agent: str,
        task: str,
        *,
        routing_key: str = "",
        workdir: str = "",
        timeout_seconds: int | None = None,
        trace_id: str = "",
        streamer: Any | None = None,
        priority: int = 3,
        deadline_ms: int | None = None,
    ):
        """Enqueue a dispatch request with priority metadata (Phase γ-2).

        If no priority queue is attached (the default), this falls back to
        a direct :meth:`dispatch` call, preserving the original synchronous
        behaviour — so callers can switch to priority dispatch without
        branching on configuration.

        When a queue is attached, the request is wrapped in a
        :class:`~maop.core.priority_queue.PriorityTask` and pushed; the
        returned awaitable resolves to the :class:`DispatchResult` once
        :meth:`drain_pending` (or a worker loop) eventually executes it.

        Returns
        -------
        asyncio.Future | Awaitable[DispatchResult]
            A future that resolves to the DispatchResult.
        """
        if self._priority_queue is None:
            # Backward-compatible fallback: execute directly.
            return await self.dispatch(
                agent, task,
                routing_key=routing_key, workdir=workdir,
                timeout_seconds=timeout_seconds, trace_id=trace_id,
                streamer=streamer,
                priority=priority, deadline_ms=deadline_ms,
            )

        # Lazy import to avoid a hard import cycle in tests that stub
        # the queue with a duck-typed object.
        from maop.core.reliability.priority_queue import PriorityTask

        fut = asyncio.get_running_loop().create_future()

        # ── 入队前两道闸（2026-11-09）────────────────────────────────
        # ① 死线已过：不占队列、不等待，直接响亮失败。此前入队后由 drain
        #    执行，过期的任务会白跑一遍——调用方拿到的是"迟到但成功"的结果，
        #    而死线语义本该是"过期即失败"。
        # ② 队列已满：拒收并让 future 带错，而不是无限堆积（静默丢弃会让
        #    调用方永远等一个不会来的 future）。
        if deadline_ms is not None and _now_ms() > int(deadline_ms):
            self._count(_M_QUEUE_DEADLINE)
            logger.warning(
                "[dispatch] 入队即过期（deadline_ms=%s, now=%s），直接失败",
                deadline_ms, _now_ms(),
            )
            # 注意必须 await：_deadline_result 返回的是 future 本身，直接 return
            # 会把 Future 泄漏给调用方（2026-11-09 实测踩到——调用方拿到的是
            # asyncio.Future 而非 DispatchResult，类型契约被破坏）。
            return await self._deadline_result(
                fut, agent=agent, task=task, trace_id=trace_id,
                routing_key=routing_key,
            )
        depth = self._queue_depth()
        if depth is not None and depth >= QUEUE_MAX_LEN:
            self._count(_M_QUEUE_REJECTED)
            logger.error(
                "[dispatch] 优先级队列已满（%d ≥ %d），拒收 agent=%s trace=%s",
                depth, QUEUE_MAX_LEN, agent, trace_id,
            )
            fut.set_exception(RuntimeError(
                f"dispatch priority queue full ({depth}/{QUEUE_MAX_LEN})"
            ))
            return await fut

        pt = PriorityTask(
            payload={
                "agent": agent,
                "task": task,
                "routing_key": routing_key,
                "workdir": workdir,
                "timeout_seconds": timeout_seconds,
                "trace_id": trace_id,
                "streamer": streamer,
                "future": fut,
            },
            priority=priority,
            deadline_ms=deadline_ms,
        )
        self._priority_queue.push(pt)
        self._count(_M_QUEUE_ENQUEUED)
        self._publish_depth()
        logger.info(
            "[dispatch] 入队 agent=%s priority=%s depth=%s trace=%s",
            agent, priority, self._queue_depth(), trace_id,
        )
        # 尝试立即排空一次（可能已有空闲槽位），失败不影响入队本身。
        self._schedule_drain()
        return await fut

    # ── 队列可观测性 / 死线结果（2026-11-09）──────────────────────

    def _queue_depth(self) -> int | None:
        """队列当前深度（探测多种常见接口；取不到就返回 None，不猜测）。"""
        q = self._priority_queue
        for attr in ("__len__", "depth", "qsize", "size"):
            if hasattr(q, attr):
                try:
                    return int(getattr(q, attr)())
                except (AttributeError, TypeError, ValueError) as exc:
                    logger.debug("[dispatch] queue depth probe %s failed: %s", attr, exc)
                    continue
        return None

    def _count(self, name: str) -> None:
        m = _get_metrics()
        if m is None:
            return
        try:
            m.collector.counter(name, name).inc()
        # 计数器只是看不到，不改变派发行为；类型已收窄到四类现实失败面。
        except (ImportError, AttributeError, TypeError, ValueError):  # silent-ok: optional-dep — 队列计数器上报失败不影响调度
            pass

    def _publish_depth(self) -> None:
        m = _get_metrics()
        if m is None:
            return
        depth = self._queue_depth()
        if depth is None:
            return
        try:
            m.collector.gauge(_M_QUEUE_DEPTH, "Pending priority-dispatch depth").set(depth)
        # 指标子系统不可用时队列深度只是看不到，派发/排空不依赖它。
        except (ImportError, AttributeError, TypeError, ValueError):  # silent-ok: optional-dep — 深度 gauge 上报失败不影响调度
            pass

    @staticmethod
    def _deadline_result(
        fut: asyncio.Future, *, agent: str, task: str, trace_id: str, routing_key: str,
    ):
        """构造"过期即失败"的 DispatchResult 并结算 future。"""
        from maop.core.reliability.error_schema import new_result
        from maop.delegate.models import DispatchResult

        result = new_result(
            agent=agent, task=task,
            exit_code=EXIT_QUEUE_DEADLINE,
            error="dispatch deadline exceeded while queued",
            trace_id=trace_id, routing_key=routing_key,
        )
        if not fut.done():
            fut.set_result(DispatchResult(result=result, breaker_tripped=False))
        return fut

    def _schedule_drain(self) -> None:
        """在运行中的事件循环上安排一次排空（无循环则跳过，等显式调用）。

        两道闸，避免"调度出一个没人等的 task"：

        ① **队列必须非空**。空队列时调度 = 造出一个注定立刻返回的游离 task，
           却要把 ``asyncio.run()`` 的 loop 拖到它跑完——单次派发（既有的
            ``asyncio.run(d.dispatch(...))`` 写法）会被无谓拖慢，看起来像挂起
           （2026-11-09 实测：13 条存量用例因此超时）。
        ② ``_drain_scheduled`` 去重，同一时刻只安排一个排空任务。

        没有运行中的事件循环时直接返回——调用方下次显式 ``drain_pending``
        （或下一次 release）会再触发。
        """
        if self._priority_queue is None:
            return
        depth = self._queue_depth()
        if depth is None or depth <= 0:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        if getattr(self, "_drain_scheduled", False):
            return
        self._drain_scheduled = True

        async def _run() -> None:
            try:
                await self.drain_pending(limit=QUEUE_MAX_LEN)
            except (RuntimeError, ValueError, TypeError, OSError) as exc:
                logger.warning("[dispatch] 自动排空失败: %s", exc)
            finally:
                self._drain_scheduled = False

        loop.create_task(_run())

    async def drain_pending(self, limit: int = 1) -> int:
        """Execute up to ``limit`` queued dispatch requests in priority order.

        每条队列项的处理顺序（2026-11-09 起）：

        1. **死线检查**——已过期则结算为 `EXIT_QUEUE_DEADLINE` 失败并继续下一条，
           不白跑一遍（此前过期任务照常执行，调用方拿到"迟到但成功"的结果）。
        2. **申请并发槽位**——为该项的 agent `acquire`；拿不到则**原样推回队列**并
           停止本轮排空（不是丢弃、不是降级执行）。
        3. **带 `slot_reserved=True` 执行**——inner 因此不会再次 acquire，也就不会
           再次入队；否则会形成"排空→无槽→入队→排空"的活锁。
        4. `finally` 里 release 槽位，并再触发一次排空（槽位刚空出来，队列里可能
           正有同 agent 的任务在等）。

        Returns the number of tasks actually dispatched.

        No-op (returns 0) when no priority queue is attached.
        """
        if self._priority_queue is None:
            return 0
        dispatched = 0
        for _ in range(max(0, limit)):
            pt = self._priority_queue.pop()
            if pt is None:
                break
            payload = pt.payload or {}
            fut: asyncio.Future | None = payload.get("future")
            agent = payload.get("agent", "") or ""

            # 1) 死线已过：响亮失败，不占槽位、不白跑。
            if _expired(pt):
                self._count(_M_QUEUE_DEADLINE)
                logger.warning(
                    "[dispatch] 排空时发现过期任务 agent=%s trace=%s，按死线失败结算",
                    agent, payload.get("trace_id", ""),
                )
                if fut is not None and not fut.done():
                    self._deadline_result(
                        fut, agent=agent, task=payload.get("task", "") or "",
                        trace_id=payload.get("trace_id", "") or "",
                        routing_key=payload.get("routing_key", "") or "",
                    )
                dispatched += 1
                continue

            # 2) 申请槽位；拿不到就原样推回并停止（等下一次槽位释放再排）。
            #
            # 但"拿不到"要分两种：**真的满载** ⇒ 推回；**根本没法 tracking**
            # （router 不可用 / agent 未登记 catalog）⇒ 不能拿这个当满载，
            # 否则 drain 会在一个永不变化的假象上永久停摆（2026-11-09 实测：
            # 用 MagicMock 队列 + 未登记 agent 时 drain 直接 break；真实部署里
            # catalog 未初始化时同样会静默不排空）。
            _really_held = self._acquire_agent_slot(agent)
            if not _really_held and not self._agent_is_tracked(agent):
                # 无从判断负载（router 不可用 / agent 未登记 catalog）⇒ 不把这种情况
                # 当"满载"，否则 drain 会在假象上永久停摆。照常执行，
                # 但不声称持有槽位（下面 release 也就不做）。
                _really_held = False
            elif not _really_held:
                self._priority_queue.push(pt)
                logger.info(
                    "[dispatch] 排空让位：agent=%s 无空闲槽位，任务已推回队列", agent,
                )
                break

            # 3)+4) 带预留槽位执行，finally 释放并再触发排空。
            try:
                result = await self.dispatch(
                    agent,
                    payload.get("task", "") or "",
                    routing_key=payload.get("routing_key", "") or "",
                    workdir=payload.get("workdir", "") or "",
                    timeout_seconds=payload.get("timeout_seconds"),
                    trace_id=payload.get("trace_id", ""),
                    streamer=payload.get("streamer"),
                    priority=pt.priority,
                    deadline_ms=pt.deadline_ms,
                    _slot_reserved=_really_held,
                )
                if fut is not None and not fut.done():
                    fut.set_result(result)
            except (RuntimeError, ValueError, TypeError, OSError) as exc:
                if fut is not None and not fut.done():
                    fut.set_exception(exc)
            finally:
                if _really_held:
                    self._release_agent_slot(agent)
                self._publish_depth()
            self._count(_M_QUEUE_DRAINED)
            dispatched += 1
        self._publish_depth()
        return dispatched

    def _record_soft_preemption_for_dispatch(
        self,
        incoming_priority: int,
        running_priorities: list[int],
    ) -> None:
        """Record a soft-preemption event for dispatcher-driven dispatch.

        Delegates to :class:`SLAMonitor` (N2 refactor). Exposed as a helper
        so that callers managing their own worker pool can signal "a
        higher-priority dispatch arrived while lower-priority dispatches
        are in flight". Under soft preemption the running dispatches are
        not interrupted; the counter records demand only.
        """
        self._sla.record_preemption(incoming_priority, running_priorities)