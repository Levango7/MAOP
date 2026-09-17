"""MAOP Dashboard — ReAct Loop & Change Tracker API endpoints.

Business logic (snapshot/artifact CRUD) lives in
:mod:`maop.dashboard.services.chat_service`; this module only does
request parsing, auth, service dispatch, and response formatting.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from maop.core.security.middleware import require_admin
from maop.dashboard.error_handler import handle_api_errors
from maop.dashboard.services import chat_service

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
    """Return a ChangeTracker bound to MAOP_ROOT.

    Thin wrapper over :func:`chat_service.make_change_tracker` kept on
    the router module so tests can monkeypatch the factory (see
    ``tests/test_router_misc_coverage.py``).
    """
    return chat_service.make_change_tracker(MAOP_ROOT)


def _get_artifact_store():
    """Return an ArtifactStore bound to MAOP_ROOT.

    Thin wrapper over :func:`chat_service.make_artifact_store` kept on
    the router module so tests can monkeypatch the factory (see
    ``tests/test_router_misc_coverage.py``).
    """
    return chat_service.make_artifact_store(MAOP_ROOT)


@router.get("/snapshots")
@handle_api_errors
async def list_snapshots(
    request: Request,
    workdir: str = Query("", description="Filter by workdir"),
    limit: int = Query(20, ge=1, le=100),
) -> dict[str, Any]:
    require_admin(request)
    tracker = _get_change_tracker()
    snapshots = chat_service.list_snapshots(tracker, workdir=workdir, limit=limit)
    return {"status": "ok", "snapshots": snapshots}


@router.post("/snapshots")
@handle_api_errors
async def create_snapshot(body: CreateSnapshotRequest, request: Request) -> dict[str, Any]:
    require_admin(request)
    tracker = _get_change_tracker()
    snap = chat_service.create_snapshot(tracker, workdir=body.workdir, label=body.label)
    return {"status": "ok", "snapshot": snap}


@router.get("/diff")
@handle_api_errors
async def diff_snapshots(
    request: Request,
    workdir: str = Query(..., description="Working directory"),
    since_label: str = Query("", description="Compare since this label"),
) -> dict[str, Any]:
    require_admin(request)
    tracker = _get_change_tracker()
    diff = chat_service.diff_snapshots(tracker, workdir, since_label=since_label)
    return {"status": "ok", "diff": diff}


@router.get("/changes")
@handle_api_errors
async def get_change_log(
    request: Request,
    workdir: str = Query(..., description="Working directory"),
    limit: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    require_admin(request)
    tracker = _get_change_tracker()
    changes = chat_service.get_change_log(tracker, workdir, limit=limit)
    return {"status": "ok", "changes": changes}


@router.delete("/snapshots/{snapshot_id}")
@handle_api_errors
async def delete_snapshot(snapshot_id: str, request: Request) -> dict[str, Any]:
    require_admin(request)
    tracker = _get_change_tracker()
    ok = chat_service.delete_snapshot(tracker, snapshot_id)
    return {"status": "ok", "deleted": ok}


@router.get("/artifacts")
@handle_api_errors
async def list_artifacts(request: Request, limit: int = Query(50, ge=1, le=200)) -> dict[str, Any]:
    require_admin(request)
    store = _get_artifact_store()
    artifacts = chat_service.list_artifacts(store, limit=limit)
    return {"status": "ok", "artifacts": artifacts}


@router.post("/artifacts")
@handle_api_errors
async def save_artifact(body: SaveArtifactRequest, request: Request) -> dict[str, Any]:
    require_admin(request)
    store = _get_artifact_store()
    version = chat_service.save_artifact(
        store,
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
    content = chat_service.load_artifact(store, name, version=version)
    if content is None:
        raise HTTPException(status_code=404, detail="Artifact not found")
    return {"status": "ok", "name": name, "content": content}


@router.get("/artifacts/{name}/history")
@handle_api_errors
async def artifact_history(request: Request, name: str, limit: int = Query(20, ge=1, le=1000)) -> dict[str, Any]:
    require_admin(request)
    store = _get_artifact_store()
    history = chat_service.artifact_history(store, name, limit=limit)
    return {"status": "ok", "history": history}


@router.post("/artifacts/{name}/restore")
@handle_api_errors
async def restore_artifact(name: str, body: RestoreArtifactRequest, request: Request) -> dict[str, Any]:
    require_admin(request)
    store = _get_artifact_store()
    ok = chat_service.restore_artifact(store, name, version=body.version)
    return {"status": "ok", "restored": ok}


@router.delete("/artifacts/{name}")
@handle_api_errors
async def delete_artifact(name: str, request: Request) -> dict[str, Any]:
    require_admin(request)
    store = _get_artifact_store()
    ok = chat_service.delete_artifact(store, name)
    return {"status": "ok", "deleted": ok}
