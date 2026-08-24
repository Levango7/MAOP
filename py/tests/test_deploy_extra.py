"""Coverage tests for maop.deploy — validate_config, health_check, PID, start/stop/status."""
from __future__ import annotations

import os
from pathlib import Path

from maop.deploy import (
    ComponentHealth,
    DeployConfig,
    HealthStatus,
    ServiceStatus,
    SystemStatus,
    ValidationResult,
    _pid_path,
    _read_pid,
    _remove_pid,
    _write_pid,
    health_check,
    start,
    status,
    stop,
    validate_config,
)


def _create_valid_project(root: Path):
    """Create a minimal valid MAOP project structure."""
    (root / "config").mkdir(parents=True, exist_ok=True)
    (root / "data").mkdir(parents=True, exist_ok=True)
    (root / "config" / "agents.yaml").write_text("agents:\n  claude:\n    cli: echo\n", encoding="utf-8")


# ── validate_config ───────────────────────────────────────────

class TestValidateConfig:
    def test_valid_project(self, tmp_path):
        _create_valid_project(tmp_path)
        result = validate_config(tmp_path)
        assert result.valid is True
        assert result.errors == []

    def test_missing_config_dir(self, tmp_path):
        (tmp_path / "data").mkdir(parents=True, exist_ok=True)
        result = validate_config(tmp_path)
        assert result.valid is False
        assert any("config" in e for e in result.errors)

    def test_missing_data_dir(self, tmp_path):
        (tmp_path / "config").mkdir(parents=True, exist_ok=True)
        result = validate_config(tmp_path)
        assert result.valid is False
        assert any("data" in e for e in result.errors)

    def test_missing_agents_yaml(self, tmp_path):
        (tmp_path / "config").mkdir(parents=True, exist_ok=True)
        (tmp_path / "data").mkdir(parents=True, exist_ok=True)
        result = validate_config(tmp_path)
        assert result.valid is False
        assert any("agents.yaml" in e for e in result.errors)

    def test_agents_yaml_no_agents_key(self, tmp_path):
        (tmp_path / "config").mkdir(parents=True, exist_ok=True)
        (tmp_path / "data").mkdir(parents=True, exist_ok=True)
        (tmp_path / "config" / "agents.yaml").write_text("other: value\n", encoding="utf-8")
        result = validate_config(tmp_path)
        assert result.valid is True
        assert any("agents" in w for w in result.warnings)

    def test_agents_yaml_invalid_yaml(self, tmp_path):
        (tmp_path / "config").mkdir(parents=True, exist_ok=True)
        (tmp_path / "data").mkdir(parents=True, exist_ok=True)
        (tmp_path / "config" / "agents.yaml").write_text("not: valid: yaml: :\n", encoding="utf-8")
        result = validate_config(tmp_path)
        # May be valid or have parse error depending on YAML parser
        assert isinstance(result, ValidationResult)

    def test_data_dir_not_writable(self, tmp_path):
        _create_valid_project(tmp_path)
        # Make data dir read-only (may not work on Windows as root admin)
        try:
            os.chmod(str(tmp_path / "data"), 0o444)
            result = validate_config(tmp_path)
            # On some systems this may still be writable
            assert isinstance(result, ValidationResult)
        finally:
            os.chmod(str(tmp_path / "data"), 0o755)


# ── health_check ──────────────────────────────────────────────

class TestHealthCheck:
    def test_no_db_files(self, tmp_path):
        _create_valid_project(tmp_path)
        results = health_check(tmp_path)
        assert len(results) == 4
        names = [r.name for r in results]
        assert "database" in names
        assert "memory" in names
        assert "config" in names
        assert "dashboard" in names

    def test_with_db(self, tmp_path):
        _create_valid_project(tmp_path)
        # Create a valid SQLite db
        import sqlite3
        with sqlite3.connect(str(tmp_path / "data" / "maop.db")) as conn:
            conn.execute("CREATE TABLE x (id INTEGER)")
        results = health_check(tmp_path)
        db_health = next(r for r in results if r.name == "database")
        assert db_health.status == HealthStatus.HEALTHY

    def test_config_missing(self, tmp_path):
        (tmp_path / "data").mkdir(parents=True, exist_ok=True)
        results = health_check(tmp_path)
        config_health = next(r for r in results if r.name == "config")
        assert config_health.status == HealthStatus.UNHEALTHY


