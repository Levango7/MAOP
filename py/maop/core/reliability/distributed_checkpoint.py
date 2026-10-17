"""MAOP Distributed Checkpoint — Redis-backed DAG execution resume.

Extends the single-node ``PipelineCheckpoint`` with a distributed mode
that persists DAG run state to Redis so that *any* worker can resume a
run after a crash. The checkpoint tracks per-layer completion so the
scheduler re-dispatches only un-finished layers on resume.

Components
----------
1. ``DAGCheckpoint`` — per-run checkpoint record in Redis:
   - ``run_id``, ``layers`` (serialized DAG structure)
   - ``completed_layers`` (set of layer indices done)
   - ``pending_layers`` (set of layer indices still to process)
   - ``results`` (partial results for completed nodes)
   - ``status`` (running / completed / failed)

2. ``DAGCheckpointManager`` — lifecycle + Redis schema:
   - ``create_run`` → new DAG checkpoint
   - ``mark_layer_done`` → record a completed layer
   - ``recover_run`` → load + resume an interrupted run
   - ``list_interrupted`` → find runs that need recovery

Storage keys:
  - ``maop:dagckpt:{run_id}`` (hash — run metadata + results)
  - ``maop:dagckpt:{run_id}:completed`` (set — completed layer indices)
  - ``maop:dagckpt:{run_id}:pending`` (set — pending layer indices)

Usage::

    from maop.core.reliability.distributed_checkpoint import DAGCheckpointManager

    mgr = DAGCheckpointManager(redis_client)
    run = await mgr.create_run("run-123", layers=[["a"], ["b", "c"]])

    # After layer 0 completes
    await mgr.mark_layer_done("run-123", 0, {"a": {"status": "success"}})

    # After crash, on restart:
    recovered = await mgr.recover_run("run-123")
    # recovered.pending_layers == {1}
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)


class DAGCheckpoint:
    """Immutable snapshot of a distributed DAG run checkpoint.

    Attributes
    ----------
    run_id : str
        Unique run identifier.
    layers : list[list[str]]
        The DAG layer structure: each layer is a list of node ids that
        can be executed in parallel.
    completed_layers : set[int]
        Indices of layers that have fully completed.
    pending_layers : set[int]
        Indices of layers that still need processing.
    results : dict[str, dict]
        Accumulated results for nodes that have completed.
    status : str
        ``"running"`` | ``"completed"`` | ``"failed"``.
    created_at : float
        Unix timestamp of run creation.
    updated_at : float
        Unix timestamp of last update.
    """

    def __init__(
        self,
        run_id: str,
        layers: list[list[str]],
        *,
        completed_layers: set[int] | None = None,
        pending_layers: set[int] | None = None,
        results: dict[str, dict[str, Any]] | None = None,
        status: str = "running",
        created_at: float | None = None,
        updated_at: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.run_id = run_id
        self.layers = layers
        # NOTE: ``x or default`` is wrong for these fields — an empty set and a
        # 0.0 timestamp are both falsy but meaningful. With ``or``, a checkpoint
        # deserialized from Redis (pending="[]") would be re-filled with *every*
        # layer, so recover_run() re-ran completed layers and is_complete could
        # never be true. Use explicit ``is None`` checks.
        self.completed_layers = completed_layers if completed_layers is not None else set()
        self.pending_layers = (
            pending_layers if pending_layers is not None else set(range(len(layers)))
        )
        self.results = results if results is not None else {}
        self.status = status
        self.created_at = created_at if created_at is not None else time.time()
        self.updated_at = updated_at if updated_at is not None else time.time()
        self.metadata = metadata if metadata is not None else {}

    @property
    def total_layers(self) -> int:
        return len(self.layers)

    @property
    def progress(self) -> float:
        total = len(self.layers)
        if total == 0:
            return 1.0
        return len(self.completed_layers) / total

    @property
    def is_complete(self) -> bool:
        return len(self.pending_layers) == 0 and self.status == "completed"

    @property
    def next_layer(self) -> int | None:
        """Smallest pending layer index, or None."""
        if not self.pending_layers:
            return None
        return min(self.pending_layers)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "layers": json.dumps(self.layers),
            "completed": json.dumps(sorted(self.completed_layers)),
            "pending": json.dumps(sorted(self.pending_layers)),
            "results": json.dumps(self.results),
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "metadata": json.dumps(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DAGCheckpoint:
        """Deserialize from Redis hash."""
        return cls(
            run_id=data["run_id"],
            layers=json.loads(data["layers"]),
            completed_layers=set(json.loads(data.get("completed", "[]"))),
            pending_layers=set(json.loads(data.get("pending", "[]"))),
            results=json.loads(data.get("results", "{}")),
            status=data.get("status", "running"),
            created_at=float(data.get("created_at", 0)),
            updated_at=float(data.get("updated_at", 0)),
            metadata=json.loads(data.get("metadata", "{}")),
        )

    def __repr__(self) -> str:
        return (
            f"DAGCheckpoint(run_id={self.run_id!r}, "
            f"layers={self.total_layers}, "
            f"completed={len(self.completed_layers)}/{self.total_layers}, "
            f"status={self.status!r})"
        )


class DAGCheckpointManager:
    """Manages DAG run checkpoints in Redis.

    Parameters
    ----------
    redis : Redis client
        ``redis.Redis`` or ``fakeredis.FakeRedis`` instance.
    ttl : int (default=86400)
        Auto-expire time for checkpoint data (24h).
    """

    def __init__(self, redis: Any, *, ttl: int = 86400) -> None:
        self._redis = redis
        self._ttl = ttl

    def _run_key(self, run_id: str) -> str:
        return f"maop:dagckpt:{run_id}"

    def _completed_key(self, run_id: str) -> str:
        return f"maop:dagckpt:{run_id}:completed"

    def _pending_key(self, run_id: str) -> str:
        return f"maop:dagckpt:{run_id}:pending"

    async def create_run(
        self,
        run_id: str,
        layers: list[list[str]],
        *,
        metadata: dict[str, Any] | None = None,
    ) -> DAGCheckpoint:
        """Create a new DAG run checkpoint.

        Initializes all layers as pending. Returns the new DAGCheckpoint.
        """
        ckpt = DAGCheckpoint(
            run_id=run_id,
            layers=layers,
            pending_layers=set(range(len(layers))),
            metadata=metadata or {},
        )
        await self._persist(ckpt, init=True)
        logger.info(
            "[dagckpt] created run %s (%d layers)", run_id, len(layers),
        )
        return ckpt

    async def mark_layer_done(
        self,
        run_id: str,
        layer_idx: int,
        layer_results: dict[str, dict[str, Any]] | None = None,
        *,
        status: str = "success",
    ) -> DAGCheckpoint | None:
        """Mark a layer as completed.

        Atomically moves the layer index from pending → completed set
        in Redis using a Lua pipeline.
        """
        run_key = self._run_key(run_id)
        completed_key = self._completed_key(run_id)
        pending_key = self._pending_key(run_id)

        try:
            pipe = self._redis.pipeline()
            pipe.sadd(completed_key, layer_idx)
            pipe.srem(pending_key, layer_idx)
            pipe.expire(completed_key, self._ttl)
            pipe.expire(pending_key, self._ttl)
            if layer_results:
                # Update results hash
                existing = self._redis.hget(run_key, "results")
                merged = json.loads(existing.decode() if isinstance(existing, bytes) else existing or "{}")
                merged.update(layer_results)
                pipe.hset(run_key, "results", json.dumps(merged))
            pipe.hset(run_key, "updated_at", str(time.time()))
            pipe.expire(run_key, self._ttl)
            await asyncio.get_event_loop().run_in_executor(None, pipe.execute)
        except Exception as exc:
            logger.error("[dagckpt] mark_layer_done failed for %s layer %d: %s", run_id, layer_idx, exc)
            return None

        # Check if all layers done
        remaining = await self._count_pending(run_id)
        if remaining == 0:
            try:
                self._redis.hset(run_key, "status", "completed")
            except Exception:  # silent-ok: best-effort-cleanup — 附属状态标记，下轮重驱动
                pass

        return await self.get_run(run_id)

    async def mark_failed(self, run_id: str, error: str = "") -> None:
        """Mark a run as failed."""
        try:
            self._redis.hset(
                self._run_key(run_id), mapping={
                    "status": "failed",
                    "updated_at": str(time.time()),
                    "metadata": json.dumps({"error": error}),
                },
            )
        except Exception as exc:
            logger.warning("[dagckpt] mark_failed error: %s", exc)

    async def recover_run(self, run_id: str) -> DAGCheckpoint | None:
        """Load an interrupted run for recovery.

        Returns the checkpoint with pending layers ready to be re-dispatched.
        """
        ckpt = await self.get_run(run_id)
        if ckpt is None:
            return None
        # status stays "running" (skipped layers are already excluded)
        logger.info(
            "[dagckpt] recovered run %s: %d/%d layers pending",
            run_id,
            len(ckpt.pending_layers),
            ckpt.total_layers,
        )
        return ckpt

    async def get_run(self, run_id: str) -> DAGCheckpoint | None:
        """Load a checkpoint by run_id."""
        try:
            data_raw = self._redis.hgetall(self._run_key(run_id))
            if not data_raw:
                return None
            # Decode bytes keys/values
            data: dict[str, Any] = {}
            for k, v in data_raw.items():
                key = k.decode() if isinstance(k, bytes) else k
                val = v.decode() if isinstance(v, bytes) else v
                data[key] = val
                if key == "run_id":
                    data["run_id"] = val
            ckpt = DAGCheckpoint.from_dict(data)
            # The :pending / :completed SETS are the source of truth.
            # mark_layer_done() updates them atomically but never rewrites the
            # hash's JSON copies, so the hash still carries the state as of
            # create_run() — trusting it made recover_run() re-dispatch layers
            # that had already completed. Both keys share the run's TTL, so a
            # missing set means the whole checkpoint is gone (hgetall was empty
            # above), never a partially-expired run.
            ckpt.pending_layers = await self.get_pending_layers(run_id)
            ckpt.completed_layers = await self.get_completed_layers(run_id)
            return ckpt
        except Exception as exc:
            logger.warning("[dagckpt] get_run error for %s: %s", run_id, exc)
            return None

    async def list_interrupted(self) -> list[DAGCheckpoint]:
        """List all runs that are not completed/failed (need recovery)."""
        try:
            keys = self._redis.keys("maop:dagckpt:*")
            # Filter out sub-keys (:completed / :pending)
            run_keys = [
                k.decode() if isinstance(k, bytes) else k
                for k in keys
                if not k.decode().endswith(":completed") and not k.decode().endswith(":pending")
            ]
            results = []
            for key in run_keys:
                try:
                    data_raw = self._redis.hgetall(key)
                    if not data_raw:
                        continue
                    data = {
                        (k.decode() if isinstance(k, bytes) else k): (v.decode() if isinstance(v, bytes) else v)
                        for k, v in data_raw.items()
                    }
                    ckpt = DAGCheckpoint.from_dict(data)
                    if ckpt.status in ("running", None):
                        # Same reason as get_run(): the sets are authoritative.
                        ckpt.pending_layers = await self.get_pending_layers(ckpt.run_id)
                        ckpt.completed_layers = await self.get_completed_layers(ckpt.run_id)
                        results.append(ckpt)
                except Exception:  # noqa: S112  # silent-ok: scan-continue — 逐 key 加载
                    continue
            return results
        except Exception as exc:
            logger.warning("[dagckpt] list_interrupted error: %s", exc)
            return []

    async def delete_run(self, run_id: str) -> None:
        """Delete a run checkpoint and its sub-keys."""
        try:
            pipe = self._redis.pipeline()
            pipe.delete(self._run_key(run_id))
            pipe.delete(self._completed_key(run_id))
            pipe.delete(self._pending_key(run_id))
            pipe.execute()
        except Exception as exc:
            logger.warning("[dagckpt] delete_run error for %s: %s", run_id, exc)

    async def _persist(self, ckpt: DAGCheckpoint, *, init: bool = False) -> None:
        """Persist checkpoint to Redis."""
        data = ckpt.to_dict()
        try:
            if init:
                pipe = self._redis.pipeline()
                pipe.hset(self._run_key(ckpt.run_id), mapping=data)
                # Initialize pending set
                for idx in ckpt.pending_layers:
                    pipe.sadd(self._pending_key(ckpt.run_id), idx)
                pipe.expire(self._run_key(ckpt.run_id), self._ttl)
                pipe.expire(self._pending_key(ckpt.run_id), self._ttl)
                pipe.expire(self._completed_key(ckpt.run_id), self._ttl)
                pipe.execute()
            else:
                self._redis.hset(self._run_key(ckpt.run_id), mapping=data)
        except Exception as exc:
            raise RuntimeError(f"persist failed for {ckpt.run_id}: {exc}") from exc

    async def _count_pending(self, run_id: str) -> int:
        """Count pending layer indices."""
        try:
            return int(self._redis.scard(self._pending_key(run_id)))
        except Exception:
            return 0

    async def get_completed_layers(self, run_id: str) -> set[int]:
        """Return the set of completed layer indices."""
        try:
            members = self._redis.smembers(self._completed_key(run_id))
            return {int(m.decode() if isinstance(m, bytes) else m) for m in members}
        except Exception:
            return set()

    async def get_pending_layers(self, run_id: str) -> set[int]:
        """Return the set of pending layer indices."""
        try:
            members = self._redis.smembers(self._pending_key(run_id))
            return {int(m.decode() if isinstance(m, bytes) else m) for m in members}
        except Exception:
            return set()


# ── Global singleton ──────────────────────────────────────────────

_checkpoint_manager: DAGCheckpointManager | None = None
_checkpoint_lock = asyncio.Lock()


async def get_checkpoint_manager(redis: Any) -> DAGCheckpointManager:
    """Get process-wide DAGCheckpointManager singleton."""
    global _checkpoint_manager
    if _checkpoint_manager is None:
        async with _checkpoint_lock:
            if _checkpoint_manager is None:
                _checkpoint_manager = DAGCheckpointManager(redis)
    return _checkpoint_manager


def reset_checkpoint_manager() -> None:
    """Reset singleton (testing only)."""
    global _checkpoint_manager
    _checkpoint_manager = None
