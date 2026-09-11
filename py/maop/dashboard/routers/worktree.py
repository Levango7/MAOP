"""MAOP Dashboard — Worktree management API endpoints."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from maop.core.security.middleware import require_admin
from maop.dashboard.error_handler import handle_api_errors

from .state import MAOP_ROOT

logger = logging.getLogger(__name__)

router = APIRouter()


# ── Pydantic 请求模型 ──────────────────────────────────────────────
class WorktreeCreateRootRequest(BaseModel):
    """创建 worktree root 的请求体。"""
    task: str = Field(default="", max_length=10000)
    description: str = Field(default="", max_length=10000)


class WorktreeBranchRequest(BaseModel):
    """创建分支的请求体。"""
    parent_id: str = Field(default="", max_length=128)
    name: str = Field(default="", max_length=256)
    description: str = Field(default="", max_length=10000)
    metadata: dict[str, Any] | None = None


class WorktreeAbandonRequest(BaseModel):
    """放弃节点的请求体。"""
    id: str = Field(default="", max_length=128)


class WorktreeMergeRequest(BaseModel):
    """合并分支的请求体。"""
    source_branch: str = Field(default="", max_length=256)
    target_branch: str = Field(default="", max_length=256)


class WorktreeCheckpointRequest(BaseModel):
    """创建检查点的请求体。"""
    node_id: str = Field(default="", max_length=128)
    label: str = Field(default="", max_length=256)


class WorktreeRollbackRequest(BaseModel):
    """回滚的请求体。"""
    node_id: str = Field(default="", max_length=128)
    checkpoint_id: str = Field(default="", max_length=128)

_worktree_mgr = None

def _get_worktree_mgr() -> Any:
    global _worktree_mgr
    if _worktree_mgr is None:
        from maop.core.agent.memory_ctx.worktree import WorktreeManager
        _worktree_mgr = WorktreeManager(root_dir=str(MAOP_ROOT))
    return _worktree_mgr


@router.post("/api/worktree/create-root")
@handle_api_errors("Worktree create-root", error_value={"status": "error", "error": "Create failed"})
async def api_worktree_create_root(body: WorktreeCreateRootRequest, request: Request) -> dict[str, Any]:
    require_admin(request)
    task = body.task
    description = body.description
    if not task:
        raise HTTPException(400, "missing task")
    mgr = _get_worktree_mgr()
    node_id = mgr.create_root(task=task, description=description)
    return {"status": "ok", "node_id": node_id}


@router.post("/api/worktree/branch")
@handle_api_errors("Worktree branch", error_value={"status": "error", "error": "Branch failed"})
async def api_worktree_branch(body: WorktreeBranchRequest, request: Request) -> dict[str, Any]:
    require_admin(request)
    parent_id = body.parent_id
    name = body.name
    description = body.description
    metadata = body.metadata
    if not parent_id or not name:
        raise HTTPException(400, "missing parent_id or name")
    mgr = _get_worktree_mgr()
    try:
        node_id = mgr.branch(parent_id=parent_id, name=name, description=description, metadata=metadata)
        return {"status": "ok", "node_id": node_id}
    except ValueError as exc:
        # 批次3A: 脱敏——ValueError 细节不暴露给客户端，仅日志记录。
        logger.warning("[worktree] Branch failed: %s", exc)
        raise HTTPException(400, "Worktree branch failed") from exc


@router.post("/api/worktree/abandon")
@handle_api_errors("Worktree abandon", error_value={"status": "error", "error": "Abandon failed"})
async def api_worktree_abandon(body: WorktreeAbandonRequest, request: Request) -> dict[str, Any]:
    require_admin(request)
    node_id = body.id
    if not node_id:
        raise HTTPException(400, "missing id")
    mgr = _get_worktree_mgr()
    ok = mgr.abandon(node_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Node {node_id} not found")
    return {"status": "ok", "id": node_id}


@router.get("/api/worktree/get")
@handle_api_errors("Worktree get", error_value={"status": "error", "error": "Get failed"})
async def api_worktree_get(request: Request, node_id: str = "") -> dict[str, Any]:
    require_admin(request)
    if not node_id:
        raise HTTPException(400, "missing node_id")
    mgr = _get_worktree_mgr()
    info = mgr.get_branch(node_id)
    if info is None:
        raise HTTPException(404, f"Node {node_id} not found")
    return {"status": "ok", "branch": info.model_dump()}


@router.get("/api/worktree/list")
@handle_api_errors("Worktree list", error_value={"branches": [], "count": 0, "error": "List failed"})
async def api_worktree_list(request: Request, root_id: str = "", active_only: bool = False) -> dict[str, Any]:
    require_admin(request)
    mgr = _get_worktree_mgr()
    branches = mgr.list_branches(root_id=root_id, active_only=active_only)
    return {"status": "ok", "branches": [b.model_dump() for b in branches], "count": len(branches)}


@router.post("/api/worktree/merge")
@handle_api_errors("Worktree merge", error_value={"status": "error", "error": "Merge failed"})
async def api_worktree_merge(body: WorktreeMergeRequest, request: Request) -> dict[str, Any]:
    require_admin(request)
    source = body.source_branch
    target = body.target_branch
    if not source:
        raise HTTPException(400, "missing source_branch")
    mgr = _get_worktree_mgr()
    result = mgr.merge(source_branch=source, target_branch=target)
    return {"status": "ok", "merge": result.model_dump()}


@router.post("/api/worktree/checkpoint")
@handle_api_errors("Worktree checkpoint", error_value={"status": "error", "error": "Checkpoint failed"})
async def api_worktree_checkpoint(body: WorktreeCheckpointRequest, request: Request) -> dict[str, Any]:
    require_admin(request)
    node_id = body.node_id
    label = body.label
    if not node_id:
        raise HTTPException(400, "missing node_id")
    mgr = _get_worktree_mgr()
    try:
        cp_id = mgr.checkpoint(node_id, label=label)
        return {"status": "ok", "checkpoint_id": cp_id}
    except ValueError as exc:
        # 批次3A: 脱敏——ValueError 细节不暴露给客户端，仅日志记录。
        logger.warning("[worktree] Checkpoint failed: %s", exc)
        raise HTTPException(400, "Worktree checkpoint failed") from exc


@router.post("/api/worktree/rollback")
@handle_api_errors("Worktree rollback", error_value={"status": "error", "error": "Rollback failed"})
async def api_worktree_rollback(body: WorktreeRollbackRequest, request: Request) -> dict[str, Any]:
    require_admin(request)
    node_id = body.node_id
    checkpoint_id = body.checkpoint_id
    if not node_id or not checkpoint_id:
        raise HTTPException(400, "missing node_id or checkpoint_id")
    mgr = _get_worktree_mgr()
    ok = mgr.rollback(node_id, to_checkpoint=checkpoint_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Rollback target not found")
    return {"status": "ok"}
