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

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from maop.core.reliability.blackboard import (
    BlackboardDomain,
    InvalidDomainError,
    get_blackboard,
)
from maop.core.security.middleware import require_admin
from maop.dashboard.error_handler import handle_api_errors

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
    bb = get_blackboard()
    return {"status": "ok", "data": bb.get_snapshot()}


@router.get("/domains")
@handle_api_errors("blackboard domains")
async def list_domains() -> dict[str, Any]:
    """列出所有允许的域（白名单）与当前非空域。"""
    bb = get_blackboard()
    return {
        "status": "ok",
        "data": {
            "allowed": [d.value for d in BlackboardDomain],
            "active": bb.get_domains(),
        },
    }


@router.get("/domains/{domain}")
@handle_api_errors("blackboard read domain")
async def read_domain(domain: str) -> dict[str, Any]:
    """读取指定域内所有条目。"""
    bb = get_blackboard()
    try:
        entries = bb.read(domain)
    except InvalidDomainError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "status": "ok",
        "data": [e.to_dict() for e in entries],
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
    bb = get_blackboard()
    try:
        entry = await bb.write(
            body.domain,
            body.content,
            body.contributor,
            confidence=body.confidence,
            metadata=body.metadata,
        )
    except InvalidDomainError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "ok", "data": entry.to_dict()}


@router.post("/clear/{domain}")
@handle_api_errors("blackboard clear")
async def clear_domain(domain: str, request: Request) -> dict[str, Any]:
    """清除指定域（admin 鉴权）。返回被清除的条目数。"""
    require_admin(request)
    bb = get_blackboard()
    try:
        cleared = await bb.clear(domain)
    except InvalidDomainError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "ok", "domain": domain, "cleared": cleared}


@router.get("/history")
@handle_api_errors("blackboard history")
async def get_history(limit: int = 100) -> dict[str, Any]:
    """获取操作历史（最近 ``limit`` 条）。"""
    bb = get_blackboard()
    return {"status": "ok", "data": bb.get_history(limit=limit)}


@router.get("/stats")
@handle_api_errors("blackboard stats")
async def blackboard_stats() -> dict[str, Any]:
    """黑板统计信息。"""
    bb = get_blackboard()
    return {
        "status": "ok",
        "data": {
            "total_entries": bb.total_entries(),
            "active_domains": bb.get_domains(),
            "event_bus_enabled": bb.event_bus_enabled,
            "allowed_domains": [d.value for d in BlackboardDomain],
        },
    }