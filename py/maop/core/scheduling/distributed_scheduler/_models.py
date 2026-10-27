"""Data models, constants, and helpers for the distributed scheduler.

Extracted from ``distributed_scheduler.py`` during the subpackage split.
Holds the stream-field constants, dataclasses, and serialisation helpers
that the scheduler mixins and main class depend on.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

logger: logging.Logger = logging.getLogger(__name__)

# Redis key namespace for the distributed scheduler. Kept separate from
# the worker-registry namespace so the two subsystems can be inspected
# independently.
_SCHED_NS = "maop:sched"
# Stream field names (short to minimise Redis memory).
_F_NODE_ID = "node_id"
_F_RUN_ID = "run_id"
_F_LAYER = "layer"
_F_PRIORITY = "priority"
_F_AFFINITY = "affinity"
_F_PAYLOAD = "payload"
_F_STATUS = "status"
_F_OUTPUT = "output"
_F_ERROR = "error"
_F_WORKER_ID = "worker_id"
_F_DURATION_MS = "duration_ms"
# Spawn directives field (M0 L0.1-T0.1.8): a result may carry
# spawn directives that fan out new steps at runtime.
_F_SPAWN = "spawn"


class SchedulingError(RuntimeError):
    """Raised on distributed-scheduling failures (cycle, no worker, …)."""


@dataclass
class NodeAffinity:
    """Affinity declaration for a DAG node.

    Attributes
    ----------
    required : set[str]
        Capability tags the executing worker must advertise. Empty set
        means "any worker".
    prefer : set[str]
        Tags that are *preferred* but not required. Used to pick the
        least-loaded capable worker when multiple qualify.
    """

    required: set[str] = field(default_factory=set)
    prefer: set[str] = field(default_factory=set)

    @classmethod
    def parse(cls, spec: str | set[str] | NodeAffinity | None) -> NodeAffinity:
        """Build a :class:`NodeAffinity` from a flexible spec.

        - ``None`` / empty → no affinity (any worker).
        - ``str`` → a single required tag (``"gpu"``).
        - ``set[str]`` → required tags.
        - :class:`NodeAffinity` → returned as-is.
        """
        if spec is None:
            return cls()
        if isinstance(spec, NodeAffinity):
            return spec
        if isinstance(spec, str):
            return cls(required={spec}) if spec else cls()
        if isinstance(spec, set):
            return cls(required=set(spec))
        raise TypeError(f"Unsupported affinity spec type: {type(spec).__name__}")


@dataclass
class TaskAssignment:
    """Record of a node-to-worker assignment for a single run.

    Attributes
    ----------
    node_id : str
        The DAG node being executed.
    worker_id : str
        The worker it was dispatched to.
    stream_msg_id : str
        The Redis Streams message id of the dispatched task.
    dispatched_at : float
        Epoch timestamp (seconds) of dispatch.
    """

    node_id: str
    worker_id: str
    stream_msg_id: str
    dispatched_at: float = field(default_factory=time.time)


@dataclass
class _NodeSpec:
    """Internal node specification passed to the scheduler."""

    id: str
    func: Callable[..., Any] | None = None
    args: tuple[Any, ...] = field(default_factory=tuple)
    kwargs: dict[str, Any] = field(default_factory=dict)
    depends_on: list[str] = field(default_factory=list)
    affinity: NodeAffinity = field(default_factory=NodeAffinity)
    priority: int = 3
    timeout: float = 0.0
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class DistributedResult:
    """Aggregate result of a distributed DAG run.

    Attributes
    ----------
    run_id : str
        Unique id for this run (used as the Redis results-stream key).
    results : dict[str, dict[str, Any]]
        Per-node result dict. Each value has keys ``status`` (``success``
        | ``failed`` | ``skipped``), ``output``, ``error``,
        ``worker_id``, ``duration_ms``.
    success : bool
        ``True`` iff every node succeeded (or was skipped).
    total_duration_ms : int
        Wall-clock duration of :meth:`DistributedScheduler.run`.
    """

    run_id: str
    results: dict[str, dict[str, Any]] = field(default_factory=dict)
    success: bool = False
    total_duration_ms: int = 0


def _json_dumps(obj: Any) -> str:
    """JSON dumps with a str fallback for non-serialisable values."""
    try:
        return json.dumps(obj, ensure_ascii=False, default=str)
    except Exception:
        return str(obj)


def _decode(v: Any) -> Any:
    """Decode a Redis bytes-or-str value to str."""
    if isinstance(v, bytes):
        return v.decode()
    return v


def node_spec_from_step(
    step_id: str,
    *,
    depends_on: list[str] | None = None,
    affinity: str | set[str] | NodeAffinity | None = None,
    priority: int = 3,
    payload: dict[str, Any] | None = None,
    timeout: float = 0.0,
) -> _NodeSpec:
    """Build a :class:`_NodeSpec` from engine-step-style arguments.

    The engine wraps each :class:`~maop.engine.WorkflowStep` in a
    ``_NodeSpec`` whose ``payload`` carries the step's serialised
    description. Workers interpret the payload to execute the step.
    """
    return _NodeSpec(
        id=step_id,
        depends_on=list(depends_on or []),
        affinity=NodeAffinity.parse(affinity),
        priority=int(priority),
        timeout=float(timeout),
        payload=dict(payload or {}),
    )
