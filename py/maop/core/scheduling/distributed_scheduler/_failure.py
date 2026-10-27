"""Failure-detection mixin for the distributed scheduler.

Extracted from ``distributed_scheduler.py`` during the subpackage split.
Holds :meth:`_FailureDetectionMixin._failure_detection_loop`, which
periodically sweeps the worker registry for dead workers and enqueues
their in-flight task ids for rescheduling.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from maop.core.scheduling.worker_pool import WorkerRegistry

logger: logging.Logger = logging.getLogger(__name__)


class _FailureDetectionMixin:
    """Mixin providing :meth:`_failure_detection_loop`.

    Requires ``self._registry`` and ``self._failure_check_interval``
    to be set by the concrete class's ``__init__``.
    """

    _registry: WorkerRegistry
    _failure_check_interval: float

    async def _failure_detection_loop(
        self,
        run_id: str,
        reschedule_queue: asyncio.Queue[tuple[str, str]],
        stop: asyncio.Event,
    ) -> None:
        """Periodically detect failed workers and enqueue their tasks for reschedule."""
        while not stop.is_set():
            try:
                failed = self._registry.detect_failures()
                for _wid, task_ids in failed:
                    for task_id in task_ids:
                        await reschedule_queue.put((task_id, task_id))
            except Exception as exc:
                logger.debug("[dist-sched] failure detection error: %s", exc)
            try:
                await asyncio.wait_for(
                    stop.wait(),
                    timeout=self._failure_check_interval,
                )
            except (
                asyncio.TimeoutError
            ):  # silent-ok: expected-signal — 超时/不存在即等待的信号，非错误
                continue
            else:
                return
