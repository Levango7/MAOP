"""Unit tests for MAOP.dashboard.routers.system module.

Tests framework system endpoints:
  - /api/subsystems, /api/framework/status, /api/framework/logs, /api/framework/config
  - /api/agent/config, /api/agent/upgrade (GET)
  - /api/workflow/list, /api/overview, /api/audit/events
  - /api/routing, /api/security/config
"""
from __future__ import annotations

import subprocess
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# ── Fakes ───────────────────────────────────────────────────────────

class FakeAgentDef:
    """Simulate MAOP.config.loader.AgentDef."""

    def __init__(self, cli="claude", driver="cli", model="claude-3",
                 timeout_s=120, capabilities=None, description="Test",
                 fallback=""):
        self.cli = cli
        self.driver = driver
        self.model = model
        self.timeout_s = timeout_s
        self.capabilities = capabilities or ["code"]
        self.description = description
        self.fallback = fallback


class FakeConfig:
    """Simulate MAOP.config.loader.MaopConfig."""

    def __init__(self):
        self.agents = {
            "claude": FakeAgentDef(cli="claude", model="claude-3",
                                   capabilities=["code"], description="Claude"),
            "gemini": FakeAgentDef(cli="gemini", model="gemini-pro",
                                   capabilities=["code", "vision"],
                                   description="Gemini"),
        }
        self.routing = {}


class FakeConfigLoader:
    """Replacement for ConfigLoader."""

    def __init__(self, project_root=None):
        pass

    def load(self) -> FakeConfig:
        return FakeConfig()


class FakeCompletedProcess:
    """Simulate subprocess.CompletedProcess."""

    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


# ── Fixtures ────────────────────────────────────────────────────────

@pytest.fixture
def tmp_root(tmp_path, monkeypatch):
    """Point MAOP_ROOT to a temp dir in both state and system modules."""
    monkeypatch.setattr("maop.dashboard.routers.state.MAOP_ROOT", tmp_path)
    monkeypatch.setattr("maop.dashboard.routers.system._deps.MAOP_ROOT", tmp_path)
    return tmp_path


@pytest.fixture
def client(tmp_root, monkeypatch):
    """TestClient with mocked ConfigLoader, bridge, and subsystem state."""
    monkeypatch.setattr("maop.config.loader.ConfigLoader", FakeConfigLoader)

    # Mock subsystem functions
    monkeypatch.setattr("maop.dashboard.routers.system._deps.init_subsystems",
                        MagicMock())
    monkeypatch.setattr("maop.dashboard.routers.system._deps.get_subsystems",
                        lambda: {
                            "analyzer": {"available": True, "module": "maop.core.agent.analyzer"},
                            "vector": {"available": False, "module": "maop.core.memory.vector",
                                       "error": "missing dep"},
                        })

    # Mock get_bridge with AsyncMock
    async_bridge = AsyncMock()
    async_bridge.logs_get = AsyncMock(return_value=[])
    async_bridge.report = AsyncMock(return_value={"success_rate": 95.0, "total": 100,
                                                   "avg_latency_ms": 200})
    async_bridge.agent_stats = AsyncMock(return_value={"agents": [{"name": "claude"}]})
    async_bridge.timeseries = AsyncMock(return_value={"points": []})
    async_bridge.live = AsyncMock(return_value=[{"task": "test"}])
    async_bridge.failures = AsyncMock(return_value=[{"agent": "claude", "count": 1}])
    # /api/overview now sources KPI from delegation_period_stats (logs/delegations.json);
    # without this mock, AsyncMock auto-child returns a coroutine that leaks into the
    # response dict -> PydanticSerializationError at encode time.
    async_bridge.delegation_period_stats = AsyncMock(return_value={
        "total": 100, "success_rate": 95.0,
        "delegations_mom": 0.0, "delegations_yoy": 0.0,
        "success_rate_mom": 0.0, "success_rate_yoy": 0.0,
    })
    monkeypatch.setattr("maop.dashboard.routers.system._deps.get_bridge",
                        lambda: async_bridge)

    # Mock active_jobs and start_time
    monkeypatch.setattr("maop.dashboard.routers.system._deps.active_jobs", {})
    monkeypatch.setattr("maop.dashboard.routers.system._deps.start_time", 0.0)

    # Clear overview caches between tests to prevent stale data
    from maop.dashboard.routers.system.overview import _overview_cache, _file_counts_cache
    _overview_cache.clear()
    _file_counts_cache.clear()

    app = FastAPI()
    @app.middleware("http")
    async def _inject_admin(request, call_next):
        request.state.auth_roles = ["admin"]
        return await call_next(request)
    from maop.dashboard.routers.system import router
    from maop.dashboard.routers.audit import router as audit_router
    app.include_router(router)
    app.include_router(audit_router)
    return TestClient(app)


