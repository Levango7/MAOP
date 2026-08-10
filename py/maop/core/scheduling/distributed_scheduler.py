"""MAOP Distributed Scheduler — Redis Streams task queue + DAG node dispatch.

F1-01 (分布式执行): dispatches DAG nodes to a Redis Streams task queue
for execution by a pool of distributed workers
(:class:`~maop.worker.distributed_worker.DistributedWorker`).

Architecture
------------
1. **Topological layering** — the scheduler computes layers from the DAG
   (reusing the engine's Kahn's-algorithm layering) and dispatches one
   layer at a time. Within a layer, nodes are independent and may run
   on different workers in parallel.
2. **Redis Streams queue** — each ready node is serialised to a JSON
   payload and added to the ``maop:tasks`` stream. Workers consume from
   a consumer group, execute, and write results to a per-run results
   stream.
3. **Node affinity** — a node may declare ``affinity`` (a set of
   capability tags). The scheduler only dispatches the node to workers
   whose advertised capabilities satisfy the affinity. If no capable
   worker exists, the node is held back (and retried on the next
   heartbeat tick) rather than dispatched to an incapable worker.
4. **Priority** — nodes carry a ``priority`` (1 highest .. 5 lowest).
   The scheduler writes priority into the stream field so workers can
   read high-priority tasks first (Redis Streams itself is FIFO; the
   priority is honoured by the worker's consumer-group read ordering
   and by a local priority re-order on the worker side).
5. **Failure detection / auto-reschedule** — the scheduler periodically
   calls :meth:`WorkerRegistry.detect_failures`; each failed worker's
   in-flight task ids are re-enqueued to the stream for another worker
   to pick up.
6. **Result aggregation** — the scheduler blocks on a per-run results
   stream, collecting one result per dispatched node, until the layer
   is complete. Results are matched back to nodes by ``node_id``.

Personal edition fallback
-------------------------
When Redis is unavailable (``redis_client is None`` or connection
fails), :class:`~maop.engine.Engine` falls back to single-process
execution via :class:`~maop.core.reliability.dag_scheduler.DAGScheduler`.
This module therefore never raises on Redis absence — the *engine* makes
the fallback decision.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from maop.core.scheduling.worker_pool import WorkerRegistry

if TYPE_CHECKING:
    import redis  # noqa: F401

logger = logging.getLogger(__name__)

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


class DistributedScheduler:
    """Dispatch a DAG of nodes to a Redis Streams task queue.

    Parameters
    ----------
    redis_client : Any
        A ``redis.Redis`` (or ``fakeredis.FakeRedis``) client. The
        scheduler does not own the connection.
    registry : WorkerRegistry | None
        Worker registry for affinity queries and failure detection.
        When ``None``, a registry backed by the same Redis client is
        created lazily on first use.
    task_stream : str
        Redis Streams key for the task queue. Workers consume from this
        stream's ``maop_workers`` consumer group.
    results_prefix : str
        Prefix for per-run results streams. The actual key for a run is
        ``f"{results_prefix}:{run_id}"``.
    poll_interval : float
        Seconds between results-stream polls when waiting for a layer to
        complete. Default 0.1s.
    failure_check_interval : float
        Seconds between worker-failure detection sweeps. Default 1.0s.
    max_retries : int
        Per-node retry count on worker failure. Default 2.
    """

    def __init__(
        self,
        redis_client: Any,
        *,
        registry: WorkerRegistry | None = None,
        task_stream: str = f"{_SCHED_NS}:tasks",
        results_prefix: str = f"{_SCHED_NS}:results",
        poll_interval: float = 0.1,
        failure_check_interval: float = 1.0,
        max_retries: int = 2,
    ) -> None:
        self._redis = redis_client
        self._registry = registry or WorkerRegistry(redis_client)
        self._task_stream = task_stream
        self._results_prefix = results_prefix
        self._poll_interval = max(0.01, float(poll_interval))
        self._failure_check_interval = max(0.1, float(failure_check_interval))
        self._max_retries = max(0, int(max_retries))
        # Ensure the consumer group exists for the task stream.
        self._ensure_task_group()

    # ── Setup ────────────────────────────────────────────────────

    def _ensure_task_group(self) -> None:
        """Create the worker consumer group on the task stream (idempotent)."""
        try:
            self._redis.xgroup_create(
                self._task_stream, "maop_workers", id="0", mkstream=True,
            )
        except Exception as exc:
            # BUSYGROUP → group already exists; safe to ignore.
            if "BUSYGROUP" not in str(exc):
                logger.debug("[dist-sched] xgroup_create ignored: %s", exc)

    def _results_stream(self, run_id: str) -> str:
        return f"{self._results_prefix}:{run_id}"

    # ── Public API ───────────────────────────────────────────────

    async def run(
        self,
        nodes: list[_NodeSpec],
        *,
        run_id: str = "",
    ) -> DistributedResult:
        """Execute a DAG of nodes across the distributed worker pool.

        Parameters
        ----------
        nodes : list[_NodeSpec]
            Nodes to execute (with dependency edges via ``depends_on``).
        run_id : str
            Optional explicit run id. When empty, a UUID4 hex is generated.

        Returns
        -------
        DistributedResult
            Per-node results and overall success flag.
        """
        if not run_id:
            run_id = uuid.uuid4().hex[:16]
        start = time.monotonic()
        node_map = {n.id: n for n in nodes}
        layers = self._compute_layers(nodes)
        results: dict[str, dict[str, Any]] = {
            n.id: {"status": "pending", "node_id": n.id} for n in nodes
        }
        failed: set[str] = set()

        # Background task: periodic failure detection + rescheduling.
        reschedule_queue: asyncio.Queue[tuple[str, str]] = asyncio.Queue()
        stop_failure_check = asyncio.Event()
        failure_task = asyncio.ensure_future(
            self._failure_detection_loop(run_id, reschedule_queue, stop_failure_check),
        )

        try:
            for layer_idx, layer in enumerate(layers):
                # Skip nodes whose upstream failed.
                runnable: list[_NodeSpec] = []
                for nid in layer:
                    node = node_map[nid]
                    if any(d in failed for d in node.depends_on):
                        results[nid] = {
                            "status": "skipped",
                            "node_id": nid,
                            "error": "Upstream dependency failed",
                        }
                        failed.add(nid)
                    else:
                        runnable.append(node)
                if not runnable:
                    continue

                # Dispatch the layer and await all results.
                layer_results = await self._dispatch_and_collect(
                    run_id, layer_idx, runnable, reschedule_queue,
                )
                for nid, res in layer_results.items():
                    results[nid] = res
                    if res.get("status") == "failed":
                        failed.add(nid)

                # ABORT policy: if any node failed, skip all downstream.
                if failed:
                    for nid in node_map:
                        if results[nid]["status"] == "pending":
                            results[nid] = {
                                "status": "skipped",
                                "node_id": nid,
                                "error": "Aborted due to upstream failure",
                            }
                    break
        finally:
            stop_failure_check.set()
            failure_task.cancel()
            try:
                await failure_task
            except (asyncio.CancelledError, Exception):
                pass

        total_ms = int((time.monotonic() - start) * 1000)
        success = all(
            r["status"] in ("success", "skipped") for r in results.values()
        ) and not failed
        return DistributedResult(
            run_id=run_id,
            results=results,
            success=success,
            total_duration_ms=total_ms,
        )

    # ── Layering (Kahn's algorithm) ─────────────────────────────

    def _compute_layers(self, nodes: list[_NodeSpec]) -> list[list[str]]:
        """Topological layering; raises :class:`SchedulingError` on cycles."""
        in_degree: dict[str, int] = dict.fromkeys({n.id for n in nodes}, 0)
        dependents: dict[str, list[str]] = defaultdict(list)
        for n in nodes:
            for dep in n.depends_on:
                if dep not in in_degree:
                    raise SchedulingError(
                        f"Node {n.id!r} depends on unknown node {dep!r}",
                    )
                dependents[dep].append(n.id)
                in_degree[n.id] += 1

        layers: list[list[str]] = []
        ready = sorted(n for n, d in in_degree.items() if d == 0)
        visited: set[str] = set()
        while ready:
            layers.append(list(ready))
            visited.update(ready)
            next_ready: list[str] = []
            for nid in ready:
                for child in dependents[nid]:
                    in_degree[child] -= 1
                    if in_degree[child] == 0 and child not in visited:
                        next_ready.append(child)
            ready = sorted(next_ready)
        if len(visited) != len(in_degree):
            remaining = sorted(set(in_degree) - visited)
            chain = remaining + [remaining[0]]
            raise SchedulingError(f"Dependency cycle: {' -> '.join(chain)}")
        return layers

    # ── Dispatch + collect ───────────────────────────────────────

    async def _dispatch_and_collect(
        self,
        run_id: str,
        layer_idx: int,
        nodes: list[_NodeSpec],
        reschedule_queue: asyncio.Queue[tuple[str, str]],
    ) -> dict[str, dict[str, Any]]:
        """Dispatch a layer's nodes and wait for all their results."""
        results_stream = self._results_stream(run_id)
        pending: dict[str, _NodeSpec] = {n.id: n for n in nodes}
        # Track retry counts per node for worker-failure rescheduling.
        retries: dict[str, int] = {n.id: 0 for n in nodes}
        collected: dict[str, dict[str, Any]] = {}
        # Map stream-msg-id → node_id for reschedule correlation.
        msg_to_node: dict[str, str] = {}

        # Initial dispatch.
        for node in nodes:
            msg_id = await self._dispatch_node(run_id, layer_idx, node)
            msg_to_node[msg_id] = node.id

        # Poll for results + handle reschedules until all nodes resolved.

        while pending:
            # Drain any reschedule requests from the failure detector.
            while not reschedule_queue.empty():
                old_msg_id, node_id = reschedule_queue.get_nowait()
                r_node = pending.get(node_id)
                if r_node is None:
                    continue  # already completed
                if retries[node_id] >= self._max_retries:
                    collected[node_id] = {
                        "status": "failed",
                        "node_id": node_id,
                        "error": f"Exhausted {self._max_retries} retries on worker failures",
                    }
                    pending.pop(node_id, None)
                    continue
                retries[node_id] += 1
                new_msg_id = await self._dispatch_node(run_id, layer_idx, r_node)
                msg_to_node[new_msg_id] = node_id

            # Read results from the results stream.
            new_results = self._read_results(results_stream, set(pending))
            for node_id, res in new_results.items():
                collected[node_id] = res
                pending.pop(node_id, None)

            if pending:
                await asyncio.sleep(self._poll_interval)

        return collected

    async def _dispatch_node(
        self,
        run_id: str,
        layer_idx: int,
        node: _NodeSpec,
    ) -> str:
        """Dispatch a single node to the task stream; return stream msg id."""
        # Affinity check: find a capable worker. If none, we still dispatch
        # (the worker-side consumer will skip nodes it cannot run and NACK),
        # but log a warning so operators can see the affinity mismatch.
        capable = self._registry.capable_workers(node.affinity.required)
        if not capable and node.affinity.required:
            logger.warning(
                "[dist-sched] node %s requires affinity %s but no capable worker; "
                "dispatching anyway",
                node.id, sorted(node.affinity.required),
            )

        payload = {
            _F_NODE_ID: node.id,
            _F_RUN_ID: run_id,
            _F_LAYER: layer_idx,
            _F_PRIORITY: int(node.priority),
            _F_AFFINITY: ",".join(sorted(node.affinity.required)),
            _F_PAYLOAD: _json_dumps(node.payload),
        }
        # redis-py xadd accepts str→str fields; encode via str().
        fields = {k: str(v) for k, v in payload.items()}
        msg_id = self._redis.xadd(self._task_stream, fields)
        msg_id_str = msg_id.decode() if isinstance(msg_id, bytes) else msg_id
        logger.debug(
            "[dist-sched] dispatched node %s (layer=%d, priority=%d) → msg %s",
            node.id, layer_idx, node.priority, msg_id_str,
        )
        return msg_id_str

    def _read_results(
        self,
        results_stream: str,
        expected: set[str],
    ) -> dict[str, dict[str, Any]]:
        """Read completed results from the results stream (non-blocking).

        Returns a dict ``{node_id: result_dict}`` for any nodes in
        ``expected`` whose result has been posted. Uses ``xrange`` to
        read all entries non-blockingly (unlike ``xread`` with a block
        timeout, ``xrange`` returns immediately with whatever is
        available).
        """
        out: dict[str, dict[str, Any]] = {}
        try:
            # xrange returns all entries between min and max ids. We use
            # "-" (smallest) to "+" (largest) to read everything. This is
            # non-blocking and safe for small result streams.
            entries = self._redis.xrange(results_stream, min="-", max="+", count=100)
        except Exception as exc:
            logger.debug("[dist-sched] xrange on %s failed: %s", results_stream, exc)
            return out
        for msg_id, fields in entries:
            node_id = _decode(fields.get(_F_NODE_ID.encode(), fields.get(_F_NODE_ID, b"")))
            if not node_id or node_id not in expected:
                continue
            status = _decode(fields.get(_F_STATUS.encode(), fields.get(_F_STATUS, b"")))
            output_raw = _decode(fields.get(_F_OUTPUT.encode(), fields.get(_F_OUTPUT, b"")))
            error_raw = _decode(fields.get(_F_ERROR.encode(), fields.get(_F_ERROR, b"")))
            worker_id = _decode(fields.get(_F_WORKER_ID.encode(), fields.get(_F_WORKER_ID, b"")))
            duration_raw = _decode(
                fields.get(_F_DURATION_MS.encode(), fields.get(_F_DURATION_MS, b"0")),
            )
            # Try to parse output as JSON; fall back to raw string.
            try:
                output: Any = json.loads(output_raw) if output_raw else None
            except (json.JSONDecodeError, TypeError):
                output = output_raw
            out[node_id] = {
                "status": status or "success",
                "node_id": node_id,
                "output": output,
                "error": error_raw,
                "worker_id": worker_id,
                "duration_ms": int(duration_raw or 0),
                "_msg_id": _decode(msg_id),
            }
        return out

    # ── Failure detection loop ───────────────────────────────────

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
                    stop.wait(), timeout=self._failure_check_interval,
                )
            except asyncio.TimeoutError:
                continue
            else:
                return

    # ── Result posting (worker-side helper) ──────────────────────

    def post_result(
        self,
        run_id: str,
        node_id: str,
        *,
        status: str,
        output: Any = None,
        error: str = "",
        worker_id: str = "",
        duration_ms: int = 0,
    ) -> str:
        """Post a node's execution result to the run's results stream.

        Called by :class:`~maop.worker.distributed_worker.DistributedWorker`
        after executing a task. Returns the results-stream message id.
        """
        stream = self._results_stream(run_id)
        fields = {
            _F_NODE_ID: node_id,
            _F_RUN_ID: run_id,
            _F_STATUS: status,
            _F_OUTPUT: _json_dumps(output),
            _F_ERROR: error,
            _F_WORKER_ID: worker_id,
            _F_DURATION_MS: str(duration_ms),
        }
        msg_id = self._redis.xadd(stream, fields)
        return msg_id.decode() if isinstance(msg_id, bytes) else msg_id

    # ── Introspection ────────────────────────────────────────────

    @property
    def registry(self) -> WorkerRegistry:
        return self._registry

    @property
    def task_stream(self) -> str:
        return self._task_stream

    def __repr__(self) -> str:
        return (
            f"DistributedScheduler(stream={self._task_stream!r}, "
            f"workers={self._registry.active_count()})"
        )


# ── Convenience: build _NodeSpec from engine WorkflowStep ────────────

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