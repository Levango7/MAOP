"""MAOP Dashboard — Tool Audit Log API endpoints."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from maop.core.security.middleware import require_admin
from maop.dashboard.error_handler import handle_api_errors

from .state import MAOP_ROOT

logger = logging.getLogger(__name__)

router = APIRouter()


# ── Pydantic 请求模型 ──────────────────────────────────────────────
class ToolAuditCleanupRequest(BaseModel):
    """清理工具审计日志的请求体。"""
    max_age_days: int = Field(default=90, ge=1, le=3650)

_tool_audit = None

def _get_tool_audit() -> Any:
    global _tool_audit
    if _tool_audit is None:
        from maop.core.agent.tools.tool_audit import ToolAuditLog
        _tool_audit = ToolAuditLog(root_dir=str(MAOP_ROOT))
    return _tool_audit


@router.get("/api/tool-audit/entries")
@handle_api_errors("Tool audit entries", error_value={"entries": [], "count": 0, "error": "Query failed"})
async def api_tool_audit_entries(
    request: Request,
    tool_name: str = "",
    agent: str = "",
    success: bool | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    require_admin(request)
    audit = _get_tool_audit()
    entries = audit.query(tool_name=tool_name, agent=agent, success=success, limit=limit)
    return {"entries": [e.model_dump() for e in entries], "count": len(entries)}


@router.get("/api/tool-audit/stats")
@handle_api_errors("Tool audit stats", error_value={"status": "error", "error": "Stats failed"})
async def api_tool_audit_stats(request: Request) -> dict[str, Any]:
    require_admin(request)
    audit = _get_tool_audit()
    stats = audit.stats()
    return {"status": "ok", "stats": stats.model_dump()}


@router.post("/api/tool-audit/cleanup")
@handle_api_errors("Tool audit cleanup", error_value={"status": "error", "error": "Cleanup failed"})
async def api_tool_audit_cleanup(body: ToolAuditCleanupRequest, request: Request) -> dict[str, Any]:
    require_admin(request)
    max_age_days = body.max_age_days
    audit = _get_tool_audit()
    removed = audit.cleanup(max_age_days=max_age_days)
    return {"status": "ok", "removed": removed}
