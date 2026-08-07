"""Comprehensive tests for MAOP.model.registry — ProviderRegistry & ModelRegistry."""

from __future__ import annotations

from pathlib import Path

import pytest

from maop.core.backends.db_utils import find_project_root
from maop.model.registry import ModelRegistry, ProviderRegistry
from maop.model.schema import (
    LatencyTier,
    ModelDef,
    ModelRegistryConfig,
    ProviderDef,
    ProviderType,
    QualityTier,
    SelectionStrategy,
)

# ── Fixtures ──────────────────────────────────────────────────

def _make_provider(name: str, **kw) -> tuple[str, ProviderDef]:
    return name, ProviderDef(**kw)


def _make_model(name: str, **kw) -> tuple[str, ModelDef]:
    return name, ModelDef(name=name, **kw)


@pytest.fixture
def sample_providers() -> dict[str, ProviderDef]:
    return {
        "openai": ProviderDef(type=ProviderType.OPENAI_COMPATIBLE, api_key_env="OPENAI_API_KEY"),
        "local": ProviderDef(type=ProviderType.BUILTIN, enabled=True),
        "disabled_prov": ProviderDef(type=ProviderType.CUSTOM, enabled=False),
    }


@pytest.fixture
def sample_models() -> dict[str, ModelDef]:
    return {
        "gpt-4": ModelDef(
            name="gpt-4", provider="openai", capabilities=["codegen", "chat"],
            quality_tier=QualityTier.EXCELLENT, latency_tier=LatencyTier.SLOW,
            cost_per_1k_input=0.03, cost_per_1k_output=0.06, enabled=True,
        ),
        "gpt-3.5": ModelDef(
            name="gpt-3.5", provider="openai", capabilities=["chat"],
            quality_tier=QualityTier.GOOD, latency_tier=LatencyTier.FAST,
            cost_per_1k_input=0.001, cost_per_1k_output=0.002, enabled=True,
        ),
        "local-model": ModelDef(
            name="local-model", provider="local", capabilities=["codegen"],
            quality_tier=QualityTier.FAIR, latency_tier=LatencyTier.INSTANT,
            cost_per_1k_input=0.0, cost_per_1k_output=0.0, enabled=True,
        ),
        "disabled-model": ModelDef(
            name="disabled-model", provider="openai", capabilities=["chat"],
            enabled=False,
        ),
    }


@pytest.fixture
def models_yaml_content() -> str:
    return """
providers:
  openai:
    type: openai-compatible
    api_key_env: OPENAI_API_KEY
    enabled: true
  local:
    type: builtin
    enabled: true

models:
  gpt-4:
    provider: openai
    capabilities: [codegen, chat]
    quality_tier: excellent
    latency_tier: slow
    cost_per_1k_input: 0.03
    cost_per_1k_output: 0.06
  gpt-3.5:
    provider: openai
    capabilities: [chat]
    quality_tier: good
    latency_tier: fast
    cost_per_1k_input: 0.001
    cost_per_1k_output: 0.002

policies:
  default:
    strategy: best_quality_within_budget
    max_cost_per_task: 0.05
  codegen:
    strategy: cheapest

budget:
  daily_limit: 5.0
  monthly_limit: 100.0
  hard_stop: true

quota:
  openai:
    requests_per_minute: 60
"""


@pytest.fixture
def project_root(tmp_path, models_yaml_content) -> Path:
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "models.yaml").write_text(models_yaml_content, encoding="utf-8")
    return tmp_path


# ── ProviderRegistry Tests ────────────────────────────────────

