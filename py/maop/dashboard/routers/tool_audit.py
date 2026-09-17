"""MAOP Dashboard — Tool Audit Log API endpoints.

Business logic lives in :mod:`maop.dashboard.services.observability_service`;
this router only does request parsing, auth, service dispatch, and
response formatting.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field

from maop.core.security.middleware import require_admin
from maop.dashboard.error_handler import handle_api_errors
from maop.dashboard.services import observability_service

router = APIRouter()


# ── Pydantic 请求模型 ──────────────────────────────────────────────
class ToolAuditCleanupRequest(BaseModel):
    """清理工具审计日志的请求体。"""
    max_age_days: int = Field(default=90, ge=1, le=3650)


@router.get("/api/tool-audit/entries")
@handle_api_errors("Tool audit entries", error_value={"entries": [], "count": 0, "error": "Query failed"})
async def api_tool_audit_entries(
    request: Request,
    tool_name: str = "",
    agent: str = "",
    success: bool | None = None,
    limit: int = Query(50, ge=1, le=1000),
) -> dict[str, Any]:
    require_admin(request)
    return await observability_service.get_tool_audit_entries(
        tool_name=tool_name, agent=agent, success=success, limit=limit,
    )


@router.get("/api/tool-audit/stats")
@handle_api_errors("Tool audit stats", error_value={"status": "error", "error": "Stats failed"})
async def api_tool_audit_stats(request: Request) -> dict[str, Any]:
    require_admin(request)
    return await observability_service.get_tool_audit_stats()


@router.post("/api/tool-audit/cleanup")
@handle_api_errors("Tool audit cleanup", error_value={"status": "error", "error": "Cleanup failed"})
async def api_tool_audit_cleanup(body: ToolAuditCleanupRequest, request: Request) -> dict[str, Any]:
    require_admin(request)
    return await observability_service.cleanup_tool_audit(max_age_days=body.max_age_days)
