"""Run sharing endpoints.

Permission model, deliberately asymmetric:

* ``POST /api/share`` / ``DELETE /api/share/{token}`` / ``GET /api/share``
  require admin — creating or revoking a public link is an admin action.
* ``GET /api/share/{token}`` requires **no** authentication: possession of the
  token is the authorization. That is the whole point of a share link, and it
  is also why the shared payload is a summary with no event payloads.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from maop.core.security.middleware import require_admin
from maop.dashboard.error_handler import handle_api_errors
from maop.dashboard.routers.share_schemas import ShareCreateEnvelope, ShareEnvelope
from maop.dashboard.services import share_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/share", tags=["share"])


class CreateShareRequest(BaseModel):
    """POST /api/share 请求体。"""

    trace_id: str = Field(..., min_length=1, max_length=128, description="要分享的运行 trace_id")
    expires_in_hours: int = Field(
        default=168, ge=0, le=8760,
        description="有效小时数；0 表示永不过期（上限一年，防止忘掉的链接永久存活）",
    )


@router.post("", response_model=ShareCreateEnvelope)
@handle_api_errors(
    "Create share",
    error_value={"status": "error", "error": "Create failed"},
)
async def create_share(body: CreateShareRequest, request: Request) -> dict[str, Any]:
    """为一次运行创建只读分享链接（admin）。"""
    require_admin(request)
    try:
        result = share_service.create_share(
            body.trace_id,
            expires_in_hours=body.expires_in_hours,
            created_by="api",
        )
    except KeyError:
        raise HTTPException(404, f"Run not found: {body.trace_id}")
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"status": "ok", **result}


@router.get("")
@handle_api_errors(
    "List shares",
    error_value={"status": "error", "shares": [], "count": 0, "error": "List failed"},
)
async def list_shares(request: Request) -> dict[str, Any]:
    """列出仍有效的分享链接（admin）。"""
    require_admin(request)
    shares = share_service.list_shares()
    return {"status": "ok", "shares": shares, "count": len(shares)}


@router.delete("/{token}")
@handle_api_errors(
    "Revoke share",
    error_value={"status": "error", "error": "Revoke failed"},
)
async def revoke_share(token: str, request: Request) -> dict[str, Any]:
    """撤销分享链接（admin）。已过期的 token 也会返回 404。"""
    require_admin(request)
    if not share_service.revoke_share(token):
        raise HTTPException(404, "share not found")
    return {"status": "ok", "revoked": token}


@router.get("/{token}", response_model=ShareEnvelope)
@handle_api_errors(
    "Get share",
    error_value={"status": "error", "share": None, "error": "Not found"},
)
async def get_share(token: str) -> dict[str, Any]:
    """读取分享内容 —— **无需认证**，token 本身即凭证。

    过期与不存在的 token 一律返回 404，不区分两者：区分会让攻击者能探测
    某个 token 是否曾经存在。
    """
    share = share_service.get_share(token)
    if share is None:
        raise HTTPException(404, "share not found")
    return {"status": "ok", "share": share}
