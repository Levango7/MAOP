"""MAOP Dashboard — Alertmanager webhook receiver.

Minimal endpoint that accepts Alertmanager webhook payloads
(``POST /api/alerts/webhook``) and records them in the application log.

It deliberately does NOT persist alerts or perform any side effects — it is
the lowest-friction sink so the monitoring chain (Prometheus → Alertmanager →
here) is fully wired even before a richer notification backend exists.
"""
from __future__ import annotations

import hmac
import logging
import os
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from maop.dashboard.error_handler import handle_api_errors

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/alerts", tags=["alerts"])


class _Alert(BaseModel):
    """A single Alertmanager alert inside a webhook payload."""

    status: str = ""
    labels: dict[str, str] = Field(default_factory=dict)
    annotations: dict[str, str] = Field(default_factory=dict)


class WebhookPayload(BaseModel):
    """Alertmanager webhook payload (version 4, subset used here)."""

    receiver: str = ""
    status: str = ""  # "firing" | "resolved"
    alerts: list[_Alert] = Field(default_factory=list)
    commonLabels: dict[str, str] = Field(default_factory=dict)
    commonAnnotations: dict[str, str] = Field(default_factory=dict)
    externalURL: str = ""


@router.post("/webhook")
@handle_api_errors
async def alertmanager_webhook(payload: WebhookPayload, request: Request) -> Any:
    """Receive Alertmanager webhook posts and log each alert.

    Logs ``alertname`` / ``severity`` / ``summary`` for every alert so
    operators can triage from the dashboard logs. Returns a 200 so
    Alertmanager does not retry indefinitely.

    Security: when the ``ALERTS_WEBHOOK_SECRET`` environment variable is
    set, the request must carry a matching ``X-Webhook-Secret`` header.
    H-3 fix: fail-closed —— 若 env var 未设置则默认拒绝（403），
    避免未配置密钥时 webhook 被任意调用。
    """
    # ── Shared-secret guard (fail-closed) ───────────────────────────
    expected_secret = os.getenv("ALERTS_WEBHOOK_SECRET", "").strip()
    if not expected_secret:
        # H-3 fix: fail-closed —— 密钥未配置时拒绝请求，而非放行。
        logger.warning("[alerts/webhook] rejected: ALERTS_WEBHOOK_SECRET not configured")
        raise HTTPException(status_code=403, detail="Webhook secret not configured")
    provided_secret = (request.headers.get("X-Webhook-Secret", "") or "").strip()
    if not hmac.compare_digest(provided_secret, expected_secret):
        logger.warning("[alerts/webhook] rejected: missing or mismatched X-Webhook-Secret")
        raise HTTPException(status_code=401, detail="Unauthorized")

    if not payload.alerts:
        logger.info(
            "[alerts/webhook] received empty alert batch (status=%s, receiver=%s)",
            payload.status, payload.receiver,
        )
        return {"status": "ok", "received": 0}

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

    return {"status": "ok", "received": len(payload.alerts)}


__all__ = ["router"]
