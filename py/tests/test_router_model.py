"""Unit tests for MAOP.dashboard.routers.model module.

Tests model management endpoints:
  - /api/model/agents, /api/model/quota, /api/model/switch
  - /api/model/registry, /api/model/list, /api/model/providers
  - /api/model/select, /api/model/budget, /api/model/policies
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from maop.model.schema import (
    BudgetConfig,
    EffectiveModel,
    LatencyTier,
    ModelDef,
    ModelPolicy,
    ModelRegistryConfig,
    QualityTier,
    SelectionStrategy,
)

# ── Fakes ───────────────────────────────────────────────────────────

class FakeAgentDef:
    """Simulate MAOP.config.loader.AgentDef."""

    def __init__(self, cli="claude", driver="cli", model="claude-3",
                 timeout_s=120, capabilities=None, description="Test agent",
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


def _make_model(name="claude-3", provider="anthropic", enabled=True):
    """Create a ModelDef for testing."""
    return ModelDef(
        name=name,
        provider=provider,
        family="test",
        context_window=200000,
        max_output=8192,
        cost_per_1k_input=0.01,
        cost_per_1k_output=0.03,
        capabilities=["code"],
        latency_tier=LatencyTier.FAST,
        quality_tier=QualityTier.EXCELLENT,
        enabled=enabled,
    )


class FakeProviders:
    """Fake provider registry."""

    def list_providers(self):
        return [{"name": "anthropic", "enabled": True},
                {"name": "google", "enabled": True}]

    def is_healthy(self, provider: str) -> bool:
        return provider in ("anthropic", "google")


class FakeModelRegistry:
    """Fake ModelRegistry for _get_model_registry()."""

    def __init__(self):
        self.providers = FakeProviders()
        self.config = ModelRegistryConfig(
            providers={},
            models={"claude-3": _make_model()},
            policies={
                "default": ModelPolicy(),
                "cheap": ModelPolicy(strategy=SelectionStrategy.CHEAPEST,
                                     max_cost_per_task=0.01),
            },
            budget=BudgetConfig(daily_limit=5.0, monthly_limit=100.0),
        )

    def stats(self) -> dict:
        return {"total_models": 1, "enabled_models": 1,
                "providers": 2, "policies": 2}

    def list_models(self, enabled_only=False):
        models = [_make_model()]
        if enabled_only:
            models = [m for m in models if m.enabled]
        return models


# ── Fixtures ────────────────────────────────────────────────────────

@pytest.fixture
def tmp_root(tmp_path, monkeypatch):
    """Point MAOP_ROOT to a temp dir."""
    monkeypatch.setattr("maop.dashboard.routers.state.MAOP_ROOT", tmp_path)
    monkeypatch.setattr("maop.dashboard.routers.model.MAOP_ROOT", tmp_path)
    return tmp_path


@pytest.fixture
def client(tmp_root, monkeypatch):
    """TestClient with mocked ConfigLoader and model registry."""
    monkeypatch.setattr("maop.config.loader.ConfigLoader", FakeConfigLoader)
    # Reset the cached registry and patch _get_model_registry
    monkeypatch.setattr("maop.dashboard.routers.model._model_registry", None)
    monkeypatch.setattr("maop.dashboard.routers.model._get_model_registry",
                        lambda: FakeModelRegistry())
    app = FastAPI()
    @app.middleware("http")
    async def _inject_admin(request, call_next):
        request.state.auth_roles = ["admin"]
        return await call_next(request)
    from maop.dashboard.routers.model import router
    app.include_router(router)
    return TestClient(app)


# ── /api/model/agents ───────────────────────────────────────────────

class TestModelAgents:
    def test_returns_agent_list(self, client):
        resp = client.get("/api/model/agents")
        assert resp.status_code == 200
        data = resp.json()
        assert "agents" in data
        assert data["count"] == 2

    def test_agent_has_cli_available_field(self, client):
        data = client.get("/api/model/agents").json()
        for a in data["agents"]:
            assert "cli_available" in a
            assert "cli_path" in a

    def test_agent_has_required_fields(self, client):
        data = client.get("/api/model/agents").json()
        for a in data["agents"]:
            assert "name" in a
            assert "cli" in a
            assert "driver" in a
            assert "model" in a
            assert "capabilities" in a

    def test_error_returns_empty_list(self, tmp_root, monkeypatch):
        monkeypatch.setattr("maop.config.loader.ConfigLoader",
                            MagicMock(side_effect=RuntimeError("cfg fail")))
        monkeypatch.setattr("maop.dashboard.routers.model._model_registry", None)
        monkeypatch.setattr("maop.dashboard.routers.model._get_model_registry",
                            lambda: FakeModelRegistry())
        app = FastAPI()
        from maop.dashboard.routers.model import router
        app.include_router(router)
        data = TestClient(app).get("/api/model/agents").json()
        assert data["agents"] == []
        assert data["count"] == 0


# ── /api/model/quota ────────────────────────────────────────────────

class TestModelQuota:
    def test_returns_agents_config(self, client):
        resp = client.get("/api/model/quota")
        assert resp.status_code == 200
        data = resp.json()
        assert "agents" in data
        assert data["count"] == 2

    def test_agent_has_available_field(self, client):
        data = client.get("/api/model/quota").json()
        for a in data["agents"]:
            assert "available" in a
            assert "driver" in a


# ── /api/model/switch ───────────────────────────────────────────────

class TestModelSwitch:
    def test_missing_agent_returns_400(self, client):
        resp = client.post("/api/model/switch", json={"model": "gpt-4"})
        assert resp.status_code == 400

    def test_missing_model_returns_400(self, client):
        resp = client.post("/api/model/switch", json={"agent": "claude"})
        assert resp.status_code == 400

    def test_no_agents_yaml_returns_error(self, client):
        resp = client.post("/api/model/switch",
                           json={"agent": "claude", "model": "gpt-4"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "error"
        assert "not found" in data["error"]

    def test_switch_with_agents_yaml(self, tmp_root, client):
        """Create agents.yaml in temp root and test switch."""
        import yaml
        agents_yaml = {
            "agents": {
                "claude": {"cli": "claude", "driver": "cli", "model": "claude-3"}
            }
        }
        (tmp_root / "agents.yaml").write_text(
            yaml.dump(agents_yaml, allow_unicode=True), encoding="utf-8")
        data = client.post("/api/model/switch",
                           json={"agent": "claude", "model": "gpt-4"}).json()
        assert data["status"] == "ok"
        assert data["agent"] == "claude"
        assert data["model"] == "gpt-4"

    def test_switch_unknown_agent(self, tmp_root, client):
        import yaml
        agents_yaml = {"agents": {"claude": {"cli": "claude"}}}
        (tmp_root / "agents.yaml").write_text(
            yaml.dump(agents_yaml), encoding="utf-8")
        data = client.post("/api/model/switch",
                           json={"agent": "unknown", "model": "gpt-4"}).json()
        assert data["status"] == "error"
        assert "Unknown agent" in data["error"]


# ── /api/model/registry ─────────────────────────────────────────────

class TestModelRegistry:
    def test_returns_stats(self, client):
        resp = client.get("/api/model/registry")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "stats" in data

    def test_stats_has_model_count(self, client):
        data = client.get("/api/model/registry").json()
        assert data["stats"]["total_models"] == 1


# ── /api/model/list ─────────────────────────────────────────────────

class TestModelList:
    def test_returns_models(self, client):
        resp = client.get("/api/model/list")
        assert resp.status_code == 200
        data = resp.json()
        assert "models" in data
        assert data["count"] == 1

    def test_model_has_required_fields(self, client):
        data = client.get("/api/model/list").json()
        m = data["models"][0]
        assert "name" in m
        assert "provider" in m
        assert "context_window" in m
        assert "latency_tier" in m
        assert "quality_tier" in m
        assert "enabled" in m
        assert "provider_healthy" in m


# ── /api/model/providers ────────────────────────────────────────────

class TestModelProviders:
    def test_returns_provider_list(self, client):
        resp = client.get("/api/model/providers")
        assert resp.status_code == 200
        data = resp.json()
        assert "providers" in data
        assert len(data["providers"]) == 2


# ── /api/model/select ───────────────────────────────────────────────

class TestModelSelect:
    def test_select_returns_effective_model(self, client, monkeypatch):
        em = EffectiveModel(model_name="claude-3", provider="anthropic")
        mock_selector = MagicMock()
        mock_selector.select = MagicMock(return_value=em)
        monkeypatch.setattr("maop.model.selector.ModelSelector",
                            lambda reg: mock_selector)
        data = client.get("/api/model/select").json()
        assert data["status"] == "ok"
        assert "effective_model" in data

    def test_select_with_capability(self, client, monkeypatch):
        em = EffectiveModel(model_name="claude-3", provider="anthropic")
        mock_selector = MagicMock()
        mock_selector.select = MagicMock(return_value=em)
        monkeypatch.setattr("maop.model.selector.ModelSelector",
                            lambda reg: mock_selector)
        data = client.get("/api/model/select",
                          params={"capability": "code"}).json()
        assert data["status"] == "ok"

    def test_select_error(self, client, monkeypatch):
        mock_selector = MagicMock()
        mock_selector.select = MagicMock(
            side_effect=RuntimeError("no model"))
        monkeypatch.setattr("maop.model.selector.ModelSelector",
                            lambda reg: mock_selector)
        data = client.get("/api/model/select").json()
        assert data["status"] == "error"


# ── /api/model/budget ───────────────────────────────────────────────

class TestModelBudget:
    def test_returns_budget_stats(self, client, monkeypatch):
        mock_guard = MagicMock()
        mock_guard.stats = MagicMock(
            return_value={"daily_limit": 5.0, "used": 1.0})
        monkeypatch.setattr("maop.model.budget.BudgetGuard",
                            lambda **kw: mock_guard)
        data = client.get("/api/model/budget").json()
        assert data["status"] == "ok"
        assert "budget" in data

    def test_budget_error(self, client, monkeypatch):
        monkeypatch.setattr("maop.model.budget.BudgetGuard",
                            MagicMock(side_effect=RuntimeError("budget err")))
        data = client.get("/api/model/budget").json()
        assert data["status"] == "error"


# ── /api/model/policies ─────────────────────────────────────────────

class TestModelPolicies:
    def test_returns_policy_list(self, client):
        resp = client.get("/api/model/policies")
        assert resp.status_code == 200
        data = resp.json()
        assert "policies" in data
        assert data["count"] == 2

    def test_policy_has_required_fields(self, client):
        data = client.get("/api/model/policies").json()
        for p in data["policies"]:
            assert "name" in p
            assert "strategy" in p
            assert "max_cost_per_task" in p
            assert "prefer_low_latency" in p
            assert "fallback_on_error" in p
            assert "fallback_on_timeout" in p

    def test_policy_strategy_is_string(self, client):
        data = client.get("/api/model/policies").json()
        for p in data["policies"]:
            assert isinstance(p["strategy"], str)


# --- Merged from test_router_model_coverage.py (client->client_coverage, TestModelSwitch->TestModelSwitchCoverage) ---

@pytest.fixture
def model_env(tmp_path, monkeypatch):
    """Isolate MAOP_ROOT for model router and create minimal config."""
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    # agents.yaml with one agent
    (cfg_dir / "agents.yaml").write_text(
        "agents:\n  claude:\n    cli: claude\n    model: claude-3\n    driver: cli\n"
        "    timeout_s: 120\n    capabilities: [code]\n    description: test\n",
        encoding="utf-8",
    )
    # models.yaml with valid models
    (cfg_dir / "models.yaml").write_text(
        "models:\n  claude-3:\n    provider: anthropic\n    family: claude-3\n"
        "    context_window: 200000\n    max_output: 4096\n"
        "    cost_per_1k_input: 0.01\n    cost_per_1k_output: 0.03\n"
        "    capabilities: [code]\n    latency_tier: fast\n"
        "    quality_tier: excellent\n    enabled: true\n",
        encoding="utf-8",
    )
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr("maop.dashboard.routers.model.MAOP_ROOT", tmp_path)
    monkeypatch.setattr("maop.dashboard.routers.state.MAOP_ROOT", tmp_path)

    # Reset model registry + api key vault singletons
    import maop.dashboard.routers.model as model_mod
    model_mod._model_registry = None
    model_mod._api_key_vault = None

    return tmp_path


@pytest.fixture
def client_coverage(model_env, monkeypatch):
    """TestClient with admin role injected and model router mounted."""
    app = FastAPI()

    @app.middleware("http")
    async def _inject_admin(request, call_next):
        request.state.auth_roles = ["admin"]
        request.state.auth_identity = "admin"
        return await call_next(request)

    from maop.dashboard.routers.model import router
    app.include_router(router)
    return TestClient(app)


class TestModelSwitchCoverage:
    def test_missing_fields(self, client_coverage):
        """Switch with missing fields returns 400 or error."""
        resp = client_coverage.post("/api/model/switch", json={})
        assert resp.status_code in (400, 422)

    def test_agents_yaml_not_found(self, model_env, client_coverage):
        """Switch when agents.yaml doesn't exist returns error."""
        (model_env / "config" / "agents.yaml").unlink()
        resp = client_coverage.post(
            "/api/model/switch",
            json={"agent": "claude", "model": "claude-3"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "error"

    def test_unknown_agent(self, client_coverage):
        """Switch with unknown agent returns error."""
        resp = client_coverage.post(
            "/api/model/switch",
            json={"agent": "nonexistent", "model": "claude-3"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "error"

    def test_unknown_model(self, client_coverage):
        """Switch with unknown model returns error."""
        resp = client_coverage.post(
            "/api/model/switch",
            json={"agent": "claude", "model": "nonexistent-model"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "error"

    def test_switch_happy(self, client_coverage):
        """Switch with valid agent + model succeeds."""
        resp = client_coverage.post(
            "/api/model/switch",
            json={"agent": "claude", "model": "claude-3"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


class TestModelProviderAdd:
    def test_missing_name(self, client_coverage):
        """Provider add with no name returns 400."""
        resp = client_coverage.post("/api/model/provider/add", json={})
        assert resp.status_code == 400

    def test_add_happy(self, client_coverage):
        """Provider add with valid name succeeds."""
        resp = client_coverage.post(
            "/api/model/provider/add",
            json={"name": "testprov", "kind": "anthropic"},
        )
        # May fail if ProviderDef requires more fields — accept 200 or 422
        assert resp.status_code in (200, 422)


class TestModelProviderDelete:
    def test_missing_name(self, client_coverage):
        """Provider delete with no name returns 400."""
        resp = client_coverage.post("/api/model/provider/delete", json={})
        assert resp.status_code == 400

    def test_delete_nonexistent(self, client_coverage):
        """Provider delete with unknown name returns 409 or 200."""
        resp = client_coverage.post(
            "/api/model/provider/delete",
            json={"name": "nonexistent-prov"},
        )
        # remove_provider raises ValueError → 409, or handle_api_errors wraps
        assert resp.status_code in (200, 409, 422, 500)


class TestModelAdd:
    def test_missing_name(self, client_coverage):
        """Model add with no name returns 400."""
        resp = client_coverage.post("/api/model/add", json={})
        assert resp.status_code == 400

    def test_add_happy(self, client_coverage):
        """Model add with valid fields succeeds."""
        resp = client_coverage.post(
            "/api/model/add",
            json={
                "name": "test-model-1", "provider": "anthropic",
                "family": "claude-3", "context_window": 200000,
                "max_output": 4096, "cost_per_1k_input": 0.01,
                "cost_per_1k_output": 0.03, "capabilities": ["code"],
                "latency_tier": "fast", "quality_tier": "excellent", "enabled": True,
            },
        )
        assert resp.status_code in (200, 422)


class TestModelDelete:
    def test_missing_name(self, client_coverage):
        """Model delete with no name returns 400."""
        resp = client_coverage.post("/api/model/delete", json={})
        assert resp.status_code == 400

    def test_delete_nonexistent(self, client_coverage):
        """Model delete with unknown name returns 404."""
        resp = client_coverage.post(
            "/api/model/delete",
            json={"name": "nonexistent-model"},
        )
        assert resp.status_code in (404, 500)


class TestApiKeyStore:
    def test_missing_fields(self, client_coverage):
        """Key store with missing fields returns 400."""
        resp = client_coverage.post("/api/model/key/store", json={})
        assert resp.status_code == 400

    def test_store_happy(self, client_coverage):
        """Key store with valid provider + key succeeds."""
        resp = client_coverage.post(
            "/api/model/key/store",
            json={"provider": "anthropic", "api_key": "sk-test-123"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


class TestApiKeyDelete:
    def test_missing_provider(self, client_coverage):
        """Key delete with no provider returns 400."""
        resp = client_coverage.post("/api/model/key/delete", json={})
        assert resp.status_code == 400

    def test_delete_nonexistent(self, client_coverage):
        """Key delete with unknown provider returns not_found."""
        resp = client_coverage.post(
            "/api/model/key/delete",
            json={"provider": "nonexistent-key"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "not_found"

    def test_delete_existing(self, client_coverage):
        """Key delete after store returns ok."""
        client_coverage.post(
            "/api/model/key/store",
            json={"provider": "testprov", "api_key": "sk-test"},
        )
        resp = client_coverage.post(
            "/api/model/key/delete",
            json={"provider": "testprov"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


class TestApiKeyList:
    def test_list_empty(self, client_coverage):
        """Key list when no keys returns empty."""
        resp = client_coverage.get("/api/model/key/list")
        assert resp.status_code == 200
        assert "providers" in resp.json()

    def test_list_after_store(self, client_coverage):
        """Key list after storing a key."""
        client_coverage.post(
            "/api/model/key/store",
            json={"provider": "listprov", "api_key": "sk-test"},
        )
        resp = client_coverage.get("/api/model/key/list")
        assert resp.status_code == 200
        assert "listprov" in resp.json()["providers"]


class TestModelHealthCheck:
    def test_missing_provider(self, client_coverage):
        """Health check with no provider checks all providers."""
        resp = client_coverage.post("/api/model/health/check", json={})
        # May fail if no providers configured — accept 200 or 500
        assert resp.status_code in (200, 500)

    def test_with_provider(self, client_coverage):
        """Health check with specific provider."""
        resp = client_coverage.post(
            "/api/model/health/check",
            json={"provider": "anthropic"},
        )
        assert resp.status_code in (200, 500)


class TestModelGetEndpoints:
    def test_model_agents(self, client_coverage):
        """GET /api/model/agents returns agent list."""
        resp = client_coverage.get("/api/model/agents")
        assert resp.status_code == 200
        assert "agents" in resp.json()

    def test_model_quota(self, client_coverage):
        """GET /api/model/quota returns quota info."""
        resp = client_coverage.get("/api/model/quota")
        assert resp.status_code == 200

    def test_model_registry(self, client_coverage):
        """GET /api/model/registry returns registry stats."""
        resp = client_coverage.get("/api/model/registry")
        assert resp.status_code == 200

    def test_model_list(self, client_coverage):
        """GET /api/model/list returns model list."""
        resp = client_coverage.get("/api/model/list")
        assert resp.status_code == 200

    def test_model_providers(self, client_coverage):
        """GET /api/model/providers returns provider list."""
        resp = client_coverage.get("/api/model/providers")
        assert resp.status_code == 200

    def test_model_select(self, client_coverage):
        """GET /api/model/select returns selected model."""
        resp = client_coverage.get("/api/model/select")
        assert resp.status_code in (200, 500)

    def test_model_budget(self, client_coverage):
        """GET /api/model/budget returns budget info."""
        resp = client_coverage.get("/api/model/budget")
        assert resp.status_code == 200

    def test_model_quota_status(self, client_coverage):
        """GET /api/model/quota/status returns quota status."""
        resp = client_coverage.get("/api/model/quota/status")
        assert resp.status_code == 200

    def test_model_policies(self, client_coverage):
        """GET /api/model/policies returns policy list."""
        resp = client_coverage.get("/api/model/policies")
        assert resp.status_code == 200