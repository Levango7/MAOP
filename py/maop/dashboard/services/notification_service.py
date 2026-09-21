"""Notification & alert service layer.

Encapsulates business logic for notification management (notifications.py)
and Alertmanager webhook ingestion (alerts.py). Extracted from the
router layer so routers only do parameter parsing + auth + service call +
response formatting.

The service is framework-agnostic: it does not import FastAPI (except
for ``HTTPException`` in the alerts helper where an HTTP error is
intrinsic to the API contract) and can be unit-tested or invoked
without an HTTP context.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time  # noqa: F401
from typing import Any

logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════════════════════
# Notification manager singleton
# ════════════════════════════════════════════════════════════════════════════

_notification_manager: Any = None
_event_bus: Any = None
_manager_lock = threading.Lock()


def _get_manager() -> Any:
    # P1-18: 双重检查锁定保护单例初始化
    global _notification_manager, _event_bus
    if _notification_manager is None:
        with _manager_lock:
            if _notification_manager is None:
                from maop.enterprise.notification import EventBus, NotificationManager
                _event_bus = EventBus()
                _notification_manager = NotificationManager(event_bus=_event_bus)
    return _notification_manager


def get_notification_manager() -> Any:
    """Public accessor — used by server.py to wire the WS broadcaster."""
    return _get_manager()


# ════════════════════════════════════════════════════════════════════════════
# Channels
# ════════════════════════════════════════════════════════════════════════════

def list_channels(tenant_id: str = "") -> list[dict[str, Any]]:
    """List notification channels filtered by tenant."""
    mgr = _get_manager()
    items = mgr.list_channels(tenant_id=tenant_id)
    return [i.model_dump() for i in items]


def create_channel(body: Any) -> dict[str, Any]:
    """Create a notification channel. Returns model_dump."""
    mgr = _get_manager()
    channel = mgr.create_channel(body)
    return channel.model_dump()


def get_channel(channel_id: str) -> dict[str, Any] | None:
    """Get a single channel. Returns model_dump or None."""
    mgr = _get_manager()
    channel = mgr.get_channel(channel_id)
    return channel.model_dump() if channel else None


def update_channel(channel_id: str, body: Any) -> dict[str, Any] | None:
    """Update a channel. Returns model_dump or None."""
    mgr = _get_manager()
    channel = mgr.update_channel(channel_id, body)
    return channel.model_dump() if channel else None


def delete_channel(channel_id: str) -> bool:
    """Delete a channel. Returns ok flag."""
    mgr = _get_manager()
    return mgr.delete_channel(channel_id)


# ════════════════════════════════════════════════════════════════════════════
# Rules
# ════════════════════════════════════════════════════════════════════════════

def list_rules(tenant_id: str = "", event_type: str = "") -> list[dict[str, Any]]:
    """List notification rules filtered by tenant/event_type."""
    mgr = _get_manager()
    items = mgr.list_rules(tenant_id=tenant_id, event_type=event_type)
    return [i.model_dump() for i in items]


def create_rule(body: Any) -> dict[str, Any]:
    """Create a rule. Returns model_dump."""
    mgr = _get_manager()
    rule = mgr.create_rule(body)
    return rule.model_dump()


def get_rule(rule_id: str) -> dict[str, Any] | None:
    """Get a single rule. Returns model_dump or None."""
    mgr = _get_manager()
    rule = mgr.get_rule(rule_id)
    return rule.model_dump() if rule else None


def update_rule(rule_id: str, body: Any) -> dict[str, Any] | None:
    """Update a rule. Returns model_dump or None."""
    mgr = _get_manager()
    rule = mgr.update_rule(rule_id, body)
    return rule.model_dump() if rule else None


def delete_rule(rule_id: str) -> bool:
    """Delete a rule. Returns ok flag."""
    mgr = _get_manager()
    return mgr.delete_rule(rule_id)


# ════════════════════════════════════════════════════════════════════════════
# Templates
# ════════════════════════════════════════════════════════════════════════════

def list_templates(tenant_id: str = "") -> list[dict[str, Any]]:
    """List templates filtered by tenant."""
    mgr = _get_manager()
    items = mgr.list_templates(tenant_id=tenant_id)
    return [i.model_dump() for i in items]


def create_template(body: Any) -> dict[str, Any]:
    """Create a template. Returns model_dump."""
    mgr = _get_manager()
    template = mgr.create_template(body)
    return template.model_dump()


def get_template(template_id: str) -> dict[str, Any] | None:
    """Get a single template. Returns model_dump or None."""
    mgr = _get_manager()
    template = mgr.get_template(template_id)
    return template.model_dump() if template else None


def delete_template(template_id: str) -> bool:
    """Delete a template. Returns ok flag."""
    mgr = _get_manager()
    return mgr.delete_template(template_id)


# ════════════════════════════════════════════════════════════════════════════
# Notifications (user-facing)
# ════════════════════════════════════════════════════════════════════════════

def list_notifications(
    tenant_id: str = "",
    user_id: str = "",
    notif_status: str = "",
    channel_id: str = "",
    event_type: str = "",
    unread_only: bool = False,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    """List notifications. Returns (items_dump, total)."""
    mgr = _get_manager()
    items, total = mgr.list_notifications(
        tenant_id=tenant_id, user_id=user_id, status=notif_status,
        channel_id=channel_id, event_type=event_type,
        unread_only=unread_only, limit=limit, offset=offset,
    )
    return [i.model_dump() for i in items], total


def mark_all_read(user_id: str, tenant_id: str) -> int:
    """Mark all notifications read for a user. Returns count."""
    mgr = _get_manager()
    return mgr.mark_all_read(user_id=user_id, tenant_id=tenant_id)


def unread_count(user_id: str, tenant_id: str) -> int:
    """Return unread notification count for a user."""
    mgr = _get_manager()
    return mgr.unread_count(user_id=user_id, tenant_id=tenant_id)


def get_notification(notification_id: str) -> Any:
    """Get a notification model (raw, for ownership check). Returns None if not found."""
    mgr = _get_manager()
    return mgr.get_notification(notification_id)


def mark_read(notification_id: str, user_id: str = "") -> bool:
    """Mark a notification read. Returns ok flag."""
    mgr = _get_manager()
    if user_id:
        return mgr.mark_read(notification_id, user_id=user_id)
    return mgr.mark_read(notification_id)


def delete_notification(notification_id: str) -> bool:
    """Delete a notification. Returns ok flag."""
    mgr = _get_manager()
    return mgr.delete_notification(notification_id)


# ════════════════════════════════════════════════════════════════════════════
# Direct send (admin)
# ════════════════════════════════════════════════════════════════════════════

async def send_notification(
    channel_id: str,
    title: str,
    body: str,
    level: str = "info",
    tenant_id: str = "",
    user_id: str = "",
    event_type: str = "",
    event_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Send a notification directly (bypass rules). Returns model_dump."""
    from maop.enterprise.notification.models import NotificationLevel
    mgr = _get_manager()
    notif = await mgr.send_notification(
        channel_id=channel_id, title=title, body=body,
        level=NotificationLevel(level), tenant_id=tenant_id,
        user_id=user_id, event_type=event_type,
        event_payload=event_payload or {},
    )
    return notif.model_dump()