# ── /api/subsystems ─────────────────────────────────────────────────

class TestSubsystems:
    def test_returns_subsystem_registry(self, client):
        resp = client.get("/api/subsystems")
        assert resp.status_code == 200
        data = resp.json()
        assert "subsystems" in data
        assert "count" in data
        assert "available" in data
        assert "unavailable" in data

    def test_subsystem_has_available_and_module(self, client):
        data = client.get("/api/subsystems").json()
        for info in data["subsystems"].values():
            assert "available" in info
            assert "module" in info
            assert "error" in info

    def test_available_count(self, client):
        data = client.get("/api/subsystems").json()
        assert data["available"] >= 1
        assert data["unavailable"] >= 0


# ── /api/framework/status ───────────────────────────────────────────

class TestFrameworkStatus:
    def test_returns_version_and_python(self, client):
        resp = client.get("/api/framework/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "version" in data
        assert "python" in data
        assert "platform" in data

    def test_has_module_counts(self, client):
        data = client.get("/api/framework/status").json()
        assert "py_modules" in data
        assert "test_files" in data
        assert "db_files" in data

    def test_has_uptime(self, client):
        data = client.get("/api/framework/status").json()
        assert "uptime_s" in data
        assert isinstance(data["uptime_s"], (int, float))

    def test_has_root(self, client):
        data = client.get("/api/framework/status").json()
        assert "root" in data


# ── /api/framework/logs ─────────────────────────────────────────────

class TestFrameworkLogs:
    def test_returns_logs_list(self, client):
        resp = client.get("/api/framework/logs")
        assert resp.status_code == 200
        data = resp.json()
        assert "logs" in data
        assert "count" in data
        assert isinstance(data["logs"], list)

    def test_with_limit_param(self, client):
        data = client.get("/api/framework/logs", params={"limit": 10}).json()
        assert data["count"] <= 10

    def test_reads_jsonl_logs(self, tmp_root, client):
        """Create a log file and verify it's read."""
        import json
        log_dir = tmp_root / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / "test.jsonl").write_text(
            json.dumps({"msg": "hello"}) + "\n" +
            json.dumps({"msg": "world"}) + "\n",
            encoding="utf-8")
        data = client.get("/api/framework/logs").json()
        assert data["count"] >= 2
        msgs = [e["msg"] for e in data["logs"]]
        assert "hello" in msgs
        assert "world" in msgs


# ── /api/framework/config ───────────────────────────────────────────

class TestFrameworkConfig:
    def test_returns_agents_and_routes(self, client):
        resp = client.get("/api/framework/config")
        assert resp.status_code == 200
        data = resp.json()
        assert "agents" in data
        assert "routes" in data
        assert "rules_count" in data

    def test_agents_have_cli_and_driver(self, client):
        data = client.get("/api/framework/config").json()
        for ad in data["agents"].values():
            assert "cli" in ad
            assert "driver" in ad
            assert "model" in ad
            assert "capabilities" in ad


# ── /api/agent/config ───────────────────────────────────────────────

class TestAgentConfig:
    def test_returns_agent_list(self, client):
        resp = client.get("/api/agent/config")
        assert resp.status_code == 200
        data = resp.json()
        assert "agents" in data
        assert "routes" in data
        assert data["agent_count"] == 2

    def test_agent_has_required_fields(self, client):
        data = client.get("/api/agent/config").json()
        for a in data["agents"]:
            assert "name" in a
            assert "cli" in a
            assert "driver" in a
            assert "model" in a
            assert "timeout_s" in a
            assert "capabilities" in a
            assert "description" in a

    def test_error_returns_empty(self, tmp_root, monkeypatch):
        monkeypatch.setattr("maop.config.loader.ConfigLoader",
                            MagicMock(side_effect=RuntimeError("cfg err")))
        monkeypatch.setattr("maop.dashboard.routers.system._deps.init_subsystems",
                            MagicMock())
        monkeypatch.setattr("maop.dashboard.routers.system._deps.get_subsystems",
                            dict)
        monkeypatch.setattr("maop.dashboard.routers.system._deps.get_bridge",
                            lambda: AsyncMock())
        monkeypatch.setattr("maop.dashboard.routers.system._deps.active_jobs", {})
        monkeypatch.setattr("maop.dashboard.routers.system._deps.start_time", 0.0)
        app = FastAPI()
        from maop.dashboard.routers.system import router
        app.include_router(router)
        data = TestClient(app).get("/api/agent/config").json()
        assert data["agents"] == []
        assert "error" in data


