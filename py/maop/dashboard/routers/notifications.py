"""Notifications router — exposes NotificationManager via FastAPI endpoints.

All endpoints are mounted under ``/api/notifications/*``. Write operations
require admin role via :func:`require_admin`. Read operations (list/get for
the authenticated user) are open to any authenticated user — the manager
filters by ``tenant_id`` from the JWT claim for isolation.

Business logic (manager singleton, CRUD, send, broadcast, stats) lives in
:mod:`maop.dashboard.services.notification_service`; this router only does
request parsing, auth/identity extraction, ownership checks, service
dispatch, and response formatting.

Endpoints:
  Channels:
    GET    /api/notifications/channels
    POST   /api/notifications/channels
    GET    /api/notifications/channels/{channel_id}
    PUT    /api/notifications/channels/{channel_id}
    DELETE /api/notifications/channels/{channel_id}

  Rules:
    GET    /api/notifications/rules
    POST   /api/notifications/rules
    GET    /api/notifications/rules/{rule_id}
    PUT    /api/notifications/rules/{rule_id}
    DELETE /api/notifications/rules/{rule_id}

  Templates:
    GET    /api/notifications/templates
    POST   /api/notifications/templates
    GET    /api/notifications/templates/{template_id}
    DELETE /api/notifications/templates/{template_id}

  Notifications (user-facing):
    GET    /api/notifications/list
    GET    /api/notifications/{notification_id}
    POST   /api/notifications/{notification_id}/read
    POST   /api/notifications/read-all
    GET    /api/notifications/unread-count
    DELETE /api/notifications/{notification_id}

  Direct send (admin):
    POST   /api/notifications/send

  Dead letters (admin):
    GET    /api/notifications/dead-letters

  Preferences:
    GET    /api/notifications/preferences
    PUT    /api/notifications/preferences

  Event publishing (admin / internal):
    POST   /api/notifications/events/publish

  Stats (admin):
    GET    /api/notifications/stats

  WebSocket (real-time push):
    WS     /api/notifications/ws
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, WebSocket, WebSocketDisconnect, status
from pydantic import BaseModel, Field

from maop.core.security.middleware import require_admin
from maop.dashboard.error_handler import handle_api_errors
from maop.dashboard.services import notification_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


# ── Request models for endpoints not covered by manager models ────


class SendNotificationRequest(BaseModel):
    """Direct send (bypass rules)."""

    channel_id: str
    title: str = Field(min_length=1, max_length=500)
    body: str = Field(min_length=1)
    level: str = "info"
    tenant_id: str = ""
    user_id: str = ""
    event_type: str = ""
    event_payload: dict[str, Any] = Field(default_factory=dict)


class PublishEventRequest(BaseModel):
    """Publish an event to the bus (triggers rule-matched delivery)."""

    event_type: str = Field(min_length=1)
    payload: dict[str, Any] = Field(default_factory=dict)
    tenant_id: str = ""


# ── 请求模型：直接复用 enterprise.notification.models 中的 Create/Update ──
# 让 FastAPI 自动校验请求体，避免手动 ``dict → Pydantic`` 转换。
from maop.enterprise.notification.models import (
    ChannelCreate,
    ChannelUpdate,
    PreferenceUpdate,
    RuleCreate,
    RuleUpdate,
    TemplateCreate,
)

# ── Identity / auth helpers (router-layer concerns) ───────────────


def _tenant_id_from_request(request: Request) -> str:
    """Extract tenant_id from JWT-injected request state.

    Falls back to empty string (single-tenant / personal edition).
    """
    return getattr(request.state, "tenant_id", "") or ""


def _user_id_from_request(request: Request) -> str:
    """Extract the authenticated user's identity from request state."""
    return getattr(request.state, "auth_identity", "") or ""


def _is_admin(request: Request) -> bool:
    roles = getattr(request.state, "auth_roles", None) or []
    return bool({"admin", "superadmin"} & set(roles))


def _require_feature() -> None:
    """Gate enterprise-only feature. Notifications work in both editions
    but the router is registered only when MULTI_USER is on (server.py).
    For personal edition we still allow the router (notifications are
    useful in single-user mode too) — no-op here.
    """
    # Intentionally permissive: notifications are available in both editions.
    # The FeatureFlag check is done at router registration time in server.py.
    return