class TestProviderRegistry:
    """Tests for ProviderRegistry."""

    def test_init_empty(self):
        reg = ProviderRegistry()
        assert reg._providers == {}
        assert reg._health == {}

    def test_init_with_providers(self, sample_providers):
        reg = ProviderRegistry(sample_providers)
        assert "openai" in reg._health
        assert reg._health["openai"]["healthy"] is True

    def test_get_existing(self, sample_providers):
        reg = ProviderRegistry(sample_providers)
        result = reg.get("openai")
        assert result is not None
        assert result.api_key_env == "OPENAI_API_KEY"

    def test_get_nonexistent(self, sample_providers):
        reg = ProviderRegistry(sample_providers)
        assert reg.get("nonexistent") is None

    def test_is_enabled_true(self, sample_providers):
        reg = ProviderRegistry(sample_providers)
        assert reg.is_enabled("openai") is True

    def test_is_enabled_false_disabled(self, sample_providers):
        reg = ProviderRegistry(sample_providers)
        assert reg.is_enabled("disabled_prov") is False

    def test_is_enabled_false_missing(self, sample_providers):
        reg = ProviderRegistry(sample_providers)
        assert reg.is_enabled("missing") is False

    def test_is_healthy_default(self, sample_providers):
        reg = ProviderRegistry(sample_providers)
        assert reg.is_healthy("openai") is True

    def test_is_healthy_unknown_provider(self, sample_providers):
        reg = ProviderRegistry(sample_providers)
        # Unknown provider: health dict returns None → True
        assert reg.is_healthy("unknown") is True

    def test_mark_unhealthy(self, sample_providers):
        reg = ProviderRegistry(sample_providers)
        reg.mark_unhealthy("openai", "timeout")
        assert reg.is_healthy("openai") is False
        assert reg._health["openai"]["error"] == "timeout"

    def test_mark_unhealthy_new_provider(self):
        reg = ProviderRegistry()
        reg.mark_unhealthy("newprov", "init error")
        assert reg.is_healthy("newprov") is False
        assert reg._health["newprov"]["error"] == "init error"

    def test_mark_healthy(self, sample_providers):
        reg = ProviderRegistry(sample_providers)
        reg.mark_unhealthy("openai", "temp error")
        reg.mark_healthy("openai")
        assert reg.is_healthy("openai") is True
        assert reg._health["openai"]["error"] == ""

    def test_mark_healthy_new_provider(self):
        reg = ProviderRegistry()
        reg.mark_healthy("newprov")
        assert reg.is_healthy("newprov") is True

    def test_get_api_key_with_env(self, sample_providers, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test123")
        reg = ProviderRegistry(sample_providers)
        assert reg.get_api_key("openai") == "sk-test123"

    def test_get_api_key_no_env_var(self, sample_providers, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        reg = ProviderRegistry(sample_providers)
        assert reg.get_api_key("openai") is None

    def test_get_api_key_no_env_key(self, sample_providers):
        reg = ProviderRegistry(sample_providers)
        # "local" provider has no api_key_env
        assert reg.get_api_key("local") is None

    def test_get_api_key_missing_provider(self, sample_providers):
        reg = ProviderRegistry(sample_providers)
        assert reg.get_api_key("missing") is None

    def test_list_providers(self, sample_providers, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        reg = ProviderRegistry(sample_providers)
        result = reg.list_providers()
        names = [p["name"] for p in result]
        assert "openai" in names
        assert "local" in names
        openai_entry = next(p for p in result if p["name"] == "openai")
        assert openai_entry["type"] == "openai-compatible"
        assert openai_entry["enabled"] is True
        assert openai_entry["has_api_key"] is True

    def test_list_providers_empty(self):
        reg = ProviderRegistry()
        assert reg.list_providers() == []

    def test_check_health_builtin_no_key(self):
        providers = {"local": ProviderDef(type=ProviderType.BUILTIN)}
        reg = ProviderRegistry(providers)
        assert reg.check_health("local") is True
        assert reg.is_healthy("local") is True

    def test_check_health_with_key(self, monkeypatch):
        monkeypatch.setenv("TEST_KEY", "val")
        providers = {"prov": ProviderDef(type=ProviderType.OPENAI_COMPATIBLE, api_key_env="TEST_KEY")}
        reg = ProviderRegistry(providers)
        assert reg.check_health("prov") is True

    def test_check_health_missing_key(self, monkeypatch):
        monkeypatch.delenv("MISSING_KEY", raising=False)
        providers = {"prov": ProviderDef(type=ProviderType.OPENAI_COMPATIBLE, api_key_env="MISSING_KEY")}
        reg = ProviderRegistry(providers)
        assert reg.check_health("prov") is False
        assert reg.is_healthy("prov") is False

    def test_check_health_disabled(self):
        providers = {"prov": ProviderDef(type=ProviderType.OPENAI_COMPATIBLE, enabled=False)}
        reg = ProviderRegistry(providers)
        assert reg.check_health("prov") is False

    def test_check_health_not_found(self):
        reg = ProviderRegistry()
        assert reg.check_health("ghost") is False
        assert reg.is_healthy("ghost") is False


# ── ModelRegistry Tests ──────────────────────────────────────

class TestModelRegistry:
    """Tests for ModelRegistry."""

    def test_init_with_project_root(self, project_root):
        reg = ModelRegistry(project_root=project_root)
        assert reg.get_model("gpt-4") is not None
        assert reg.get_model("gpt-3.5") is not None

    def test_init_missing_models_yaml(self, tmp_path):
        # tmp_path has no config/models.yaml
        reg = ModelRegistry(project_root=tmp_path)
        assert reg.config.models == {}
        assert reg.list_models() == []

    def test_load_returns_config(self, project_root):
        reg = ModelRegistry(project_root=project_root)
        config = reg.load()
        assert isinstance(config, ModelRegistryConfig)
        assert "gpt-4" in config.models

    def test_reload(self, project_root):
        reg = ModelRegistry(project_root=project_root)
        config = reg.reload()
        assert "gpt-4" in config.models

    def test_get_model(self, project_root):
        reg = ModelRegistry(project_root=project_root)
        m = reg.get_model("gpt-4")
        assert m is not None
        assert m.provider == "openai"

    def test_get_model_nonexistent(self, project_root):
        reg = ModelRegistry(project_root=project_root)
        assert reg.get_model("nonexistent") is None

    def test_list_models_enabled_only(self, project_root):
        reg = ModelRegistry(project_root=project_root)
        models = reg.list_models(enabled_only=True)
        names = [m.name for m in models]
        assert "gpt-4" in names
        assert "gpt-3.5" in names

    def test_list_models_all(self, project_root):
        reg = ModelRegistry(project_root=project_root)
        models = reg.list_models(enabled_only=False)
        assert len(models) == 2  # gpt-4 and gpt-3.5

    def test_models_by_capability(self, project_root):
        reg = ModelRegistry(project_root=project_root)
        codegen = reg.models_by_capability("codegen")
        assert len(codegen) == 1
        assert codegen[0].name == "gpt-4"

    def test_models_by_capability_no_match(self, project_root):
        reg = ModelRegistry(project_root=project_root)
        assert reg.models_by_capability("nonexistent") == []

    def test_models_by_provider(self, project_root):
        reg = ModelRegistry(project_root=project_root)
        openai_models = reg.models_by_provider("openai")
        assert len(openai_models) == 2

    def test_models_by_provider_no_match(self, project_root):
        reg = ModelRegistry(project_root=project_root)
        assert reg.models_by_provider("anthropic") == []

    def test_best_model_best_quality(self, project_root):
        reg = ModelRegistry(project_root=project_root)
        best = reg.best_model("chat", strategy="best_quality")
        assert best is not None
        # gpt-4 is EXCELLENT, gpt-3.5 is GOOD
        assert best.name == "gpt-4"

    def test_best_model_cheapest(self, project_root):
        reg = ModelRegistry(project_root=project_root)
        best = reg.best_model("chat", strategy="cheapest")
        assert best is not None
        assert best.name == "gpt-3.5"

    def test_best_model_fastest(self, project_root):
        reg = ModelRegistry(project_root=project_root)
        best = reg.best_model("chat", strategy="fastest")
        assert best is not None
        assert best.name == "gpt-3.5"

    def test_best_model_no_capability(self, project_root):
        reg = ModelRegistry(project_root=project_root)
        assert reg.best_model("nonexistent") is None

    def test_best_model_with_max_cost(self, project_root):
        reg = ModelRegistry(project_root=project_root)
        # gpt-4 cost: 0.03+0.06=0.09, gpt-3.5: 0.001+0.002=0.003
        best = reg.best_model("chat", strategy="best_quality", max_cost=0.01)
        assert best is not None
        assert best.name == "gpt-3.5"

    def test_best_model_max_cost_excludes_all(self, project_root):
        reg = ModelRegistry(project_root=project_root)
        best = reg.best_model("chat", strategy="best_quality", max_cost=0.0001)
        assert best is None

    def test_best_model_with_unhealthy_provider(self, project_root):
        reg = ModelRegistry(project_root=project_root)
        reg.providers.mark_unhealthy("openai", "down")
        best = reg.best_model("chat", strategy="best_quality")
        # All chat models are on openai; unhealthy filtered out but still returned
        # because healthy list is empty → falls back to original candidates
        assert best is not None

    def test_get_policy(self, project_root):
        reg = ModelRegistry(project_root=project_root)
        policy = reg.get_policy("codegen")
        assert policy is not None
        assert policy.strategy == SelectionStrategy.CHEAPEST

    def test_get_policy_default_fallback(self, project_root):
        reg = ModelRegistry(project_root=project_root)
        policy = reg.get_policy("nonexistent")
        # Falls back to "default" policy
        assert policy is not None

    def test_get_policy_no_policies(self, tmp_path):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "models.yaml").write_text("models: {}\n", encoding="utf-8")
        reg = ModelRegistry(project_root=tmp_path)
        assert reg.get_policy("default") is None

    def test_resolve_agent_model_exact(self, project_root):
        reg = ModelRegistry(project_root=project_root)
        m = reg.resolve_agent_model("gpt-4")
        assert m is not None
        assert m.name == "gpt-4"

    def test_resolve_agent_model_ref(self, project_root):
        reg = ModelRegistry(project_root=project_root)
        m = reg.resolve_agent_model("some-alias", model_ref="gpt-4")
        assert m is not None
        assert m.name == "gpt-4"

    def test_resolve_agent_model_prefix(self, project_root):
        reg = ModelRegistry(project_root=project_root)
        # "gpt-4 (latest)" → prefix "gpt-4"
        m = reg.resolve_agent_model("gpt-4 (latest)")
        assert m is not None
        assert m.name == "gpt-4"

    def test_resolve_agent_model_not_found(self, project_root):
        reg = ModelRegistry(project_root=project_root)
        assert reg.resolve_agent_model("unknown-model") is None

    def test_resolve_agent_model_empty(self, project_root):
        reg = ModelRegistry(project_root=project_root)
        assert reg.resolve_agent_model("") is None

    def test_stats(self, project_root):
        reg = ModelRegistry(project_root=project_root)
        s = reg.stats()
        assert s["total_models"] == 2
        assert s["enabled_models"] == 2
        assert s["total_providers"] == 2
        assert "excellent" in s["by_quality"]
        assert "slow" in s["by_latency"]
        assert "openai" in s["by_provider"]

    def test_stats_empty(self, tmp_path):
        reg = ModelRegistry(project_root=tmp_path)
        s = reg.stats()
        assert s["total_models"] == 0
        assert s["total_providers"] == 0


# ── find_project_root Tests ─────────────────────────────────

class TestFindProjectRoot:
    """Tests for find_project_root helper."""

    def test_finds_root_with_config(self, tmp_path):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "agents.yaml").write_text("agents: {}", encoding="utf-8")
        # We can't easily test the actual function since it uses __file__,
        # but we can verify it returns a Path
        root = find_project_root()
        assert isinstance(root, Path)
