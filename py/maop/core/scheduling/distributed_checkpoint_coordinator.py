"""MAOP Distributed DAG — Checkpoint Coordinator with Layer-Level Resume.

Wraps :class:`~maop.core.scheduling.distributed_scheduler.DistributedScheduler`
so that a multi-layer DAG run can survive coordinator crashes. After each
layer completes, checkpoint state is persisted to Redis. On restart, the
coordinator detects an in-progress run and resumes from the last completed
layer instead of re-executing already-finished work.

Usage::

    scheduler = DistributedScheduler(redis_client)
    coord = DistributedCheckpointCoordinator(scheduler, redis_client)
    result = coord_run = await coord.run_with_checkpoint(
        run_id="run-123", nodes=layers, node_map=node_map,
    )
    # On crash + restart: pass resume_from=<run_id> to resume
    result = await coord.run_with_checkpoint(run_id="run-123", resume_from="run-123", ...)

Design reference: docs/design-ha-checkpoint.md §Coordinator Resume (M2-C).
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# ── Redis key patterns ─────────────────────────────────────────────

_CHECKPOINT_HASH = "maop:sched:checkpoints"  # hash: run_id → JSON checkpoint
_LAYER_STATUS_HASH = "maop:sched:layer-status"  # hash: f"{run_id}:{layer}" → result_ids JSON
_RUN_META = "maop:sched:run-meta"  # hash: run_id → run metadata JSON


# ── Checkpoint data structures ─────────────────────────────────────


@dataclass
class LayerCheckpoint:
    """Checkpoint for a single completed layer."""

    layer_idx: int
    node_ids: list[str]
    status: str = "completed"  # completed | failed | partial
    timestamp: float = field(default_factory=time.monotonic)
    duration_ms: int = 0


@dataclass
class DAGRunCheckpoint:
    """Full checkpoint for a distributed DAG run."""

    run_id: str
    total_layers: int = 0
    completed_layers: list[LayerCheckpoint] = field(default_factory=list)
    failed_nodes: list[str] = field(default_factory=list)
    status: str = "pending"  # pending | running | completed | resumed
    created_at: float = field(default_factory=time.monotonic)
    updated_at: float = field(default_factory=time.monotonic)
    resume_count: int = 0
    node_map_snapshot: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "total_layers": self.total_layers,
            "completed_layers": [
                {
                    "layer_idx": lc.layer_idx,
                    "node_ids": lc.node_ids,
                    "status": lc.status,
                    "timestamp": lc.timestamp,
                    "duration_ms": lc.duration_ms,
                }
                for lc in self.completed_layers
            ],
            "failed_nodes": self.failed_nodes,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "resume_count": self.resume_count,
            "node_map_snapshot": self.node_map_snapshot,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> DAGRunCheckpoint:
        completed = []
        for lc in d.get("completed_layers", []):
            completed.append(LayerCheckpoint(
                layer_idx=lc["layer_idx"],
                node_ids=lc["node_ids"],
                status=lc.get("status", "completed"),
                timestamp=lc.get("timestamp", 0.0),
                duration_ms=lc.get("duration_ms", 0),
            ))
        return cls(
            run_id=d["run_id"],
            total_layers=d.get("total_layers", 0),
            completed_layers=completed,
            failed_nodes=d.get("failed_nodes", []),
            status=d.get("status", "pending"),
            created_at=d.get("created_at", 0.0),
            updated_at=d.get("updated_at", 0.0),
            resume_count=d.get("resume_count", 0),
            node_map_snapshot=d.get("node_map_snapshot", {}),
        )


# ── Distributed Checkpoint Coordinator ─────────────────────────────


class DistributedCheckpointCoordinator:
    """Wraps a scheduler to provide layer-level checkpoint + resume.

    Parameters
    ----------
    scheduler : object
        Must implement ``async run(run_id, nodes, node_map, ...)``.
    redis : _CheckpointStore
        Redis async client instance.
    """

    def __init__(self, scheduler: Any, redis: Any) -> None:
        self._scheduler = scheduler
        self._redis = redis

    # ── Public API ───────────────────────────────────────────────

    async def run_with_checkpoint(
        self,
        run_id: str,
        nodes: list[Any],
        node_map: dict[str, Any],
        resume_from: str | None = None,
        **scheduler_kwargs: Any,
    ) -> Any:
        """Run a DAG with checkpoint-based crash recovery.

        Parameters
        ----------
        run_id : str
            Unique identifier for this run.
        nodes : list
            Layered node list (as produced by the engine's DAG layering).
        node_map : dict
            Mapping from node id to node spec.
        resume_from : str | None
            If provided, attempt to resume from an existing checkpoint
            with this run_id instead of starting fresh.
        **scheduler_kwargs
            Additional kwargs passed through to the scheduler's ``run()``.

        Returns
        -------
        DistributedResult
            The final result of the distributed run.
        """
        checkpoint = await self._load_checkpoint(run_id)

        if resume_from and checkpoint and checkpoint.status in ("running", "pending"):
            logger.info(
                "[dag-checkpoint] resuming run %s from layer %d (resume #%d)",
                run_id, len(checkpoint.completed_layers), checkpoint.resume_count + 1,
            )
            return await self._resume_from_checkpoint(
                checkpoint, nodes, node_map, **scheduler_kwargs,
            )

        # Fresh run
        checkpoint = DAGRunCheckpoint(
            run_id=run_id,
            total_layers=len(nodes),
            status="running",
            node_map_snapshot=self._serialize_node_map(node_map),
        )
        await self._save_checkpoint(checkpoint)
        return await self._execute_layers(checkpoint, nodes, node_map, **scheduler_kwargs)

    async def get_checkpoint(self, run_id: str) -> DAGRunCheckpoint | None:
        """Load the current checkpoint for a run."""
        return await self._load_checkpoint(run_id)

    async def list_incomplete_runs(self) -> list[str]:
        """Return run_ids that are still pending or running."""
        raw = await self._redis.hgetall(_CHECKPOINT_HASH)
        incomplete = []
        for v in raw.values():
            try:
                data = json.loads(v) if isinstance(v, (bytes, bytearray)) else v
            except (json.JSONDecodeError, TypeError):  # silent-ok: scan-continue
                continue
            status = data.get("status", "")
            if status in ("running", "pending"):
                incomplete.append(data.get("run_id", ""))
        return incomplete

    async def cleanup(self, run_id: str) -> None:
        """Delete all checkpoint data for a completed/abandoned run."""
        await self._redis.hdel(_CHECKPOINT_HASH, run_id)
        await self._redis.hdel(_RUN_META, run_id)
        # Layer status cleanup
        raw = await self._redis.hgetall(_LAYER_STATUS_HASH)
        to_delete = [
            key for key, _ in (
                raw.items() if hasattr(raw, 'items') else [])
            if isinstance(key, (bytes, str)) and (
                key.decode() if isinstance(key, bytes) else key
            ).startswith(f"{run_id}:")
        ]
        if to_delete:
            for k in to_delete:
                await self._redis.hdel(_LAYER_STATUS_HASH, k)

    # ── Internal: execute with checkpointing ─────────────────────

    async def _execute_layers(
        self,
        checkpoint: DAGRunCheckpoint,
        layers: list[list[str]],
        node_map: dict[str, Any],
        **scheduler_kwargs: Any,
    ) -> Any:
        """Execute the DAG run layer by layer, checkpointing each layer."""
        from maop.core.scheduling.distributed_scheduler import DistributedResult

        layer_results: dict[str, dict[str, Any]] = {}
        failed: set[str] = set()
        start_time = time.monotonic()

        try:
            for layer_idx, layer_nodes in enumerate(layers):
                # Skip already-completed layers on resume
                if layer_idx < len(checkpoint.completed_layers):
                    # Recover previous results for completed layer
                    completed = checkpoint.completed_layers[layer_idx]
                    logger.debug(
                        "[dag-checkpoint] skipping completed layer %d (%s)",
                        layer_idx, completed.node_ids,
                    )
                    continue

                # Skip nodes whose upstream failed
                runnable: list[str] = []
                for nid in layer_nodes:
                    node = node_map.get(nid)
                    if node is None:
                        continue
                    deps = getattr(node, 'depends_on', [])
                    if any(d in failed for d in deps):
                        layer_results[nid] = {
                            "status": "skipped",
                            "node_id": nid,
                            "error": "Upstream dependency failed",
                        }
                        failed.add(nid)
                    else:
                        runnable.append(nid)

                if not runnable:
                    continue

                # Dispatch this layer via the scheduler
                layer_start = time.monotonic()
                try:
                    scheduler_result = await self._scheduler.run(
                        run_id=f"{checkpoint.run_id}:L{layer_idx}",
                        nodes=[node_map[nid] for nid in runnable],
                        node_map=node_map,
                        **scheduler_kwargs,
                    )
                    # Collect results
                    if hasattr(scheduler_result, 'results'):
                        for nid, res in scheduler_result.results.items():
                            layer_results[nid] = res
                            if res.get("status") == "failed":
                                failed.add(nid)
                except Exception as exc:
                    logger.error(
                        "[dag-checkpoint] layer %d execution failed: %s",
                        layer_idx, exc,
                    )
                    for nid in runnable:
                        layer_results[nid] = {
                            "status": "failed",
                            "node_id": nid,
                            "error": str(exc),
                        }
                        failed.add(nid)
                layer_duration_ms = int((time.monotonic() - layer_start) * 1000)

                # Checkpoint this layer
                lc = LayerCheckpoint(
                    layer_idx=layer_idx,
                    node_ids=runnable,
                    status="completed" if not any(
                        layer_results.get(n, {}).get("status") == "failed" for n in runnable
                    ) else "partial",
                    duration_ms=layer_duration_ms,
                )
                checkpoint.completed_layers.append(lc)
                checkpoint.failed_nodes = list(failed)
                checkpoint.updated_at = time.monotonic()
                await self._save_checkpoint(checkpoint)

            # All layers done
            checkpoint.status = "completed"
            checkpoint.updated_at = time.monotonic()
            await self._save_checkpoint(checkpoint)

        except Exception:
            checkpoint.status = "running"  # keep as running for resume
            checkpoint.updated_at = time.monotonic()
            await self._save_checkpoint(checkpoint)
            raise

        total_ms = int((time.monotonic() - start_time) * 1000)
        success = not failed
        return DistributedResult(
            run_id=checkpoint.run_id,
            results=layer_results,
            success=success,
            total_duration_ms=total_ms,
        )

    async def _resume_from_checkpoint(
        self,
        checkpoint: DAGRunCheckpoint,
        layers: list[list[str]],
        node_map: dict[str, Any],
        **scheduler_kwargs: Any,
    ) -> Any:
        """Resume a previously interrupted run from its last completed layer."""
        checkpoint.resume_count += 1
        checkpoint.status = "resumed"
        checkpoint.updated_at = time.monotonic()
        await self._save_checkpoint(checkpoint)
        return await self._execute_layers(checkpoint, layers, node_map, **scheduler_kwargs)

    # ── Serialization ────────────────────────────────────────────

    async def _save_checkpoint(self, checkpoint: DAGRunCheckpoint) -> None:
        """Persist checkpoint to Redis."""
        data = json.dumps(checkpoint.to_dict())
        await self._redis.hset(_CHECKPOINT_HASH, checkpoint.run_id, data)
        await self._redis.hset(
            _RUN_META,
            checkpoint.run_id,
            json.dumps({
                "updated_at": checkpoint.updated_at,
                "status": checkpoint.status,
                "completed": len(checkpoint.completed_layers),
                "total": checkpoint.total_layers,
            }),
        )

    async def _load_checkpoint(self, run_id: str) -> DAGRunCheckpoint | None:
        """Load checkpoint from Redis."""
        raw = await self._redis.hget(_CHECKPOINT_HASH, run_id)
        if raw is None:
            return None
        try:
            data = json.loads(raw) if isinstance(raw, (bytes, bytearray)) else raw
        except (json.JSONDecodeError, TypeError):
            return None
        return DAGRunCheckpoint.from_dict(data)

    def _serialize_node_map(self, node_map: dict[str, Any]) -> dict[str, Any]:
        """Snapshot essential node_map data for resume."""
        result = {}
        for nid, node in node_map.items():
            result[nid] = {
                "id": nid,
                "depends_on": getattr(node, 'depends_on', []),
                "affinity": getattr(node, 'affinity', []),
                "priority": getattr(node, 'priority', 3),
            }
        return result