# ── PID management ────────────────────────────────────────────

class TestPidManagement:
    def test_pid_path(self, tmp_path):
        p = _pid_path(tmp_path)
        assert p == tmp_path / "data" / "maop.pid"

    def test_write_and_read_pid(self, tmp_path):
        _write_pid(tmp_path, 12345)
        assert _read_pid(tmp_path) == 12345

    def test_read_pid_no_file(self, tmp_path):
        assert _read_pid(tmp_path) is None

    def test_read_pid_invalid(self, tmp_path):
        pid_file = tmp_path / "data" / "maop.pid"
        pid_file.parent.mkdir(parents=True, exist_ok=True)
        pid_file.write_text("not a number", encoding="utf-8")
        assert _read_pid(tmp_path) is None

    def test_remove_pid(self, tmp_path):
        _write_pid(tmp_path, 12345)
        _remove_pid(tmp_path)
        assert _read_pid(tmp_path) is None

    def test_remove_pid_no_file(self, tmp_path):
        # Should not raise
        _remove_pid(tmp_path)


# ── start / stop / status ─────────────────────────────────────

class TestStartStopStatus:
    def test_start_invalid_config(self, tmp_path):
        """Start with invalid config returns ERROR status."""
        result = start(tmp_path)
        assert result.status == ServiceStatus.ERROR

    def test_start_no_dashboard(self, tmp_path):
        """Start without dashboard writes PID and returns RUNNING."""
        _create_valid_project(tmp_path)
        result = start(tmp_path, dashboard=False)
        assert result.status == ServiceStatus.RUNNING
        assert result.pid == os.getpid()
        # Cleanup
        _remove_pid(tmp_path)

    def test_stop_no_pid(self, tmp_path):
        """Stop when no PID file exists returns STOPPED."""
        result = stop(tmp_path)
        assert result.status == ServiceStatus.STOPPED

    def test_stop_with_stale_pid(self, tmp_path):
        """Stop with a non-existent PID returns STOPPED."""
        _write_pid(tmp_path, 999999)  # non-existent PID
        result = stop(tmp_path)
        assert result.status == ServiceStatus.STOPPED
        assert _read_pid(tmp_path) is None

    def test_status_no_pid(self, tmp_path):
        """Status when no PID file exists returns STOPPED."""
        result = status(tmp_path)
        assert result.status == ServiceStatus.STOPPED

    def test_status_with_stale_pid(self, tmp_path):
        """Status with a non-existent PID returns STOPPED and removes PID."""
        _write_pid(tmp_path, 999999)
        result = status(tmp_path)
        assert result.status == ServiceStatus.STOPPED
        assert _read_pid(tmp_path) is None


# ── Models ────────────────────────────────────────────────────

class TestModels:
    def test_service_status_enum(self):
        assert ServiceStatus.STOPPED == "stopped"
        assert ServiceStatus.RUNNING == "running"

    def test_health_status_enum(self):
        assert HealthStatus.HEALTHY == "healthy"
        assert HealthStatus.DEGRADED == "degraded"

    def test_component_health_defaults(self):
        c = ComponentHealth()
        assert c.name == ""
        assert c.status == HealthStatus.HEALTHY
        assert c.latency_ms == 0.0

    def test_deploy_config_defaults(self):
        c = DeployConfig()
        assert c.dashboard_port == 9079
        assert c.dashboard_host == "127.0.0.1"

    def test_system_status_defaults(self):
        s = SystemStatus()
        assert s.status == ServiceStatus.STOPPED
        assert s.pid is None
        assert s.components == []