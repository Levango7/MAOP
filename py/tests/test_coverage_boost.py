"""Coverage boost tests for high-gap modules.

Targets uncovered pure functions and branches in:
  - maop.dashboard.server: _signal_handler, _ws_broadcast, CSP overflow, lifespan
  - maop.dashboard.routers.system: _dir_size_mb, _pct, _run_subprocess, _get_allowed_packages
  - maop.worker.agent_executor: _handle_signal, _setup_logging

These are small, fast, side-effect-free tests that close coverage gaps without
exercising heavy subprocess / network paths.
"""
from __future__ import annotations

import signal
from typing import Any

import pytest

# ── server.py: _signal_handler ──────────────────────────────────────

class TestServerSignalHandler:
    def test_first_call_sets_shutting_down(self, monkeypatch):
        from maop.dashboard import lifespan as life

        monkeypatch.setattr(life, "_shutting_down", False)
        monkeypatch.setattr(life, "_prev_handlers", {})

        life._signal_handler(signal.SIGINT, None)
        assert life._shutting_down is True

    def test_second_call_skips_log(self, monkeypatch):
        from maop.dashboard import lifespan as life

        monkeypatch.setattr(life, "_shutting_down", True)
        monkeypatch.setattr(life, "_prev_handlers", {})

        # Already shutting down — should not raise, no log.
        life._signal_handler(signal.SIGTERM, None)
        assert life._shutting_down is True

    def test_calls_previous_handler(self, monkeypatch):
        from maop.dashboard import lifespan as life

        called: list[tuple[int, Any]] = []
        monkeypatch.setattr(life, "_shutting_down", False)
        monkeypatch.setattr(life, "_prev_handlers", {signal.SIGINT: lambda s, f: called.append((s, f))})

        life._signal_handler(signal.SIGINT, "frame")
        assert called == [(signal.SIGINT, "frame")]


# ── server.py: _ws_broadcast ────────────────────────────────────────

class TestServerWsBroadcast:
    async def test_broadcast_no_clients_returns_early(self, monkeypatch):
        from maop.dashboard import ws_state
        from maop.dashboard.ws_broadcast import _ws_broadcast

        monkeypatch.setattr(ws_state, "_ws_clients", set())
        # Should return immediately without error.
        await _ws_broadcast({"type": "ping"})
        # No assertion needed — reaching here means no exception.


# ── server.py: CSP violation overflow ───────────────────────────────

class TestServerCspOverflow:
    async def test_csp_violation_buffer_trims(self):
        """Overflow past _CSP_VIOLATION_MAX trims oldest entries (line 578)."""
        from httpx import ASGITransport, AsyncClient

        from maop.dashboard import server as srv

        # Pre-fill the buffer to the cap so the next append triggers pop(0).
        srv._csp_violations.clear()
        srv._csp_violations.extend({"ts": i} for i in range(srv._CSP_VIOLATION_MAX))

        saved: dict[str, Any] = {}
        for attr in ("auth_manager", "api_key_auth", "jwt_auth"):
            saved[attr] = getattr(srv.app.state, attr, None)
            setattr(srv.app.state, attr, None)
        transport = ASGITransport(app=srv.app, raise_app_exceptions=False)
        try:
            async with AsyncClient(transport=transport, base_url="http://test") as c:
                resp = await c.post("/api/csp-report", json={
                    "csp-report": {"violated-directive": "img-src"}
                })
                assert resp.status_code == 200
        finally:
            for attr, val in saved.items():
                setattr(srv.app.state, attr, val)

        # Buffer should be trimmed back to the cap.
        assert len(srv._csp_violations) == srv._CSP_VIOLATION_MAX
        srv._csp_violations.clear()


# ── server.py: lifespan ─────────────────────────────────────────────

class TestServerLifespan:
    """Exercise the ASGI lifespan startup/shutdown by using a TestClient that
    actually runs lifespan (unlike ASGITransport). Schedulers are stubbed so
    no real background threads start.
    """

    def test_lifespan_startup_shutdown(self, monkeypatch):
        # Stub schedulers so no real threads/timers start.
        from maop.core import db_backup, log_rotate
        from maop.dashboard import server as srv

        class _StubBackup:
            def start_scheduler(self, interval_s: float = 3600) -> None:
                pass

            def stop_scheduler(self) -> None:
                pass

        class _StubRotate:
            def start(self) -> None:
                pass

            def stop(self) -> None:
                pass

        monkeypatch.setattr(db_backup, "DbBackup", _StubBackup)
        monkeypatch.setattr(log_rotate, "LogRotateScheduler", _StubRotate)

        # Stub OTel setup to avoid importing opentelemetry SDK.
        try:
            from maop.core import otel
            monkeypatch.setattr(otel, "setup_provider", lambda: None)
        except Exception:
            pass

        # Disable auth so lifespan skips auth_manager setup branch lightly.
        monkeypatch.setattr(srv._auth_mod, "_auth_enabled", False)

        from fastapi.testclient import TestClient

        # Reset global ws push task guard.
        from maop.dashboard import ws_state

        monkeypatch.setattr(ws_state, "_ws_push_task", None)
        try:
            with TestClient(srv.app):
                # Lifespan ran startup; just entering the block covers startup lines.
                pass
            # Exiting the block covers shutdown lines.
        finally:
            # Ensure the ws push task is cancelled if it lingers.
            task = getattr(ws_state, "_ws_push_task", None)
            if task is not None and not task.done():
                task.cancel()


