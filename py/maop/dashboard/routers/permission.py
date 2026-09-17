"""MAOP Dashboard — Permission & Approval API routes.

Permission rule management and human-proxy approval operations live in
``maop.dashboard.services.rbac_service``.  This router only does request
parsing, permission checks, service calls, response formatting, and
error handling.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from maop.core.security.middleware import require_admin
from maop.dashboard.error_handler import handle_api_errors
from maop.dashboard.routers.state import MAOP_ROOT

# ── Service layer ──────────────────────────────────────────────────
from maop.dashboard.services import rbac_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["permission"])


class RuleCreate(BaseModel):
    agent: str = "*"
    action: str = "*"
    decision: str = "ask"
    reason: str = ""
    priority: int = 0


@router.post("/permission/rules")
@handle_api_errors
async def add_rule(body: RuleCreate, request: Request) -> dict[str, Any]:
    require_admin(request)
    rid = rbac_service.add_permission_rule(
        str(MAOP_ROOT),
        agent=body.agent,
        action=body.action,
        decision=body.decision,
        reason=body.reason,
        priority=body.priority,
    )
    return {"status": "ok", "rule_id": rid}


@router.delete("/permission/rules/{rule_id}")
@handle_api_errors
async def remove_rule(rule_id: str, request: Request) -> dict[str, Any]:
    require_admin(request)
    removed = rbac_service.remove_permission_rule(str(MAOP_ROOT), rule_id)
    if not removed:
        raise HTTPException(status_code=404, detail=f"Rule {rule_id} not found")
    return {"status": "ok", "rule_id": rule_id}


@router.get("/permission/rules")
@handle_api_errors
async def list_rules(request: Request, limit: int = Query(100, ge=1, le=500)) -> dict[str, Any]:
    require_admin(request)
    rules = rbac_service.list_permission_rules(str(MAOP_ROOT), limit=limit)
    return {"status": "ok", "rules": rules, "count": len(rules)}


@router.get("/permission/check")
@handle_api_errors
async def check_permission(request: Request, agent: str, action: str = "*") -> dict[str, Any]:
    require_admin(request)
    check = rbac_service.check_permission(str(MAOP_ROOT), agent=agent, action=action)
    return {"status": "ok", **check}


@router.get("/approval/pending")
@handle_api_errors
async def list_pending_approvals(request: Request, limit: int = Query(50, ge=1, le=200)) -> dict[str, Any]:
    require_admin(request)
    pending = rbac_service.list_pending_approvals(str(MAOP_ROOT), limit=limit)
    return {"status": "ok", "pending": pending, "count": len(pending)}


@router.post("/approval/{request_id}/approve")
@handle_api_errors
async def approve_request(request_id: str, request: Request) -> dict[str, Any]:
    require_admin(request)
    ok = rbac_service.approve_request(str(MAOP_ROOT), request_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Request {request_id} not found")
    return {"status": "ok", "request_id": request_id}


@router.post("/approval/{request_id}/reject")
@handle_api_errors
async def reject_request(request_id: str, request: Request, reason: str = "") -> dict[str, Any]:
    require_admin(request)
    ok = rbac_service.reject_request(str(MAOP_ROOT), request_id, reason=reason)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Request {request_id} not found")
    return {"status": "ok", "request_id": request_id}
