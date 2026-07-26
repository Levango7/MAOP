"""MAOP Dashboard — Worktree management API endpoints."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from maop.core.middleware import require_admin

from .error_handler import handle_api_errors
from .state import MAOP_ROOT

logger = logging.getLogger(__name__)

router = APIRouter()

_worktree_mgr = None

def _get_worktree_mgr() -> Any:
    global _worktree_mgr
    if _worktree_mgr is None:
        from maop.core.worktree import WorktreeManager
        _worktree_mgr = WorktreeManager(root_dir=str(MAOP_ROOT))
    return _worktree_mgr


@router.post("/api/worktree/create-root")
@handle_api_errors("Worktree create-root", error_value={"status": "error", "error": "Create failed"})
async def api_worktree_create_root(request: Request) -> dict[str, Any]:
    require_admin(request)
    body = await request.json()
    task = body.get("task", "")
    description = body.get("description", "")
    if not task:
        raise HTTPException(400, "missing task")
    mgr = _get_worktree_mgr()
    node_id = mgr.create_root(task=task, description=description)
    return {"status": "ok", "node_id": node_id}


@router.post("/api/worktree/branch")
@handle_api_errors("Worktree branch", error_value={"status": "error", "error": "Branch failed"})
async def api_worktree_branch(request: Request) -> dict[str, Any]:
    require_admin(request)
    body = await request.json()
    parent_id = body.get("parent_id", "")
    name = body.get("name", "")
    description = body.get("description", "")
    metadata = body.get("metadata")
    if not parent_id or not name:
        raise HTTPException(400, "missing parent_id or name")
    mgr = _get_worktree_mgr()
    try:
        node_id = mgr.branch(parent_id=parent_id, name=name, description=description, metadata=metadata)
        return {"status": "ok", "node_id": node_id}
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.post("/api/worktree/abandon")
@handle_api_errors("Worktree abandon", error_value={"status": "error", "error": "Abandon failed"})
async def api_worktree_abandon(request: Request) -> dict[str, Any]:
    require_admin(request)
    body = await request.json()
    node_id = body.get("id", "")
    if not node_id:
        raise HTTPException(400, "missing id")
    mgr = _get_worktree_mgr()
    ok = mgr.abandon(node_id)
    return {"status": "ok" if ok else "not_found", "id": node_id}


@router.get("/api/worktree/get")
@handle_api_errors("Worktree get", error_value={"status": "error", "error": "Get failed"})
async def api_worktree_get(node_id: str = "") -> dict[str, Any]:
    if not node_id:
        raise HTTPException(400, "missing node_id")
    mgr = _get_worktree_mgr()
    info = mgr.get_branch(node_id)
    if info is None:
        raise HTTPException(404, f"Node {node_id} not found")
    return {"status": "ok", "branch": info.model_dump()}


@router.get("/api/worktree/list")
@handle_api_errors("Worktree list", error_value={"branches": [], "count": 0, "error": "List failed"})
async def api_worktree_list(root_id: str = "", active_only: bool = False) -> dict[str, Any]:
    mgr = _get_worktree_mgr()
    branches = mgr.list_branches(root_id=root_id, active_only=active_only)
    return {"branches": [b.model_dump() for b in branches], "count": len(branches)}


@router.post("/api/worktree/merge")
@handle_api_errors("Worktree merge", error_value={"status": "error", "error": "Merge failed"})
async def api_worktree_merge(request: Request) -> dict[str, Any]:
    require_admin(request)
    body = await request.json()
    source = body.get("source_branch", "")
    target = body.get("target_branch", "")
    if not source:
        raise HTTPException(400, "missing source_branch")
    mgr = _get_worktree_mgr()
    result = mgr.merge(source_branch=source, target_branch=target)
    return {"status": "ok", "merge": result.model_dump()}


@router.post("/api/worktree/checkpoint")
@handle_api_errors("Worktree checkpoint", error_value={"status": "error", "error": "Checkpoint failed"})
async def api_worktree_checkpoint(request: Request) -> dict[str, Any]:
    require_admin(request)
    body = await request.json()
    node_id = body.get("node_id", "")
    label = body.get("label", "")
    if not node_id:
        raise HTTPException(400, "missing node_id")
    mgr = _get_worktree_mgr()
    try:
        cp_id = mgr.checkpoint(node_id, label=label)
        return {"status": "ok", "checkpoint_id": cp_id}
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.post("/api/worktree/rollback")
@handle_api_errors("Worktree rollback", error_value={"status": "error", "error": "Rollback failed"})
async def api_worktree_rollback(request: Request) -> dict[str, Any]:
    require_admin(request)
    body = await request.json()
    node_id = body.get("node_id", "")
    checkpoint_id = body.get("checkpoint_id", "")
    if not node_id or not checkpoint_id:
        raise HTTPException(400, "missing node_id or checkpoint_id")
    mgr = _get_worktree_mgr()
    ok = mgr.rollback(node_id, to_checkpoint=checkpoint_id)
    return {"status": "ok" if ok else "failed"}
