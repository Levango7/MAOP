"""MAOP Dashboard — Blackboard WebSocket Bridge.

Subscribes to Blackboard write events and pushes them to connected
WebSocket clients in real time. This lets the dashboard UI react
instantly to agent contributions, partial solutions, and evidence
without polling.

**Not wired yet.** Nothing starts this bridge: the dashboard lifespan does not
construct it, so the live `/ws` stream is fed by ``_ws_push_loop`` (periodic
snapshots) and M3-op events only. Wiring it into the lifespan would add a
second, event-driven write path — that is a behaviour change needing its own
test, so the module is registered as an orphan in
``tests/test_dead_wiring_inventory.py`` instead.

Usage (once wired)::

    from maop.dashboard.ws_blackboard_bridge import BlackboardWSBridge

    bridge = BlackboardWSBridge(blackboard_instance)
    await bridge.start()
    # ... dashboard pushes blackboard events to WS clients ...
    await bridge.stop()
"""

from __future__ import annotations

import logging

from maop.core.reliability.blackboard import Blackboard, BlackboardEntry
from maop.core.reliability.event_bus import Event

logger = logging.getLogger(__name__)


class BlackboardWSBridge:
    """Bridges Blackboard events to WebSocket broadcast.

    Parameters
    ----------
    bb : Blackboard
        The blackboard instance to monitor.
    """

    def __init__(self, bb: Blackboard) -> None:
        self._bb = bb
        self._running = False

    async def start(self) -> None:
        """Start bridging: subscribe to blackboard events and broadcast."""
        self._running = True
        # Subscribe to the blackboard write events via EventBus
        if self._bb.event_bus_enabled:
            event_bus = self._bb._event_bus
            if event_bus is not None:
                event_bus.subscribe("blackboard.write", self._on_blackboard_write)
                logger.info("[bb-ws-bridge] started (EventBus mode)")
                return
        # Fallback: subscribe directly to blackboard callback
        for domain in ("problem", "partial_solution", "hypothesis", "evidence", "decision"):
            self._bb.subscribe(domain, self._on_direct_write)
        logger.info("[bb-ws-bridge] started (direct callback mode)")

    async def stop(self) -> None:
        """Stop bridging."""
        self._running = False
        if self._bb.event_bus_enabled:
            event_bus = self._bb._event_bus
            if event_bus is not None:
                try:
                    event_bus.unsubscribe("blackboard.write", self._on_blackboard_write)
                except Exception:  # silent-ok: best-effort-cleanup — 退订为 best-effort 清理
                    pass

    async def _on_blackboard_write(self, event: Event | BlackboardEntry) -> None:
        """Handle blackboard write event and push to WS clients."""
        if not self._running:
            return
        try:
            from maop.dashboard.ws_broadcast import _ws_broadcast
        except ImportError:
            logger.warning("[bb-ws-bridge] ws_broadcast not available")
            return

        if isinstance(event, BlackboardEntry):
            payload = {
                "type": "blackboard.write",
                "data": event.to_dict(),
            }
        elif isinstance(event, Event):
            payload = {
                "type": "blackboard.write",
                "data": event.data,
            }
        else:
            return

        try:
            await _ws_broadcast(payload)
        except Exception as exc:
            logger.debug("[bb-ws-bridge] broadcast failed: %s", exc)

    async def _on_direct_write(self, entry: BlackboardEntry) -> None:
        """Handle direct blackboard callback (non-EventBus mode)."""
        if not self._running:
            return
        try:
            from maop.dashboard.ws_broadcast import _ws_broadcast
            await _ws_broadcast({
                "type": "blackboard.write",
                "data": entry.to_dict(),
            })
        except Exception as exc:
            logger.debug("[bb-ws-bridge] broadcast failed: %s", exc)