# ── Backward-compat accessor re-exports ───────────────────────────


def get_notification_manager() -> Any:
    """Public accessor — used by server.py to wire the WS broadcaster."""
    return notification_service.get_notification_manager()


def wire_broadcaster() -> None:
    """Wire the notification manager's broadcaster to this router's WS pool.

    Called by server.py after both the notification router and the main
    WebSocket pool are initialised.
    """
    notification_service.wire_broadcaster()


# ── Channel endpoints ─────────────────────────────────────────────


@router.get("/channels")
@handle_api_errors
async def list_channels(
    request: Request,
    tenant_id: str = Query("", description="Filter by tenant (admin only)"),
) -> dict[str, Any]:
    _require_feature()
    # Non-admin users can only see their own tenant's channels
    req_tenant = _tenant_id_from_request(request)
    if not _is_admin(request) and req_tenant:
        tenant_id = req_tenant
    items = notification_service.list_channels(tenant_id=tenant_id)
    return {"status": "ok", "channels": items, "count": len(items)}


@router.post("/channels")
@handle_api_errors
async def create_channel(body: ChannelCreate, request: Request) -> dict[str, Any]:
    require_admin(request)
    _require_feature()
    # Inject tenant_id from JWT if not provided in body
    if not body.tenant_id:
        body.tenant_id = _tenant_id_from_request(request)
    channel = notification_service.create_channel(body)
    return {"status": "ok", "channel": channel}


@router.get("/channels/{channel_id}")
@handle_api_errors
async def get_channel(channel_id: str, request: Request) -> dict[str, Any]:
    require_admin(request)
    _require_feature()
    channel = notification_service.get_channel(channel_id)
    if channel is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Channel not found")
    return {"status": "ok", "channel": channel}


@router.put("/channels/{channel_id}")
@handle_api_errors
async def update_channel(channel_id: str, body: ChannelUpdate, request: Request) -> dict[str, Any]:
    require_admin(request)
    _require_feature()
    channel = notification_service.update_channel(channel_id, body)
    if channel is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Channel not found")
    return {"status": "ok", "channel": channel}


@router.delete("/channels/{channel_id}")
@handle_api_errors
async def delete_channel(channel_id: str, request: Request) -> dict[str, Any]:
    require_admin(request)
    _require_feature()
    ok = notification_service.delete_channel(channel_id)
    if not ok:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Channel not found")
    return {"status": "ok", "deleted": True}


# ── Rule endpoints ────────────────────────────────────────────────


@router.get("/rules")
@handle_api_errors
async def list_rules(
    request: Request,
    tenant_id: str = Query(""),
    event_type: str = Query(""),
) -> dict[str, Any]:
    _require_feature()
    req_tenant = _tenant_id_from_request(request)
    if not _is_admin(request) and req_tenant:
        tenant_id = req_tenant
    items = notification_service.list_rules(tenant_id=tenant_id, event_type=event_type)
    return {"status": "ok", "rules": items, "count": len(items)}


@router.post("/rules")
@handle_api_errors
async def create_rule(body: RuleCreate, request: Request) -> dict[str, Any]:
    require_admin(request)
    _require_feature()
    if not body.tenant_id:
        body.tenant_id = _tenant_id_from_request(request)
    rule = notification_service.create_rule(body)
    return {"status": "ok", "rule": rule}


@router.get("/rules/{rule_id}")
@handle_api_errors
async def get_rule(rule_id: str, request: Request) -> dict[str, Any]:
    require_admin(request)
    _require_feature()
    rule = notification_service.get_rule(rule_id)
    if rule is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rule not found")
    return {"status": "ok", "rule": rule}


@router.put("/rules/{rule_id}")
@handle_api_errors
async def update_rule(rule_id: str, body: RuleUpdate, request: Request) -> dict[str, Any]:
    require_admin(request)
    _require_feature()
    rule = notification_service.update_rule(rule_id, body)
    if rule is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rule not found")
    return {"status": "ok", "rule": rule}


@router.delete("/rules/{rule_id}")
@handle_api_errors
async def delete_rule(rule_id: str, request: Request) -> dict[str, Any]:
    require_admin(request)
    _require_feature()
    ok = notification_service.delete_rule(rule_id)
    if not ok:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rule not found")
    return {"status": "ok", "deleted": True}


