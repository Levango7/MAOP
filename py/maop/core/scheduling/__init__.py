"""MAOP Distributed Scheduling — Redis Streams task queue + worker pool.

F1-01 (分布式执行): provides a distributed scheduler that dispatches DAG
nodes to a Redis Streams task queue, and a worker pool registry that
tracks worker heartbeats and automatically reschedules tasks when a
worker is detected as failed.

Public API::

    from maop.core.scheduling import (
        DistributedScheduler,
        WorkerRegistry,
        NodeAffinity,
        TaskAssignment,
        SchedulingError,
    )

Personal edition fallback: when Redis is unavailable, callers should
fall back to the in-process :class:`~maop.engine.Engine` (single-process
execution). The :class:`~maop.engine.Engine` itself handles this
selection — see ``Engine.run(distributed=True)``.
"""

from __future__ import annotations

from maop.core.scheduling.distributed_scheduler import (
    DistributedScheduler,
    NodeAffinity,
    SchedulingError,
    TaskAssignment,
)
from maop.core.scheduling.worker_pool import (
    WorkerInfo,
    WorkerRegistry,
    WorkerStatus,
)

__all__ = [
    "DistributedScheduler",
    "NodeAffinity",
    "SchedulingError",
    "TaskAssignment",
    "WorkerInfo",
    "WorkerRegistry",
    "WorkerStatus",
]