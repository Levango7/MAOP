"""Tests for MAOP.cli — argument parsing and command dispatch."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

from maop import cli

# ── Argument parsing ──────────────────────────────────────────

class TestArgParsing:
    def test_default_action_is_start(self):
        with patch.object(sys, "argv", ["MAOP"]), patch("maop.cli.cmd_start") as mock_start:
            cli.main()
            mock_start.assert_called_once_with(9079, "127.0.0.1")

    def test_explicit_start_with_port_host(self):
        with patch.object(sys, "argv", ["MAOP", "start", "--port", "8080", "--host", "0.0.0.0"]), \
             patch("maop.cli.cmd_start") as mock_start:
            cli.main()
            mock_start.assert_called_once_with(8080, "0.0.0.0")

    def test_stop_action(self):
        with patch.object(sys, "argv", ["MAOP", "stop"]), patch("maop.cli.cmd_stop") as mock_stop:
            cli.main()
            mock_stop.assert_called_once()

    def test_status_action(self):
        with patch.object(sys, "argv", ["MAOP", "status"]), \
             patch("maop.cli.cmd_status") as mock_status:
            cli.main()
            mock_status.assert_called_once()

    def test_run_action_with_task(self):
        with patch.object(sys, "argv", ["MAOP", "run", "--task", "fix bug"]), \
             patch("maop.cli.cmd_run") as mock_run:
            cli.main()
            mock_run.assert_called_once_with("fix bug")

    def test_run_action_without_task_exits(self):
        with patch.object(sys, "argv", ["MAOP", "run"]), patch("maop.cli.cmd_run") as mock_run:
            with pytest.raises(SystemExit) as exc_info:
                cli.main()
            assert exc_info.value.code == 1
            mock_run.assert_not_called()

    def test_validate_action(self):
        with patch.object(sys, "argv", ["MAOP", "validate"]), \
             patch("maop.cli.cmd_validate") as mock_validate:
            cli.main()
            mock_validate.assert_called_once()

    def test_health_action(self):
        with patch.object(sys, "argv", ["MAOP", "health"]), \
             patch("maop.cli.cmd_health") as mock_health:
            cli.main()
            mock_health.assert_called_once()

    def test_invalid_action_exits(self):
        with patch.object(sys, "argv", ["MAOP", "invalid_action"]), pytest.raises(SystemExit):
            cli.main()

    def test_short_task_flag(self):
        with patch.object(sys, "argv", ["MAOP", "run", "-t", "do something"]), \
             patch("maop.cli.cmd_run") as mock_run:
            cli.main()
            mock_run.assert_called_once_with("do something")

    def test_short_port_flag(self):
        with patch.object(sys, "argv", ["MAOP", "start", "-p", "9999"]), \
             patch("maop.cli.cmd_start") as mock_start:
            cli.main()
            mock_start.assert_called_once_with(9999, "127.0.0.1")


# ── cmd_start ────────────────────────────────────────────────

class TestCmdStart:
    def test_start_import_error_exits(self):
        with patch("builtins.__import__", side_effect=ImportError("no fastapi")):
            with pytest.raises(SystemExit) as exc_info:
                cli.cmd_start(port=9079)
            assert exc_info.value.code == 1

    def test_start_calls_uvicorn(self):
        """Test that cmd_start invokes uvicorn.run with correct params."""
        mock_app = MagicMock()
        mock_uvicorn = MagicMock()
        mock_server_module = MagicMock(app=mock_app)
        with patch.dict(sys.modules, {
            "maop.dashboard.server": mock_server_module,
            "uvicorn": mock_uvicorn,
        }):
            cli.cmd_start(port=8080, host="localhost")
            mock_uvicorn.run.assert_called_once()
            args, kwargs = mock_uvicorn.run.call_args
            assert kwargs["host"] == "localhost"
            assert kwargs["port"] == 8080


# ── cmd_stop ────────────────────────────────────────────────

class TestCmdStop:
    @patch("maop.cli.MAOP_ROOT")
    def test_stop_success(self, mock_root):
        mock_deploy = MagicMock()
        mock_result = MagicMock()
        mock_result.status.value = "stopped"
        mock_result.pid = 12345
        mock_deploy.stop.return_value = mock_result
        with patch.dict(sys.modules, {"maop.deploy": mock_deploy}):
            cli.cmd_stop()

    @patch("maop.cli.MAOP_ROOT")
    def test_stop_failure_exits(self, mock_root):
        mock_deploy = MagicMock()
        mock_deploy.stop.side_effect = Exception("deploy error")
        with patch.dict(sys.modules, {"maop.deploy": mock_deploy}):
            with pytest.raises(SystemExit) as exc_info:
                cli.cmd_stop()
            assert exc_info.value.code == 1


# ── cmd_status ───────────────────────────────────────────────

class TestCmdStatus:
    @patch("maop.cli.MAOP_ROOT")
    def test_status_success(self, mock_root):
        mock_deploy = MagicMock()
        mock_result = MagicMock()
        mock_result.status.value = "running"
        mock_result.pid = 999
        mock_result.components = []
        mock_deploy.status.return_value = mock_result
        with patch.dict(sys.modules, {"maop.deploy": mock_deploy}):
            cli.cmd_status()

    @patch("maop.cli.MAOP_ROOT")
    def test_status_failure_no_exit(self, mock_root):
        """cmd_status catches exceptions and prints, doesn't exit."""
        mock_deploy = MagicMock()
        mock_deploy.status.side_effect = Exception("status error")
        with patch.dict(sys.modules, {"maop.deploy": mock_deploy}):
            # Should not raise SystemExit
            cli.cmd_status()