# ════════════════════════════════════════════════════════════════════════════
# Dead letters (admin)
# ════════════════════════════════════════════════════════════════════════════

def list_dead_letters(tenant_id: str = "", limit: int = 100) -> list[dict[str, Any]]:
    """List dead-letter notifications."""
    mgr = _get_manager()
    items = mgr.list_dead_letters(tenant_id=tenant_id, limit=limit)
    return [i.model_dump() for i in items]


# ════════════════════════════════════════════════════════════════════════════
# Preferences
# ════════════════════════════════════════════════════════════════════════════

def get_preference(user_id: str) -> dict[str, Any] | None:
    """Get a user's notification preference. Returns model_dump or None."""
    mgr = _get_manager()
    pref = mgr.get_preference(user_id)
    return pref.model_dump() if pref else None


def update_preference(user_id: str, body: Any, tenant_id: str = "") -> dict[str, Any]:
    """Update a user's notification preference. Returns model_dump."""
    mgr = _get_manager()
    pref = mgr.update_preference(user_id, body, tenant_id=tenant_id)
    return pref.model_dump()


# ════════════════════════════════════════════════════════════════════════════
# Event publishing
# ════════════════════════════════════════════════════════════════════════════

async def publish_event(
    event_type: str,
    payload: dict[str, Any],
    tenant_id: str = "",
) -> list:
    """Publish an event to the bus (triggers rule-matched delivery).

    Returns the list of delivered target IDs.
    """
    mgr = _get_manager()
    delivered = await mgr.event_bus.emit(event_type, payload, tenant_id=tenant_id)
    return delivered


