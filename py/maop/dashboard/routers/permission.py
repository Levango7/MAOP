"""MAOP Dashboard — Permission & Approval API routes."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel

from maop.dashboard.error_handler import handle_api_errors
from maop.dashboard.routers.state import MAOP_ROOT
from maop.core.middleware import require_admin

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
    from maop.core.permission import PermissionManager
    pm = PermissionManager(root_dir=str(MAOP_ROOT))
    rid = pm.add_rule(agent=body.agent, action=body.action, decision=body.decision,
                       reason=body.reason, priority=body.priority)
    return {"status": "ok", "rule_id": rid}


@router.delete("/permission/rules/{rule_id}")
@handle_api_errors
async def remove_rule(rule_id: str, request: Request) -> dict[str, Any]:
    require_admin(request)
    from maop.core.permission import PermissionManager
    pm = PermissionManager(root_dir=str(MAOP_ROOT))
    removed = pm.remove_rule(rule_id)
    return {"status": "ok" if removed else "not_found", "rule_id": rule_id}


@router.get("/permission/rules")
@handle_api_errors
async def list_rules(limit: int = Query(100, ge=1, le=500)) -> dict[str, Any]:
    from maop.core.permission import PermissionManager
    pm = PermissionManager(root_dir=str(MAOP_ROOT))
    rules = pm.list_rules(limit=limit)
    return {"rules": [r.model_dump() for r in rules], "count": len(rules)}


@router.get("/permission/check")
@handle_api_errors
async def check_permission(agent: str, action: str = "*") -> dict[str, Any]:
    from maop.core.permission import PermissionManager
    pm = PermissionManager(root_dir=str(MAOP_ROOT))
    check = pm.check(agent=agent, action=action)
    return check.model_dump()


@router.get("/approval/pending")
@handle_api_errors
async def list_pending_approvals(limit: int = Query(50, ge=1, le=200)) -> dict[str, Any]:
    from maop.core.human_proxy import HumanProxy
    hp = HumanProxy(root_dir=str(MAOP_ROOT))
    pending = hp.pending(limit=limit)
    return {"pending": [p.model_dump() for p in pending], "count": len(pending)}


@router.post("/approval/{request_id}/approve")
@handle_api_errors
async def approve_request(request_id: str, request: Request) -> dict[str, Any]:
    require_admin(request)
    from maop.core.human_proxy import HumanProxy
    hp = HumanProxy(root_dir=str(MAOP_ROOT))
    ok = hp.approve(request_id)
    return {"status": "ok" if ok else "not_found", "request_id": request_id}


@router.post("/approval/{request_id}/reject")
@handle_api_errors
async def reject_request(request_id: str, request: Request, reason: str = "") -> dict[str, Any]:
    require_admin(request)
    from maop.core.human_proxy import HumanProxy
    hp = HumanProxy(root_dir=str(MAOP_ROOT))
    ok = hp.reject(request_id, reason=reason)
    return {"status": "ok" if ok else "not_found", "request_id": request_id}
