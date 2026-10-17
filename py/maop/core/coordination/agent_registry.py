"""MAOP — A2A Agent Registry with Dynamic Capability Registration.

Provides:

- ``register_agent(card)`` / ``unregister_agent(card_id)`` for dynamic
  join/leave.
- Periodic health probes via a background task (checks each agent's
  ``health_url``).
- Capability-based lookup: ``find_agents(capability)``.
- AgentCard registry backed by Redis (shared across distributed nodes).

Usage::

    registry = RedisAgentRegistry(redis_url="redis://localhost:6379/10")
    await registry.start()
    await registry.register_agent(AgentCard(card_id="a1", name="Solver-A", ...))
    active = registry.list_agents()
    targets = registry.find_agents(capability="math.solve")
    await registry.stop()
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from json import JSONDecodeError
from typing import Any, Protocol

logger = logging.getLogger(__name__)


# ── Protocols ───────────────────────────────────────────────────────


class HealthChecker(Protocol):
    """Protocol for checking agent health asynchronously."""

    async def check(self, card: Any) -> bool: ...


class AgentStore(Protocol):
    """Protocol for persistent agent storage."""

    async def save(self, card: Any) -> None: ...
    async def remove(self, card_id: str) -> None: ...
    async def get(self, card_id: str) -> Any | None: ...
    async def list(self) -> list[Any]: ...


# ── Local In-Memory Store ──────────────────────────────────────────


class InMemoryAgentStore:
    """A simple in-memory agent store for testing / single-node usage."""

    def __init__(self) -> None:
        self._agents: dict[str, Any] = {}

    async def save(self, card: Any) -> None:
        self._agents[card.card_id] = card

    async def remove(self, card_id: str) -> None:
        self._agents.pop(card_id, None)

    async def get(self, card_id: str) -> Any | None:
        return self._agents.get(card_id)

    async def list(self) -> list[Any]:
        return list(self._agents.values())


# ── Redis Distributed Store ────────────────────────────────────────


class RedisAgentStore:
    """Redis-backed distributed agent store.

    All MAOP nodes share the same Redis ``maop:a2a:agents`` hash, so
    every node sees the same agent topology.
    """

    _REDIS_KEY = "maop:a2a:agents"
    _CHANNEL_KEY = "maop:a2a:registry-changes"

    def __init__(self, redis_url: str = "redis://localhost:6379/10") -> None:
        self._redis_url = redis_url
        self._redis = None  # redis.asyncio.Redis | None

    async def _get_redis(self) -> Any:
        if self._redis is None:
            import redis.asyncio as aioredis
            self._redis = await aioredis.from_url(self._redis_url)
        return self._redis

    async def save(self, card: Any) -> None:
        import json as _json
        r = await self._get_redis()
        data = _json.dumps({
            "card_id": card.card_id,
            "name": card.name,
            "url": card.url,
            "description": card.description,
            "capabilities": card.capabilities,
            "status": card.status,
            "health_url": card.health_url,
            "metadata": card.metadata,
            "registered_at": datetime.now(timezone.utc).isoformat(),
        })
        pipe = r.pipeline()
        pipe.hset(self._REDIS_KEY, card.card_id, data)
        pipe.publish(self._CHANNEL_KEY, _json.dumps({"action": "register", "card_id": card.card_id}))
        await pipe.execute()
        logger.debug("[redis-agent-store] saved agent %s", card.card_id)

    async def remove(self, card_id: str) -> None:
        import json as _json
        r = await self._get_redis()
        pipe = r.pipeline()
        pipe.hdel(self._REDIS_KEY, card_id)
        pipe.publish(self._CHANNEL_KEY, _json.dumps({"action": "unregister", "card_id": card_id}))
        await pipe.execute()
        logger.debug("[redis-agent-store] removed agent %s", card_id)

    async def get(self, card_id: str) -> Any | None:
        import json as _json
        r = await self._get_redis()
        # redis.asyncio: hget is a coroutine — without await this returns a
        # coroutine object, isinstance(bytes) is False, and d["card_id"] raises
        # TypeError on every lookup. list() already awaited hgetall.
        data = await r.hget(self._REDIS_KEY, card_id)
        if data is None:
            return None
        d = _json.loads(data) if isinstance(data, (bytes, bytearray)) else data
        # Lazy import to avoid circular
        from maop.core.coordination.a2a_blackboard_bridge import AgentCard
        return AgentCard(
            card_id=d["card_id"],
            name=d.get("name", ""),
            url=d.get("url", ""),
            description=d.get("description", ""),
            capabilities=d.get("capabilities", []),
            status=d.get("status", "active"),
            health_url=d.get("health_url"),
            metadata=d.get("metadata", {}),
        )

    async def list(self) -> list[Any]:
        import json as _json

        from maop.core.coordination.a2a_blackboard_bridge import AgentCard
        r = await self._get_redis()
        raw_items = await r.hgetall(self._agents_key())
        agents: list[Any] = []
        # redis.asyncio returns Mapping[bytes, bytes]
        for v in raw_items.values():
            try:
                raw = v.decode() if isinstance(v, (bytes, bytearray)) else v
                d = _json.loads(raw)
            except (JSONDecodeError, TypeError, AttributeError):  # silent-ok: scan-continue
                continue
            agents.append(AgentCard(
                card_id=d["card_id"],
                name=d.get("name", ""),
                url=d.get("url", ""),
                description=d.get("description", ""),
                capabilities=d.get("capabilities", []),
                status=d.get("status", "active"),
                health_url=d.get("health_url"),
                metadata=d.get("metadata", {}),
            ))
        return agents

    def _agents_key(self) -> str:
        return self._REDIS_KEY


# ── Default Health Checker ─────────────────────────────────────────


class DefaultHealthChecker:
    """Pings the agent's ``health_url`` and returns True if HTTP 2xx."""

    def __init__(self, timeout: float = 5.0) -> None:
        self._timeout = timeout

    async def check(self, card: Any) -> bool:
        if not getattr(card, "health_url", None):
            return True  # no health endpoint → assume healthy
        try:
            import httpx
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.get(card.health_url)
                return 200 <= resp.status_code < 300
        except Exception:
            return False