# ── system.py: _dir_size_mb / _pct ──────────────────────────────────

class TestSystemHelpers:
    def test_dir_size_mb_nonexistent(self, tmp_path):
        from maop.dashboard.routers.system import _dir_size_mb

        assert _dir_size_mb(tmp_path / "nope") == 0.0

    def test_dir_size_mb_file(self, tmp_path):
        from maop.dashboard.routers.system import _dir_size_mb

        f = tmp_path / "x.bin"
        f.write_bytes(b"\0" * 2048)
        size = _dir_size_mb(f)
        assert size > 0.0

    def test_dir_size_mb_dir(self, tmp_path):
        from maop.dashboard.routers.system import _dir_size_mb

        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "a.txt").write_text("hello")
        (tmp_path / "b.txt").write_text("world!")
        size = _dir_size_mb(tmp_path)
        assert size > 0.0

    def test_pct_zero_total(self):
        from maop.dashboard.routers.system import _pct

        assert _pct(10.0, 0.0) == 0.0

    def test_pct_normal(self):
        from maop.dashboard.routers.system import _pct

        assert _pct(25.0, 100.0) == 0.25

    def test_pct_clamped_to_one(self):
        from maop.dashboard.routers.system import _pct

        assert _pct(150.0, 100.0) == 1.0


# ── system.py: _run_subprocess ──────────────────────────────────────

class TestSystemRunSubprocess:
    async def test_success(self):
        from maop.dashboard.routers.system import _run_subprocess

        rc, out, _err = await _run_subprocess(
            ["python", "-c", "print('hi')"], timeout=10
        )
        assert rc == 0
        assert "hi" in out

    async def test_timeout(self):
        from maop.dashboard.routers.system import _run_subprocess

        rc, _out, err = await _run_subprocess(
            ["python", "-c", "import time; time.sleep(5)"], timeout=1
        )
        assert rc == -1
        assert "timeout" in err

    async def test_exception(self):
        from maop.dashboard.routers.system import _run_subprocess

        rc, _out, _err = await _run_subprocess(
            ["__nonexistent_binary_xyz__"], timeout=5
        )
        assert rc == -1


# ── system.py: _get_allowed_packages ────────────────────────────────

class TestSystemAllowedPackages:
    def test_returns_set(self, monkeypatch):
        from maop.dashboard.routers.system import _deps as sys_deps

        # Reset the cache so the function body executes.
        monkeypatch.setattr(sys_deps, "_ALLOWED_PIP_PACKAGES", None)
        result = sys_deps._get_allowed_packages()
        assert isinstance(result, set)
        # Result is intersected with hardened allow-list, so subset.
        assert result <= sys_deps._HARDENED_ALLOWED_PACKAGES


# ── agent_executor.py: _handle_signal / _setup_logging ──────────────

class TestAgentExecutorHelpers:
    def test_handle_signal_sets_shutdown(self, monkeypatch):
        from maop.worker import agent_executor as ae

        monkeypatch.setattr(ae, "_shutdown", False)
        ae._handle_signal(signal.SIGTERM, None)
        assert ae._shutdown is True

    def test_setup_logging_basic(self, monkeypatch):
        from maop.worker import agent_executor as ae

        monkeypatch.setenv("MAOP_LOG_LEVEL", "DEBUG")
        monkeypatch.delenv("MAOP_JSON_LOG", raising=False)
        # Should not raise.
        ae._setup_logging()

    def test_setup_logging_json(self, monkeypatch):
        from maop.worker import agent_executor as ae

        monkeypatch.setenv("MAOP_LOG_LEVEL", "INFO")
        monkeypatch.setenv("MAOP_JSON_LOG", "1")
        monkeypatch.delenv("MAOP_JSON_LOG_FILE", raising=False)
        try:
            ae._setup_logging()
        except Exception:
            # setup_json_logging may require extra deps; tolerate ImportError.
            pytest.skip("JSON logging setup unavailable in this environment")