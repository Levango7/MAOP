"""MAOP Dashboard — Blackboard Architecture API.

Endpoints:
  GET  /api/blackboard/snapshot        — 获取黑板完整快照
  GET  /api/blackboard/domains/{domain} — 读取指定域内所有条目
  POST /api/blackboard/write           — 写入条目（admin）
  POST /api/blackboard/clear/{domain}  — 清除域（admin）
  GET  /api/blackboard/history         — 获取操作历史
  GET  /api/blackboard/domains         — 列出所有允许的域（白名单）
  GET  /api/blackboard/stats           — 黑板统计信息

读操作开放访问；写操作（write/clear）需 admin 鉴权。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from maop.core.reliability.blackboard import InvalidDomainError
from maop.core.security.middleware import require_admin
from maop.dashboard.error_handler import handle_api_errors
from maop.dashboard.services import blackboard_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/blackboard", tags=["blackboard"])


# ── Request models ───────────────────────────────────────────────


class WriteEntryRequest(BaseModel):
    """写入黑板条目请求。"""

    domain: str = Field(..., description="目标域（必须在白名单内）")
    content: Any = Field(..., description="知识内容")
    contributor: str = Field("", description="贡献者标识")
    confidence: float = Field(1.0, ge=0.0, le=1.0, description="置信度")
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="附加元数据"
    )


# ── Endpoints ────────────────────────────────────────────────────


@router.get("/snapshot")
@handle_api_errors("blackboard snapshot")
async def get_snapshot() -> dict[str, Any]:
    """获取黑板完整快照。

    返回 ``{domain: [entry_dict...]}`` 字典。
    """
    return {"status": "ok", "data": blackboard_service.get_snapshot()}


@router.get("/domains")
@handle_api_errors("blackboard domains")
async def list_domains() -> dict[str, Any]:
    """列出所有允许的域（白名单）与当前非空域。"""
    return {"status": "ok", "data": blackboard_service.list_domains()}


@router.get("/domains/{domain}")
@handle_api_errors("blackboard read domain")
async def read_domain(domain: str) -> dict[str, Any]:
    """读取指定域内所有条目。"""
    try:
        entries = blackboard_service.read_domain(domain)
    except InvalidDomainError as exc:
        # 批次3A: 脱敏——InvalidDomainError 细节不暴露给客户端，仅日志记录。
        logger.warning("[blackboard] Invalid domain: %s", exc)
        raise HTTPException(status_code=400, detail="Invalid domain") from exc
    return {
        "status": "ok",
        "data": entries,
        "count": len(entries),
    }


@router.post("/write")
@handle_api_errors("blackboard write")
async def write_entry(
    body: WriteEntryRequest, request: Request
) -> dict[str, Any]:
    """写入条目（admin 鉴权）。

    - R-8：域必须在白名单内，否则返回 400。
    - 若黑板已启用 EventBus，写入会通过 ``publish(Event)`` 广播。
    """
    require_admin(request)
    try:
        entry = await blackboard_service.write_entry(
            body.domain,
            body.content,
            body.contributor,
            confidence=body.confidence,
            metadata=body.metadata,
        )
    except InvalidDomainError as exc:
        # 批次3A: 脱敏——InvalidDomainError 细节不暴露给客户端，仅日志记录。
        logger.warning("[blackboard] Invalid domain: %s", exc)
        raise HTTPException(status_code=400, detail="Invalid domain") from exc
    return {"status": "ok", "data": entry}


@router.post("/clear/{domain}")
@handle_api_errors("blackboard clear")
async def clear_domain(domain: str, request: Request) -> dict[str, Any]:
    """清除指定域（admin 鉴权）。返回被清除的条目数。"""
    require_admin(request)
    try:
        cleared = await blackboard_service.clear_domain(domain)
    except InvalidDomainError as exc:
        # 批次3A: 脱敏——InvalidDomainError 细节不暴露给客户端，仅日志记录。
        logger.warning("[blackboard] Invalid domain: %s", exc)
        raise HTTPException(status_code=400, detail="Invalid domain") from exc
    return {"status": "ok", "domain": domain, "cleared": cleared}


@router.get("/history")
@handle_api_errors("blackboard history")
async def get_history(limit: int = Query(100, ge=1, le=1000)) -> dict[str, Any]:
    """获取操作历史（最近 ``limit`` 条）。"""
    return {"status": "ok", "data": blackboard_service.get_history(limit=limit)}


@router.get("/stats")
@handle_api_errors("blackboard stats")
async def blackboard_stats() -> dict[str, Any]:
    """黑板统计信息。"""
    return {"status": "ok", "data": blackboard_service.get_stats()}
