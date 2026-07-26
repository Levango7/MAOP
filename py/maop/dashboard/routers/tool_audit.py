"""MAOP Dashboard — Tool Audit Log API endpoints."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Request

from maop.core.middleware import require_admin

from .error_handler import handle_api_errors
from .state import MAOP_ROOT

logger = logging.getLogger(__name__)

router = APIRouter()

_tool_audit = None

def _get_tool_audit() -> Any:
    global _tool_audit
    if _tool_audit is None:
        from maop.core.tool_audit import ToolAuditLog
        _tool_audit = ToolAuditLog(root_dir=str(MAOP_ROOT))
    return _tool_audit


@router.get("/api/tool-audit/entries")
@handle_api_errors("Tool audit entries", error_value={"entries": [], "count": 0, "error": "Query failed"})
async def api_tool_audit_entries(
    tool_name: str = "",
    agent: str = "",
    success: bool | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    audit = _get_tool_audit()
    entries = audit.query(tool_name=tool_name, agent=agent, success=success, limit=limit)
    return {"entries": [e.model_dump() for e in entries], "count": len(entries)}


@router.get("/api/tool-audit/stats")
@handle_api_errors("Tool audit stats", error_value={"status": "error", "error": "Stats failed"})
async def api_tool_audit_stats() -> dict[str, Any]:
    audit = _get_tool_audit()
    stats = audit.stats()
    return {"status": "ok", "stats": stats.model_dump()}


@router.post("/api/tool-audit/cleanup")
@handle_api_errors("Tool audit cleanup", error_value={"status": "error", "error": "Cleanup failed"})
async def api_tool_audit_cleanup(request: Request) -> dict[str, Any]:
    require_admin(request)
    body = await request.json()
    max_age_days = body.get("max_age_days", 90)
    audit = _get_tool_audit()
    removed = audit.cleanup(max_age_days=max_age_days)
    return {"status": "ok", "removed": removed}
