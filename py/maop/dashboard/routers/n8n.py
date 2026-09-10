"""Dashboard API routes for n8n integration (Enterprise only).

Endpoints:
  POST /api/n8n/webhook        - Receive webhook from n8n
  GET  /api/n8n/workflows      - List n8n workflows
  POST /api/n8n/workflows/{id}/trigger - Trigger an n8n workflow
  GET  /api/n8n/executions/{id} - Get execution status
  GET  /api/n8n/health         - Check n8n connectivity
"""

from __future__ import annotations

import logging
import os
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from maop.config.edition import FeatureFlag, has_feature
from maop.core.security.middleware import require_admin
from maop.dashboard.error_handler import handle_api_errors
from maop.enterprise.n8n import (
    N8nClient,
    N8nIntegrationError,
    handle_n8n_webhook,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/n8n", tags=["n8n"])

# Module-level singleton cache for N8nClient — avoids re-creating the client
# (and re-reading env config) on every request. Invalidated if env changes.
_n8n_client: N8nClient | None = None
_n8n_client_env: tuple[str, str] | None = None


def _get_client() -> N8nClient:
    """Return a cached N8nClient singleton, re-created only when env config changes."""
    global _n8n_client, _n8n_client_env
    base_url = os.getenv("N8N_BASE_URL", "http://localhost:5678")
    api_key = os.getenv("N8N_API_KEY", "")
    current_env = (base_url, api_key)
    if _n8n_client is None or _n8n_client_env != current_env:
        _n8n_client = N8nClient(base_url=base_url, api_key=api_key)
        _n8n_client_env = current_env
    return _n8n_client


@router.post("/webhook")
@handle_api_errors("n8n webhook")
async def receive_webhook(request: Request) -> dict[str, Any]:
    """Receive a webhook from n8n.

    通过 HMAC-SHA256 签名校验（请求头 ``X-N8N-Signature`` 或
    ``X-MAOP-Signature``）替代管理员鉴权——n8n 在请求头中携带用共享密钥
    计算的签名。需配置环境变量 ``N8N_WEBHOOK_SECRET``；未配置时返回 403
    Forbidden 以拒绝无鉴权的 webhook 请求。端点仍受 Enterprise 特性开关保护。
    """
    if not has_feature(FeatureFlag.N8N_INTEGRATION):
        raise HTTPException(status_code=404, detail="n8n integration not available")

    # P0 fix: 未配置 N8N_WEBHOOK_SECRET 时，webhook 完全无鉴权——拒绝请求。
    if not os.getenv("N8N_WEBHOOK_SECRET"):
        logger.warning("[n8n] N8N_WEBHOOK_SECRET not configured; rejecting webhook request")
        raise HTTPException(status_code=403, detail="webhook secret not configured")

    raw_body = await request.body()
    signature = request.headers.get("X-N8N-Signature") or request.headers.get("X-MAOP-Signature")

    try:
        payload = await request.json()
    except Exception as exc:
        # 批次3A: 脱敏——JSON 解析错误细节不暴露给客户端，仅日志记录。
        logger.warning("[n8n] Invalid JSON in webhook: %s", exc)
        raise HTTPException(status_code=400, detail="Invalid JSON body") from exc

    return handle_n8n_webhook(payload, raw_body=raw_body, signature=signature)


@router.get("/workflows")
@handle_api_errors("n8n list workflows")
async def list_workflows(request: Request) -> dict[str, Any]:
    """List all n8n workflows."""
    require_admin(request)
    if not has_feature(FeatureFlag.N8N_INTEGRATION):
        raise HTTPException(status_code=404, detail="n8n integration not available")

    with _get_client() as client:
        try:
            workflows = client.list_workflows()
            return {"status": "ok", "workflows": workflows, "count": len(workflows)}
        except N8nIntegrationError as exc:
            # 批次3A: 脱敏——集成错误细节不暴露给客户端，仅日志记录。
            logger.warning("[n8n] Integration error: %s", exc)
            raise HTTPException(status_code=502, detail="n8n integration error") from exc


@router.post("/workflows/{workflow_id}/trigger")
@handle_api_errors("n8n trigger workflow")
async def trigger_workflow(
    workflow_id: str,
    request: Request,
) -> dict[str, Any]:
    """Trigger an n8n workflow by ID.

    P0 fix: 移除函数签名中的 ``data`` 和 ``wait`` 参数——它们会被 FastAPI
    解释为请求体参数，与 ``await request.json()`` 再次读取请求体冲突。
    请求体统一通过 ``await request.json()`` 解析。
    """
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
            return {"status": "ok", **execution.model_dump()}
        except N8nIntegrationError as exc:
            # 批次3A: 脱敏——集成错误细节不暴露给客户端，仅日志记录。
            logger.warning("[n8n] Integration error: %s", exc)
            raise HTTPException(status_code=502, detail="n8n integration error") from exc


@router.get("/executions/{execution_id}")
@handle_api_errors("n8n get execution")
async def get_execution(execution_id: str, request: Request) -> dict[str, Any]:
    """Get the status of an n8n workflow execution."""
    require_admin(request)
    if not has_feature(FeatureFlag.N8N_INTEGRATION):
        raise HTTPException(status_code=404, detail="n8n integration not available")

    with _get_client() as client:
        try:
            execution = client.get_execution(execution_id)
            return {"status": "ok", **execution.model_dump()}
        except N8nIntegrationError as exc:
            # 批次3A: 脱敏——集成错误细节不暴露给客户端，仅日志记录。
            logger.warning("[n8n] Integration error: %s", exc)
            raise HTTPException(status_code=502, detail="n8n integration error") from exc


@router.get("/health")
@handle_api_errors("n8n health check")
async def health_check(request: Request) -> dict[str, Any]:
    """Check if n8n is reachable."""
    require_admin(request)
    if not has_feature(FeatureFlag.N8N_INTEGRATION):
        raise HTTPException(status_code=404, detail="n8n integration not available")

    # 从环境变量读取 base_url，避免访问 client 的内部属性 _base_url
    base_url = os.getenv("N8N_BASE_URL", "http://localhost:5678")
    with _get_client() as client:
        healthy = client.health_check()
        return {"status": "ok", "n8n_reachable": healthy, "base_url": base_url}
