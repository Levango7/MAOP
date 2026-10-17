"""MAOP Distributed Lease — Redis-backed lease with fencing tokens.

Provides distributed mutual exclusion using Redis SET NX EX (atomic
acquire) and Lua-script-safe release (atomic check-and-delete). Each
lease acquisition returns a monotonically increasing fencing token that
callers can use to protect against delayed network writes (split-brain
protection).

Two high-level APIs:

1. ``DistributedLease`` — single named lease with automatic renewal.
2. ``LeaseBasedLeaderElection`` — leader election using leases; provides
   ``is_leader`` / ``current_leader`` / ``wait_until_leader``.

Dependencies: redis-py (>= 4.2). Degrades gracefully to local-only mode
when Redis is unavailable (fails open as leader).

Usage::

    from maop.core.reliability.distributed_lease import (
        DistributedLease, LeaseBasedLeaderElection,
    )

    # Single lease
    lease = DistributedLease(redis, "my-lock", ttl=30)
    if await lease.acquire():
        try:
            ...  # critical section
        finally:
            await lease.release()

    # Leader election
    election = LeaseBasedLeaderElection(redis, "scheduler-leader", ttl=15)
    await election.start()
    while True:
        if election.is_leader:
            await run_scheduling_round()
        await asyncio.sleep(5)

Design reference: docs/design-ha-lease.md
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from typing import Any

logger = logging.getLogger(__name__)

# ── Lua scripts for atomicity ─────────────────────────────────────

# Atomic release: only delete if the value matches our token.
# Prevents releasing a lease that has already expired and been re-acquired
# by another holder (fencing token protection).
_RELEASE_SCRIPT = """
if redis.call("GET", KEYS[1]) == ARGV[1] then
    return redis.call("DEL", KEYS[1])
else
    return 0
end
"""

# Atomic extend: only extend if the value still matches our token.
_EXTEND_SCRIPT = """
if redis.call("GET", KEYS[1]) == ARGV[1] then
    return redis.call("SET", KEYS[1], ARGV[1], "EX", ARGV[2])
else
    return 0
