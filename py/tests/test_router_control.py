"""Unit tests for MAOP.dashboard.routers.control module."""

from __future__ import annotations

import asyncio
import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from maop.dashboard.routers import control as ctrl
from maop.dashboard.routers import state


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
    proc.communicate = AsyncMock(return_value=(b"", b""))
    return proc


@pytest.fixture
def temp_maop_root(tmp_path, monkeypatch):
    """Patch MAOP_ROOT in state module to a temp dir."""
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(state, "MAOP_ROOT", tmp_path)
    return tmp_path


@pytest.fixture
def clean_jobs(monkeypatch):
    """Provide a fresh active_jobs dict."""
    jobs: dict = {}
    monkeypatch.setattr(state, "active_jobs", jobs)
    return jobs


@pytest.fixture
def clean_cache(monkeypatch):
    """Provide a fresh cache dict and lock."""
    cache: dict = {}
    lock = asyncio.Lock()
    monkeypatch.setattr(state, "cache", cache)
    monkeypatch.setattr(state, "cache_lock", lock)
    return cache


@pytest.fixture
def client(temp_maop_root):
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

    def test_status_excludes_drain_task_and_includes_capture(self, client, clean_jobs):
        class _DoneTask:
            def done(self) -> bool:
                return True

        proc = _fake_proc(returncode=0)
        clean_jobs["j1"] = {
            "action": "run", "status": "running", "task": "t1",
            "process": proc, "_drain_task": _DoneTask(),
            "stdout": "ok\n", "stderr": "warn\n",
        }
        resp = client.get("/api/control/status")
        assert resp.status_code == 200
        job = resp.json()["jobs"][0]
        assert "_drain_task" not in job
        assert job["stdout"] == "ok\n"
        assert job["stderr"] == "warn\n"

    def test_job_detail_returns_capture(self, client, clean_jobs):
        clean_jobs["j1"] = {
            "action": "run", "status": "completed", "task": "t1",
            "stdout": "done", "stderr": "",
        }
        resp = client.get("/api/control/jobs/j1")
        assert resp.status_code == 200
        assert resp.json()["job"]["stdout"] == "done"

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


class TestControlHistory:
    def _write(self, root, records):
        path = root / "data" / "control_jobs.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records),
            encoding="utf-8",
        )

    def test_persisted_job_is_listed_and_readable_after_restart(
        self, client, temp_maop_root, clean_jobs
    ):
        self._write(temp_maop_root, [
            {"id": "old1", "action": "run", "status": "completed",
             "task": "旧任务", "start": "2026-10-19T10:00:00",
             "finished_at": "2026-10-19T10:00:05",
             "exit_code": 0, "stdout": "done", "stderr": ""},
        ])
        data = client.get("/api/control/status").json()
        assert data["count"] == 1
        assert data["jobs"][0]["id"] == "old1"
        assert data["jobs"][0]["task"] == "旧任务"
        detail = client.get("/api/control/jobs/old1")
        assert detail.status_code == 200
        assert detail.json()["job"]["stdout"] == "done"

    def test_last_record_per_id_wins_and_stale_running_is_interrupted(
        self, client, temp_maop_root, clean_jobs
    ):
        self._write(temp_maop_root, [
            {"id": "j2", "status": "running", "task": "t", "start": "2026-10-19T10:00:00"},
            {"id": "j2", "status": "failed", "task": "t", "start": "2026-10-19T10:00:00",
             "exit_code": 1},
            {"id": "j3", "status": "running", "task": "stale", "start": "2026-10-19T11:00:00"},
        ])
        jobs = {job["id"]: job for job in client.get("/api/control/status").json()["jobs"]}
        assert jobs["j2"]["status"] == "failed"
        assert jobs["j2"]["exit_code"] == 1
        assert jobs["j3"]["status"] == "interrupted"