# ── Template endpoints ────────────────────────────────────────────


@router.get("/templates")
@handle_api_errors
async def list_templates(
    request: Request,
    tenant_id: str = Query(""),
) -> dict[str, Any]:
    _require_feature()
    req_tenant = _tenant_id_from_request(request)
    if not _is_admin(request) and req_tenant:
        tenant_id = req_tenant
    items = notification_service.list_templates(tenant_id=tenant_id)
    return {"status": "ok", "templates": items, "count": len(items)}


@router.post("/templates")
@handle_api_errors
async def create_template(body: TemplateCreate, request: Request) -> dict[str, Any]:
    require_admin(request)
    _require_feature()
    if not body.tenant_id:
        body.tenant_id = _tenant_id_from_request(request)
    template = notification_service.create_template(body)
    return {"status": "ok", "template": template}


@router.get("/templates/{template_id}")
@handle_api_errors
async def get_template(template_id: str, request: Request) -> dict[str, Any]:
    require_admin(request)
    _require_feature()
    template = notification_service.get_template(template_id)
    if template is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template not found")
    return {"status": "ok", "template": template}


@router.delete("/templates/{template_id}")
@handle_api_errors
async def delete_template(template_id: str, request: Request) -> dict[str, Any]:
    require_admin(request)
    _require_feature()
    ok = notification_service.delete_template(template_id)
    if not ok:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template not found")
    return {"status": "ok", "deleted": True}


# ── Notification list / read / delete ────────────────────────────


@router.get("/list")
@handle_api_errors
async def list_notifications(
    request: Request,
    user_id: str = Query(""),
    tenant_id: str = Query(""),
    channel_id: str = Query(""),
    event_type: str = Query(""),
    notif_status: str = Query("", alias="status"),
    unread_only: bool = Query(False),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    _require_feature()
    # Non-admin: force user_id and tenant_id to their own
    req_tenant = _tenant_id_from_request(request)
    req_user = _user_id_from_request(request)
    if not _is_admin(request):
        user_id = req_user
        if req_tenant:
            tenant_id = req_tenant
    items, total = notification_service.list_notifications(
        tenant_id=tenant_id,
        user_id=user_id,
        notif_status=notif_status,
        channel_id=channel_id,
        event_type=event_type,
        unread_only=unread_only,
        limit=limit,
        offset=offset,
    )
    return {
        "status": "ok",
        "notifications": items,
        "count": len(items),
        "total": total,
    }


@router.post("/read-all")
@handle_api_errors
async def mark_all_read(request: Request, user_id: str = Query("")) -> dict[str, Any]:
    _require_feature()
    req_tenant = _tenant_id_from_request(request)
    req_user = _user_id_from_request(request)
    if not _is_admin(request):
        user_id = req_user
    count = notification_service.mark_all_read(user_id=user_id, tenant_id=req_tenant)
    return {"status": "ok", "marked_read": count}


@router.get("/unread-count")
@handle_api_errors
async def unread_count(
    request: Request,
    user_id: str = Query(""),
) -> dict[str, Any]:
    _require_feature()
    req_tenant = _tenant_id_from_request(request)
    req_user = _user_id_from_request(request)
    if not _is_admin(request):
        user_id = req_user
    count = notification_service.unread_count(user_id=user_id, tenant_id=req_tenant)
    return {"status": "ok", "unread_count": count}


# ── Direct send (admin) ──────────────────────────────────────────


@router.post("/send")
@handle_api_errors
async def send_notification(body: SendNotificationRequest, request: Request) -> dict[str, Any]:
    require_admin(request)
    _require_feature()
    if not body.tenant_id:
        body.tenant_id = _tenant_id_from_request(request)
    notif = await notification_service.send_notification(
        channel_id=body.channel_id,
        title=body.title,
        body=body.body,
        level=body.level,
        tenant_id=body.tenant_id,
        user_id=body.user_id,
        event_type=body.event_type,
        event_payload=body.event_payload,
    )
    return {"status": "ok", "notification": notif}


# ── Dead letters (admin) ─────────────────────────────────────────


@router.get("/dead-letters")
@handle_api_errors
async def list_dead_letters(
    request: Request,
    tenant_id: str = Query(""),
    limit: int = Query(100, ge=1, le=1000),
) -> dict[str, Any]:
    require_admin(request)
    _require_feature()
    items = notification_service.list_dead_letters(tenant_id=tenant_id, limit=limit)
    return {"status": "ok", "dead_letters": items, "count": len(items)}


# ── Preferences ──────────────────────────────────────────────────


@router.get("/preferences")
@handle_api_errors
async def get_preferences(request: Request, user_id: str = Query("")) -> dict[str, Any]:
    _require_feature()
    req_user = _user_id_from_request(request)
    if not _is_admin(request):
        user_id = req_user
    if not user_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="user_id required")
    pref = notification_service.get_preference(user_id)
    return {"status": "ok", "preference": pref}


