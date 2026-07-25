"""Dashboard API routes for n8n integration (Enterprise only).

Endpoints:
  POST /api/n8n/webhook        - Receive webhook from n8n
  GET  /api/n8n/workflows      - List n8n workflows
  POST /api/n8n/workflows/{id}/trigger - Trigger an n8n workflow
  GET  /api/n8n/executions/{id} - Get execution status
  GET  /api/n8n/health         - Check n8n connectivity
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status

from maop.core.middleware import require_admin
from maop.config.edition import FeatureFlag, has_feature
from maop.enterprise.n8n import (
    N8nClient,
    N8nIntegrationError,
    handle_n8n_webhook,
    require_n8n_feature,
)

router = APIRouter(prefix="/api/n8n", tags=["n8n"])


def _get_client() -> N8nClient:
    """Create an N8nClient from environment configuration."""
    return N8nClient(
        base_url=os.getenv("N8N_BASE_URL", "http://localhost:5678"),
        api_key=os.getenv("N8N_API_KEY", ""),
    )


@router.post("/webhook")
async def receive_webhook(request: Request) -> dict[str, Any]:
    """Receive a webhook from n8n.

    通过 HMAC-SHA256 签名校验（请求头 ``X-N8N-Signature`` 或
    ``X-MAOP-Signature``）替代管理员鉴权——n8n 在请求头中携带用共享密钥
    计算的签名。需配置环境变量 ``N8N_WEBHOOK_SECRET``；未配置时仅记录
    警告（向后兼容）。端点仍受 Enterprise 特性开关保护。
    """
    if not has_feature(FeatureFlag.N8N_INTEGRATION):
        raise HTTPException(status_code=404, detail="n8n integration not available")

    raw_body = await request.body()
    signature = request.headers.get("X-N8N-Signature") or request.headers.get("X-MAOP-Signature")

    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {exc}") from exc

    return handle_n8n_webhook(payload, raw_body=raw_body, signature=signature)


@router.get("/workflows")
async def list_workflows(request: Request) -> dict[str, Any]:
    """List all n8n workflows."""
    require_admin(request)
    if not has_feature(FeatureFlag.N8N_INTEGRATION):
        raise HTTPException(status_code=404, detail="n8n integration not available")

    with _get_client() as client:
        try:
            workflows = client.list_workflows()
            return {"workflows": workflows, "count": len(workflows)}
        except N8nIntegrationError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/workflows/{workflow_id}/trigger")
async def trigger_workflow(
    workflow_id: str,
    request: Request,
    data: dict[str, Any] | None = None,
    wait: bool = False,
) -> dict[str, Any]:
    """Trigger an n8n workflow by ID."""
    require_admin(request)
    if not has_feature(FeatureFlag.N8N_INTEGRATION):
        raise HTTPException(status_code=404, detail="n8n integration not available")

    body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}

    with _get_client() as client:
        try:
            execution = client.trigger_workflow(
                workflow_id,
                data=body.get("data", {}),
                wait_for_completion=body.get("wait", False),
            )
            return execution.model_dump()
        except N8nIntegrationError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/executions/{execution_id}")
async def get_execution(execution_id: str, request: Request) -> dict[str, Any]:
    """Get the status of an n8n workflow execution."""
    require_admin(request)
    if not has_feature(FeatureFlag.N8N_INTEGRATION):
        raise HTTPException(status_code=404, detail="n8n integration not available")

    with _get_client() as client:
        try:
            execution = client.get_execution(execution_id)
            return execution.model_dump()
        except N8nIntegrationError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/health")
async def health_check(request: Request) -> dict[str, Any]:
    """Check if n8n is reachable."""
    require_admin(request)
    if not has_feature(FeatureFlag.N8N_INTEGRATION):
        raise HTTPException(status_code=404, detail="n8n integration not available")

    with _get_client() as client:
        healthy = client.health_check()
        return {"n8n_reachable": healthy, "base_url": client._base_url}