# ── POST /api/control/run ──────────────────────────────────────────
class TestControlRunPost:
    def test_missing_task_uses_default(self, client, temp_maop_root, clean_jobs):
        fake_proc = _fake_proc(returncode=None)
        with patch.object(ctrl.asyncio, "create_subprocess_exec", new_callable=AsyncMock, return_value=fake_proc):
            resp = client.post("/api/control/run", json={})
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["task"] == "default"
        assert "job_id" in data

    def test_starts_job_with_task(self, client, temp_maop_root, clean_jobs):
        fake_proc = _fake_proc(returncode=None)
        with patch.object(ctrl.asyncio, "create_subprocess_exec", new_callable=AsyncMock, return_value=fake_proc):
            resp = client.post("/api/control/run", json={"task": "hello"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["task"] == "hello"
        assert data["job_id"] in clean_jobs

    def test_unicode_task_is_accepted(self, client, temp_maop_root, clean_jobs):
        fake_proc = _fake_proc(returncode=None)
        with patch.object(ctrl.asyncio, "create_subprocess_exec", new_callable=AsyncMock, return_value=fake_proc):
            resp = client.post("/api/control/run", json={"task": "重构 auth 模块，改用 JWT。"})
        assert resp.status_code == 200
        assert resp.json()["task"] == "重构 auth 模块，改用 JWT。"

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
        assert (temp_maop_root / "logs" / ".maop_pause").exists()


# ── POST /api/control/resume ──────────────────────────────────────
class TestControlResume:
    def test_resume_removes_file(self, client, temp_maop_root, clean_jobs):
        pause_file = temp_maop_root / "logs" / ".maop_pause"
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
        assert data["status"] == "ok"


# ── POST /api/control/doctor ──────────────────────────────────────
class TestControlDoctor:
    def test_returns_job_id(self, client, temp_maop_root, clean_jobs):
        resp = client.post("/api/control/doctor")
        assert resp.status_code == 200
        data = resp.json()
        assert "job_id" in data
        assert data["status"] == "ok"


# ── POST /api/control/cancel ──────────────────────────────────────
class TestControlCancel:
    def test_cancel_existing(self, client, temp_maop_root, clean_jobs):
        proc = _fake_proc(returncode=None)
        clean_jobs["abc123"] = {"action": "run", "status": "running", "process": proc}
        resp = client.post("/api/control/cancel", json={"job_id": "abc123"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
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

    def test_prune_ages_out_run_events_too(self, client, temp_maop_root):
        """The action must reach the timeline, not only the memory store."""
        import sqlite3

        from maop.core import run_events as ev

        assert ev.record_run_event("stale-1", "run.step", agent="claude", status="ok")
        db_path = ev.get_db_path("run_events", legacy_fallback="maop")
        with sqlite3.connect(str(db_path)) as conn:
            conn.execute(
                "UPDATE run_events SET timestamp = '2000-01-01T00:00:00+00:00' "
                "WHERE trace_id = 'stale-1'"
            )
            conn.commit()

        resp = client.post("/api/control/maintain", json={"action": "prune"})
        assert resp.status_code == 200
        assert resp.json()["run_events_pruned"] >= 1
        assert ev.fetch_run_events("stale-1") == []

    def test_prune_ages_out_quota_audit_too(self, client, temp_maop_root):
        """prune 也要清掉过期的配额采样/历史，否则两张表只增不减。"""
        import time

        from maop.core.backends.db_utils import sqlite_connect, unified_db_path
        from maop.dashboard.services import quota_audit

        quota_audit.record_usage_sample("t-ctl", "api_calls", 10)
        quota_audit.record_quota_change("t-ctl", "api_calls", "hard_limit", 1, 2)
        old = time.time() - 400 * 86400.0
        with sqlite_connect(unified_db_path()) as conn:
            conn.execute("UPDATE quota_usage_samples SET sampled_at = ?", (old,))
            conn.execute("UPDATE quota_history SET changed_at = ?", (old,))

        resp = client.post("/api/control/maintain", json={"action": "prune"})
        assert resp.status_code == 200
        pruned = resp.json()["quota_audit_pruned"]
        assert pruned["samples"] >= 1
        assert pruned["history"] >= 1
        with sqlite_connect(unified_db_path()) as conn:
            assert conn.execute("SELECT COUNT(*) FROM quota_usage_samples").fetchone()[0] == 0

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


# ── Coverage tests (merged from test_router_control_coverage.py) ──

class FakeProc:
    """Minimal asyncio subprocess stand-in."""

    def __init__(self, returncode: int | None = None):
        self.returncode = returncode

    def terminate(self) -> None:
        self.returncode = 0

    async def wait(self) -> int:
        return self.returncode or 0

    async def communicate(self) -> tuple[bytes, bytes]:
        return (b"", b"")


@pytest.fixture
def control_env(tmp_path, monkeypatch):
    """Isolate MAOP_ROOT for control router."""
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr("maop.dashboard.routers.control.MAOP_ROOT", tmp_path)
    monkeypatch.setattr("maop.dashboard.routers.state.MAOP_ROOT", tmp_path)

    # Clear active jobs
    from maop.dashboard.routers import state
    state.active_jobs.clear()

    return tmp_path


@pytest.fixture
def client_coverage(control_env, monkeypatch):
    """TestClient with admin role injected, control router mounted, subprocess mocked."""
    async def fake_exec(*args, **kwargs):
        return FakeProc(returncode=None)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    app = FastAPI()

    @app.middleware("http")
    async def _inject_admin(request, call_next):
        request.state.auth_roles = ["admin"]
        request.state.auth_identity = "admin"
        return await call_next(request)

    from maop.dashboard.routers.control import router
    app.include_router(router)
    return TestClient(app)


class TestControlStatusCoverage:
    def test_status_empty(self, client_coverage):
        """GET /api/control/status with no active jobs."""
        resp = client_coverage.get("/api/control/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 0


class TestControlRun:
    def test_run_happy(self, client_coverage):
        """POST /api/control/run starts a job."""
        resp = client_coverage.post("/api/control/run", json={"task": "test-task"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "job_id" in data

    def test_run_with_workflow(self, client_coverage):
        """POST /api/control/run with workflow field."""
        resp = client_coverage.post("/api/control/run", json={"workflow": "build"})
        assert resp.status_code == 200

    def test_run_default_task(self, client_coverage):
        """POST /api/control/run with no task uses 'default'."""
        resp = client_coverage.post("/api/control/run", json={})
        assert resp.status_code == 200
        assert resp.json()["task"] == "default"


class TestControlPauseResume:
    def test_pause(self, client_coverage):
        """POST /api/control/pause creates pause file."""
        resp = client_coverage.post("/api/control/pause")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_resume(self, client_coverage):
        """POST /api/control/resume removes pause file."""
        client_coverage.post("/api/control/pause")
        resp = client_coverage.post("/api/control/resume")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_resume_no_pause_file(self, client_coverage):
        """POST /api/control/resume without pause file."""
        resp = client_coverage.post("/api/control/resume")
        assert resp.status_code == 200


class TestControlStopCoverage:
    def test_stop_no_jobs(self, client_coverage):
        """POST /api/control/stop with no running jobs."""
        resp = client_coverage.post("/api/control/stop")
        assert resp.status_code == 200
        assert resp.json()["stopped"] == 0


class TestControlValidateCoverage:
    def test_validate(self, client_coverage):
        """POST /api/control/validate runs config validation."""
        resp = client_coverage.post("/api/control/validate")
        assert resp.status_code == 200
        assert "status" in resp.json()


class TestControlDoctorCoverage:
    def test_doctor(self, client_coverage):
        """POST /api/control/doctor runs system diagnostics."""
        resp = client_coverage.post("/api/control/doctor")
        assert resp.status_code == 200
        assert "status" in resp.json()


class TestControlCancelCoverage:
    def test_cancel_not_found(self, client_coverage):
        """POST /api/control/cancel with unknown job_id returns 404."""
        resp = client_coverage.post("/api/control/cancel", json={"job_id": "nonexistent"})
        assert resp.status_code == 404

    def test_cancel_existing_job(self, client_coverage):
        """POST /api/control/cancel with existing job_id."""
        # Start a job
        run_resp = client_coverage.post("/api/control/run", json={"task": "test"})
        job_id = run_resp.json()["job_id"]
        resp = client_coverage.post("/api/control/cancel", json={"job_id": job_id})
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


class TestControlRefreshCoverage:
    def test_refresh(self, client_coverage):
        """POST /api/control/refresh clears cache."""
        resp = client_coverage.post("/api/control/refresh")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


class TestControlClearCacheCoverage:
    def test_clear_cache(self, client_coverage):
        """POST /api/control/clear-cache clears cache."""
        resp = client_coverage.post("/api/control/clear-cache")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


class TestControlProviderHealth:
    def test_provider_health(self, client_coverage):
        """POST /api/control/provider-health returns component health."""
        resp = client_coverage.post("/api/control/provider-health")
        assert resp.status_code == 200
        assert "status" in resp.json()


class TestControlMaintainCoverage:
    def test_no_action(self, client_coverage):
        """POST /api/control/maintain with no action returns noop."""
        resp = client_coverage.post("/api/control/maintain", json={})
        assert resp.status_code == 200
        assert resp.json()["action"] == "noop"

    def test_log_rotate(self, client_coverage):
        """POST /api/control/maintain with action=log-rotate."""
        resp = client_coverage.post("/api/control/maintain", json={"action": "log-rotate"})
        assert resp.status_code == 200

    def test_prune(self, client_coverage):
        """POST /api/control/maintain with action=prune."""
        resp = client_coverage.post("/api/control/maintain", json={"action": "prune"})
        assert resp.status_code == 200

    def test_health(self, client_coverage):
        """POST /api/control/maintain with action=health."""
        resp = client_coverage.post("/api/control/maintain", json={"action": "health"})
        assert resp.status_code == 200

    def test_backup(self, client_coverage):
        """POST /api/control/maintain with action=backup."""
        resp = client_coverage.post("/api/control/maintain", json={"action": "backup"})
        assert resp.status_code == 200

    def test_cache_clear(self, client_coverage):
        """POST /api/control/maintain with action=cache-clear."""
        resp = client_coverage.post("/api/control/maintain", json={"action": "cache-clear"})
        assert resp.status_code == 200

    def test_reload(self, client_coverage):
        """POST /api/control/maintain with action=reload."""
        resp = client_coverage.post("/api/control/maintain", json={"action": "reload"})
        assert resp.status_code == 200

    def test_reindex(self, client_coverage):
        """POST /api/control/maintain with action=reindex."""
        resp = client_coverage.post("/api/control/maintain", json={"action": "reindex"})
        assert resp.status_code == 200

    def test_vacuum(self, client_coverage):
        """POST /api/control/maintain with action=vacuum."""
        resp = client_coverage.post("/api/control/maintain", json={"action": "vacuum"})
        assert resp.status_code == 200

    def test_cleanup(self, client_coverage):
        """POST /api/control/maintain with action=cleanup."""
        resp = client_coverage.post("/api/control/maintain", json={"action": "cleanup"})
        assert resp.status_code == 200

    def test_reset(self, client_coverage):
        """POST /api/control/maintain with action=reset."""
        resp = client_coverage.post("/api/control/maintain", json={"action": "reset"})
        assert resp.status_code == 200

    def test_rebuild(self, client_coverage):
        """POST /api/control/maintain with action=rebuild."""
        resp = client_coverage.post("/api/control/maintain", json={"action": "rebuild"})
        assert resp.status_code == 200

    def test_gc(self, client_coverage):
        """POST /api/control/maintain with action=gc."""
        resp = client_coverage.post("/api/control/maintain", json={"action": "gc"})
        assert resp.status_code == 200


# ── run trace linkage (M1: job → /api/runs/{trace_id}) ─────────────
class TestRunTraceLinkage:
    def test_extract_trace_id_requires_own_line(self):
        from maop.dashboard.services import system_service

        assert system_service.extract_trace_id(
            "你好\n[TRACE] deadbeef\n[OK] run 完成 (12ms)"
        ) == "deadbeef"
        # agent 输出里引用这个标记不算数（必须独占一行且是十六进制 trace）
        assert system_service.extract_trace_id("日志: [TRACE] zzzzzz tail") == ""
        assert system_service.extract_trace_id("") == ""

    @pytest.mark.asyncio
    async def test_drain_records_trace_id_on_job(self, temp_maop_root, clean_jobs):
        from maop.dashboard.services import system_service

        proc = _fake_proc(returncode=0)
        proc.communicate = AsyncMock(return_value=(b"[TRACE] cafe1234\n", b""))
        with patch.object(system_service.asyncio, "create_subprocess_exec",
                          new_callable=AsyncMock, return_value=proc):
            job_id = await system_service.start_control_run("中文任务")

        await clean_jobs[job_id]["_drain_task"]
        job = system_service.get_control_job(job_id)
        assert job["trace_id"] == "cafe1234"
        assert job["status"] == "completed"