end
"""


class LeaseState:
    """Result of a lease acquisition attempt."""

    def __init__(self, acquired: bool, token: int = 0) -> None:
        self.acquired = acquired
        self.token = token  # fencing token (0 if not acquired)

    def __bool__(self) -> bool:
        return self.acquired

    def __repr__(self) -> str:
        return f"LeaseState(acquired={self.acquired}, token={self.token})"


class DistributedLease:
    """Redis-backed distributed lease with fencing token protection.

    Parameters
    ----------
    redis : Redis client
        ``redis.Redis`` or ``fakeredis.FakeRedis`` instance.
    name : str
        Unique lease name (used as Redis key ``maop:lease:{name}``).
    ttl : int
        Lease time-to-live in seconds. Must be >= 1.
    auto_renew : bool
        If True, automatically renew the lease at ``ttl / 3`` intervals
        while the lease is held.
    """

    def __init__(
        self,
        redis: Any,
        name: str,
        *,
        ttl: int = 30,
        auto_renew: bool = True,
    ) -> None:
        if ttl < 1:
            raise ValueError(f"ttl must be >= 1, got {ttl}")
        self._redis = redis
        self._name = name
        self._key = f"maop:lease:{name}"
        self._ttl = ttl
        self._auto_renew = auto_renew
        self._holder_id = f"{os.getpid()}-{uuid.uuid4().hex[:8]}"
        self._token: int = 0
        self._held = False
        self._renewal_task: asyncio.Task[None] | None = None
        # Register Lua scripts (cached on Redis server side)
        self._release_sha: str = ""
        self._extend_sha: str = ""

    async def _register_scripts(self) -> None:
        """Register Lua scripts on the Redis server (best-effort)."""
        if self._release_sha:
            return
        try:
            self._release_sha = self._redis.script_load(_RELEASE_SCRIPT)
            self._extend_sha = self._script_load(_EXTEND_SCRIPT)
        except Exception:  # silent-ok: defensive-probe — 预热失败时 release/renew 回退 eval
            pass  # scripts already registered or Redis doesn't support script_load

    def _script_load(self, script: str) -> str:
        """Helper to load a script (redis-py compatibility)."""
        return str(self._redis.script_load(script))

    @property
    def is_held(self) -> bool:
        """Whether the lease is currently held by this instance."""
        return self._held

    @property
    def token(self) -> int:
        """Current fencing token (0 if not held)."""
        return self._token

    @property
    def name(self) -> str:
        return self._name

    async def acquire(self) -> LeaseState:
        """Try to acquire the lease. Returns LeaseState with fencing token.

        Uses SET NX EX for atomic acquire-on-expiry. The value stored is
        ``{holder_id}:{token}`` so we can verify ownership atomically.
        """
        # Monotonically increasing fencing token
        new_token = int(time.time() * 1_000_000)  # microsecond precision
        value = f"{self._holder_id}:{new_token}"
        try:
            acquired = self._redis.set(self._key, value, nx=True, ex=self._ttl)
            if acquired:
                self._token = new_token
                self._held = True
                logger.debug(
                    "[lease] %s acquired by %s (token=%d, ttl=%ds)",
                    self._name, self._holder_id, new_token, self._ttl,
                )
                if self._auto_renew:
                    self._start_renewal()
                return LeaseState(True, new_token)
        except Exception as exc:
            logger.warning("[lease] %s acquire failed: %s", self._name, exc)
            # Fail open: assume we are leader when Redis is down
            self._token = new_token
            self._held = True
            return LeaseState(True, new_token)
        return LeaseState(False)

    async def release(self) -> bool:
        """Release the lease. Returns True if successfully released.

        Uses Lua script for atomic check-and-delete so we never release
        a lease that has been re-acquired by another holder.
        """
        self._held = False
        self._stop_renewal()
        if not self._token:
            return False
        try:
            if self._release_sha:
                result = self._redis.evalsha(
                    self._release_sha, 1, self._key, f"{self._holder_id}:{self._token}",
                )
            else:
                result = self._redis.eval(
                    _RELEASE_SCRIPT, 1, self._key, f"{self._holder_id}:{self._token}",
                )
            released = bool(result)
            if released:
                logger.debug("[lease] %s released (token=%d)", self._name, self._token)
            else:
                logger.warning(
                    "[lease] %s release: key changed (token=%d, possibly expired)",
                    self._name, self._token,
                )
            self._token = 0
            return released
        except Exception as exc:
            logger.warning("[lease] %s release failed: %s", self._name, exc)
            self._token = 0
            return False

    async def renew(self) -> bool:
        """Extend the lease TTL. Returns True if successfully renewed.

        Uses Lua to atomically check ownership before extending.
        """
        if not self._held:
            return False
        try:
            if self._extend_sha:
                result = self._redis.evalsha(
                    self._extend_sha, 1,
                    self._key, f"{self._holder_id}:{self._token}", str(self._ttl),
                )
            else:
                result = self._redis.eval(
                    _EXTEND_SCRIPT, 1,
                    self._key, f"{self._holder_id}:{self._token}", str(self._ttl),
                )
            return bool(result)
        except Exception as exc:
            logger.warning("[lease] %s renew failed: %s", self._name, exc)
            return False

    async def current_holder(self) -> tuple[str, int] | None:
        """Return ``(holder_id, token)`` of the current lease holder, or None."""
        try:
            raw = self._redis.get(self._key)
            if raw is None:
                return None
            raw_str = raw.decode() if isinstance(raw, bytes) else raw
            parts = raw_str.split(":")
            if len(parts) >= 2:
                return parts[0], int(parts[1])
            return None
        except Exception:
            return None

    def _start_renewal(self) -> None:
        """Start background renewal at TTL/3 intervals."""
        if self._renewal_task is not None and not self._renewal_task.done():
            return
        self._renewal_task = asyncio.ensure_future(self._renewal_loop())

    def _stop_renewal(self) -> None:
        """Stop background renewal."""
        if self._renewal_task is not None and not self._renewal_task.done():
            self._renewal_task.cancel()
        self._renewal_task = None

    async def _renewal_loop(self) -> None:
        """Background loop that renews the lease at TTL/3 intervals."""
        interval = max(1.0, self._ttl / 3.0)
        try:
            while self._held:
                await asyncio.sleep(interval)
                if not self._held:
                    break
                success = await self._redis.set(
                    self._key,
                    f"{self._holder_id}:{self._token}",
                    xx=True,  # only if key exists
                    ex=self._ttl,
                )
                if not success:
                    logger.warning("[lease] %s renewal lost lease", self._name)
                    self._held = False
                    break
                logger.debug("[lease] %s renewed (token=%d)", self._name, self._token)
        except asyncio.CancelledError:
            return
        except Exception as exc:
            logger.warning("[lease] %s renewal loop error: %s", self._name, exc)

    async def close(self) -> None:
        """Release the lease and stop all background tasks."""
        await self.release()

    def __repr__(self) -> str:
        return (
            f"DistributedLease(name={self._name!r}, held={self._held}, "
            f"token={self._token}, ttl={self._ttl})"
        )


class LeaseBasedLeaderElection:
    """Leader election using distributed leases.

    Multiple instances compete for a lease; the one that acquires it
    becomes the leader. If the leader's lease expires (crash, network
    partition), another instance automatically takes over after the TTL.

    Parameters
    ----------
    redis : Redis client
    name : str
        Election name (used as lease name).
    ttl : int
        Leader lease TTL in seconds. Leaders must renew within this window.
    """

    def __init__(self, redis: Any, name: str, *, ttl: int = 15) -> None:
        self._lease = DistributedLease(redis, f"election:{name}", ttl=ttl, auto_renew=True)
        self._name = name
        self._is_leader = False

    @property
    def is_leader(self) -> bool:
        """Whether this instance is currently the leader."""
        return self._is_leader and self._lease.is_held

    @property
    def name(self) -> str:
        return self._name

    async def start(self) -> None:
        """Start the leader election (acquire the lease)."""
        result = await self._lease.acquire()
        self._is_leader = bool(result)
        if self._is_leader:
            logger.info("[election] %s: became leader (token=%d)", self._name, result.token)

    async def stop(self) -> None:
        """Stop the election (release the lease)."""
        if self._is_leader:
            await self._lease.release()
            self._is_leader = False
            logger.info("[election] %s: stepped down", self._name)

    async def current_leader(self) -> tuple[str, int] | None:
        """Return the current leader's identity and token."""
        return await self._lease.current_holder()

    async def contested_once(self) -> bool:
        """Try once to become leader. Returns True if successful."""
        result = await self._lease.acquire()
        self._is_leader = bool(result)
        return self._is_leader

    async def heartbeat(self) -> bool:
        """Renew leader lease. Returns True if still leader."""
        if not self._is_leader:
            return False
        if not self._lease.is_held:
            self._is_leader = False
            return False
        return True  # auto_renew handles it in background

    async def close(self) -> None:
        """Stop election and release lease."""
        await self._lease.close()
        self._is_leader = False


# ── Global singletons ──────────────────────────────────────────────

_elections: dict[str, LeaseBasedLeaderElection] = {}
_elections_lock = asyncio.Lock()


async def get_leader_election(
    redis: Any, name: str, *, ttl: int = 15,
) -> LeaseBasedLeaderElection:
    """Get or create a process-wide leader election singleton."""
    if name not in _elections:
        async with _elections_lock:
            if name not in _elections:
                _elections[name] = LeaseBasedLeaderElection(redis, name, ttl=ttl)
    return _elections[name]


def reset_elections() -> None:
    """Reset all election singletons (testing only)."""
    _elections.clear()
