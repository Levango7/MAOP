"""Coverage tests for maop.dashboard.routers.agents — Agent Platform Management API.

Covers all 23 endpoints under /api/agents with happy + error paths using
FastAPI TestClient and mocked registry/scanner/repair/memory/evolution/matcher
factories. No real CLI subprocess or DB is invoked.
"""
from __future__ import annotations

import asyncio
import shutil
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from maop.core.agent.evolution.agent_evolution import EvolutionResult, EvolutionSuggestion
from maop.core.agent.lifecycle.agent_registry import HealthCheckResult, RegisteredAgent
from maop.core.agent.lifecycle.agent_repair import DiagnosisResult, RepairResult
from maop.core.agent.lifecycle.agent_scanner import AgentStatus, ScannedAgent

# ── Fakes ───────────────────────────────────────────────────────────

class FakeProc:
    """Minimal asyncio subprocess stand-in for upgrade endpoints."""

    def __init__(self, returncode: int = 0, stdout: bytes = b"", stderr: bytes = b""):
        self.returncode = returncode
        self._stdout = stdout
        self._stderr = stderr

    async def communicate(self):
        return self._stdout, self._stderr

    async def wait(self):
        return self.returncode

    def kill(self) -> None:
        pass


def _agent(name: str = "claude", **kw) -> RegisteredAgent:
    defaults = {
        "cli_path": "/usr/bin/claude",
        "provider": "anthropic",
        "capabilities": ["code"],
        "description": "Claude agent",
        "model": "claude-3",
        "driver": "cli",
        "enabled": True,
    }
    defaults.update(kw)
    return RegisteredAgent(name=name, **defaults)


def _score(name: str = "claude", score: float = 0.9) -> SimpleNamespace:
    return SimpleNamespace(model_dump=lambda: {"name": name, "score": score})


# ── Fixtures ────────────────────────────────────────────────────────

@pytest.fixture
def agents_env(tmp_path, monkeypatch):
    """Point agents router at a temp root and replace all factory functions."""
    monkeypatch.setattr("maop.dashboard.routers.agents._deps.MAOP_ROOT", tmp_path)

    registry = MagicMock()
    registry.list_agents = MagicMock(return_value=[_agent("claude"), _agent("gemini", provider="google")])
    registry.get_agent = MagicMock(side_effect=lambda n: _agent(n) if n in ("claude", "gemini") else None)
    registry.health_check = MagicMock(return_value=HealthCheckResult(agent_name="claude", healthy=True, latency_ms=12, version="1.0"))
    registry.health_check_all = MagicMock(return_value=[HealthCheckResult(agent_name="claude", healthy=True)])
    registry.enable = MagicMock(return_value=True)
    registry.disable = MagicMock(return_value=True)
    registry.register = MagicMock()
    registry.unregister = MagicMock(return_value=True)
    registry.get_health_log = MagicMock(return_value=[{"ts": "2026-01-01", "healthy": True}])

    scanner = MagicMock()
    # scan() really returns ScannedAgent, not RegisteredAgent — the stub has to
    # match, or the response_model silently defaults fields the UI reads.
    scanner.scan = MagicMock(return_value=[ScannedAgent(
        name="claude",
        cli_path="/usr/bin/claude",
        version="1.0",
        status=AgentStatus.AVAILABLE,
        capabilities=["code"],
        provider="anthropic",
    )])
    scanner.sync_from_scanner = MagicMock(return_value=1)
    scanner.unregister = MagicMock()

    repair = MagicMock()
    repair.diagnose = AsyncMock(return_value=DiagnosisResult(agent_name="claude", cli_exists=True, overall_status="healthy"))
    repair.repair = AsyncMock(return_value=RepairResult(agent_name="claude", success=True, actions_taken=["install"]))

    memory = MagicMock()
    # Shapes copied from AgentMemory._row_to_dict / .summarize.
    memory.retrieve = MagicMock(return_value=[{
        "id": 1,
        "agent_name": "claude",
        "memory_type": "interaction",
        "content": {"k": "v"},
        "metadata": {"source": "test"},
        "importance": 0.5,
        "created_at": "2026-01-01T00:00:00+00:00",
    }])
    memory.store = MagicMock(return_value=1)
    memory.forget = MagicMock(return_value=1)
    memory.summarize = MagicMock(return_value={
        "agent_name": "claude",
        "total_memories": 1,
        "by_type": {"interaction": 1},
        "last_activity": "2026-01-01T00:00:00+00:00",
        "avg_importance": 0.5,
        "top_error_patterns": [{"pattern": {"k": "v"}, "frequency": 1}],
        "evolution_count": 0,
    })

    evolution = MagicMock()
    evolution.evolve = AsyncMock(return_value=EvolutionResult(
        agent_name="claude",
        suggestions=[EvolutionSuggestion(
            category="performance", priority="medium", description="raise timeout",
            action="manual_required", changes={"timeout_s": 300}, confidence=0.6,
        )],
        auto_applied=[{"category": "preference", "changes": {}}],
        errors=[],
        summary="1 suggestion",
    ))
    evolution.get_status = MagicMock(return_value={"last_run": "2026-01-01", "applied": 0})

    matcher = MagicMock()
    matcher.match = MagicMock(return_value=[_score()])

    monkeypatch.setattr("maop.dashboard.routers.agents._deps._get_registry", lambda: registry)
    monkeypatch.setattr("maop.dashboard.routers.agents._deps._get_scanner", lambda: scanner)
    monkeypatch.setattr("maop.dashboard.routers.agents._deps._get_repair", lambda: repair)
    monkeypatch.setattr("maop.dashboard.routers.agents._deps._get_memory", lambda: memory)
    monkeypatch.setattr("maop.dashboard.routers.agents._deps._get_evolution", lambda: evolution)
    monkeypatch.setattr("maop.dashboard.routers.agents._deps._get_matcher", lambda: matcher)
    # _get_agent_config returns an object with a .cli attribute (AgentDef-like).
    monkeypatch.setattr(
        "maop.dashboard.routers.agents._deps._get_agent_config",
        lambda name: SimpleNamespace(cli="claude", model="claude-3") if name == "claude" else None,
    )

    return SimpleNamespace(
        registry=registry, scanner=scanner, repair=repair, memory=memory,
        evolution=evolution, matcher=matcher, root=tmp_path,
    )