@router.put("/preferences")
@handle_api_errors
async def update_preferences(body: PreferenceUpdate, request: Request, user_id: str = Query("")) -> dict[str, Any]:
    _require_feature()
    req_user = _user_id_from_request(request)
    req_tenant = _tenant_id_from_request(request)
    if not _is_admin(request):
        user_id = req_user
    if not user_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="user_id required")
    pref = notification_service.update_preference(user_id, body, tenant_id=req_tenant)
    return {"status": "ok", "preference": pref}


# ── Event publishing ─────────────────────────────────────────────


@router.post("/events/publish")
@handle_api_errors
async def publish_event(body: PublishEventRequest, request: Request) -> dict[str, Any]:
    require_admin(request)
    _require_feature()
    if not body.tenant_id:
        body.tenant_id = _tenant_id_from_request(request)
    delivered = await notification_service.publish_event(
        body.event_type, body.payload, tenant_id=body.tenant_id
    )
    return {"status": "ok", "delivered_to": delivered}


# ── Stats (admin) ────────────────────────────────────────────────


@router.get("/stats")
@handle_api_errors
async def get_stats(request: Request) -> dict[str, Any]:
    require_admin(request)
    _require_feature()
    return {"status": "ok", "stats": notification_service.get_stats()}


# ── Dynamic notification routes (MUST come after all static paths) ──
# WARNING: route order matters - static paths must come before parameterized paths.
# FastAPI matches routes in registration order. The /{notification_id}
# wildcard would otherwise shadow /unread-count, /dead-letters, /stats,
# /send, /preferences, /events/publish, /ws, etc. So we register these
# dynamic routes last.


def _check_notification_ownership(notif: Any, request: Request) -> None:
    """Verify the authenticated user owns ``notif``.

    Non-admin users may only access notifications whose ``user_id`` matches
    their own identity (and, when multi-tenant, whose ``tenant_id`` matches).
    Mismatch raises 404 (not 403) to avoid leaking resource existence.
    Admins bypass the check. Mirrors the isolation logic in
    :func:`list_notifications` (line 353).
    """
    if _is_admin(request):
        return
    req_user = _user_id_from_request(request)
    req_tenant = _tenant_id_from_request(request)
    if req_user and getattr(notif, "user_id", "") != req_user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notification not found")
    if req_tenant and getattr(notif, "tenant_id", "") != req_tenant:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notification not found")


@router.get("/{notification_id}")
@handle_api_errors
async def get_notification(notification_id: str, request: Request) -> dict[str, Any]:
    _require_feature()
    notif = notification_service.get_notification(notification_id)
    if notif is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notification not found")
    _check_notification_ownership(notif, request)
    return {"status": "ok", "notification": notif.model_dump()}


@router.post("/{notification_id}/read")
@handle_api_errors
async def mark_read(notification_id: str, request: Request) -> dict[str, Any]:
    _require_feature()
    # Fetch first to enforce ownership before mutating (IDOR fix).
    notif = notification_service.get_notification(notification_id)
    if notif is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notification not found")
    _check_notification_ownership(notif, request)
    ok = notification_service.mark_read(notification_id)
    if not ok:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notification not found")
    return {"status": "ok", "read": True}


@router.delete("/{notification_id}")
@handle_api_errors
async def delete_notification(notification_id: str, request: Request) -> dict[str, Any]:
    _require_feature()
    # Fetch first to enforce ownership before deleting (IDOR fix).
    notif = notification_service.get_notification(notification_id)
    if notif is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notification not found")
    _check_notification_ownership(notif, request)
    ok = notification_service.delete_notification(notification_id)
    if not ok:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notification not found")
    return {"status": "ok", "deleted": True}