# ── cmd_run ────────────────────────────────────────────────

class TestCmdRun:
    def test_run_import_error_exits(self):
        with patch("builtins.__import__", side_effect=ImportError("no maop_loop")):
            with pytest.raises(SystemExit) as exc_info:
                cli.cmd_run("test task")
            assert exc_info.value.code == 1


# ── cmd_validate ────────────────────────────────────────────

class TestCmdValidate:
    @patch("maop.cli.MAOP_ROOT")
    def test_validate_valid(self, mock_root):
        mock_deploy = MagicMock()
        mock_result = MagicMock()
        mock_result.valid = True
        mock_result.errors = []
        mock_result.warnings = ["minor issue"]
        mock_deploy.validate_config.return_value = mock_result
        with patch.dict(sys.modules, {"maop.deploy": mock_deploy}):
            cli.cmd_validate()

    @patch("maop.cli.MAOP_ROOT")
    def test_validate_invalid_exits(self, mock_root):
        mock_deploy = MagicMock()
        mock_result = MagicMock()
        mock_result.valid = False
        mock_result.errors = ["bad config"]
        mock_result.warnings = []
        mock_deploy.validate_config.return_value = mock_result
        with patch.dict(sys.modules, {"maop.deploy": mock_deploy}):
            with pytest.raises(SystemExit) as exc_info:
                cli.cmd_validate()
            assert exc_info.value.code == 1


# ── cmd_health ──────────────────────────────────────────────

class TestCmdHealth:
    @patch("maop.cli.MAOP_ROOT")
    def test_health_all_healthy(self, mock_root):
        mock_deploy = MagicMock()
        mock_r = MagicMock()
        mock_r.name = "db"
        mock_r.status.value = "healthy"
        mock_r.latency_ms = 1.5
        mock_r.message = "ok"
        mock_deploy.health_check.return_value = [mock_r]
        with patch.dict(sys.modules, {"maop.deploy": mock_deploy}):
            cli.cmd_health()

    @patch("maop.cli.MAOP_ROOT")
    def test_health_degraded_exits(self, mock_root):
        mock_deploy = MagicMock()
        mock_r = MagicMock()
        mock_r.name = "db"
        mock_r.status.value = "unhealthy"
        mock_r.latency_ms = 100.0
        mock_r.message = "down"
        mock_deploy.health_check.return_value = [mock_r]
        with patch.dict(sys.modules, {"maop.deploy": mock_deploy}):
            with pytest.raises(SystemExit) as exc_info:
                cli.cmd_health()
            assert exc_info.value.code == 1