@pytest.fixture
def client(agents_env, monkeypatch):
    """TestClient with admin role injected and agents router mounted."""
    # Avoid real subprocess / CLI lookups in upgrade endpoints.
    monkeypatch.setattr(shutil, "which", lambda cmd: f"/usr/bin/{cmd}" if cmd else None)

    app = FastAPI()

    @app.middleware("http")
    async def _inject_admin(request, call_next):
        request.state.auth_roles = ["admin"]
        return await call_next(request)

    from maop.dashboard.routers.agents import router
    app.include_router(router)
    return TestClient(app)


@pytest.fixture
def no_cli_client(agents_env, monkeypatch):
    """TestClient where no CLI binary is found on PATH (upgrade error paths)."""
    monkeypatch.setattr(shutil, "which", lambda cmd: None)

    app = FastAPI()

    @app.middleware("http")
    async def _inject_admin(request, call_next):
        request.state.auth_roles = ["admin"]
        return await call_next(request)

    from maop.dashboard.routers.agents import router
    app.include_router(router)
    return TestClient(app)


# ── List / Routes / Match ───────────────────────────────────────────

class TestListAgents:
    def test_list_default(self, client):
        resp = client.get("/api/agents")
        assert resp.status_code == 200
        data = resp.json()
        assert "agents" in data
        assert len(data["agents"]) == 2

    def test_list_enabled_only(self, client):
        data = client.get("/api/agents", params={"enabled_only": "true"}).json()
        assert isinstance(data["agents"], list)

    def test_list_capability_filter(self, client):
        data = client.get("/api/agents", params={"capability": "code"}).json()
        assert isinstance(data["agents"], list)


class TestAgentRoutes:
    def test_routes_returned(self, client):
        resp = client.get("/api/agents/routes")
        assert resp.status_code == 200
        data = resp.json()
        assert "routes" in data
        assert "count" in data
        assert isinstance(data["routes"], list)

    def test_routes_with_yaml(self, agents_env, client):
        """When agents.yaml has a routing block, routes are built from it."""
        cfg_dir = agents_env.root / "config"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        (cfg_dir / "agents.yaml").write_text(
            "agents:\n  claude:\n    cli: claude\n    model: claude-3\n"
            "routing:\n  code:\n    primary: claude\n    fallback: gemini\n",
            encoding="utf-8",
        )
        data = client.get("/api/agents/routes").json()
        assert data["count"] >= 1
        assert data["routes"][0]["capability"] == "code"


class TestMatchAgents:
    def test_match_happy(self, client):
        resp = client.get("/api/agents/match", params={"task": "write code", "requirements": "code"})
        assert resp.status_code == 200
        data = resp.json()
        assert "matches" in data
        assert len(data["matches"]) == 1

    def test_match_missing_task(self, client):
        # `task` is Query(...) (required) → FastAPI 422.
        resp = client.get("/api/agents/match")
        assert resp.status_code == 422


# ── Single agent ────────────────────────────────────────────────────

