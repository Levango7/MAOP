"""DistributedScheduler subpackage.

Split from the original ``distributed_scheduler.py`` (950 lines) into six
modules during the services-layer split movement.  The public API is
unchanged: callers still import from ``maop.core.scheduling.distributed_scheduler``.

Internal module map
-------------------
_models.py            – constants, dataclasses, exceptions, JSON helpers
_worker_selection.py  – _WorkerSelectionMixin (affinity-based worker pick)
_failure.py           – _FailureDetectionMixin (background heartbeat sweep)
_results.py           – _ResultsMixin (results stream read/decode, spawn merge)
_scheduler.py         – DistributedScheduler (composition + DAG execution)
"""

from __future__ import annotations

from maop.core.scheduling.distributed_scheduler._models import (
    _F_AFFINITY,
    _F_DURATION_MS,
    _F_ERROR,
    _F_LAYER,
    _F_NODE_ID,
    _F_OUTPUT,
    _F_PAYLOAD,
    _F_PRIORITY,
    _F_RUN_ID,
    _F_SPAWN,
    _F_STATUS,
    _F_WORKER_ID,
    _SCHED_NS,
    DistributedResult,
    NodeAffinity,
    SchedulingError,
    TaskAssignment,
    _NodeSpec,
    node_spec_from_step,
)
from maop.core.scheduling.distributed_scheduler._scheduler import (
    DistributedScheduler,
)

__all__ = [
    "_F_AFFINITY",
    "_F_DURATION_MS",
    "_F_ERROR",
    "_F_LAYER",
    "_F_NODE_ID",
    "_F_OUTPUT",
    "_F_PAYLOAD",
    "_F_PRIORITY",
    "_F_RUN_ID",
    "_F_SPAWN",
    "_F_STATUS",
    "_F_WORKER_ID",
    "_SCHED_NS",
    "DistributedResult",
    "DistributedScheduler",
    "NodeAffinity",
    "SchedulingError",
    "TaskAssignment",
    "_NodeSpec",
    "node_spec_from_step",
]
