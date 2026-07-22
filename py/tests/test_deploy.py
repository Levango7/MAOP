"""Tests for MAOP.deploy — Deployment entry point."""

from __future__ import annotations



from maop.deploy import (
    ServiceStatus,
    HealthStatus,
    ComponentHealth,
    DeployConfig,
    ValidationResult,
    SystemStatus,
    validate_config,
    health_check,
    _pid_path,
    _read_pid,
    _write_pid,
    _remove_pid,
)


class TestServiceStatus:
    def test_values(self):
        assert ServiceStatus.STOPPED == "stopped"
        assert ServiceStatus.RUNNING == "running"
        assert ServiceStatus.ERROR == "error"


class TestHealthStatus:
    def test_values(self):
        assert HealthStatus.HEALTHY == "healthy"
        assert HealthStatus.DEGRADED == "degraded"
        assert HealthStatus.UNHEALTHY == "unhealthy"


class TestComponentHealth:
    def test_default(self):
        h = ComponentHealth()
        assert h.name == ""
        assert h.status == HealthStatus.HEALTHY
        assert h.message == ""
        assert h.latency_ms == 0.0
        assert h.details == {}

    def test_with_values(self):
        h = ComponentHealth(name="db", status=HealthStatus.UNHEALTHY, message="timeout")
        assert h.name == "db"
        assert h.status == HealthStatus.UNHEALTHY


class TestDeployConfig:
    def test_default(self):
        c = DeployConfig()
        assert c.root_dir == ""
        assert c.dashboard_port == 9079
        assert c.dashboard_host == "127.0.0.1"
        assert c.log_level == "INFO"
        assert c.workers == 1
        assert c.pid_file == ""

    def test_custom(self):
        c = DeployConfig(root_dir="/tmp/MAOP", dashboard_port=8080, workers=4)
        assert c.root_dir == "/tmp/MAOP"
        assert c.dashboard_port == 8080
        assert c.workers == 4


class TestValidationResult:
    def test_default(self):
        r = ValidationResult()
        assert r.valid is True
        assert r.errors == []
        assert r.warnings == []

    def test_with_errors(self):
        r = ValidationResult(valid=False, errors=["bad config"])
        assert r.valid is False
        assert r.errors == ["bad config"]


class TestSystemStatus:
    def test_default(self):
        s = SystemStatus()
        assert s.status == ServiceStatus.STOPPED
        assert s.pid is None
        assert s.uptime_s == 0.0
        assert s.components == []


class TestPidManagement:
    def test_pid_path(self, tmp_path):
        p = _pid_path(str(tmp_path))
        assert "maop.pid" in str(p)

    def test_write_and_read_pid(self, tmp_path):
        _write_pid(str(tmp_path), 12345)
        pid = _read_pid(str(tmp_path))
        assert pid == 12345

    def test_read_nonexistent_pid(self, tmp_path):
        pid = _read_pid(str(tmp_path / "nonexistent"))
        assert pid is None

    def test_remove_pid(self, tmp_path):
        _write_pid(str(tmp_path), 999)
        p = _pid_path(str(tmp_path))
        assert p.exists()
        _remove_pid(str(tmp_path))
        assert not p.exists()

    def test_remove_nonexistent_pid(self, tmp_path):
        # Should not raise
        _remove_pid(str(tmp_path / "nonexistent"))


class TestValidateConfig:
    def test_validate_valid_config(self, tmp_path):
        result = validate_config(str(tmp_path))
        assert isinstance(result, ValidationResult)

    def test_validate_empty_root(self):
        result = validate_config(".")
        assert isinstance(result, ValidationResult)


class TestHealthCheck:
    def test_health_check_returns_list(self, tmp_path):
        components = health_check(str(tmp_path))
        assert isinstance(components, list)
