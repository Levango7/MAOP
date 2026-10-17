"""MAOP — Blackboard Task Context for Distributed DAG Workers.

When a DistributedWorker executes a DAG node, it often needs to:

1. **Read** shared context from the blackboard (e.g. hypotheses, evidence,
   partial solutions contributed by upstream nodes running on *other*
   nodes).
2. **Write** results back to the blackboard so downstream nodes (also
   potentially on other workers) can consume them.

This module provides :class:`BlackboardTaskContext`, a lightweight bridge
between a DAG worker task and the Redis-synchronized blackboard
(M2-A). The worker process creates one context per DAG run and passes it
into the executor callable.

Usage::

    # In the worker process:
    ctx = BlackboardTaskContext(redis_url="redis://localhost:6379/12")
    await ctx.attach()

    # In the executor:
    bb = ctx.blackboard
    hypotheses = bb.read("hypothesis")
    await bb.write("evidence", {"result": result}, contributor=node_id)

    await ctx.detach()

Design reference: docs/design-blackboard.md §Task Context (M2-C).
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from maop.core.reliability.blackboard import Blackboard, BlackboardEntry

logger = logging.getLogger(__name__)


class BlackboardTaskContext:
    """Binds a distributed blackboard to a worker's DAG run.

    Each worker creates one context at DAG-run start, calls
    :meth:`attach` to connect to Redis and bootstrap the snapshot,
    then uses :attr:`blackboard` to read/write shared knowledge.

    Parameters
    ----------
    redis_url : str
        Redis URL for the distributed blackboard (default db 12).
    node_id : str
        Unique identifier for the worker node/task.
    auto_sync : bool
        If True (default), creates a RedisBlackboardSync that keeps the
        local blackboard in sync with remote changes during the run.
    """

    def __init__(
        self,
        redis_url: str = "redis://localhost:6379/12",
        node_id: str | None = None,
        auto_sync: bool = True,
    ) -> None:
        self._redis_url = redis_url
        self._node_id = node_id or f"worker-{uuid.uuid4().hex[:8]}"
        self._auto_sync = auto_sync
        self._blackboard: Blackboard | None = None
        self._sync: Any = None

    @property
    def blackboard(self) -> Blackboard:
        """The blackboard instance (read-only access to shared knowledge).

        Raises if called before :meth:`attach`.
        """
        if self._blackboard is None:
            raise RuntimeError(
                "BlackboardTaskContext not attached; call attach() first."
            )
        return self._blackboard

    @property
    def node_id(self) -> str:
        return self._node_id

    # ── Lifecycle ─────────────────────────────────────────────────

    async def attach(self) -> None:
        """Connect to Redis, bootstrap snapshot, start live sync."""
        self._blackboard = Blackboard()

        if self._auto_sync:
            from maop.core.reliability.blackboard_redis_sync import RedisBlackboardSync
            self._sync = RedisBlackboardSync(
                self._blackboard,
                redis_url=self._redis_url,
                node_id=self._node_id,
            )
            await self._sync.start()
            logger.info(
                "[bb-task-context] attached (node=%s, auto_sync=on)", self._node_id
            )
        else:
            # Bootstrap snapshot only (no live sync)
            await self._bootstrap_snapshot_only()
            logger.info(
                "[bb-task-context] attached (node=%s, auto_sync=off)", self._node_id
            )

    async def detach(self) -> None:
        """Stop sync and release resources."""
        if self._sync is not None:
            await self._sync.stop()
            self._sync = None
        self._blackboard = None

    # ── Convenience read/write methods ────────────────────────────

    def read_domain(self, domain: str) -> list[BlackboardEntry]:
        """Read all entries from a domain."""
        if self._blackboard is None:
            raise RuntimeError("not attached")
        return self._blackboard.read(domain)

    def get_snapshot(self) -> dict[str, list[dict[str, Any]]]:
        """Get a full snapshot of the blackboard (entry dicts per domain)."""
        if self._blackboard is None:
            raise RuntimeError("not attached")
        return self._blackboard.get_snapshot()

    async def contribute(
        self,
        domain: str,
        content: Any,
        contributor: str | None = None,
        confidence: float = 1.0,
        metadata: dict[str, Any] | None = None,
    ) -> BlackboardEntry:
        """Write a contribution to the blackboard (convenience method)."""
        if self._blackboard is None:
            raise RuntimeError("not attached")
        return await self._blackboard.write(
            domain=domain,
            content=content,
            contributor=contributor or self._node_id,
            confidence=confidence,
            metadata=metadata,
        )

    # ── Bootstrap without sync ───────────────────────────────────

    async def _bootstrap_snapshot_only(self) -> None:
        """Fetch snapshot hash without starting live subscription."""
        if self._blackboard is None:
            return
        try:
            import redis.asyncio as aioredis
            r = await aioredis.from_url(self._redis_url)
            items = await r.hgetall("maop:bb:snapshot")
            from maop.core.reliability.blackboard_redis_sync import _entry_from_dict
            for raw in items.values():
                try:
                    data = raw.decode() if isinstance(raw, (bytes, bytearray)) else raw
                    import json
                    entry = _entry_from_dict(json.loads(data))
                    entry.metadata["_local_origin"] = True
                    with self._blackboard._lock:
                        self._blackboard._entries[entry.domain].append(entry)
                except Exception:  # noqa: S112  # silent-ok: scan-continue — 逐条加载快照
                    continue
            await r.close()
        except Exception as exc:
            logger.warning("[bb-task-context] bootstrap-only failed: %s", exc)