class TestGetAgent:
    def test_found(self, client):
        resp = client.get("/api/agents/claude")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["agent"]["name"] == "claude"

    def test_not_found(self, client):
        resp = client.get("/api/agents/unknown")
        assert resp.status_code == 404
        assert resp.json()["status"] == "error"


# ── Scan / Health ───────────────────────────────────────────────────

class TestScan:
    def test_scan(self, client):
        resp = client.post("/api/agents/scan")
        assert resp.status_code == 200
        data = resp.json()
        assert data["scanned"] == 1
        assert "synced" in data
        # Agents.vue renders name/status/version/cli_path from each scanned agent.
        assert set(data["agents"][0]) >= {"name", "status", "version", "cli_path"}
        assert data["agents"][0]["status"] == "available"


class TestPresets:
    def test_presets_carry_the_register_payload(self, client):
        resp = client.get("/api/agents/presets")
        assert resp.status_code == 200
        presets = resp.json()["presets"]
        assert presets, "presets must not be empty — loadPresets() no-ops otherwise"
        # Agents.vue forwards exactly these keys to POST /api/agents/register.
        for preset in presets:
            assert set(preset) >= {
                "name", "cli", "capabilities", "description",
                "model", "driver", "cli_args", "timeout_s",
            }


class TestHealthCheck:
    def test_single(self, client):
        resp = client.post("/api/agents/claude/health-check")
        assert resp.status_code == 200
        assert "result" in resp.json()

    def test_all(self, client):
        resp = client.post("/api/agents/health-check-all")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data["results"], list)


class TestEnableDisable:
    def test_enable(self, client):
        resp = client.post("/api/agents/claude/enable")
        assert resp.status_code == 200
        assert resp.json()["enabled"] is True

    def test_disable(self, client):
        resp = client.post("/api/agents/claude/disable")
        assert resp.status_code == 200
        assert resp.json()["disabled"] is True


# ── Register / Unregister ───────────────────────────────────────────

