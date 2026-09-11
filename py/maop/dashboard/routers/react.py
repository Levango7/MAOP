"""MAOP Dashboard — ReAct Loop & Change Tracker API endpoints."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from maop.core.security.middleware import require_admin
from maop.dashboard.error_handler import handle_api_errors

from .state import MAOP_ROOT

router = APIRouter(prefix="/api/react", tags=["react"])


# ── Pydantic 请求模型 (P2-#4 fix: 输入校验) ────────────────────────

class CreateSnapshotRequest(BaseModel):
    workdir: str = ""
    label: str = ""


class SaveArtifactRequest(BaseModel):
    name: str = ""
    content: str = ""
    tag: str = ""
    metadata: dict[str, Any] | None = None


class RestoreArtifactRequest(BaseModel):
    version: int = 1


def _get_change_tracker():
    from maop.core.reliability.change_tracker import ChangeTracker
    return ChangeTracker(root_dir=str(MAOP_ROOT))


def _get_artifact_store():
    from maop.core.backends.artifact_store import ArtifactStore
    return ArtifactStore(root_dir=str(MAOP_ROOT))


@router.get("/snapshots")
@handle_api_errors
async def list_snapshots(
    request: Request,
    workdir: str = Query("", description="Filter by workdir"),
    limit: int = Query(20, ge=1, le=100),
) -> dict[str, Any]:
    require_admin(request)
    tracker = _get_change_tracker()
    snapshots = tracker.list_snapshots(workdir=workdir, limit=limit)
    return {"status": "ok", "snapshots": [s.model_dump() for s in snapshots]}


@router.post("/snapshots")
@handle_api_errors
async def create_snapshot(body: CreateSnapshotRequest, request: Request) -> dict[str, Any]:
    require_admin(request)
    tracker = _get_change_tracker()
    snap_id = tracker.snapshot(
        workdir=body.workdir,
        label=body.label,
    )
    snap = tracker.get_snapshot(snap_id)
    return {"status": "ok", "snapshot": snap.model_dump() if snap else None}


@router.get("/diff")
@handle_api_errors
async def diff_snapshots(
    request: Request,
    workdir: str = Query(..., description="Working directory"),
    since_label: str = Query("", description="Compare since this label"),
) -> dict[str, Any]:
    require_admin(request)
    tracker = _get_change_tracker()
    result = tracker.diff(workdir, since_label=since_label)
    return {"status": "ok", "diff": result.model_dump()}


@router.get("/changes")
@handle_api_errors
async def get_change_log(
    request: Request,
    workdir: str = Query(..., description="Working directory"),
    limit: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    require_admin(request)
    tracker = _get_change_tracker()
    changes = tracker.get_change_log(workdir, limit=limit)
    return {"status": "ok", "changes": changes}


@router.delete("/snapshots/{snapshot_id}")
@handle_api_errors
async def delete_snapshot(snapshot_id: str, request: Request) -> dict[str, Any]:
    require_admin(request)
    tracker = _get_change_tracker()
    ok = tracker.delete_snapshot(snapshot_id)
    return {"status": "ok", "deleted": ok}


@router.get("/artifacts")
@handle_api_errors
async def list_artifacts(request: Request, limit: int = Query(50, ge=1, le=200)) -> dict[str, Any]:
    require_admin(request)
    store = _get_artifact_store()
    artifacts = store.list_artifacts(limit=limit)
    return {"status": "ok", "artifacts": [a.model_dump() for a in artifacts]}


@router.post("/artifacts")
@handle_api_errors
async def save_artifact(body: SaveArtifactRequest, request: Request) -> dict[str, Any]:
    require_admin(request)
    store = _get_artifact_store()
    version = store.save(
        name=body.name,
        content=body.content,
        tag=body.tag,
        metadata=body.metadata,
    )
    return {"status": "ok", "version": version}


@router.get("/artifacts/{name}")
@handle_api_errors
async def load_artifact(request: Request, name: str, version: int | None = Query(None)) -> dict[str, Any]:
    require_admin(request)
    store = _get_artifact_store()
    content = store.load(name, version=version)
    if content is None:
        raise HTTPException(status_code=404, detail="Artifact not found")
    return {"status": "ok", "name": name, "content": content}


@router.get("/artifacts/{name}/history")
@handle_api_errors
async def artifact_history(request: Request, name: str, limit: int = Query(20, ge=1, le=1000)) -> dict[str, Any]:
    require_admin(request)
    store = _get_artifact_store()
    history = store.history(name, limit=limit)
    return {"status": "ok", "history": [h.model_dump() for h in history]}


@router.post("/artifacts/{name}/restore")
@handle_api_errors
async def restore_artifact(name: str, body: RestoreArtifactRequest, request: Request) -> dict[str, Any]:
    require_admin(request)
    store = _get_artifact_store()
    ok = store.restore(name, version=body.version)
    return {"status": "ok", "restored": ok}


@router.delete("/artifacts/{name}")
@handle_api_errors
async def delete_artifact(name: str, request: Request) -> dict[str, Any]:
    require_admin(request)
    store = _get_artifact_store()
    ok = store.delete_artifact(name)
    return {"status": "ok", "deleted": ok}
