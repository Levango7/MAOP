"""MAOP DAG Scheduler — Dependency-aware parallel execution (P1-7).

Executes a DAG of tasks with:

  * **Topological layering** — nodes with no outstanding dependencies
    run in parallel within a layer; layers run serially.
  * **CPU offload** — nodes flagged ``is_cpu_intensive`` are routed
    to :meth:`WorkerPool.run_cpu` (process pool) instead of the
    asyncio event loop.
  * **Configurable failure propagation** — ``on_fail`` policy
    ``abort`` | ``continue`` | ``retry`` controls what happens when
    a node raises.
  * **Backward compatibility** — a DAG with no edges is equivalent
    to ``asyncio.gather(*nodes)``, so callers can adopt the
    scheduler incrementally.

Usage::

    from maop.core.reliability.dag_scheduler import DAGScheduler, TaskNode

    nodes = [
        TaskNode(id="fetch", func=fetch_data),
        TaskNode(id="transform", func=transform, args=(raw,), depends_on=["fetch"]),
        TaskNode(id="load", func=load_db, args=(transformed,), depends_on=["transform"]),
    ]
    results = await DAGScheduler(nodes).run()
    # results: dict[str, NodeResult]

The scheduler is intentionally agnostic of MAOP-specific step types
(plan/agent/verify/condition/terminal); the higher-level
:class:`~maop.engine.Engine` wraps each step in a coroutine and
hands the resulting task graph to :class:`DAGScheduler`.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from maop.core.reliability.worker_pool import WorkerPool, get_worker_pool

logger = logging.getLogger(__name__)


# ── Models ──────────────────────────────────────────────────────

class NodeStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


class FailPolicy(str, Enum):
    """Failure propagation strategy for the scheduler."""

    ABORT = "abort"      # skip all downstream of the failed node
    CONTINUE = "continue"  # run remaining nodes; downstream still skipped
    RETRY = "retry"      # retry the failed node up to ``max_retries``


@dataclass
class TaskNode:
    """A single executable node in the DAG.

    Parameters
    ----------
    id : str
        Unique node identifier.
    func : Callable
        Sync or async callable. Sync callables are invoked directly
        inside the coroutine; async callables are awaited.
    args : tuple
        Positional arguments passed to ``func``.
    kwargs : dict
        Keyword arguments passed to ``func``.
    depends_on : list[str]
        IDs of nodes that must complete successfully before this
        node starts. Equivalent to ``edges`` with ``(dep, self.id)``
        for each ``dep``.
    is_cpu_intensive : bool
        When True and a :class:`WorkerPool` is attached, route the
        call through :meth:`WorkerPool.run_cpu` (process pool) to
        avoid blocking the event loop. The callable must be
        picklable in that case.
    retry : int
        Per-node retry override. ``-1`` means use the scheduler
        default (``max_retries``).
    timeout : float
        Per-node timeout in seconds. ``0`` means no timeout.
    """

    id: str
    func: Callable[..., Any] | None = None
    args: tuple[Any, ...] = field(default_factory=tuple)
    kwargs: dict[str, Any] = field(default_factory=dict)
    depends_on: list[str] = field(default_factory=list)
    is_cpu_intensive: bool = False
    retry: int = -1
    timeout: float = 0.0


@dataclass
class NodeResult:
    """Result of executing a single DAG node."""

    id: str
    status: NodeStatus = NodeStatus.PENDING
    output: Any = None
    error: str = ""
    duration_ms: int = 0
    attempts: int = 0


@dataclass
class DagResult:
    """Aggregate result of a DAG run."""

    results: dict[str, NodeResult] = field(default_factory=dict)
    success: bool = False
    total_duration_ms: int = 0

    def __getitem__(self, node_id: str) -> NodeResult:
        return self.results[node_id]


# ── Exceptions ──────────────────────────────────────────────────

class DagCycleError(ValueError):
    """Raised when the DAG contains a dependency cycle."""


class DagValidationError(ValueError):
    """Raised when the DAG specification is malformed (missing node, etc.)."""


# ── Scheduler ───────────────────────────────────────────────────

class DAGScheduler:
    """Dependency-aware parallel task scheduler.

    Parameters
    ----------
    nodes : list[TaskNode]
        Task nodes to execute.
    edges : list[tuple[str, str]] | None
        Explicit ``(parent, child)`` edges. Combined with
        ``node.depends_on``; either source is optional.
    on_fail : FailPolicy | str
        Failure propagation policy. Defaults to ``abort``.
    worker_pool : WorkerPool | None
        Pool used for CPU-intensive nodes. When ``None``, a node
        with ``is_cpu_intensive=True`` falls back to in-loop
        execution (with a warning).
    max_retries : int
        Default retry count for nodes with ``retry=-1``. Only
        effective when ``on_fail="retry"``.
    return_exceptions : bool
        When True, a node failure is captured in
        :attr:`NodeResult.error` and does not propagate. When
        False, the first failure (after policy resolution) is
        re-raised from :meth:`run`. Defaults to True for
        compatibility with ``asyncio.gather(return_exceptions=True)``.
    """

    def __init__(
        self,
        nodes: list[TaskNode],
        edges: list[tuple[str, str]] | None = None,
        *,
        on_fail: FailPolicy | str = FailPolicy.ABORT,
        worker_pool: WorkerPool | None = None,
        max_retries: int = 3,
        return_exceptions: bool = True,
    ) -> None:
        self._nodes: dict[str, TaskNode] = {n.id: n for n in nodes}
        # Merge explicit edges with per-node depends_on
        self._edges: list[tuple[str, str]] = list(edges or [])
        for n in nodes:
            for dep in n.depends_on:
                self._edges.append((dep, n.id))
        self._on_fail = FailPolicy(on_fail) if isinstance(on_fail, str) else on_fail
        self._pool = worker_pool
        self._max_retries = max(0, int(max_retries))
        self._return_exceptions = bool(return_exceptions)
        # Validate structure eagerly so run() failures are deterministic
        self._validate()

    # ── Validation & layering ─────────────────────────────────

    def _validate(self) -> None:
        """Check that every edge endpoint is a known node."""
        known = set(self._nodes)
        for parent, child in self._edges:
            if parent not in known:
                raise DagValidationError(
                    f"Edge ({parent!r} → {child!r}): unknown parent {parent!r}"
                )
            if child not in known:
                raise DagValidationError(
                    f"Edge ({parent!r} → {child!r}): unknown child {child!r}"
                )

    def _compute_layers(self) -> list[list[str]]:
        """Kahn's algorithm with layer grouping.

        Returns
        -------
        list[list[str]]
            One list per layer; nodes within a layer have no
            inter-dependencies and may run in parallel.
        """
        in_degree: dict[str, int] = dict.fromkeys(self._nodes, 0)
        dependents: dict[str, list[str]] = defaultdict(list)
        for parent, child in self._edges:
            dependents[parent].append(child)
            in_degree[child] += 1

        layers: list[list[str]] = []
        ready = sorted(n for n, deg in in_degree.items() if deg == 0)
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

        if len(visited) != len(self._nodes):
            remaining = sorted(set(self._nodes) - visited)
            chain = remaining + [remaining[0]]
            raise DagCycleError(
                f"Dependency cycle detected: {' -> '.join(chain)}"
            )
        return layers

    def _direct_deps(self, node_id: str) -> list[str]:
        """Return direct dependencies (parents) of ``node_id``."""
        return [p for p, c in self._edges if c == node_id]

    # ── Execution ─────────────────────────────────────────────

    async def run(self) -> DagResult:
        """Execute the DAG and return per-node results.

        Layers run serially; nodes within a layer run concurrently
        via :func:`asyncio.gather`. CPU-intensive nodes are offloaded
        to the worker pool when available.
        """
        start = time.monotonic()
        layers = self._compute_layers()
        results: dict[str, NodeResult] = {
            nid: NodeResult(id=nid) for nid in self._nodes
        }
        # Track which nodes have failed for downstream skip logic
        failed: set[str] = set()

        for layer in layers:
            # Partition layer into runnable vs skipped
            runnable: list[str] = []
            for nid in layer:
                deps = self._direct_deps(nid)
                if any(d in failed for d in deps):
                    # Upstream failed → skip this node
                    results[nid] = NodeResult(
                        id=nid,
                        status=NodeStatus.SKIPPED,
                        error="Upstream dependency failed",
                    )
                    if self._on_fail == FailPolicy.ABORT:
                        failed.add(nid)
                else:
                    runnable.append(nid)

            if not runnable:
                continue

            # Execute runnable nodes in parallel
            coros = [self._run_node(self._nodes[nid], results) for nid in runnable]
            layer_outcomes = await asyncio.gather(*coros, return_exceptions=True)

            for nid, outcome in zip(runnable, layer_outcomes):
                if isinstance(outcome, BaseException):
                    # _run_node should not raise, but defend against bugs
                    results[nid] = NodeResult(
                        id=nid,
                        status=NodeStatus.FAILED,
                        error=f"{type(outcome).__name__}: {outcome}",
                    )
                    failed.add(nid)
                    if not self._return_exceptions:
                        raise outcome
                else:
                    results[nid] = outcome
                    if outcome.status == NodeStatus.FAILED:
                        failed.add(nid)

            # ABORT policy: if any node in this layer failed, skip
            # all remaining layers by marking their nodes skipped.
            if failed and self._on_fail == FailPolicy.ABORT:
                # Mark all not-yet-visited nodes as skipped
                for nid in self._nodes:
                    if results[nid].status in (
                        NodeStatus.PENDING,
                        NodeStatus.RUNNING,
                    ):
                        results[nid] = NodeResult(
                            id=nid,
                            status=NodeStatus.SKIPPED,
                            error="Aborted due to upstream failure",
                        )
                break

        total_ms = int((time.monotonic() - start) * 1000)
        success = all(
            r.status in (NodeStatus.SUCCESS, NodeStatus.SKIPPED)
            for r in results.values()
        ) and not failed

        return DagResult(results=results, success=success, total_duration_ms=total_ms)

    async def _run_node(self, node: TaskNode, prior: dict[str, NodeResult]) -> NodeResult:
        """Execute a single node with retry/timeout/CPU-offload logic."""
        if node.func is None:
            return NodeResult(id=node.id, status=NodeStatus.SUCCESS, output=None)
        start = time.monotonic()
        max_attempts = self._resolve_max_attempts(node)
        last_error: str = ""
        for attempt in range(1, max_attempts + 1):
            try:
                output = await self._invoke(node)
                return NodeResult(
                    id=node.id,
                    status=NodeStatus.SUCCESS,
                    output=output,
                    duration_ms=int((time.monotonic() - start) * 1000),
                    attempts=attempt,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                logger.warning(
                    "[dag] node %s attempt %d/%d failed: %s",
                    node.id, attempt, max_attempts, exc,
                )
                if attempt < max_attempts and self._on_fail == FailPolicy.RETRY:
                    await asyncio.sleep(0)  # yield to event loop between retries
                    continue
                break
        return NodeResult(
            id=node.id,
            status=NodeStatus.FAILED,
            error=last_error,
            duration_ms=int((time.monotonic() - start) * 1000),
            attempts=max_attempts,
        )

    async def _invoke(self, node: TaskNode) -> Any:
        """Invoke ``node.func`` with timeout and optional CPU offload."""
        func = node.func
        assert func is not None  # guarded by caller

        awaitable: Awaitable[Any]
        if node.is_cpu_intensive:
            pool = self._pool or get_worker_pool()
            awaitable = pool.run_cpu(func, *node.args, **node.kwargs)
        else:
            result = func(*node.args, **node.kwargs)
            if isinstance(result, Awaitable):
                awaitable = result
            else:
                # Sync function — wrap so timeout logic is uniform
                async def _wrap_sync() -> Any:
                    return result
                awaitable = _wrap_sync()

        if node.timeout > 0:
            return await asyncio.wait_for(awaitable, timeout=node.timeout)
        return await awaitable

    def _resolve_max_attempts(self, node: TaskNode) -> int:
        """Return total attempts (1 + retries) for ``node``."""
        retries = node.retry if node.retry >= 0 else self._max_retries
        if self._on_fail == FailPolicy.RETRY:
            return 1 + max(0, retries)
        return 1

    # ── Introspection ─────────────────────────────────────────

    @property
    def nodes(self) -> dict[str, TaskNode]:
        return dict(self._nodes)

    @property
    def edges(self) -> list[tuple[str, str]]:
        return list(self._edges)

    def layers(self) -> list[list[str]]:
        """Return the topological layers (for inspection/debugging)."""
        return self._compute_layers()


# ── Convenience: run a flat list of coroutines (gather replacement) ─

async def run_all(
    coros: list[Awaitable[Any]],
    *,
    on_fail: FailPolicy | str = FailPolicy.ABORT,
    return_exceptions: bool = True,
) -> list[Any]:
    """Run a flat list of awaitables with the scheduler's failure policy.

    Equivalent to :func:`asyncio.gather` but with configurable
    failure propagation. Used as a drop-in replacement in
    :class:`~maop.engine.Engine` when no inter-task dependencies
    exist.
    """
    nodes: list[TaskNode] = []
    for i, coro in enumerate(coros):
        # Wrap the already-started coroutine in a no-arg closure.
        # We cannot re-await a coroutine object from a fresh call,
        # so we store it and return it on invocation.
        nodes.append(TaskNode(
            id=f"_anon_{i}",
            func=_await_prepared,
            args=(coro,),
        ))
    sched = DAGScheduler(
        nodes, on_fail=on_fail, return_exceptions=return_exceptions,
    )
    dag_res = await sched.run()
    # Preserve input order
    return [
        dag_res.results[f"_anon_{i}"].output
        if dag_res.results[f"_anon_{i}"].status == NodeStatus.SUCCESS
        else dag_res.results[f"_anon_{i}"].error
        for i in range(len(coros))
    ]


async def _await_prepared(coro: Awaitable[Any]) -> Any:
    """Await a pre-constructed coroutine/object."""
    return await coro