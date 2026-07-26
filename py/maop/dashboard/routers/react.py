"""MAOP Dashboard — ReAct Loop & Change Tracker API endpoints."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Query, Request

from maop.core.middleware import require_admin
from maop.dashboard.error_handler import handle_api_errors

router = APIRouter(prefix="/api/react", tags=["react"])


def _get_change_tracker():
    from maop.core.change_tracker import ChangeTracker
    root = Path(__file__).resolve().parent.parent.parent.parent
    return ChangeTracker(root_dir=str(root))


def _get_artifact_store():
    from maop.core.artifact_store import ArtifactStore
    root = Path(__file__).resolve().parent.parent.parent.parent
    return ArtifactStore(root_dir=str(root))


@router.get("/snapshots")
@handle_api_errors
async def list_snapshots(
    workdir: str = Query("", description="Filter by workdir"),
    limit: int = Query(20, ge=1, le=100),
) -> dict[str, Any]:
    tracker = _get_change_tracker()
    snapshots = tracker.list_snapshots(workdir=workdir, limit=limit)
    return {"snapshots": [s.model_dump() for s in snapshots]}


@router.post("/snapshots")
@handle_api_errors
async def create_snapshot(body: dict, request: Request) -> dict[str, Any]:
    require_admin(request)
    tracker = _get_change_tracker()
    snap_id = tracker.snapshot(
        workdir=body.get("workdir", ""),
        label=body.get("label", ""),
    )
    snap = tracker.get_snapshot(snap_id)
    return {"snapshot": snap.model_dump() if snap else None}


@router.get("/diff")
@handle_api_errors
async def diff_snapshots(
    workdir: str = Query(..., description="Working directory"),
    since_label: str = Query("", description="Compare since this label"),
) -> dict[str, Any]:
    tracker = _get_change_tracker()
    result = tracker.diff(workdir, since_label=since_label)
    return {"diff": result.model_dump()}


@router.get("/changes")
@handle_api_errors
async def get_change_log(
    workdir: str = Query(..., description="Working directory"),
    limit: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    tracker = _get_change_tracker()
    changes = tracker.get_change_log(workdir, limit=limit)
    return {"changes": changes}


@router.delete("/snapshots/{snapshot_id}")
@handle_api_errors
async def delete_snapshot(snapshot_id: str, request: Request) -> dict[str, Any]:
    require_admin(request)
    tracker = _get_change_tracker()
    ok = tracker.delete_snapshot(snapshot_id)
    return {"deleted": ok}


@router.get("/artifacts")
@handle_api_errors
async def list_artifacts(limit: int = Query(50, ge=1, le=200)) -> dict[str, Any]:
    store = _get_artifact_store()
    artifacts = store.list_artifacts(limit=limit)
    return {"artifacts": [a.model_dump() for a in artifacts]}


@router.post("/artifacts")
@handle_api_errors
async def save_artifact(body: dict, request: Request) -> dict[str, Any]:
    require_admin(request)
    store = _get_artifact_store()
    version = store.save(
        name=body.get("name", ""),
        content=body.get("content", ""),
        tag=body.get("tag", ""),
        metadata=body.get("metadata"),
    )
    return {"version": version}


@router.get("/artifacts/{name}")
@handle_api_errors
async def load_artifact(name: str, version: int | None = Query(None)) -> dict[str, Any]:
    store = _get_artifact_store()
    content = store.load(name, version=version)
    if content is None:
        return {"error": "Artifact not found"}
    return {"name": name, "content": content}


@router.get("/artifacts/{name}/history")
@handle_api_errors
async def artifact_history(name: str, limit: int = Query(20)) -> dict[str, Any]:
    store = _get_artifact_store()
    history = store.history(name, limit=limit)
    return {"history": [h.model_dump() for h in history]}


@router.post("/artifacts/{name}/restore")
@handle_api_errors
async def restore_artifact(name: str, body: dict, request: Request) -> dict[str, Any]:
    require_admin(request)
    store = _get_artifact_store()
    ok = store.restore(name, version=body.get("version", 1))
    return {"restored": ok}


@router.delete("/artifacts/{name}")
@handle_api_errors
async def delete_artifact(name: str, request: Request) -> dict[str, Any]:
    require_admin(request)
    store = _get_artifact_store()
    ok = store.delete_artifact(name)
    return {"deleted": ok}
