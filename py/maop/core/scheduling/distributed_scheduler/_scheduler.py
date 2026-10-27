"""Distributed scheduler main class.

Extracted from ``distributed_scheduler.py`` during the subpackage split.
Holds :class:`DistributedScheduler`, which composes the worker-selection,
failure-detection, and result-stream mixins into the public scheduler.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections import defaultdict
from typing import TYPE_CHECKING, Any

from maop.core.scheduling.distributed_scheduler._failure import _FailureDetectionMixin
from maop.core.scheduling.distributed_scheduler._models import (
    _F_AFFINITY,
    _F_LAYER,
    _F_NODE_ID,
    _F_PAYLOAD,
    _F_PRIORITY,
    _F_RUN_ID,
    _F_WORKER_ID,
    _SCHED_NS,
    DistributedResult,
    SchedulingError,
    _json_dumps,
    _NodeSpec,
)
from maop.core.scheduling.distributed_scheduler._results import _ResultsMixin
from maop.core.scheduling.distributed_scheduler._worker_selection import (
    _WorkerSelectionMixin,
)
from maop.core.scheduling.failure_detector import (
    FailurePatternDetector,
    get_failure_detector,
)
from maop.core.scheduling.worker_pool import WorkerRegistry

try:  # AgentIdentity 联邦映射（阶段1：身份与健康）
    from maop.core.agent.identity import get_agent_identity
except Exception:  # pragma: no cover - 导入失败不应阻塞调度器
    get_agent_identity = None  # type: ignore[assignment]

try:  # AgentCatalog 健康标志（阶段1：身份与健康）
    from maop.core.agent.registry.agent_catalog import AgentCatalog
except Exception:  # pragma: no cover - 导入失败不应阻塞调度器
    AgentCatalog = None  # type: ignore[assignment,misc]

if TYPE_CHECKING:
    import redis  # noqa: F401

logger = logging.getLogger(__name__)


class DistributedScheduler(
    _WorkerSelectionMixin,
    _FailureDetectionMixin,
    _ResultsMixin,
):
    """Dispatch a DAG of nodes to a Redis Streams task queue.

    职责边界（阶段2 P1-1）：
      * 只负责 **DAG 分层 + Redis Streams 分布式分发**：拓扑分层、入队
        ``maop:tasks``、多 worker 能力亲和匹配、失败自动检测与重排、
        结果聚合。
      * 不负责单 agent 的直接执行与 driver 调用——这些属于
        :class:`~maop.delegate.dispatch_core.Dispatcher`。
      * 两者不互相调用：DistributedScheduler 不调用 Dispatcher.driver，
        Dispatcher 不向 DistributedScheduler 入队。

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
        ``results_prefix + ":" + run_id``.
    poll_interval : float
        Seconds between results-stream polls when waiting for a layer to
        complete. Default 0.1s.
    failure_check_interval : float
        Seconds between worker-failure detection sweeps. Default 1.0s.
    max_retries : int
        Per-node retry count on worker failure. Default 2.
    max_collect_wait : float
        Safety cap used when ``_dispatch_and_collect`` is called *without* an
        explicit deadline. ``deadline=None`` must not mean "wait forever" — one
        lost result message would leave ``while pending`` spinning and the run
        would look alive while never advancing. Default 3600s (1 hour), which is
        deliberately far above any legitimate layer duration so it only fires on
        lost-message / dead-worker situations.
    """

    def __init__(
        self,
        redis_client: Any,
        *,
        registry: WorkerRegistry | None = None,
        task_stream: str = _SCHED_NS + ":tasks",
        results_prefix: str = _SCHED_NS + ":results",
        poll_interval: float = 0.1,
        failure_check_interval: float = 1.0,
        max_retries: int = 2,
        failure_detector: FailurePatternDetector | None = None,
        max_collect_wait: float = 3600.0,
    ) -> None:
        self._redis = redis_client
        self._registry = registry or WorkerRegistry(redis_client)
        self._task_stream = task_stream
        self._results_prefix = results_prefix
        self._poll_interval = max(0.01, float(poll_interval))
        self._failure_check_interval = max(0.1, float(failure_check_interval))
        self._max_retries = max(0, int(max_retries))
        self._max_collect_wait = max(1.0, float(max_collect_wait))
        self._failure_detector: FailurePatternDetector = failure_detector or get_failure_detector()
        self._ensure_task_group()

    # ── Setup ────────────────────────────────────────────────────

    def _ensure_task_group(self) -> None:
        """Create the worker consumer group on the task stream (idempotent)."""
        try:
            self._redis.xgroup_create(
                self._task_stream,
                "maop_workers",
                id="0",
                mkstream=True,
            )
        except Exception as exc:
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
        deadline_s: float | None = None,
    ) -> DistributedResult:
        """Execute a DAG of nodes across the distributed worker pool."""
        if not run_id:
            run_id = uuid.uuid4().hex[:16]
        start = time.monotonic()
        deadline = start + deadline_s if deadline_s else None
        node_map = {n.id: n for n in nodes}
        layers = self._compute_layers(nodes)
        results: dict[str, dict[str, Any]] = {
            n.id: {"status": "pending", "node_id": n.id} for n in nodes
        }
        failed: set[str] = set()

        reschedule_queue: asyncio.Queue[tuple[str, str]] = asyncio.Queue()
        stop_failure_check = asyncio.Event()
        failure_task = asyncio.ensure_future(
            self._failure_detection_loop(run_id, reschedule_queue, stop_failure_check),
        )

        try:
            for layer_idx, layer in enumerate(layers):
                if deadline is not None and time.monotonic() > deadline:
                    logger.error(
                        "Distributed run %s exceeded its %.0fs deadline",
                        run_id,
                        deadline_s,
                    )
                    raise SchedulingError("Distributed run exceeded its deadline")
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

                layer_results, layer_spawns = await self._dispatch_and_collect(
                    run_id,
                    layer_idx,
                    runnable,
                    reschedule_queue,
                    deadline,
                )
                for nid, res in layer_results.items():
                    results[nid] = res
                    if res.get("status") == "failed":
                        failed.add(nid)

                if layer_spawns:
                    self._merge_spawns(
                        layer_spawns,
                        node_map,
                        layers,
                        results,
                        run_id,
                    )

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
            except (asyncio.CancelledError, Exception):  # silent-ok: interrupt
                pass
            try:
                self._redis.delete(self._results_stream(run_id))
            except Exception as exc:
                logger.debug(
                    "[dist-sched] results stream cleanup failed for %s: %s",
                    run_id,
                    exc,
                )

        total_ms = int((time.monotonic() - start) * 1000)
        success = (
            all(r["status"] in ("success", "skipped") for r in results.values()) and not failed
        )
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
                    logger.error("Node %s depends on unknown node %s", n.id, dep)
                    raise SchedulingError("Node depends on unknown node")
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
            logger.error("Dependency cycle: %s", " -> ".join(chain))
            raise SchedulingError("Dependency cycle detected")
        return layers

    # ── Dispatch + collect ───────────────────────────────────────

    async def _dispatch_and_collect(
        self,
        run_id: str,
        layer_idx: int,
        nodes: list[_NodeSpec],
        reschedule_queue: asyncio.Queue[tuple[str, str]],
        deadline: float | None = None,
    ) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
        """Dispatch a layer's nodes and wait for all their results."""
        results_stream = self._results_stream(run_id)
        pending: dict[str, _NodeSpec] = {n.id: n for n in nodes}
        retries: dict[str, int] = {n.id: 0 for n in nodes}
        collected: dict[str, dict[str, Any]] = {}
        all_spawns: list[dict[str, Any]] = []
        msg_to_node: dict[str, str] = {}
        cursor: tuple[int, int] | None = None

        run_started = time.monotonic()
        effective_deadline = (
            float(deadline) if deadline is not None else run_started + self._max_collect_wait
        )

        for node in nodes:
            msg_id = await self._dispatch_node(run_id, layer_idx, node)
            msg_to_node[msg_id] = node.id

        while pending:
            if time.monotonic() > effective_deadline:
                waited = time.monotonic() - run_started
                origin = (
                    f"caller deadline={deadline}s"
                    if deadline is not None
                    else f"safety cap={int(self._max_collect_wait)}s (no explicit deadline)"
                )
                logger.error(
                    "Distributed run %s gave up after %.1fs waiting for layer %d nodes %s (%s)",
                    run_id,
                    waited,
                    layer_idx,
                    sorted(pending),
                    origin,
                )
                raise SchedulingError(
                    f"Distributed run {run_id} gave up after {waited}s waiting for "
                    f"layer {layer_idx} nodes {sorted(pending)} ({origin}). Most likely "
                    f"a result message was lost or a worker died without posting — "
                    f"retry with an explicit deadline."
                )
            while not reschedule_queue.empty():
                _old_msg_id, node_id = reschedule_queue.get_nowait()
                r_node = pending.get(node_id)
                if r_node is None:
                    continue
                if retries[node_id] >= self._max_retries:
                    collected[node_id] = {
                        "status": "failed",
                        "node_id": node_id,
                        "error": "Exhausted retries on worker failures",
                    }
                    pending.pop(node_id, None)
                    continue
                retries[node_id] += 1
                new_msg_id = await self._dispatch_node(run_id, layer_idx, r_node)
                msg_to_node[new_msg_id] = node_id

            new_results, new_spawns, cursor = self._read_results(
                results_stream,
                set(pending),
                cursor,
            )
            all_spawns.extend(new_spawns)
            for node_id, res in new_results.items():
                collected[node_id] = res
                pending.pop(node_id, None)

            if pending:
                await asyncio.sleep(self._poll_interval)

        return collected, all_spawns

    async def _dispatch_node(
        self,
        run_id: str,
        layer_idx: int,
        node: _NodeSpec,
    ) -> str:
        """Dispatch a single node to the task stream; return stream msg id."""
        capable = self._registry.capable_workers(node.affinity.required)

        # 阶段1：身份与健康 — 过滤掉 unhealthy/drained 的 worker。
        # worker_id 映射到 agent_name，再检查 AgentCatalog.healthy
        # 和 FailurePatternDetector weight。
        healthy_capable: list[str] = []
        if get_agent_identity is not None and AgentCatalog is not None:
            _identity = get_agent_identity()
            _catalog = AgentCatalog.default()
            _detector = self._failure_detector
            for wid in capable:
                agent_names = _identity.resolve_agent_name(worker_id=wid)
                if agent_names is None:
                    # 找不到映射，保守放行（避免误杀未注册的 worker）
                    healthy_capable.append(wid)
                    continue
                agent_name = agent_names
                _desc = _catalog.get(agent_name)
                if _desc is not None and not _desc.healthy:
                    logger.debug(
                        "[dist-sched] skipping unhealthy agent=%s (worker=%s)",
                        agent_name, wid,
                    )
                    continue
                if _detector.get_weight(agent_name) <= 0.0:
                    logger.debug(
                        "[dist-sched] skipping drained agent=%s (worker=%s)",
                        agent_name, wid,
                    )
                    continue
                healthy_capable.append(wid)
        else:
            healthy_capable = list(capable)

        selected = self._select_worker(
            node.affinity.required, capable=healthy_capable,
        )
        if not capable and node.affinity.required:
            logger.warning(
                "[dist-sched] node %s requires affinity %s but no capable worker; "
                "dispatching anyway",
                node.id,
                sorted(node.affinity.required),
            )
        if selected is not None:
            self._registry.assign_task(selected, node.id)

        payload = {
            _F_NODE_ID: node.id,
            _F_RUN_ID: run_id,
            _F_LAYER: layer_idx,
            _F_PRIORITY: int(node.priority),
            _F_AFFINITY: ",".join(sorted(node.affinity.required)),
            _F_PAYLOAD: _json_dumps(node.payload),
        }
        if selected is not None:
            payload[_F_WORKER_ID] = selected
        fields = {k: str(v) for k, v in payload.items()}
        msg_id = self._redis.xadd(self._task_stream, fields)
        msg_id_str = msg_id.decode() if isinstance(msg_id, bytes) else msg_id
        logger.debug(
            "[dist-sched] dispatched node %s (layer=%d, priority=%d, worker=%s)",
            node.id,
            layer_idx,
            node.priority,
            selected,
        )
        return msg_id_str

    # ── Introspection ────────────────────────────────────────────

    @property
    def registry(self) -> WorkerRegistry:
        return self._registry

    @property
    def task_stream(self) -> str:
        return self._task_stream

    @property
    def failure_detector(self) -> FailurePatternDetector:
        """Return the wired :class:`FailurePatternDetector` (F1-02)."""
        return self._failure_detector

    def __repr__(self) -> str:
        return str(
            "DistributedScheduler(stream="
            + self._task_stream
            + ", workers="
            + str(self._registry.active_count())
            + ")"
        )