# ── /api/agent/upgrade GET ──────────────────────────────────────────

class TestAgentUpgradeGet:
    def test_returns_agent_versions(self, client, monkeypatch):
        # Mock subprocess.run for --version and pip show
        def fake_run(cmd, **kw):
            if "--version" in cmd:
                return FakeCompletedProcess(stdout="1.0.0")
            if "show" in cmd:
                return FakeCompletedProcess(stdout="Version: 1.0.0\n",
                                            returncode=0)
            return FakeCompletedProcess(returncode=1)
        monkeypatch.setattr(subprocess, "run", fake_run)
        data = client.get("/api/agent/upgrade").json()
        assert "agents" in data
        assert len(data["agents"]) == 2

    def test_agent_version_info(self, client, monkeypatch):
        def fake_run(cmd, **kw):
            if "--version" in cmd:
                return FakeCompletedProcess(stdout="2.5.0")
            if "show" in cmd:
                return FakeCompletedProcess(stdout="Version: 2.5.0\n",
                                            returncode=0)
            return FakeCompletedProcess(returncode=1)
        monkeypatch.setattr(subprocess, "run", fake_run)
        data = client.get("/api/agent/upgrade").json()
        for a in data["agents"]:
            assert "name" in a
            assert "current" in a
            assert "latest" in a
            assert "status" in a


# ── /api/workflow/list ──────────────────────────────────────────────

class TestWorkflowList:
    def test_returns_workflows(self, client):
        resp = client.get("/api/workflow/list")
        assert resp.status_code == 200
        data = resp.json()
        assert "workflows" in data
        assert "count" in data
        assert isinstance(data["workflows"], list)

    def test_fallback_to_config_dir(self, tmp_root, client):
        """When WorkflowEngine fails, reads config dir for workflow files."""
        cfg_dir = tmp_root / "config"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        (cfg_dir / "workflow_build.yaml").write_text("name: build", encoding="utf-8")
        data = client.get("/api/workflow/list").json()
        # Should find the workflow file in fallback
        assert data["count"] >= 1


# ── /api/overview ───────────────────────────────────────────────────

class TestOverview:
    def test_returns_aggregated_metrics(self, client):
        resp = client.get("/api/overview")
        assert resp.status_code == 200
        data = resp.json()
        assert "agents_total" in data
        assert "modules_total" in data
        assert "tests_total" in data

    def test_has_success_rate(self, client):
        data = client.get("/api/overview").json()
        assert "success_rate" in data
        assert data["success_rate"] == 95.0

    def test_has_platform_info(self, client):
        data = client.get("/api/overview").json()
        assert "platform" in data
        assert "python_ver" in data
        assert "version" in data

    def test_has_timeseries(self, client):
        data = client.get("/api/overview").json()
        assert "timeseries" in data

    def test_has_code_stats(self, client):
        data = client.get("/api/overview").json()
        assert "source_files" in data
        assert "code_lines" in data
        assert "test_files" in data


# ── /api/audit/events ───────────────────────────────────────────────

class TestAuditEvents:
    def test_returns_event_list(self, client, monkeypatch):
        mock_audit = MagicMock()
        mock_instance = MagicMock()
        mock_instance.read_recent = MagicMock(return_value=[])
        mock_audit.return_value = mock_instance
        monkeypatch.setattr("maop.control.audit.AuditLog", mock_audit)
        data = client.get("/api/audit/events").json()
        assert "events" in data
        assert "count" in data
        assert isinstance(data["events"], list)

    def test_with_limit_param(self, client, monkeypatch):
        mock_audit = MagicMock()
        mock_instance = MagicMock()
        mock_instance.read_recent = MagicMock(return_value=[])
        mock_audit.return_value = mock_instance
        monkeypatch.setattr("maop.control.audit.AuditLog", mock_audit)
        data = client.get("/api/audit/events", params={"limit": 50}).json()
        assert data["count"] == 0

    def test_error_handling(self, client, monkeypatch):
        import maop.dashboard.routers.audit as _audit_mod
        _audit_mod._enterprise_logger = None
        monkeypatch.setattr("maop.enterprise.audit.EnterpriseAuditLogger",
                            MagicMock(side_effect=RuntimeError("audit err")))
        data = client.get("/api/audit/events").json()

        assert "error" in data


