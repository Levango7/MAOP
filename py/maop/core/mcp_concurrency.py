"""MAOP MCP Concurrency Control + Per-Server RPM Rate Limiting.

Phase δ-5: provides two coordinating primitives used by
:class:`maop.core.mcp_hub.MCPHub` to protect external MCP servers from
being overwhelmed:

  * :class:`MCPServerConcurrency` — per-server bounded-concurrency
    limiter (semaphore-equivalent). Blocks ``acquire`` when the in-flight
    call count for a server reaches its limit; supports a timeout so
    callers do not stall forever.
  * :class:`MCPServerRateLimiter` — per-server RPM limiter using a
    60-second sliding window. ``check`` is a peek; ``record`` marks a
    call against the quota.

Both classes are thread-safe and synchronous (the hub calls them from
the async ``call_tool`` without extra locking).
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)


# ── Per-server concurrency limiter ─────────────────────────────


class MCPServerConcurrency:
    """Per-server bounded-concurrency limiter.

    Limits the number of concurrent in-flight MCP tool calls per server
    so a single slow server cannot exhaust the worker pool. Internally
    each server owns a :class:`threading.Condition` backed by a single
    shared lock; the condition lets ``acquire`` block with a timeout
    while still supporting dynamic limit changes via :meth:`set_limit`.

    Parameters
    ----------
    default_max_concurrent:
        Concurrency cap applied to any server without an explicit
        per-server override.
    per_server_limits:
        Optional ``{server_id: max_concurrent}`` overrides.
    """

    def __init__(
        self,
        default_max_concurrent: int = 5,
        per_server_limits: dict[str, int] | None = None,
    ) -> None:
        self._default = max(1, default_max_concurrent)
        self._limits: dict[str, int] = dict(per_server_limits or {})
        self._active: dict[str, int] = {}
        # Single lock guards all three dicts. Per-server Conditions are
        # created on demand and reuse this lock so wait/notify and dict
        # mutation are mutually exclusive.
        self._lock = threading.Lock()
        self._conditions: dict[str, threading.Condition] = {}

    def _ensure_server(self, server_id: str) -> threading.Condition:
        """Return (creating if needed) the Condition for ``server_id``."""
        with self._lock:
            cond = self._conditions.get(server_id)
            if cond is None:
                cond = threading.Condition(self._lock)
                self._conditions[server_id] = cond
                self._active.setdefault(server_id, 0)
            return cond

    def acquire(self, server_id: str, timeout_s: float = 30.0) -> bool:
        """Acquire a concurrency slot for ``server_id``.

        Blocks until a slot is free or ``timeout_s`` elapses. Returns
        ``True`` on success, ``False`` on timeout.
        """
        cond = self._ensure_server(server_id)
        deadline: float | None
        if timeout_s is None or timeout_s <= 0:
            deadline = None
        else:
            deadline = time.monotonic() + timeout_s
        with cond:
            limit = self._limits.get(server_id, self._default)
            while self._active.get(server_id, 0) >= limit:
                if deadline is None:
                    cond.wait()
                else:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return False
                    cond.wait(timeout=remaining)
                # Re-read limit after wake-up — set_limit may have
                # changed it while we were waiting.
                limit = self._limits.get(server_id, self._default)
            self._active[server_id] = self._active.get(server_id, 0) + 1
        self._update_active_gauge(server_id)
        return True

    def release(self, server_id: str) -> None:
        """Release a previously acquired slot for ``server_id``.

        Idempotent — calling release more times than acquire is a no-op
        (the active count never goes negative).
        """
        cond = self._ensure_server(server_id)
        with cond:
            current = self._active.get(server_id, 0)
            if current > 0:
                self._active[server_id] = current - 1
            cond.notify_all()
        self._update_active_gauge(server_id)

    def get_active_count(self, server_id: str) -> int:
        """Return the current number of in-flight calls for ``server_id``."""
        with self._lock:
            return self._active.get(server_id, 0)

    def get_limit(self, server_id: str) -> int:
        """Return the concurrency limit for ``server_id``."""
        with self._lock:
            return self._limits.get(server_id, self._default)

    def set_limit(self, server_id: str, max_concurrent: int) -> None:
        """Dynamically adjust the concurrency limit for ``server_id``.

        The new limit takes effect immediately for NEW acquire calls.
        Currently-active calls are not preempted — they run to
        completion and release normally. If the new limit is lower than
        the current active count, additional acquires block until enough
        releases bring the active count below the new limit. Waiters
        are notified so a limit increase unblocks them promptly.
        """
        cond = self._ensure_server(server_id)
        with cond:
            self._limits[server_id] = max(1, max_concurrent)
            cond.notify_all()

    def _update_active_gauge(self, server_id: str) -> None:
        """Mirror the active count into the global gauge (defensive)."""
        try:
            from maop.core.monitoring import MAOP_MCP_CONCURRENT_ACTIVE
        except Exception:
            return
        try:
            MAOP_MCP_CONCURRENT_ACTIVE.set(
                self.get_active_count(server_id),
                labels={"server": server_id},
            )
        except Exception:
            logger.debug("[mcp_concurrency] gauge update failed", exc_info=True)


# ── Per-server RPM rate limiter ────────────────────────────────


class MCPServerRateLimiter:
    """Per-server RPM rate limiter using a sliding window.

    Tracks request timestamps per server in a 60-second window. When
    the count in the window reaches the server's RPM limit, further
    requests are rejected (``check`` returns ``False``).

    ``check`` is a peek — it does NOT record a call. Callers must call
    :meth:`record` after a successful call to mark it against the
    quota. This split lets the hub peek before issuing the call (so it
    can short-circuit a rate-limited request without consuming quota)
    and record only after the call actually happened.

    Parameters
    ----------
    default_rpm:
        Requests-per-minute cap applied to any server without an
        explicit per-server override.
    per_server_rpm:
        Optional ``{server_id: rpm}`` overrides.
    """

    WINDOW_S = 60.0  # 1-minute sliding window

    def __init__(
        self,
        default_rpm: int = 60,
        per_server_rpm: dict[str, int] | None = None,
    ) -> None:
        self._default = max(1, default_rpm)
        self._rpm: dict[str, int] = dict(per_server_rpm or {})
        self._timestamps: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def _cleanup(self, server_id: str) -> None:
        """Drop timestamps older than the window for ``server_id``."""
        cutoff = time.monotonic() - self.WINDOW_S
        ts = self._timestamps.get(server_id)
        if not ts:
            return
        self._timestamps[server_id] = [t for t in ts if t > cutoff]

    def check(self, server_id: str) -> bool:
        """Return True if a call is currently allowed (does NOT record).

        True = within quota, False = rate-limited.
        """
        with self._lock:
            self._cleanup(server_id)
            count = len(self._timestamps.get(server_id, []))
            limit = self._rpm.get(server_id, self._default)
            return count < limit

    def record(self, server_id: str) -> None:
        """Record one call against ``server_id``'s RPM quota."""
        with self._lock:
            self._cleanup(server_id)
            self._timestamps.setdefault(server_id, []).append(time.monotonic())

    def get_remaining(self, server_id: str) -> int:
        """Return the remaining quota for ``server_id`` in the window."""
        with self._lock:
            self._cleanup(server_id)
            count = len(self._timestamps.get(server_id, []))
            limit = self._rpm.get(server_id, self._default)
            return max(0, limit - count)

    def set_rpm(self, server_id: str, rpm: int) -> None:
        """Dynamically adjust the RPM limit for ``server_id``."""
        with self._lock:
            self._rpm[server_id] = max(1, rpm)
