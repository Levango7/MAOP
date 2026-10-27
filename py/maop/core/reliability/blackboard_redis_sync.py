"""MAOP Blackboard — Redis Distributed Sync Layer.

Makes a local ``Blackboard`` instance participate in a cross-node shared
knowledge space backed by Redis pub/sub + snapshot hash.

Architecture::

    Node A                         Node B
    ┌──────────┐   Redis pub/sub   ┌──────────┐
    │ BB local │ ◄───────────────► │ BB local │
    │  + sync  │   maop:bb:ch      │  + sync  │
    └──────────┘                   └──────────┘
         │                              │
         ▼                              ▼
    maop:bb:snapshot (Hash)  ──── full state bootstrap

Usage::

    bb = Blackboard()
    sync = RedisBlackboardSync(bb, redis_url="redis://localhost:6379/12")
    await sync.start()
    # now writes to ``bb`` are published; remote entries are merged in
    await sync.stop()

Loop-back prevention: locally-originated entries are tagged with metadata
``_local_origin = True`` so the subscriber can skip re-publishing them.

Design reference: docs/design-blackboard.md §Distributed Sync (M2-A).
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any

from maop.core.reliability.blackboard import Blackboard, BlackboardEntry

logger = logging.getLogger(__name__)

# ── Redis keys & channels ──────────────────────────────────────────

_CHANNEL = "maop:bb:channel"
_SNAPSHOT_HASH = "maop:bb:snapshot"
_ORIGIN_MARKER = "_origin_node"
_LOCAL_ORIGIN = "_local_origin"


# ── Redis connection helper ────────────────────────────────────────


class _RedisRef:
    """Lazy Redis connection wrapper (redis.asyncio)."""

    def __init__(self, redis_url: str) -> None:
        self._url = redis_url
        self._redis: Any = None

    async def connect(self) -> Any:
        if self._redis is None:
            import redis.asyncio as aioredis
            self._redis = await aioredis.from_url(self._url)
            logger.debug("[bb-redis-sync] connected to %s", self._url)
        return self._redis

    async def close(self) -> None:
        if self._redis:
            await self._redis.close()
            self._redis = None

    @property
    def is_connected(self) -> bool:
        return self._redis is not None


# ── Redis Blackboard Sync ──────────────────────────────────────────


class RedisBlackboardSync:
    """Attaches Redis distributed sync to a local Blackboard.

    Parameters
    ----------
    blackboard : Blackboard
        The local blackboard instance to synchronize.
    redis_url : str
        Redis connection URL (default db 12 for blackboard sync).
    node_id : str | None
        Unique node identifier (auto-generated if None).
    pubsub_timeout : float
        Seconds between pubsub health-check polls.
    """

    def __init__(
        self,
        blackboard: Blackboard,
        redis_url: str = "redis://localhost:6379/12",
        node_id: str | None = None,
        pubsub_timeout: float = 1.0,
    ) -> None:
        self._bb = blackboard
        self._node_id = node_id or f"node-{uuid.uuid4().hex[:8]}"
        self._redis_ref = _RedisRef(redis_url)
        self._pubsub_timeout = pubsub_timeout
        self._running = False
        self._listener_task: asyncio.Task | None = None
        self._subscriber_tasks: set[asyncio.Task] = set()
        self._subscribed = False

    # ── Lifecycle ─────────────────────────────────────────────────

    async def start(self) -> None:
        """Start the sync layer: connect Redis, bootstrap snapshot, listen."""
        if self._running:
            return
        self._running = True

        # Bootstrap full state from Redis snapshot
        await self._bootstrap_snapshot()

        # Subscribe to pub/sub channel for incremental updates
        self._listener_task = asyncio.create_task(self._listen_loop())

        # Subscribe to local blackboard writes (publish to Redis)
        for d in ("problem", "partial_solution", "hypothesis", "evidence", "decision", "constraint", "agent_contribution"):
            self._bb.subscribe(d, self._on_local_write)

        # Register the sync ref on the blackboard so we can detect local-origin writes
        self._bb._redis_sync = self  # type: ignore[attr-defined]

        logger.info("[bb-redis-sync] started (node=%s)", self._node_id)

    async def stop(self) -> None:
        """Stop the sync layer and disconnect Redis."""
        self._running = False
        if self._listener_task:
            self._listener_task.cancel()
            try:
                await self._listener_task
            except asyncio.CancelledError:  # silent-ok: interrupt — 停止时取消监听任务
                pass
        await self._redis_ref.close()
        logger.info("[bb-redis-sync] stopped")

    # ── Local write → publish ──────────────────────────────────────

    async def _on_local_write(self, entry: BlackboardEntry) -> None:
        """Called when the local blackboard receives a write.

        Publishes the entry to Redis so other nodes can merge it.
        Skip if this entry was already received from a remote node.
        """
        if not self._running or not self._redis_ref.is_connected:
            return
        # Skip remote-origin entries (already published by source node)
        if entry.metadata.get(_LOCAL_ORIGIN) is True:
            return
        # Tag with origin and publish
        entry.metadata[_ORIGIN_MARKER] = self._node_id
        try:
            await self._publish_entry(entry)
            await self._save_snapshot(entry)
        except Exception as exc:
            logger.warning("[bb-redis-sync] publish failed: %s", exc)

    async def _publish_entry(self, entry: BlackboardEntry) -> None:
        """Publish an entry to the Redis pub/sub channel."""
        r = await self._redis_ref.connect()
        payload = json.dumps({
            "origin_node": self._node_id,
            "entry": entry.to_dict(),
        })
        await r.publish(_CHANNEL, payload)
        logger.debug(
            "[bb-redis-sync] published %s (%s) to %s",
            entry.domain, entry.entry_id[:8], _CHANNEL,
        )

    async def _save_snapshot(self, entry: BlackboardEntry) -> None:
        """Persist the latest entry per domain into the snapshot hash."""
        r = await self._redis_ref.connect()
        key = f"{entry.domain}:{entry.entry_id}"
        data = json.dumps(entry.to_dict())
        await r.hset(_SNAPSHOT_HASH, key, data)

    # ── Remote entry → merge into local blackboard ────────────────

    async def _listen_loop(self) -> None:
        """Background loop: subscribe to Redis pub/sub and merge entries."""
        while self._running:
            try:
                r = await self._redis_ref.connect()
                pubsub = r.pubsub()
                await pubsub.subscribe(_CHANNEL)
                self._subscribed = True
                logger.info("[bb-redis-sync] subscribed to %s", _CHANNEL)

                while self._running:
                    msg = await pubsub.get_message(
                        ignore_subscribe_messages=True,
                        timeout=self._pubsub_timeout,
                    )
                    if msg and msg.get("type") == "message":
                        await self._handle_message(msg["data"])
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("[bb-redis-sync] listen error: %s (reconnecting…)", exc)
                self._subscribed = False
                await asyncio.sleep(1.0)

    async def _handle_message(self, raw: bytes | str) -> None:
        """Decode a pub/sub message and merge the entry locally."""
        try:
            if isinstance(raw, bytes):
                raw = raw.decode()
            payload = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            logger.warning("[bb-redis-sync] malformed message: %s", exc)
            return

        origin = payload.get("origin_node", "")
        if origin == self._node_id:
            return  # Skip our own messages (safety net)

        entry_data = payload.get("entry")
        if not entry_data:
            return

        entry = _entry_from_dict(entry_data)
        await self._merge_remote_entry(entry)
        await self._save_snapshot(entry)

    async def _merge_remote_entry(self, entry: BlackboardEntry) -> None:
        """Write a remote entry into the local blackboard without re-publishing.

        Tags the entry with ``_local_origin = True`` so that our own
        ``_on_local_write`` callback skips it (loop prevention).
        """
        # Mark as locally-merged to prevent re-publish loop
        entry.metadata[_LOCAL_ORIGIN] = True

        # Directly append to the internal store, bypassing subscribe→publish
        from maop.core.reliability.blackboard import _ALLOWED_DOMAINS
        if entry.domain not in _ALLOWED_DOMAINS:
            return

        # Use the blackboard's internal lock and entries dict
        with self._bb._lock:
            self._bb._entries[entry.domain].append(entry)
        # Notify local subscribers (so UI / controllers see the update)
        # but do NOT pass through the Redis publish path again
        for cb in self._bb._subscribers.get(entry.domain, []):
            try:
                result = cb(entry)
                if asyncio.iscoroutine(result):
                    # Schedule coroutine execution (non-blocking) and track the task
                    # so exceptions are surfaced and tasks aren't silently destroyed.
                    task = asyncio.create_task(_await_or_log(result, entry.entry_id))
                    self._subscriber_tasks.add(task)
                    task.add_done_callback(self._subscriber_tasks.discard)
            except Exception as exc:
                logger.debug("[bb-merge] subscriber error: %s", exc)

    # ── Bootstrap ──────────────────────────────────────────────────

    async def _bootstrap_snapshot(self) -> None:
        """On startup, fetch the full snapshot hash and merge into local state."""
        try:
            r = await self._redis_ref.connect()
            items = await r.hgetall(_SNAPSHOT_HASH)
            if not items:
                logger.info("[bb-redis-sync] no snapshot to bootstrap from")
                return

            count = 0
            for raw in items.values():
                try:
                    data = json.loads(raw) if isinstance(raw, (bytes, bytearray)) else raw
                except (json.JSONDecodeError, TypeError):  # silent-ok: scan-continue
                    continue
                entry = _entry_from_dict(data)
                entry.metadata[_LOCAL_ORIGIN] = True
                with self._bb._lock:
                    self._bb._entries[entry.domain].append(entry)
                count += 1

            logger.info("[bb-redis-sync] bootstrapped %d entries from snapshot", count)
        except Exception as exc:
            logger.warning("[bb-redis-sync] bootstrap failed: %s", exc)

    # ── Helpers ────────────────────────────────────────────────────

    @property
    def node_id(self) -> str:
        return self._node_id

    @property
    def is_running(self) -> bool:
        return self._running and self._subscribed


def _entry_from_dict(d: dict[str, Any]) -> BlackboardEntry:
    """Reconstruct a BlackboardEntry from a deserialized dict."""
    return BlackboardEntry(
        entry_id=d.get("entry_id", str(uuid.uuid4())),
        domain=d.get("domain", ""),
        content=d.get("content"),
        contributor=d.get("contributor", ""),
        timestamp=d.get("timestamp", ""),
        confidence=float(d.get("confidence", 1.0)),
        metadata=d.get("metadata", {}),
    )


async def _await_or_log(coro: Any, entry_id: str) -> None:
    """Await a coroutine and log any error (fire-and-forget helper)."""
    try:
        await coro
    except Exception as exc:
        logger.debug("[bb-merge] async subscriber error for %s: %s", entry_id[:8], exc)