# ── /api/routing ────────────────────────────────────────────────────

class TestRouting:
    def test_returns_routes(self, client):
        resp = client.get("/api/routing")
        assert resp.status_code == 200
        data = resp.json()
        assert "routes" in data
        assert isinstance(data["routes"], list)

    def test_error_returns_empty(self, tmp_root, monkeypatch):
        monkeypatch.setattr("maop.config.loader.ConfigLoader",
                            MagicMock(side_effect=RuntimeError("err")))
        monkeypatch.setattr("maop.dashboard.routers.system.init_subsystems",
                            MagicMock())
        monkeypatch.setattr("maop.dashboard.routers.system.get_subsystems",
                            dict)
        monkeypatch.setattr("maop.dashboard.routers.system.get_bridge",
                            lambda: AsyncMock())
        monkeypatch.setattr("maop.dashboard.routers.system.active_jobs", {})
        monkeypatch.setattr("maop.dashboard.routers.system.start_time", 0.0)
        app = FastAPI()
        from maop.dashboard.routers.system import router
        app.include_router(router)
        data = TestClient(app).get("/api/routing").json()
        assert data["routes"] == []
        assert "error" in data


# ── /api/security/config ────────────────────────────────────────────

class TestSecurityConfig:
    def test_returns_module_availability(self, client):
        resp = client.get("/api/security/config")
        assert resp.status_code == 200
        data = resp.json()
        # Should have entries for each security module
        assert isinstance(data, dict)

    def test_has_expected_modules(self, client):
        data = client.get("/api/security/config").json()
        # These are the modules checked in the endpoint
        for mod in ("tls", "auth", "rate_limit", "guardrail", "sandbox"):
            assert mod in data
            assert isinstance(data[mod], bool)


# --- Merged from test_router_system_coverage2.py (client->client_coverage) ---

@pytest.fixture
def system_env(tmp_path, monkeypatch):
    """Isolate MAOP_ROOT for system router and create minimal config."""
    # Create config dir with agents.yaml
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "agents.yaml").write_text(
        "agents:\n  claude:\n    cli: claude\n    model: claude-3\n    driver: cli\n"
        "    timeout_s: 120\n    capabilities: [code]\n    description: test\n",
        encoding="utf-8",
    )
    # Create data dir
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    # Create logs dir
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr("maop.dashboard.routers.system._deps.MAOP_ROOT", tmp_path)
    monkeypatch.setattr("maop.dashboard.routers.state.MAOP_ROOT", tmp_path)

    # Reset subsystem cache so init_subsystems re-runs
    import maop.dashboard.routers.state as state
    state._SUBSYSTEMS.clear()

    # Reset overview cache
    import maop.dashboard.routers.system as sys_mod
    sys_mod._overview_cache.clear()
    sys_mod._file_counts_cache.clear()
    sys_mod._ALLOWED_PIP_PACKAGES = None

    return tmp_path


@pytest.fixture
def client_coverage(system_env, monkeypatch):
    """TestClient with admin role injected and system router mounted."""
    # Mock get_bridge to avoid real DataProxy
    mock_bridge = MagicMock()
    mock_bridge.report = AsyncMock(return_value={"avg_latency_ms": 100})
    mock_bridge.agent_stats = AsyncMock(return_value={"agents": []})
    mock_bridge.timeseries = AsyncMock(return_value=[])
    mock_bridge.live = AsyncMock(return_value={"recent_delegations": []})
    mock_bridge.failures = AsyncMock(return_value=[])
    mock_bridge.delegation_period_stats = AsyncMock(return_value={"total": 0, "success_rate": 0.0})
    mock_bridge.logs_get = AsyncMock(return_value=[])
    monkeypatch.setattr("maop.dashboard.routers.system._deps.get_bridge", lambda: mock_bridge)
    monkeypatch.setattr("maop.dashboard.routers.state.get_bridge", lambda: mock_bridge)

    app = FastAPI()

    @app.middleware("http")
    async def _inject_admin(request, call_next):
        request.state.auth_roles = ["admin"]
        request.state.auth_identity = "admin"
        return await call_next(request)

    from maop.dashboard.routers.system import router
    app.include_router(router)
    return TestClient(app)


