"""Result-stream mixin for the distributed scheduler.

Extracted from ``distributed_scheduler.py`` during the subpackage split.
Holds :meth:`_ResultsMixin._read_results`, :meth:`_ResultsMixin._merge_spawns`,
:meth:`_ResultsMixin._parse_stream_id`, and :meth:`_ResultsMixin.post_result`.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from maop.core.scheduling.distributed_scheduler._models import (
    _F_DURATION_MS,
    _F_ERROR,
    _F_NODE_ID,
    _F_OUTPUT,
    _F_RUN_ID,
    _F_SPAWN,
    _F_STATUS,
    _F_WORKER_ID,
    NodeAffinity,
    _decode,
    _json_dumps,
    _NodeSpec,
)

if TYPE_CHECKING:
    from maop.core.scheduling.failure_detector import FailurePatternDetector
    from maop.core.scheduling.worker_pool import WorkerRegistry

logger: logging.Logger = logging.getLogger(__name__)


class _ResultsMixin:
    """Mixin providing result-stream methods for :class:`DistributedScheduler`.

    Requires ``self._redis``, ``self._results_prefix``, ``self._failure_detector``,
    and ``self._registry`` to be set by the concrete class's ``__init__``.
    """

    _redis: Any
    _results_prefix: str
    _failure_detector: FailurePatternDetector
    _registry: WorkerRegistry

    def _results_stream(self, run_id: str) -> str:
        """Return the per-run results stream key. Implemented on DistributedScheduler."""
        raise NotImplementedError  # pragma: no cover

    def _read_results(
        self,
        results_stream: str,
        expected: set[str],
        cursor: tuple[int, int] | None = None,
    ) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]], tuple[int, int]]:
        """Read completed results from the results stream (non-blocking).

        Returns ``(results, spawns, new_cursor)``: a dict ``{node_id:
        result_dict}``, a list of spawn directive dicts collected from
        result entries, and the ``(ms, seq)`` id of the last entry
        processed (``None``-cursor means "read from the head").

        The cursor makes reading incremental — every poll only
        processes entries the caller has not seen yet. Reading
        from the head on every poll was doubly wasteful: a layer
        with more than ``count`` posted results never surfaced
        its tail (the run hung forever waiting for them), and the
        failure detector re-recorded every completed task on
        every poll, drowning its weight window in duplicate
        samples.

        ``xrange``'s ``min`` is inclusive and some client-side
        fakes ignore it entirely, so already-seen entries are
        skipped with a numeric ``(ms, seq)`` comparison — stream
        ids are *not* lexicographically ordered.
        """
        spawns: list[dict[str, Any]] = []

        out: dict[str, dict[str, Any]] = {}
        min_id = "-" if cursor is None else f"{cursor[0]}-{cursor[1]}"
        try:
            entries = self._redis.xrange(results_stream, min=min_id, max="+", count=10000)
        except Exception as exc:
            logger.debug("[dist-sched] xrange on %s failed: %s", results_stream, exc)
            return out, spawns, cursor or (0, 0)
        for msg_id, fields in entries:
            parsed = self._parse_stream_id(_decode(msg_id))
            if cursor is not None and parsed <= cursor:
                continue
            node_id = _decode(fields.get(_F_NODE_ID.encode(), fields.get(_F_NODE_ID, b"")))

            # M0 L0.1-T0.1.8: collect spawn directives from result entries
            spawn_raw = _decode(fields.get(_F_SPAWN.encode(), fields.get(_F_SPAWN, b"")))
            if spawn_raw:
                try:
                    spawn_list = json.loads(spawn_raw) if isinstance(spawn_raw, str) else spawn_raw
                    if isinstance(spawn_list, list):
                        spawns.extend(spawn_list)
                except (json.JSONDecodeError, TypeError):
                    logger.debug("[dist-sched] spawn field parse failed: %s", spawn_raw)

            if not node_id or node_id not in expected:
                cursor = parsed
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
            # F1-02 (异常自适应调度): feed the outcome to the failure
            # detector so the worker's weight is updated for the next
            # dispatch. We treat any non-success status as a failure.
            if worker_id:
                try:
                    self._failure_detector.record_result(
                        worker_id,
                        success=(out[node_id]["status"] == "success"),
                        latency=(int(duration_raw or 0) / 1000.0),
                    )
                except Exception as exc:
                    logger.debug(
                        "[dist-sched] failure_detector.record_result failed: %s",
                        exc,
                    )
            # Free the in-flight slot in the registry.
            if worker_id:
                try:
                    self._registry.complete_task(worker_id, node_id)
                except Exception:
                    # registry 清理失败不应影响调度结果
                    logger.debug("complete_task failed", exc_info=True)
            cursor = parsed
        return out, spawns, cursor or (0, 0)

    def _merge_spawns(
        self,
        spawns: list[dict[str, Any]],
        node_map: dict[str, _NodeSpec],
        layers: list[list[str]],
        results: dict[str, dict[str, Any]],
        run_id: str,
    ) -> None:
        """Merge runtime-spawned steps into the DAG.

        Called after a layer completes, this method:
        1. Parses each spawn directive's ``steps`` and ``depends_on``.
        2. Validates (duplicate ids and unknown deps are dropped with a log).
        3. Adds valid spawns to ``node_map`` and appends a new layer.
        """
        new_layer: list[str] = []
        seen_ids: set[str] = set()
        for directive in spawns:
            deps_raw = directive.get("depends_on")
            steps_raw = directive.get("steps", [])
            if not steps_raw:
                continue
            for step in steps_raw:
                sid = step.get("id", "")
                if not sid or sid in node_map or sid in seen_ids:
                    logger.warning(
                        "[dist-sched] spawn: duplicate or empty step id %r; dropping",
                        sid,
                    )
                    continue
                # Resolve dependencies: default to the spawning step's id
                deps = list(deps_raw) if deps_raw else []
                unknown = [d for d in deps if d not in node_map]
                if unknown:
                    logger.warning(
                        "[dist-sched] spawn: step %r depends on unknown %s; dropping",
                        sid,
                        unknown,
                    )
                    continue
                # Build _NodeSpec for the spawned step
                payload = {k: v for k, v in step.items() if k not in ("id", "depends_on")}
                payload["workdir"] = payload.get("workdir", "")
                payload["trace_id"] = run_id
                affinity_str = payload.get("affinity")
                priority = int(payload.get("priority", 3))
                timeout = float(payload.get("timeout", 0))
                node_map[sid] = _NodeSpec(
                    id=sid,
                    depends_on=deps,
                    affinity=NodeAffinity.parse(affinity_str),
                    priority=priority,
                    timeout=timeout,
                    payload=payload,
                )
                seen_ids.add(sid)
                new_layer.append(sid)
        if new_layer:
            layers.append(new_layer)
            logger.info(
                "[dist-sched] run %s: merged %d spawned step(s) into new layer: %s",
                run_id,
                len(new_layer),
                new_layer,
            )

    @staticmethod
    def _parse_stream_id(mid: str) -> tuple[int, int]:
        """Parse a Redis stream id ``"<ms>-<seq>"`` into a tuple."""
        try:
            ms_str, _, seq_str = mid.partition("-")
            return int(ms_str), int(seq_str)
        except ValueError:
            return (0, 0)

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
        spawn_directives: list[dict[str, Any]] | None = None,
    ) -> str:
        """Post a node's execution result to the run's results stream.

        Called by :class:`~maop.worker.distributed_worker.DistributedWorker`
        after executing a task. When the executor returns spawn directives
        (runtime fan-out), pass them via ``spawn_directives`` so the
        scheduler can drain and re-enqueue them.

        Parameters
        ----------
        spawn_directives : list[dict] | None
            Optional list of {"steps": [...], "depends_on": [...]}
            directives generated by the executor.

        Returns the results-stream message id.
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
        if spawn_directives:
            fields[_F_SPAWN] = _json_dumps(spawn_directives)
        msg_id = self._redis.xadd(stream, fields)
        return msg_id.decode() if isinstance(msg_id, bytes) else msg_id