# ════════════════════════════════════════════════════════════════════════════
# Stats (admin)
# ════════════════════════════════════════════════════════════════════════════

def get_stats() -> Any:
    """Return notification manager stats."""
    mgr = _get_manager()
    return mgr.stats()


# ════════════════════════════════════════════════════════════════════════════
# WebSocket broadcast
# ════════════════════════════════════════════════════════════════════════════

_ws_clients: set = set()
_ws_lock = asyncio.Lock()


async def register_ws_client(ws: Any) -> None:
    """Register a WebSocket client for notification broadcast."""
    async with _ws_lock:
        _ws_clients.add(ws)


async def unregister_ws_client(ws: Any) -> None:
    """Remove a WebSocket client from the broadcast set."""
    async with _ws_lock:
        _ws_clients.discard(ws)


async def ws_broadcast_notification(notif: dict[str, Any]) -> None:
    """Broadcast a notification to all connected /api/notifications/ws clients."""
    async with _ws_lock:
        clients = list(_ws_clients)
    if not clients:
        return
    msg = {"type": "notification", "data": notif}
    dead: list = []
    for ws in clients:
        try:
            await asyncio.wait_for(ws.send_json(msg), timeout=5.0)
        except Exception:
            dead.append(ws)
    if dead:
        async with _ws_lock:
            for ws in dead:
                _ws_clients.discard(ws)


def wire_broadcaster() -> None:
    """Wire the notification manager's broadcaster to the WS pool.

    Called by server.py after both the notification router and the main
    WebSocket pool are initialised. Once wired, every InApp notification
    created by the manager is pushed to all connected
    ``/api/notifications/ws`` clients.
    """
    mgr = _get_manager()
    mgr.set_broadcaster(ws_broadcast_notification)


# ════════════════════════════════════════════════════════════════════════════
# Alertmanager webhook ingestion
# ════════════════════════════════════════════════════════════════════════════

def process_alertmanager_webhook(payload: Any) -> int:
    """Log alerts from an Alertmanager webhook payload.

    Iterates over ``payload.alerts`` and logs ``alertname`` / ``severity``
    / ``summary`` for each so operators can triage from dashboard logs.

    Returns the number of alerts received (0 for an empty batch).
    """
    if not payload.alerts:
        logger.info(
            "[alerts/webhook] received empty alert batch (status=%s, receiver=%s)",
            payload.status, payload.receiver,
        )
        return 0

    for alert in payload.alerts:
        alertname = alert.labels.get("alertname", "<unknown>")
        severity = alert.labels.get("severity", "unknown")
        summary = alert.annotations.get("summary", "")
        description = alert.annotations.get("description", "")
        logger.warning(
            "[alerts/webhook] %s alert=%s severity=%s summary=%s%s",
            alert.status or payload.status,
            alertname,
            severity,
            summary,
            f" description={description}" if description else "",
        )

    return len(payload.alerts)