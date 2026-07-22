"""Unit tests for MAOP.dashboard.routers.control module."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from maop.dashboard.routers import control as ctrl


def _make_app() -> FastAPI:
    app = FastAPI()
    @app.middleware("http")
    async def _inject_admin(request, call_next):
        request.state.auth_roles = ["admin"]
        return await call_next(request)
    app.include_router(ctrl.router)
    return app


def _fake_proc(returncode=None) -> MagicMock:
    """Create a fake subprocess proc with returncode and terminate."""
    proc = MagicMock()
    proc.returncode = returncode
    proc.terminate = MagicMock()
    return proc


@pytest.fixture
def temp_maop_root(tmp_path, monkeypatch):
    """Patch MAOP_ROOT in control module to a temp dir."""
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(ctrl, "MAOP_ROOT", tmp_path)
    return tmp_path


@pytest.fixture
def clean_jobs(monkeypatch):
    """Provide a fresh active_jobs dict."""
    jobs: dict = {}
    monkeypatch.setattr(ctrl, "active_jobs", jobs)
    return jobs


@pytest.fixture
def clean_cache(monkeypatch):
    """Provide a fresh cache dict and lock."""
    cache: dict = {}
    lock = asyncio.Lock()
    monkeypatch.setattr(ctrl, "cache", cache)
    monkeypatch.setattr(ctrl, "cache_lock", lock)
    return cache


@pytest.fixture
def client():
    return TestClient(_make_app())


# ── GET /api/control/status ───────────────────────────────────────
class TestControlStatus:
    def test_empty_jobs(self, client, clean_jobs):
        resp = client.get("/api/control/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["active_jobs"] == []
        assert data["jobs"] == []
        assert data["count"] == 0

    def test_with_running_job(self, client, clean_jobs):
        proc = _fake_proc(returncode=None)
        clean_jobs["j1"] = {"action": "run", "status": "running", "task": "t1", "process": proc}
        resp = client.get("/api/control/status")
        data = resp.json()
        assert data["count"] == 1
        assert data["jobs"][0]["status"] == "running"
        assert data["jobs"][0]["task"] == "t1"
        assert "process" not in data["jobs"][0]

    def test_completed_job(self, client, clean_jobs):
        proc = _fake_proc(returncode=0)
        clean_jobs["j1"] = {"action": "run", "status": "running", "task": "t1", "process": proc}
        resp = client.get("/api/control/status")
        data = resp.json()
        assert data["jobs"][0]["status"] == "completed"
        assert data["jobs"][0]["exit_code"] == 0

    def test_failed_job(self, client, clean_jobs):
        proc = _fake_proc(returncode=1)
        clean_jobs["j1"] = {"action": "run", "status": "running", "task": "t1", "process": proc}
        resp = client.get("/api/control/status")
        data = resp.json()
        assert data["jobs"][0]["status"] == "failed"
        assert data["jobs"][0]["exit_code"] == 1


# ── POST /api/control/run ──────────────────────────────────────────
class TestControlRunPost:
    def test_missing_task_uses_default(self, client, temp_maop_root, clean_jobs):
        fake_proc = _fake_proc(returncode=None)
        with patch.object(ctrl.asyncio, "create_subprocess_exec", new_callable=AsyncMock, return_value=fake_proc):
            resp = client.post("/api/control/run", json={})
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "started"
        assert data["task"] == "default"
        assert "job_id" in data

    def test_starts_job_with_task(self, client, temp_maop_root, clean_jobs):
        fake_proc = _fake_proc(returncode=None)
        with patch.object(ctrl.asyncio, "create_subprocess_exec", new_callable=AsyncMock, return_value=fake_proc):
            resp = client.post("/api/control/run", json={"task": "hello"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "started"
        assert data["task"] == "hello"
        assert data["job_id"] in clean_jobs

    def test_workflow_param_used_as_task(self, client, temp_maop_root, clean_jobs):
        fake_proc = _fake_proc(returncode=None)
        with patch.object(ctrl.asyncio, "create_subprocess_exec", new_callable=AsyncMock, return_value=fake_proc):
            resp = client.post("/api/control/run", json={"workflow": "wf1"})
        assert resp.status_code == 200
        assert resp.json()["task"] == "wf1"

    def test_task_takes_precedence_over_workflow(self, client, temp_maop_root, clean_jobs):
        fake_proc = _fake_proc(returncode=None)
        with patch.object(ctrl.asyncio, "create_subprocess_exec", new_callable=AsyncMock, return_value=fake_proc):
            resp = client.post("/api/control/run", json={"task": "t1", "workflow": "wf1"})
        assert resp.status_code == 200
        assert resp.json()["task"] == "t1"


# ── POST /api/control/pause ───────────────────────────────────────
class TestControlPause:
    def test_pause_creates_file(self, client, temp_maop_root, clean_jobs):
        resp = client.post("/api/control/pause")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["action"] == "pause"
        assert (temp_maop_root / "logs" / ".MAOP_pause").exists()


# ── POST /api/control/resume ──────────────────────────────────────
class TestControlResume:
    def test_resume_removes_file(self, client, temp_maop_root, clean_jobs):
        pause_file = temp_maop_root / "logs" / ".MAOP_pause"
        pause_file.write_text("paused")
        resp = client.post("/api/control/resume")
        assert resp.status_code == 200
        assert resp.json()["action"] == "resume"
        assert not pause_file.exists()


# ── POST /api/control/stop ────────────────────────────────────────
class TestControlStop:
    def test_stop_no_jobs(self, client, temp_maop_root, clean_jobs):
        resp = client.post("/api/control/stop")
        data = resp.json()
        assert data["action"] == "stop"
        assert data["stopped"] == 0

    def test_stop_terminates_running(self, client, temp_maop_root, clean_jobs):
        proc = _fake_proc(returncode=None)
        clean_jobs["j1"] = {"action": "run", "status": "running", "process": proc}
        resp = client.post("/api/control/stop")
        data = resp.json()
        assert data["stopped"] == 1
        proc.terminate.assert_called_once()


# ── POST /api/control/validate ────────────────────────────────────
class TestControlValidate:
    def test_returns_job_id(self, client, temp_maop_root, clean_jobs):
        resp = client.post("/api/control/validate")
        assert resp.status_code == 200
        data = resp.json()
        assert "job_id" in data
        assert data["status"] in ("completed", "failed")


# ── POST /api/control/doctor ──────────────────────────────────────
class TestControlDoctor:
    def test_returns_job_id(self, client, temp_maop_root, clean_jobs):
        resp = client.post("/api/control/doctor")
        assert resp.status_code == 200
        data = resp.json()
        assert "job_id" in data
        assert data["status"] in ("completed", "failed")


# ── POST /api/control/cancel ──────────────────────────────────────
class TestControlCancel:
    def test_cancel_existing(self, client, temp_maop_root, clean_jobs):
        proc = _fake_proc(returncode=None)
        clean_jobs["abc123"] = {"action": "run", "status": "running", "process": proc}
        resp = client.post("/api/control/cancel", json={"job_id": "abc123"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "cancelled"
        assert data["job_id"] == "abc123"
        proc.terminate.assert_called_once()

    def test_cancel_completed_job_no_terminate(self, client, temp_maop_root, clean_jobs):
        proc = _fake_proc(returncode=0)
        clean_jobs["abc"] = {"action": "run", "status": "completed", "process": proc}
        resp = client.post("/api/control/cancel", json={"job_id": "abc"})
        assert resp.status_code == 200
        proc.terminate.assert_not_called()

    def test_cancel_not_found_404(self, client, temp_maop_root, clean_jobs):
        resp = client.post("/api/control/cancel", json={"job_id": "nonexistent"})
        assert resp.status_code == 404


# ── POST /api/control/refresh ─────────────────────────────────────
class TestControlRefresh:
    def test_refresh_clears_cache(self, client, clean_cache):
        clean_cache["k1"] = (time.time(), "val")
        resp = client.post("/api/control/refresh")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok", "cache": "cleared"}
        assert len(clean_cache) == 0


# ── POST /api/control/clear-cache ─────────────────────────────────
class TestControlClearCache:
    def test_clear_cache(self, client, clean_cache):
        clean_cache["k"] = (time.time(), 1)
        resp = client.post("/api/control/clear-cache")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}
        assert len(clean_cache) == 0


# ── provider-health ───────────────────────────────────────────────
class TestProviderHealth:

    def test_post(self, client, temp_maop_root):
        resp = client.post("/api/control/provider-health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] in ("ok", "error")


# ── POST /api/control/maintain ────────────────────────────────────
class TestControlMaintain:
    def test_noop(self, client, temp_maop_root):
        resp = client.post("/api/control/maintain", json={})
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_cache_clear(self, client, temp_maop_root, clean_cache):
        clean_cache["k"] = (time.time(), 1)
        resp = client.post("/api/control/maintain", json={"action": "cache-clear"})
        assert resp.status_code == 200
        assert resp.json()["action"] == "cache-clear"
        assert len(clean_cache) == 0

    def test_log_rotate(self, client, temp_maop_root):
        resp = client.post("/api/control/maintain", json={"action": "log-rotate"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["action"] == "log-rotate"

    def test_prune(self, client, temp_maop_root):
        resp = client.post("/api/control/maintain", json={"action": "prune"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["action"] == "prune"

    def test_health(self, client, temp_maop_root):
        resp = client.post("/api/control/maintain", json={"action": "health"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["action"] == "health"

    def test_backup(self, client, temp_maop_root):
        resp = client.post("/api/control/maintain", json={"action": "backup"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["action"] == "backup"

    def test_reload(self, client, temp_maop_root):
        resp = client.post("/api/control/maintain", json={"action": "reload"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["action"] == "reload"