# ── WebSocket (real-time push) ───────────────────────────────────
# Per-client connection set + lock. The manager's broadcaster pushes
# InApp notifications to all connected clients. Clients can also send
# "ping" to keep the connection alive.
# Connection state lives in notification_service; this endpoint only
# handles auth + message dispatch.


@router.websocket("/ws")
async def notifications_ws(ws: WebSocket) -> Any:
    """WebSocket for real-time notification push.

    Auth: same JWT subprotocol scheme as the main /ws endpoint. The
    client passes the token via ``Sec-WebSocket-Protocol`` header or
    ``?token=`` query param.

    Messages pushed to clients:
      - ``{"type": "notification", "data": {...}}``  — new notification
      - ``{"type": "unread_count", "count": N}``     — updated count
      - ``{"type": "pong", "ts": ...}``              — reply to "ping"

    Client messages:
      - ``"ping"``  — keepalive
      - ``{"action": "mark_read", "id": "..."}``  — mark notification read
    """
    # Auth — same logic as the main /ws endpoint (ws_broadcast.py).
    token = ws.query_params.get("token", "")
    if not token:
        protocols = ws.headers.get("sec-websocket-protocol", "")
        if protocols:
            parts = [p.strip() for p in protocols.split(",") if p.strip()]
            if parts:
                token = parts[-1]
    from maop.dashboard.routers import auth as _auth_mod
    # P0-10: 从 JWT 中提取 tenant_id 和 user_id，用于后续命令的越权校验
    ws_user_id: str = ""
    ws_tenant_id: str = ""
    if _auth_mod._auth_enabled:
        # P0 fix (2026-08-29): a missing token must be rejected. The previous
        # `if token:` guard skipped validation entirely when no token was
        # provided, so unauthenticated sockets were accepted even with
        # MAOP_AUTH=1. ws_broadcast.py already closes with 4401 in this case.
        if not token:
            await ws.close(code=4401, reason="Authentication required")
            return
        try:
            mgr = _auth_mod.get_auth_mgr()
            payload = mgr.jwt_handler.validate_token(token)
            if not payload or not getattr(payload, "authenticated", False):
                await ws.close(code=4401, reason="Invalid token")
                return
            # P0-10: 提取 JWT 中的 identity（user_id）和 tenant_id
            ws_user_id = getattr(payload, "identity", "") or ""
            ws_tenant_id = getattr(payload, "tenant_id", "") or ""
        except Exception:
            await ws.close(code=4401, reason="Authentication failed")
            return
    await ws.accept()
    await notification_service.register_ws_client(ws)
    try:
        await ws.send_json({"type": "hello", "msg": "MAOP Notifications WebSocket", "ts": time.time()})
        while True:
            data = await ws.receive_text()
            if data == "ping":
                await ws.send_json({"type": "pong", "ts": time.time()})
            else:
                # Try to parse JSON commands
                try:
                    cmd = json.loads(data)
                    action = cmd.get("action")
                    if action == "mark_read":
                        notif_id = cmd.get("id", "")
                        if notif_id:
                            # P0-10: 越权校验 — mark_read 必须作用于当前用户的通知
                            notification_service.mark_read(notif_id, user_id=ws_user_id)
                            await ws.send_json({"type": "ok", "action": "mark_read", "id": notif_id})
                    elif action == "unread_count":
                        # P0-10: 越权校验 — 强制使用 JWT 中的 user_id，
                        # 忽略客户端提供的 user_id，防止查询其他用户的通知计数
                        user_id = ws_user_id or cmd.get("user_id", "")
                        count = notification_service.unread_count(user_id, tenant_id=ws_tenant_id)
                        await ws.send_json({"type": "unread_count", "count": count})
                except Exception:
                    # best-effort：WebSocket 命令为即时推送，畸形/异常输入直接忽略保证连接不断
                    logger.warning("处理 WebSocket 通知命令消息失败（notifications_ws），忽略畸形输入", exc_info=True)
                    # ignore malformed input
    except WebSocketDisconnect:
        pass
    finally:
        await notification_service.unregister_ws_client(ws)
