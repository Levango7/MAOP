"""Coverage tests for maop.dashboard.routers.worktree — all CRUD endpoints.

Covers POST create-root, branch, abandon, merge, checkpoint, rollback
and GET get, list. Uses isolated MAOP_ROOT + admin role injection +
mocked WorktreeManager.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def worktree_env(tmp_path, monkeypatch):
    """Isolate MAOP_ROOT and mock WorktreeManager."""
    monkeypatch.setattr("maop.dashboard.routers.worktree.MAOP_ROOT", tmp_path)

    # Reset worktree manager singleton
    import maop.dashboard.routers.worktree as wt_mod
    wt_mod._worktree_mgr = None

    # Mock WorktreeManager
    mock_mgr = MagicMock()
    mock_mgr.create_root = MagicMock(return_value="root-1")
    mock_mgr.branch = MagicMock(return_value="branch-1")
    mock_mgr.abandon = MagicMock(return_value=True)
    mock_mgr.get_branch = MagicMock(return_value=SimpleNamespace(
        model_dump=lambda: {"id": "branch-1", "name": "test"}
    ))
    mock_mgr.list_branches = MagicMock(return_value=[
        SimpleNamespace(model_dump=lambda: {"id": "b1", "name": "branch1"})
    ])
    mock_mgr.merge = MagicMock(return_value=SimpleNamespace(
        model_dump=lambda: {"merged": True, "conflicts": []}
    ))
    mock_mgr.checkpoint = MagicMock(return_value="cp-1")
    mock_mgr.rollback = MagicMock(return_value=True)

    monkeypatch.setattr("maop.dashboard.routers.worktree._get_worktree_mgr", lambda: mock_mgr)
    return mock_mgr


@pytest.fixture
def client(worktree_env, monkeypatch):
    """TestClient with admin role injected and worktree router mounted."""
    app = FastAPI()

    @app.middleware("http")
    async def _inject_admin(request, call_next):
        request.state.auth_roles = ["admin"]
        request.state.auth_identity = "admin"
        return await call_next(request)

    from maop.dashboard.routers.worktree import router
    app.include_router(router)
    return TestClient(app)


class TestCreateRoot:
    def test_missing_task(self, client):
        resp = client.post("/api/worktree/create-root", json={})
        assert resp.status_code == 400

    def test_happy(self, client):
        resp = client.post("/api/worktree/create-root", json={"task": "test-task"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
        assert resp.json()["node_id"] == "root-1"

    def test_with_description(self, client):
        resp = client.post(
            "/api/worktree/create-root",
            json={"task": "test", "description": "desc"},
        )
        assert resp.status_code == 200


class TestBranch:
    def test_missing_fields(self, client):
        resp = client.post("/api/worktree/branch", json={})
        assert resp.status_code == 400

    def test_missing_name(self, client):
        resp = client.post("/api/worktree/branch", json={"parent_id": "root-1"})
        assert resp.status_code == 400

    def test_happy(self, client):
        resp = client.post(
            "/api/worktree/branch",
            json={"parent_id": "root-1", "name": "feature-1"},
        )
        assert resp.status_code == 200
        assert resp.json()["node_id"] == "branch-1"

    def test_with_metadata(self, client):
        resp = client.post(
            "/api/worktree/branch",
            json={"parent_id": "root-1", "name": "f", "metadata": {"k": "v"}},
        )
        assert resp.status_code == 200

    def test_value_error(self, worktree_env, client):
        """ValueError from mgr.branch → 400."""
        worktree_env.branch.side_effect = ValueError("bad parent")
        resp = client.post(
            "/api/worktree/branch",
            json={"parent_id": "bad", "name": "f"},
        )
        assert resp.status_code == 400


class TestAbandon:
    def test_missing_id(self, client):
        resp = client.post("/api/worktree/abandon", json={})
        assert resp.status_code == 400

    def test_happy(self, client):
        resp = client.post("/api/worktree/abandon", json={"id": "branch-1"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_not_found(self, worktree_env, client):
        worktree_env.abandon.return_value = False
        resp = client.post("/api/worktree/abandon", json={"id": "nonexistent"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "not_found"


class TestGet:
    def test_missing_node_id(self, client):
        resp = client.get("/api/worktree/get")
        assert resp.status_code == 400

    def test_happy(self, client):
        resp = client.get("/api/worktree/get?node_id=branch-1")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_not_found(self, worktree_env, client):
        worktree_env.get_branch.return_value = None
        resp = client.get("/api/worktree/get?node_id=nonexistent")
        assert resp.status_code == 404


class TestList:
    def test_list_all(self, client):
        resp = client.get("/api/worktree/list")
        assert resp.status_code == 200
        data = resp.json()
        assert "branches" in data
        assert data["count"] >= 1

    def test_list_with_root_id(self, client):
        resp = client.get("/api/worktree/list?root_id=root-1")
        assert resp.status_code == 200

    def test_list_active_only(self, client):
        resp = client.get("/api/worktree/list?active_only=true")
        assert resp.status_code == 200


class TestMerge:
    def test_missing_source(self, client):
        resp = client.post("/api/worktree/merge", json={})
        assert resp.status_code == 400

    def test_happy(self, client):
        resp = client.post(
            "/api/worktree/merge",
            json={"source_branch": "feature", "target_branch": "main"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


class TestCheckpoint:
    def test_missing_node_id(self, client):
        resp = client.post("/api/worktree/checkpoint", json={})
        assert resp.status_code == 400

    def test_happy(self, client):
        resp = client.post(
            "/api/worktree/checkpoint",
            json={"node_id": "branch-1", "label": "v1"},
        )
        assert resp.status_code == 200
        assert resp.json()["checkpoint_id"] == "cp-1"

    def test_value_error(self, worktree_env, client):
        worktree_env.checkpoint.side_effect = ValueError("bad node")
        resp = client.post(
            "/api/worktree/checkpoint",
            json={"node_id": "bad", "label": "v1"},
        )
        assert resp.status_code == 400


class TestRollback:
    def test_missing_fields(self, client):
        resp = client.post("/api/worktree/rollback", json={})
        assert resp.status_code == 400

    def test_missing_checkpoint_id(self, client):
        resp = client.post("/api/worktree/rollback", json={"node_id": "b1"})
        assert resp.status_code == 400

    def test_happy(self, client):
        resp = client.post(
            "/api/worktree/rollback",
            json={"node_id": "b1", "checkpoint_id": "cp-1"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_failed(self, worktree_env, client):
        worktree_env.rollback.return_value = False
        resp = client.post(
            "/api/worktree/rollback",
            json={"node_id": "b1", "checkpoint_id": "cp-1"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "failed"