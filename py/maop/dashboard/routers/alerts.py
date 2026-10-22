"""MAOP Dashboard — Alertmanager webhook receiver.

Accepts Alertmanager webhook payloads (``POST /api/alerts/webhook``), records
them in the application log and stores them in the bounded
``alertmanager_alerts`` table so ``GET /api/alerts`` can serve history.

Persistence is deliberately capped (see :mod:`maop.core.alert_ingest`): rows
are keyed by ``(fingerprint, status, startsAt)`` so Alertmanager's
``repeat_interval`` re-notifications collapse into one row, retention is 30
days with a 10 000-row ceiling, and a storage failure is logged rather than
raised (the endpoint still answers 200 so Alertmanager stops retrying).

Business logic (alert logging) lives in
:mod:`maop.dashboard.services.notification_service`; this router only does
request parsing, shared-secret auth, service dispatch, and response
formatting.
"""
from __future__ import annotations

import hmac
import logging
import os
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from maop.core import alert_ingest
from maop.core.alert_ingest import MAX_ALERTS_PER_WEBHOOK
from maop.dashboard.error_handler import handle_api_errors
from maop.dashboard.services import notification_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/alerts", tags=["alerts"])


class _Alert(BaseModel):
    """A single Alertmanager alert inside a webhook payload."""

    status: str = ""
    labels: dict[str, str] = Field(default_factory=dict)
    annotations: dict[str, str] = Field(default_factory=dict)
    #: Alertmanager's stable per-incident id — the dedupe key for history.
    fingerprint: str = ""
    startsAt: str = ""
    endsAt: str = ""


class WebhookPayload(BaseModel):
    """Alertmanager webhook payload (version 4, subset used here)."""

    receiver: str = ""
    status: str = ""  # "firing" | "resolved"
    #: Memory ceiling for one inbound batch: Alertmanager ships with
    #: ``max_alerts: 0`` (no truncation), so the receiver has to set the bound.
    alerts: list[_Alert] = Field(default_factory=list, max_length=MAX_ALERTS_PER_WEBHOOK)
    commonLabels: dict[str, str] = Field(default_factory=dict)
    commonAnnotations: dict[str, str] = Field(default_factory=dict)
    externalURL: str = ""


@router.post("/webhook")
@handle_api_errors
async def alertmanager_webhook(payload: WebhookPayload, request: Request) -> Any:
    """Receive Alertmanager webhook posts, log and persist each alert.

    Logs ``alertname`` / ``severity`` / ``summary`` for every alert so
    operators can triage from the dashboard logs, then stores the batch in
    ``alertmanager_alerts`` for ``GET /api/alerts``. Returns a 200 either way
    so Alertmanager does not retry indefinitely.

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

    received = notification_service.process_alertmanager_webhook(payload)
    stored = alert_ingest.record_alerts(
        [alert.model_dump() for alert in payload.alerts],
        receiver=payload.receiver,
        external_url=payload.externalURL,
        common_labels=payload.commonLabels,
        common_annotations=payload.commonAnnotations,
        fallback_status=payload.status,
    )
    return {"status": "ok", "received": received, **stored}


@router.get("")
@handle_api_errors
async def list_stored_alerts(
    limit: int = Query(50, ge=1, le=MAX_ALERTS_PER_WEBHOOK),
    status: str = Query("", max_length=20),
    severity: str = Query("", max_length=40),
    alertname: str = Query("", max_length=200),
    since_hours: int = Query(0, ge=0, le=24 * 90),
) -> dict[str, Any]:
    """读回 Alertmanager 入站告警历史（最新在前）。

    与 ``/api/audit/alert/history``（企业版规则引擎的触发历史）是两条独立来源：
    这里是外部 Alertmanager 推给我们的事件，个人版也可用。
    """
    alerts = alert_ingest.list_alerts(
        limit=limit, status=status, severity=severity,
        alertname=alertname, since_hours=since_hours,
    )
    return {"alerts": alerts, "count": len(alerts), "total": alert_ingest.count_alerts()}


__all__ = ["router"]
