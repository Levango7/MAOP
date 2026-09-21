"""Dashboard API routes for n8n integration (Enterprise only).

业务逻辑已提取至 ``maop.dashboard.services.integration_service``（§1 n8n）。
本 router 仅保留：路由定义 / 请求参数解析 / 权限检查 /
service 调用 / 响应格式化 / 错误处理。

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
from pydantic import BaseModel, ConfigDict, Field  # noqa: F401

from maop.config.edition import FeatureFlag, has_feature
from maop.core.security.middleware import require_admin
from maop.dashboard.error_handler import handle_api_errors
from maop.dashboard.services import integration_service
from maop.enterprise.n8n import N8nIntegrationError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/n8n", tags=["n8n"])


class TriggerWorkflowRequest(BaseModel):
    """POST /api/n8n/workflows/{id}/trigger 请求体。"""
    data: dict[str, Any] = {}
    wait: bool = False


class N8nWebhookPayload(BaseModel):
    """POST /api/n8n/webhook 请求体。

    n8n webhook 载荷格式高度灵活——具体字段由工作流节点配置决定，
    不同工作流可能发送完全不同的 JSON 结构。此处仅声明 n8n 官方文档
    中常见的元数据字段作为已知字段，其余字段通过 ``extra="allow"``
    全部保留在 model 中，避免对未知字段返回 422 而拒绝合法 webhook。

    已知字段（n8n 常见 webhook 元数据）：
      * executionId   — 触发该 webhook 的执行 ID
      * workflowId    — 工作流 ID
      * workflowName  — 工作流名称
      * node          — 触发节点名称
      * event         — 事件类型
    """
    model_config = ConfigDict(extra="allow")

    executionId: str | None = None
    workflowId: str | None = None
    workflowName: str | None = None
    node: str | None = None
    event: str | None = None
    data: Any = None


# ── 单例转发（供测试注入，转发到 service 层）──────────────────────
def _get_client() -> Any:
    return integration_service._get_client()


def _set_client(client: Any) -> None:
    integration_service._set_client(client)


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

    # P2-1 fix: 用 Pydantic N8nWebhookPayload 替代裸 await request.json()，
    # 由 Pydantic 校验 JSON 结构（未知字段通过 extra="allow" 保留）。
    # 此处不能将 model 声明为函数参数——签名校验需要先读取 raw_body，
    # 而 FastAPI 解析 body 参数会消费 request stream。因此手动从已缓存
    # 的 raw_body 解析，既保留签名校验又获得 Pydantic 校验。
    try:
        payload = N8nWebhookPayload.model_validate_json(raw_body)
    except Exception as exc:
        # 批次3A: 脱敏——JSON 解析错误细节不暴露给客户端，仅日志记录。
        logger.warning("[n8n] Invalid JSON in webhook: %s", exc)
        raise HTTPException(status_code=400, detail="Invalid JSON body") from exc

    return integration_service.receive_webhook(
        payload.model_dump(), raw_body=raw_body, signature=signature,
    )


@router.get("/workflows")
@handle_api_errors("n8n list workflows")
async def list_workflows(request: Request) -> dict[str, Any]:
    """List all n8n workflows."""
    require_admin(request)
    if not has_feature(FeatureFlag.N8N_INTEGRATION):
        raise HTTPException(status_code=404, detail="n8n integration not available")

    try:
        result = integration_service.list_workflows()
    except N8nIntegrationError as exc:
        # 批次3A: 脱敏——集成错误细节不暴露给客户端，仅日志记录。
        logger.warning("[n8n] Integration error: %s", exc)
        raise HTTPException(status_code=502, detail="n8n integration error") from exc
    return {"status": "ok", **result}


@router.post("/workflows/{workflow_id}/trigger")
@handle_api_errors("n8n trigger workflow")
async def trigger_workflow(
    workflow_id: str,
    request: Request,
    body: TriggerWorkflowRequest | None = None,
) -> dict[str, Any]:
    """Trigger an n8n workflow by ID.

    M3 fix: 用 Pydantic TriggerWorkflowRequest 替代 await request.json()，
    由 FastAPI 自动校验请求体（data/wait 字段类型）。
    """
    require_admin(request)
    if not has_feature(FeatureFlag.N8N_INTEGRATION):
        raise HTTPException(status_code=404, detail="n8n integration not available")

    trigger_data = body.data if body else {}
    wait_for_completion = body.wait if body else False

    try:
        execution = integration_service.trigger_workflow(
            workflow_id,
            data=trigger_data,
            wait_for_completion=wait_for_completion,
        )
    except N8nIntegrationError as exc:
        # 批次3A: 脱敏——集成错误细节不暴露给客户端，仅日志记录。
        logger.warning("[n8n] Integration error: %s", exc)
        raise HTTPException(status_code=502, detail="n8n integration error") from exc
    return {"status": "ok", **execution}


@router.get("/executions/{execution_id}")
@handle_api_errors("n8n get execution")
async def get_execution(execution_id: str, request: Request) -> dict[str, Any]:
    """Get the status of an n8n workflow execution."""
    require_admin(request)
    if not has_feature(FeatureFlag.N8N_INTEGRATION):
        raise HTTPException(status_code=404, detail="n8n integration not available")

    try:
        execution = integration_service.get_execution(execution_id)
    except N8nIntegrationError as exc:
        # 批次3A: 脱敏——集成错误细节不暴露给客户端，仅日志记录。
        logger.warning("[n8n] Integration error: %s", exc)
        raise HTTPException(status_code=502, detail="n8n integration error") from exc
    return {"status": "ok", **execution}


@router.get("/health")
@handle_api_errors("n8n health check")
async def health_check(request: Request) -> dict[str, Any]:
    """Check if n8n is reachable."""
    require_admin(request)
    if not has_feature(FeatureFlag.N8N_INTEGRATION):
        raise HTTPException(status_code=404, detail="n8n integration not available")

    result = integration_service.health_check()
    return {"status": "ok", **result}
