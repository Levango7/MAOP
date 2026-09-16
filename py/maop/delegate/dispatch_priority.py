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
from typing import Any


class DispatchPriorityMixin:
    """Phase γ-2 priority-queue dispatch helpers (mixin for Dispatcher).

    Expects the host class to provide:
        - ``self._priority_queue``  — optional priority task queue
        - ``self.dispatch(...)``    — synchronous dispatch coroutine
        - ``self._sla``             — SLAMonitor instance
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
        return await fut

    async def drain_pending(self, limit: int = 1) -> int:
        """Execute up to ``limit`` queued dispatch requests in priority order.

        Pops the highest-priority tasks from the attached queue and
        dispatches each via :meth:`dispatch`. The per-task
        :class:`asyncio.Future` stored in the payload is resolved with the
        :class:`DispatchResult` (or the exception on failure).

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
            try:
                result = await self.dispatch(
                    payload.get("agent", ""),
                    payload.get("task", ""),
                    routing_key=payload.get("routing_key", ""),
                    workdir=payload.get("workdir", ""),
                    timeout_seconds=payload.get("timeout_seconds"),
                    trace_id=payload.get("trace_id", ""),
                    streamer=payload.get("streamer"),
                    priority=pt.priority,
                    deadline_ms=pt.deadline_ms,
                )
                if fut is not None and not fut.done():
                    fut.set_result(result)
            except Exception as exc:
                if fut is not None and not fut.done():
                    fut.set_exception(exc)
            dispatched += 1
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