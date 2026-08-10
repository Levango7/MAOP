"""MAOP Worker Pool Registry — Worker registration, heartbeat, failure detection.

F1-01 (分布式执行): maintains the set of active distributed workers in
Redis. Each worker registers itself, sends periodic heartbeats, and is
considered dead when its heartbeat expires. Dead workers' in-flight
tasks are automatically rescheduled by the
:class:`~maop.core.scheduling.distributed_scheduler.DistributedScheduler`.

Design
------
* **Registration** — ``register()`` adds a worker to the registry with a
  unique ``worker_id``, capabilities (tags), and a TTL on its heartbeat
  key. Returns the assigned id.
* **Heartbeat** — ``heartbeat()`` refreshes the TTL. Workers call this
  every ``heartbeat_interval`` seconds (default 5s); the registry TTL is
  ``heartbeat_timeout`` (default 15s, i.e. 3 missed beats).
* **Failure detection** — ``detect_failures()`` scans the registry and
  returns workers whose heartbeat key has expired. Their registration is
  pruned and their in-flight task ids are returned for rescheduling.
* **Affinity** — workers advertise ``capabilities`` (a set of tags); the
  scheduler queries ``capable_workers(tag)`` to find workers that can
  run a node with a given affinity requirement.

The registry is intentionally storage-agnostic: it talks to a Redis
client object (``redis.Redis`` or ``fakeredis.FakeRedis``). All keys are
namespaced under ``maop:workers:`` so the registry is safe to share a
Redis instance with other MAOP subsystems.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import redis  # noqa: F401  (for type hints only)

logger = logging.getLogger(__name__)

# Redis key namespace for worker registry. All keys are prefixed with this
# to avoid collisions with other MAOP subsystems sharing the same Redis.
_WORKER_NS = "maop:workers"
# Hash field names (kept short to minimise Redis memory).
_F_CAPABILITIES = "capabilities"
_F_CONCURRENCY = "concurrency"
_F_HOST = "host"
_F_LAST_HEARTBEAT = "last_heartbeat"
_F_REGISTERED_AT = "registered_at"
_F_STATUS = "status"


class WorkerStatus(str, Enum):
    """Lifecycle status of a distributed worker."""

    ACTIVE = "active"
    FAILED = "failed"
    DRAINING = "draining"  # graceful shutdown in progress
    STOPPED = "stopped"


@dataclass
class WorkerInfo:
    """Snapshot of a worker's registration metadata.

    Attributes
    ----------
    worker_id : str
        Unique identifier assigned at registration.
    host : str
        Hostname or address where the worker runs.
    concurrency : int
        Maximum concurrent tasks the worker accepts.
    capabilities : set[str]
        Tags advertising what the worker can run (e.g. ``{"gpu", "linux"}``).
        Used by the scheduler for node-affinity matching.
    registered_at : float
        Epoch timestamp (seconds) of registration.
    last_heartbeat : float
        Epoch timestamp (seconds) of the most recent heartbeat.
    status : WorkerStatus
        Current lifecycle status.
    in_flight : set[str]
        IDs of tasks currently assigned to this worker (tracked locally
        for rescheduling on failure; not persisted to Redis).
    """

    worker_id: str
    host: str = ""
    concurrency: int = 4
    capabilities: set[str] = field(default_factory=set)
    registered_at: float = field(default_factory=time.time)
    last_heartbeat: float = field(default_factory=time.time)
    status: WorkerStatus = WorkerStatus.ACTIVE
    in_flight: set[str] = field(default_factory=set)


class WorkerRegistry:
    """Redis-backed registry of distributed workers with heartbeat tracking.

    Parameters
    ----------
    redis_client : Any
        A ``redis.Redis`` (or ``fakeredis.FakeRedis``) client instance.
        The registry does not own the connection — the caller is
        responsible for closing it.
    heartbeat_timeout : float
        Seconds without a heartbeat after which a worker is considered
        failed. Default 15s (3 × default 5s heartbeat interval).
    key_prefix : str
        Redis key prefix for the registry namespace. Override only when
        running multiple isolated registries in the same Redis instance.
    """

    def __init__(
        self,
        redis_client: Any,
        *,
        heartbeat_timeout: float = 15.0,
        key_prefix: str = _WORKER_NS,
    ) -> None:
        self._redis = redis_client
        self._heartbeat_timeout = float(heartbeat_timeout)
        self._prefix = key_prefix
        # Local mirror of in-flight task ids per worker. Redis is the
        # source of truth for worker metadata; in-flight tracking is kept
        # locally because it changes on every assign/complete and would
        # generate excessive Redis writes. On failure detection we read
        # this map to know which tasks to reschedule.
        self._in_flight: dict[str, set[str]] = {}

    # ── Key helpers ──────────────────────────────────────────────

    def _registry_key(self) -> str:
        """Return the Redis hash key holding the set of registered worker ids."""
        return f"{self._prefix}:registry"

    def _worker_key(self, worker_id: str) -> str:
        """Return the Redis hash key for a single worker's metadata."""
        return f"{self._prefix}:{worker_id}"

    def _heartbeat_key(self, worker_id: str) -> str:
        """Return the Redis key used as the heartbeat TTL marker."""
        return f"{self._prefix}:hb:{worker_id}"

    # ── Registration ─────────────────────────────────────────────

    def register(
        self,
        *,
        host: str = "",
        concurrency: int = 4,
        capabilities: set[str] | None = None,
        worker_id: str = "",
    ) -> str:
        """Register a new worker and return its assigned ``worker_id``.

        Parameters
        ----------
        host : str
            Hostname or address (for diagnostics / affinity).
        concurrency : int
            Maximum concurrent tasks the worker accepts.
        capabilities : set[str] | None
            Affinity tags. ``None`` → empty set (worker accepts any node
            with no affinity requirement).
        worker_id : str
            Optional explicit id. When empty, a UUID4 hex prefix is
            generated. Explicit ids are useful for deterministic tests
            and for worker restarts that want to reclaim their identity.
        """
        if not worker_id:
            worker_id = uuid.uuid4().hex[:12]
        caps = capabilities or set()
        now = time.time()

        pipe = self._redis.pipeline()
        # 1. Add worker id to the registry set.
        pipe.sadd(self._registry_key(), worker_id)
        # 2. Store worker metadata in a hash.
        pipe.hset(self._worker_key(worker_id), mapping={
            _F_HOST: host,
            _F_CONCURRENCY: str(concurrency),
            _F_CAPABILITIES: ",".join(sorted(caps)),
            _F_REGISTERED_AT: str(now),
            _F_LAST_HEARTBEAT: str(now),
            _F_STATUS: WorkerStatus.ACTIVE.value,
        })
        # 3. Set heartbeat key with TTL = heartbeat_timeout.
        pipe.set(self._heartbeat_key(worker_id), str(now), ex=int(self._heartbeat_timeout))
        pipe.execute()

        self._in_flight[worker_id] = set()
        logger.info(
            "[worker-registry] registered worker %s (host=%s, concurrency=%d, caps=%s)",
            worker_id, host, concurrency, sorted(caps),
        )
        return worker_id

    def unregister(self, worker_id: str) -> bool:
        """Remove a worker from the registry (graceful shutdown).

        Returns ``True`` if the worker was registered, ``False`` otherwise.
        Does *not* reschedule in-flight tasks — the caller (worker) is
        expected to drain them before unregistering.
        """
        existed = bool(self._redis.srem(self._registry_key(), worker_id))
        self._redis.delete(self._worker_key(worker_id))
        self._redis.delete(self._heartbeat_key(worker_id))
        self._in_flight.pop(worker_id, None)
        if existed:
            logger.info("[worker-registry] unregistered worker %s", worker_id)
        return existed

    # ── Heartbeat ────────────────────────────────────────────────

    def heartbeat(self, worker_id: str) -> bool:
        """Refresh a worker's heartbeat TTL.

        Returns ``True`` if the heartbeat was refreshed, ``False`` if the
        worker is not registered (or was already pruned as failed).
        """
        if not bool(self._redis.sismember(self._registry_key(), worker_id)):
            return False
        now = time.time()
        pipe = self._redis.pipeline()
        pipe.set(self._heartbeat_key(worker_id), str(now), ex=int(self._heartbeat_timeout))
        pipe.hset(self._worker_key(worker_id), mapping={_F_LAST_HEARTBEAT: str(now)})
        pipe.execute()
        return True

    # ── Failure detection ────────────────────────────────────────

    def detect_failures(self) -> list[tuple[str, set[str]]]:
        """Scan the registry and prune workers whose heartbeat has expired.

        Returns
        -------
        list[tuple[str, set[str]]]
            One ``(worker_id, in_flight_task_ids)`` per failed worker.
            The caller (scheduler) reschedules these tasks.
        """
        failed: list[tuple[str, set[str]]] = []
        worker_ids = self.list_workers()
        now = time.time()
        for wid in worker_ids:
            hb = self._redis.get(self._heartbeat_key(wid))
            if hb is None:
                # Heartbeat key expired → worker is dead.
                in_flight = self._in_flight.pop(wid, set())
                # Prune registration.
                self._redis.srem(self._registry_key(), wid)
                self._redis.delete(self._worker_key(wid))
                # Mark status as failed in the metadata hash before delete
                # is a no-op (we already deleted); keep the local bookkeeping.
                failed.append((wid, set(in_flight)))
                logger.warning(
                    "[worker-registry] worker %s failed (heartbeat expired at %s, "
                    "in_flight=%d)",
                    wid, now, len(in_flight),
                )
        return failed

    # ── Affinity query ───────────────────────────────────────────

    def capable_workers(self, required: str | set[str] | None) -> list[str]:
        """Return ids of active workers whose capabilities satisfy ``required``.

        Parameters
        ----------
        required : str | set[str] | None
            A single tag, a set of tags (all must be present), or
            ``None``/empty (any worker qualifies).
        """
        if not required:
            return self.list_workers()
        needed: set[str] = {required} if isinstance(required, str) else set(required)
        capable: list[str] = []
        for wid in self.list_workers():
            caps = self._worker_capabilities(wid)
            if needed.issubset(caps):
                capable.append(wid)
        return capable

    def _worker_capabilities(self, worker_id: str) -> set[str]:
        """Return the capability-tag set for a worker."""
        raw = self._redis.hget(self._worker_key(worker_id), _F_CAPABILITIES)
        if raw is None:
            return set()
        if isinstance(raw, bytes):
            raw = raw.decode()
        return {t for t in raw.split(",") if t}

    # ── In-flight task tracking ──────────────────────────────────

    def assign_task(self, worker_id: str, task_id: str) -> None:
        """Record that ``task_id`` is now running on ``worker_id``."""
        self._in_flight.setdefault(worker_id, set()).add(task_id)

    def complete_task(self, worker_id: str, task_id: str) -> None:
        """Record that ``task_id`` has finished on ``worker_id``."""
        self._in_flight.get(worker_id, set()).discard(task_id)

    def in_flight(self, worker_id: str) -> set[str]:
        """Return the set of task ids currently assigned to ``worker_id``."""
        return set(self._in_flight.get(worker_id, set()))

    # ── Introspection ────────────────────────────────────────────

    def list_workers(self) -> list[str]:
        """Return ids of all workers currently in the registry."""
        ids = self._redis.smembers(self._registry_key())
        decoded: list[str] = []
        for wid in ids:
            if isinstance(wid, bytes):
                wid = wid.decode()
            decoded.append(wid)
        return sorted(decoded)

    def get_worker(self, worker_id: str) -> WorkerInfo | None:
        """Return a :class:`WorkerInfo` snapshot, or ``None`` if not registered."""
        raw = self._redis.hgetall(self._worker_key(worker_id))
        if not raw:
            return None
        # redis-py returns bytes; decode defensively.
        def _dec(v: Any) -> str:
            return v.decode() if isinstance(v, bytes) else v
        caps_raw = _dec(raw.get(_F_CAPABILITIES.encode(), raw.get(_F_CAPABILITIES, b"")))
        caps = {t for t in caps_raw.split(",") if t}
        status_raw = _dec(raw.get(_F_STATUS.encode(), raw.get(_F_STATUS, b"")))
        try:
            status = WorkerStatus(status_raw)
        except ValueError:
            status = WorkerStatus.ACTIVE
        return WorkerInfo(
            worker_id=worker_id,
            host=_dec(raw.get(_F_HOST.encode(), raw.get(_F_HOST, b""))),
            concurrency=int(_dec(raw.get(_F_CONCURRENCY.encode(), raw.get(_F_CONCURRENCY, b"4"))) or 4),
            capabilities=caps,
            registered_at=float(_dec(raw.get(_F_REGISTERED_AT.encode(), raw.get(_F_REGISTERED_AT, b"0"))) or 0),
            last_heartbeat=float(_dec(raw.get(_F_LAST_HEARTBEAT.encode(), raw.get(_F_LAST_HEARTBEAT, b"0"))) or 0),
            status=status,
            in_flight=set(self._in_flight.get(worker_id, set())),
        )

    def active_count(self) -> int:
        """Return the number of workers with a live heartbeat."""
        return sum(
            1 for wid in self.list_workers()
            if self._redis.exists(self._heartbeat_key(wid))
        )

    def __repr__(self) -> str:
        return f"WorkerRegistry(prefix={self._prefix!r}, timeout={self._heartbeat_timeout}s)"