class TestRegister:
    def test_register_synced(self, agents_env, client):
        cfg_dir = agents_env.root / "config"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        (cfg_dir / "agents.yaml").write_text(
            "agents:\n  claude:\n    cli: claude\n", encoding="utf-8"
        )
        resp = client.post("/api/agents/register", json={
            "name": "newbot", "cli_path": "/bin/newbot", "capabilities": ["code"],
            "provider": "test", "model": "m1", "driver": "cli",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["agent"]["name"] == "newbot"
        assert data["synced_to_yaml"] is True

    def test_register_no_yaml(self, client):
        resp = client.post("/api/agents/register", json={"name": "newbot2"})
        assert resp.status_code == 200
        assert resp.json()["synced_to_yaml"] is False

    def test_register_invalid_timeout(self, client):
        # timeout_s must be 1..3600 → 0 violates constraint → 422.
        resp = client.post("/api/agents/register", json={"name": "x", "timeout_s": 0})
        assert resp.status_code == 422


class TestUnregister:
    def test_unregister(self, client):
        resp = client.delete("/api/agents/claude")
        assert resp.status_code == 200
        data = resp.json()
        assert data["deleted"] is True

    def test_unregister_not_in_registry(self, agents_env, client):
        agents_env.registry.unregister = MagicMock(return_value=False)
        resp = client.delete("/api/agents/ghost")
        assert resp.status_code == 200
        assert resp.json()["deleted"] is False
        assert "not found in registry" in resp.json()["errors"]


# ── Health log / Diagnose / Repair ──────────────────────────────────

class TestHealthLog:
    def test_health_log(self, client):
        resp = client.get("/api/agents/claude/health-log")
        assert resp.status_code == 200
        assert "log" in resp.json()


class TestDiagnose:
    def test_diagnose(self, client):
        resp = client.get("/api/agents/claude/diagnose")
        assert resp.status_code == 200
        # repairAgent() short-circuits on diagnosis.overall_status.
        assert resp.json()["diagnosis"]["overall_status"] == "healthy"


class TestRepair:
    def test_repair(self, client):
        resp = client.post("/api/agents/claude/repair")
        assert resp.status_code == 200
        result = resp.json()["result"]
        # repairAgent() reads success / actions_taken / errors.
        assert result["success"] is True
        assert result["actions_taken"] == ["install"]
        assert result["errors"] == []


# ── Upgrade status / check / run ────────────────────────────────────

class TestUpgradeStatus:
    def test_status(self, client, monkeypatch):
        # ConfigLoader is imported inside the endpoint; patch at source.

        class FakeAD:
            cli = "claude"

        class FakeCfg:
            agents = {"claude": FakeAD()}  # noqa: RUF012

        class FakeLoader:
            def __init__(self, project_root=None):
                pass

            def load(self):
                return FakeCfg()

        monkeypatch.setattr("maop.config.loader.ConfigLoader", FakeLoader)
        # create_subprocess_exec is used for --version / pip show.
        async def fake_exec(*a, **kw):
            return FakeProc(0, b"1.0.0\n", b"")
        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
        resp = client.get("/api/agents/upgrade/status")
        assert resp.status_code == 200
        assert "agents" in resp.json()

    def test_status_config_error(self, client, monkeypatch):
        monkeypatch.setattr(
            "maop.config.loader.ConfigLoader",
            MagicMock(side_effect=RuntimeError("cfg")),
        )
        resp = client.get("/api/agents/upgrade/status")
        assert resp.status_code == 200
        assert resp.json()["agents"] == []


class TestUpgradeCheck:
    def test_agent_not_in_config(self, no_cli_client):
        # _get_agent_config returns None for non-"claude" names.
        # Batch3C: error returns 404 instead of 200.
        resp = no_cli_client.get("/api/agents/ghost/upgrade/check")
        assert resp.status_code == 404
        body = resp.json()
        assert body["status"] == "error"

    def test_check_happy_pip(self, client, monkeypatch):
        async def fake_exec(*a, **kw):
            # pip show succeeds with Version line; pip index versions returns list.
            return FakeProc(0, b"Version: 1.2.3\nName: claude\n", b"")
        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
        resp = client.get("/api/agents/claude/upgrade/check")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["install_method"] == "pip"
        # Upgrade panel reads these five; the error branch leaves them defaulted.
        for key in ("current_version", "latest_version", "update_available", "release_notes", "agent"):
            assert key in body, key


class TestUpgradeRun:
    def test_upgrade_agent_not_in_config(self, no_cli_client):
        # Batch3C: error returns 404 instead of 200.
        resp = no_cli_client.post("/api/agents/ghost/upgrade")
        assert resp.status_code == 404
        assert resp.json()["status"] == "error"

    def test_upgrade_pip_success(self, client, monkeypatch):
        calls = {"n": 0}

        async def fake_exec(*a, **kw):
            calls["n"] += 1
            # First call: pip show (rc=0). Second: pip install --upgrade (rc=0).
            return FakeProc(0, b"Version: 1.0.0\n", b"")

        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
        resp = client.post("/api/agents/claude/upgrade")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["info"]["upgrade_status"] == "success"


# ── Memory ──────────────────────────────────────────────────────────

class TestAgentMemory:
    def test_get_memory(self, client):
        resp = client.get("/api/agents/claude/memory")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        # The memory list renders these per record; a model that silently
        # defaults them would show blank rows instead of failing loudly.
        assert data["memories"][0] == {
            "id": 1,
            "agent_name": "claude",
            "memory_type": "interaction",
            "content": {"k": "v"},
            "metadata": {"source": "test"},
            "importance": 0.5,
            "created_at": "2026-01-01T00:00:00+00:00",
        }

    def test_store_memory(self, client):
        resp = client.post("/api/agents/claude/memory", json={
            "memory_type": "interaction", "content": {"k": "v"},
        })
        assert resp.status_code == 200
        assert resp.json()["status"] == "stored"

    def test_clear_all(self, client):
        resp = client.delete("/api/agents/claude/memory")
        assert resp.status_code == 200
        assert resp.json()["deleted"] == 1

    def test_clear_one(self, client):
        resp = client.delete("/api/agents/claude/memory", params={"memory_id": 1})
        assert resp.status_code == 200
        assert resp.json()["deleted"] == 1

    def test_summary(self, client):
        resp = client.get("/api/agents/claude/memory/summary")
        assert resp.status_code == 200
        summary = resp.json()["summary"]
        # Memory panel iterates by_type and shows total_memories/evolution_count.
        assert summary["by_type"] == {"interaction": 1}
        assert summary["total_memories"] == 1
        assert summary["evolution_count"] == 0

    def test_store_invalid_importance(self, client):
        # importance must be 0..1 → 2 violates → 422.
        resp = client.post("/api/agents/claude/memory", json={
            "memory_type": "x", "importance": 2.0,
        })
        assert resp.status_code == 422


# ── Evolution ───────────────────────────────────────────────────────

class TestEvolution:
    def test_evolve(self, client):
        resp = client.post("/api/agents/claude/evolve")
        assert resp.status_code == 200
        result = resp.json()["result"]
        # Evolution panel: summary text, suggestions list, auto_applied count.
        assert result["summary"] == "1 suggestion"
        assert result["suggestions"][0]["description"] == "raise timeout"
        assert len(result["auto_applied"]) == 1
        assert result["errors"] == []
        assert result["agent_name"] == "claude"

    def test_status(self, client):
        resp = client.get("/api/agents/claude/evolution-status")
        assert resp.status_code == 200
        assert "status" in resp.json()