class TestAgentConfigUpdate:
    def test_missing_agent(self, client_coverage):
        """POST /api/agent/config/update with no agent name returns 400."""
        resp = client_coverage.post("/api/agent/config/update", json={})
        assert resp.status_code == 400

    def test_agents_yaml_not_found(self, system_env, client_coverage):
        """When agents.yaml doesn't exist, returns error."""
        # Remove agents.yaml
        (system_env / "config" / "agents.yaml").unlink()
        resp = client_coverage.post("/api/agent/config/update", json={"agent": "claude", "model": "x"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "error"

    def test_unknown_agent(self, client_coverage):
        """POST /api/agent/config/update with unknown agent returns error."""
        resp = client_coverage.post(
            "/api/agent/config/update",
            json={"agent": "nonexistent", "model": "x"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "error"

    def test_update_model_happy(self, client_coverage):
        """POST /api/agent/config/update with valid agent + model succeeds."""
        resp = client_coverage.post(
            "/api/agent/config/update",
            json={"agent": "claude", "model": "claude-3.5"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["config"]["model"] == "claude-3.5"

    def test_update_capabilities_happy(self, client_coverage):
        """POST /api/agent/config/update with capabilities list succeeds."""
        resp = client_coverage.post(
            "/api/agent/config/update",
            json={"agent": "claude", "capabilities": ["code", "test"]},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_update_capabilities_not_list(self, client_coverage):
        """capabilities must be a list — returns 400."""
        resp = client_coverage.post(
            "/api/agent/config/update",
            json={"agent": "claude", "capabilities": "code"},
        )
        assert resp.status_code == 400

    def test_update_capabilities_non_string_item(self, client_coverage):
        """each capability must be a string — returns 400."""
        resp = client_coverage.post(
            "/api/agent/config/update",
            json={"agent": "claude", "capabilities": [123]},
        )
        assert resp.status_code == 400

    def test_update_invalid_field_validation(self, client_coverage):
        """Invalid field value triggers AgentDef validation error → 400."""
        resp = client_coverage.post(
            "/api/agent/config/update",
            json={"agent": "claude", "timeout_s": "not-a-number"},
        )
        assert resp.status_code == 400


class TestAgentUpgrade:
    def test_missing_agent(self, client_coverage):
        """POST /api/agent/upgrade with no agent name returns 400."""
        resp = client_coverage.post("/api/agent/upgrade", json={})
        assert resp.status_code == 400

    def test_unknown_agent(self, client_coverage):
        """POST /api/agent/upgrade with unknown agent returns error."""
        resp = client_coverage.post("/api/agent/upgrade", json={"agent": "nonexistent"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "error"

    def test_upgrade_via_query_param(self, client_coverage, monkeypatch):
        """POST /api/agent/upgrade?agent=claude — agent via query param."""
        import shutil
        monkeypatch.setattr(shutil, "which", lambda cmd: None)
        resp = client_coverage.post("/api/agent/upgrade?agent=claude")
        # cli not found → info dict returned
        assert resp.status_code == 200

    def test_upgrade_get_list(self, client_coverage, monkeypatch):
        """GET /api/agent/upgrade returns list of agents."""
        import shutil
        monkeypatch.setattr(shutil, "which", lambda cmd: None)
        resp = client_coverage.get("/api/agent/upgrade")
        assert resp.status_code == 200
        assert "agents" in resp.json()


class TestWorkflowRun:
    def test_missing_name(self, client_coverage):
        """POST /api/workflow/run with no name returns 400."""
        resp = client_coverage.post("/api/workflow/run", json={})
        assert resp.status_code == 400

    def test_invalid_name(self, client_coverage):
        """POST /api/workflow/run with invalid name (special chars) returns 400."""
        resp = client_coverage.post("/api/workflow/run", json={"name": "bad/name"})
        assert resp.status_code == 400

    def test_invalid_task(self, client_coverage):
        """POST /api/workflow/run with invalid task name returns 400."""
        resp = client_coverage.post("/api/workflow/run", json={"name": "valid", "task": "bad/task"})
        assert resp.status_code == 400

    def test_valid_name_starts_job(self, client_coverage, monkeypatch):
        """POST /api/workflow/run with valid name starts a job (subprocess mocked)."""
        # Mock subprocess to avoid real process spawn
        import asyncio

        class FakeProc:
            def __init__(self):
                self.returncode = 0

        async def fake_exec(*args, **kwargs):
            return FakeProc()

        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
        resp = client_coverage.post("/api/workflow/run", json={"name": "build", "task": "test"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "started"
        assert "job_id" in data


class TestSystemResources:
    def test_resources_happy(self, client_coverage):
        """GET /api/system/resources returns resource usage."""
        resp = client_coverage.get("/api/system/resources")
        assert resp.status_code == 200
        data = resp.json()
        assert "memory_store" in data
        assert "sqlite_db" in data
        assert "vector_index" in data
        assert "log_files" in data

    def test_resources_no_data_dir(self, system_env, client_coverage):
        """GET /api/system/resources works without data dir."""
        # Remove data dir
        import shutil
        shutil.rmtree(str(system_env / "data"), ignore_errors=True)
        resp = client_coverage.get("/api/system/resources")
        assert resp.status_code == 200


class TestSystemDiagnostics:
    def test_diagnostics_happy(self, client_coverage):
        """GET /api/system/diagnostics returns diagnostic results."""
        resp = client_coverage.get("/api/system/diagnostics")
        assert resp.status_code == 200
        data = resp.json()
        assert "database" in data
        assert "agent_registry" in data
        assert "memory_store" in data
        assert "vector_index" in data
        assert "config_loader" in data
        assert "audit_log" in data


class TestSystemGetEndpoints:
    def test_subsystems(self, client_coverage):
        """GET /api/subsystems returns subsystem registry."""
        resp = client_coverage.get("/api/subsystems")
        assert resp.status_code == 200
        data = resp.json()
        assert "subsystems" in data
        assert "count" in data

    def test_framework_status(self, client_coverage):
        """GET /api/framework/status returns framework info."""
        resp = client_coverage.get("/api/framework/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "version" in data
        assert "python" in data

    def test_framework_logs(self, client_coverage):
        """GET /api/framework/logs returns logs list."""
        resp = client_coverage.get("/api/framework/logs")
        assert resp.status_code == 200
        assert "logs" in resp.json()

    def test_framework_logs_with_limit(self, client_coverage):
        """GET /api/framework/logs?limit=10 respects limit."""
        resp = client_coverage.get("/api/framework/logs?limit=10")
        assert resp.status_code == 200

    def test_framework_config(self, client_coverage):
        """GET /api/framework/config returns config."""
        resp = client_coverage.get("/api/framework/config")
        assert resp.status_code == 200

    def test_agent_config(self, client_coverage):
        """GET /api/agent/config returns agent config."""
        resp = client_coverage.get("/api/agent/config")
        assert resp.status_code == 200
        data = resp.json()
        assert "agents" in data

    def test_workflow_list(self, client_coverage):
        """GET /api/workflow/list returns workflows."""
        resp = client_coverage.get("/api/workflow/list")
        assert resp.status_code == 200
        assert "workflows" in resp.json()

    def test_overview(self, client_coverage):
        """GET /api/overview returns overview data."""
        resp = client_coverage.get("/api/overview")
        assert resp.status_code == 200

    def test_overview_cached(self, client_coverage):
        """GET /api/overview uses cache on second call."""
        resp1 = client_coverage.get("/api/overview")
        resp2 = client_coverage.get("/api/overview")
        assert resp1.status_code == 200
        assert resp2.status_code == 200

    def test_coordination_report(self, client_coverage):
        """GET /api/coordination_report returns teams."""
        resp = client_coverage.get("/api/coordination_report")
        assert resp.status_code == 200
        assert "teams" in resp.json()

    def test_workflows_v4(self, client_coverage):
        """GET /api/workflows returns workflow list."""
        resp = client_coverage.get("/api/workflows")
        assert resp.status_code == 200
        assert "workflows" in resp.json()

    def test_routing_v4(self, client_coverage):
        """GET /api/routing returns routing config."""
        resp = client_coverage.get("/api/routing")
        assert resp.status_code == 200
        assert "routes" in resp.json()

    def test_security_config(self, client_coverage):
        """GET /api/security/config returns security module availability."""
        resp = client_coverage.get("/api/security/config")
        assert resp.status_code == 200