# ── Registry Implementation ────────────────────────────────────────


class RedisAgentRegistry:
    """Distributed A2A agent registry with health probes.

    Parameters
    ----------
    redis_url : str
        Redis connection URL.
    health_checker : HealthChecker | None
        Custom health checker. If None, uses ``DefaultHealthChecker``.
    probe_interval : float
        Period between health probes (seconds).
    """

    def __init__(
        self,
        redis_url: str = "redis://localhost:6379/10",
        health_checker: HealthChecker | None = None,
        probe_interval: float = 30.0,
    ) -> None:
        self._store = RedisAgentStore(redis_url)
        self._health_checker = health_checker or DefaultHealthChecker()
        self._probe_interval = probe_interval
        self._running = False
        self._probe_task: asyncio.Task | None = None
        self._local_status: dict[str, str] = {}  # card_id → status (local view)
        # capability 查询要在同步上下文里做，所以注册时把能力快照下来 ——
        # find_agents() 之前完全忽略 capability 参数，把"按能力找 agent"
        # 变成了"列出所有 agent"。
        self._local_capabilities: dict[str, list[str]] = {}

    async def start(self) -> None:
        """Start the background health probe task."""
        self._running = True
        self._probe_task = asyncio.create_task(self._probe_loop())
        logger.info(
            "[agent-registry] started (probe_interval=%.1fs)",
            self._probe_interval,
        )

    async def stop(self) -> None:
        """Stop health probes."""
        self._running = False
        if self._probe_task:
            self._probe_task.cancel()
            try:
                await self._probe_task
            except asyncio.CancelledError:  # silent-ok: interrupt — 停止时取消探测任务
                pass
        logger.info("[agent-registry] stopped")

    # ── Registration API ─────────────────────────────────────────

    async def register_agent(self, card: Any) -> None:
        """Register an A2A agent card."""
        await self._store.save(card)
        self._local_status[card.card_id] = "active"
        self._local_capabilities[card.card_id] = list(
            getattr(card, "capabilities", []) or [],
        )
        logger.info("[agent-registry] registered agent %s (%s)", card.card_id, card.name)

    async def unregister_agent(self, card_id: str) -> None:
        """Remove an agent card from the registry."""
        await self._store.remove(card_id)
        self._local_status.pop(card_id, None)
        self._local_capabilities.pop(card_id, None)
        logger.info("[agent-registry] unregistered agent %s", card_id)

    async def list_agents(self) -> list[Any]:
        """Return the currently registered agent cards."""
        return await self._store.list()

    async def get_agent_async(self, card_id: str) -> Any | None:
        """Async fetch of a specific agent card from Redis store."""
        return await self._store.get(card_id)

    def get_agent(self, card_id: str) -> Any | None:
        """Return local-status cache for an agent (sync, best-effort)."""
        return self._local_status.get(card_id)

    def find_agents(self, capability: str, only_active: bool = True) -> list[Any]:
        """Return a list of agents providing the given capability.

        Parameters
        ----------
        capability : str
            The capability tag to match.
        only_active : bool
            If True, only return agents with ``status == "active"``.
        Returns
        -------
        list
            Filtered agent cards (fetched synchronously from local cache).
        """
        matching = []
        for card_id, status in list(self._local_status.items()):
            if only_active and status != "active":
                continue
            caps = self._local_capabilities.get(card_id, [])
            # 之前这里直接 append，capability 参数形同虚设。
            if capability and capability not in caps:
                continue
            matching.append(
                CachedAgentRef(card_id=card_id, status=status, capabilities=list(caps)),
            )
        return matching

    # ── Health Probe Loop ─────────────────────────────────────────

    async def _probe_loop(self) -> None:
        """Periodically check agent health and update the local status cache."""
        while self._running:
            try:
                await asyncio.sleep(self._probe_interval)
                agents = await self._store.list()
                for agent in agents:
                    if not self._running:
                        break
                    is_healthy = await self._health_checker.check(agent)
                    new_status = "active" if is_healthy else "degraded"
                    old_status = self._local_status.get(agent.card_id)
                    self._local_status[agent.card_id] = new_status
                    if old_status != new_status:
                        logger.info(
                            "[agent-registry] agent %s status: %s → %s",
                            agent.card_id, old_status, new_status,
                        )
                        # Update Redis with the new status
                        try:
                            from maop.core.coordination.a2a_blackboard_bridge import AgentCard
                            updated = AgentCard(
                                card_id=agent.card_id,
                                name=agent.name,
                                url=agent.url,
                                description=agent.description,
                                capabilities=agent.capabilities,
                                status=new_status,
                                health_url=agent.health_url,
                                metadata={**agent.metadata, "last_probe": time.time()},
                            )
                            await self._store.save(updated)
                        except Exception as exc:
                            logger.warning(
                                "[agent-registry] failed to persist status for %s: %s",
                                agent.card_id, exc,
                            )
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("[agent-registry] probe error: %s", exc)


# ── Local lightweight reference for find_agents ────────────────────


class CachedAgentRef:
    """A lightweight in-memory reference for capability queries."""

    def __init__(self, card_id: str, status: str = "active", capabilities: list[str] | None = None) -> None:
        self.card_id = card_id
        self.status = status
        self._capabilities = capabilities or []

    def __repr__(self) -> str:
        return f"CachedAgentRef(card_id={self.card_id!r}, status={self.status!r})"
