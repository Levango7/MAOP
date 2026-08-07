"""Model & Provider Registry — Load from models.yaml, query by capability/provider/budget."""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path

import yaml

from maop.model.schema import (
    EffectiveModel,
    LatencyTier,
    ModelDef,
    ModelRegistryConfig,
    ProtocolType,
    ProviderDef,
    ProviderType,
    QualityTier,
)

logger = logging.getLogger(__name__)


from maop.core.backends.db_utils import find_project_root

# ── ProviderRegistry ──────────────────────────────────────────

class ProviderRegistry:
    """Tracks provider definitions and runtime health status."""

    def __init__(self, providers: dict[str, ProviderDef] | None = None) -> None:
        self._providers = providers or {}
        self._health: dict[str, dict] = {}  # provider_name -> {healthy, last_check, error}
        for name in self._providers:
            self._health[name] = {"healthy": True, "last_check": 0.0, "error": ""}

    def get(self, name: str) -> ProviderDef | None:
        return self._providers.get(name)

    def is_enabled(self, name: str) -> bool:
        p = self._providers.get(name)
        return p is not None and p.enabled

    def is_healthy(self, name: str) -> bool:
        h = self._health.get(name)
        return h is None or h.get("healthy", True)

    def mark_unhealthy(self, name: str, error: str = "") -> None:
        if name not in self._health:
            self._health[name] = {"healthy": False, "last_check": time.time(), "error": error}
        else:
            self._health[name].update(healthy=False, last_check=time.time(), error=error)

    def mark_healthy(self, name: str) -> None:
        if name not in self._health:
            self._health[name] = {"healthy": True, "last_check": time.time(), "error": ""}
        else:
            self._health[name].update(healthy=True, last_check=time.time(), error="")

    def get_api_key(self, name: str) -> str | None:
        """Resolve API key: direct key takes precedence over env variable."""
        p = self._providers.get(name)
        if not p:
            return None
        # Direct api_key takes precedence
        if p.api_key:
            return p.api_key
        if p.api_key_env:
            return os.environ.get(p.api_key_env)
        return None

    def list_providers(self) -> list[dict]:
        result = []
        for name, p in self._providers.items():
            h = self._health.get(name, {})
            result.append({
                "name": name, "type": p.type.value, "enabled": p.enabled,
                "protocol": p.protocol.value,
                "healthy": h.get("healthy", True),
                "has_api_key": bool(self.get_api_key(name)),
                "base_url": p.base_url,
            })
        return result

    def check_health(self, name: str) -> bool:
        """Check provider health (simple: API key present + enabled)."""
        p = self._providers.get(name)
        if not p:
            self.mark_unhealthy(name, "not found")
            return False
        if not p.enabled:
            self.mark_unhealthy(name, "disabled")
            return False
        if p.type != ProviderType.BUILTIN and not self.get_api_key(name):
            self.mark_unhealthy(name, f"missing env {p.api_key_env}")
            return False
        self.mark_healthy(name)
        return True

    def add(self, name: str, provider: ProviderDef) -> ProviderDef:
        """Add or replace a provider definition at runtime."""
        self._providers[name] = provider
        self._health[name] = {"healthy": True, "last_check": 0.0, "error": ""}
        return provider

    def remove(self, name: str) -> bool:
        """Remove a provider definition at runtime."""
        if name not in self._providers:
            return False
        del self._providers[name]
        self._health.pop(name, None)
        return True


# ── ModelRegistry ─────────────────────────────────────────────

class ModelRegistry:
    """Central model registry — loads models.yaml and provides query APIs.

    Usage::

        registry = ModelRegistry(project_root="/path/to/MAOP")
        models = registry.models_by_capability("codegen")
        best = registry.best_model("codegen", strategy="best_quality")
    """

    def __init__(self, project_root: Path | str | None = None) -> None:
        self._root = Path(project_root) if project_root else find_project_root()
        self._config: ModelRegistryConfig = ModelRegistryConfig()
        self._provider_registry = ProviderRegistry()
        self.load()

    def load(self) -> ModelRegistryConfig:
        """Load models.yaml from config/."""
        path = self._root / "config" / "models.yaml"
        if not path.exists():
            logger.warning("models.yaml not found at %s", path)
            self._config = ModelRegistryConfig()
            return self._config

        try:
            raw = path.read_text(encoding="utf-8")
            data = yaml.safe_load(raw) or {}
        except Exception as exc:
            logger.error("Failed to load models.yaml: %s", exc)
            self._config = ModelRegistryConfig()
            return self._config

        # Parse providers
        providers: dict[str, ProviderDef] = {}
        for name, entry in (data.get("providers") or {}).items():
            providers[name] = ProviderDef(**entry)

        # Parse models
        models: dict[str, ModelDef] = {}
        for name, entry in (data.get("models") or {}).items():
            m = ModelDef(name=name, **entry)
            models[name] = m

        # Parse policies
        from maop.model.schema import ModelPolicy
        policies: dict[str, ModelPolicy] = {}
        for name, entry in (data.get("policies") or {}).items():
            policies[name] = ModelPolicy(**entry)

        # Parse budget
        from maop.model.schema import BudgetConfig
        budget = BudgetConfig(**(data.get("budget") or {}))

        # Parse quota
        from maop.model.schema import QuotaConfig
        quota: dict[str, QuotaConfig] = {}
        for name, entry in (data.get("quota") or {}).items():
            quota[name] = QuotaConfig(**entry)

        self._config = ModelRegistryConfig(
            providers=providers, models=models,
            policies=policies, budget=budget, quota=quota,
            default_provider=str(data.get("default_provider") or ""),
            default_model=str(data.get("default_model") or ""),
        )
        self._provider_registry = ProviderRegistry(providers)
        return self._config

    def reload(self) -> ModelRegistryConfig:
        return self.load()

    # ── Query APIs ────────────────────────────────────────────

    @property
    def providers(self) -> ProviderRegistry:
        return self._provider_registry

    @property
    def config(self) -> ModelRegistryConfig:
        return self._config

    def get_model(self, name: str) -> ModelDef | None:
        return self._config.models.get(name)

    def get_model_id(self, name: str) -> str:
        """Resolve the actual API model ID (falls back to name if model_id is empty)."""
        m = self._config.models.get(name)
        if m and m.model_id:
            return m.model_id
        return name

    def build_effective_model(
        self,
        model_name: str,
        policy_name: str = "default",
        fallback_chain: list[str] | None = None,
    ) -> EffectiveModel | None:
        """Build an EffectiveModel from a model name, enriching with provider info."""
        m = self._config.models.get(model_name)
        if not m:
            return None
        provider = self._config.providers.get(m.provider)
        protocol = provider.protocol if provider else ProtocolType.OPENAI_COMPLETIONS
        base_url = provider.base_url if provider else ""
        api_key = self._provider_registry.get_api_key(m.provider) or ""
        return EffectiveModel(
            model_name=model_name,
            provider=m.provider,
            model_id=m.model_id or model_name,
            protocol=protocol,
            cli_model_arg=m.model_id or model_name,
            fallback_chain=fallback_chain or [],
            policy_name=policy_name,
            capability_matrix=m.capability_matrix,
            thinking=m.thinking,
            base_url=base_url,
            api_key=api_key,
        )

    def list_models(self, enabled_only: bool = True) -> list[ModelDef]:
        return [m for m in self._config.models.values()
                if not enabled_only or m.enabled]

    def models_by_capability(self, capability: str) -> list[ModelDef]:
        return [m for m in self._config.models.values()
                if m.enabled and capability in m.capabilities]

    def models_by_provider(self, provider: str) -> list[ModelDef]:
        return [m for m in self._config.models.values()
                if m.enabled and m.provider == provider]

    def get_default_model(self) -> ModelDef | None:
        """Return the configured default model, if any."""
        if not self._config.default_model:
            return None
        return self._config.models.get(self._config.default_model)

    def get_default_provider(self) -> str:
        """Return the configured default provider name, if any."""
        return self._config.default_provider

    def best_model(
        self,
        capability: str,
        strategy: str = "best_quality",
        max_cost: float | None = None,
    ) -> ModelDef | None:
        """Select best model for a capability under a strategy."""
        candidates = self.models_by_capability(capability)
        if not candidates:
            return None

        # Filter by cost if specified
        if max_cost is not None:
            candidates = [m for m in candidates
                          if m.cost_per_1k_input + m.cost_per_1k_output <= max_cost]
            if not candidates:
                return None

        # Filter by provider health
        healthy = [m for m in candidates
                   if self._provider_registry.is_healthy(m.provider)]
        if healthy:
            candidates = healthy

        # Rank by strategy
        quality_order = {
            QualityTier.EXCELLENT: 4, QualityTier.GOOD: 3,
            QualityTier.FAIR: 2, QualityTier.POOR: 1,
        }
        latency_order = {
            LatencyTier.INSTANT: 4, LatencyTier.FAST: 3,
            LatencyTier.MEDIUM: 2, LatencyTier.SLOW: 1,
        }

        if strategy == "cheapest":
            candidates.sort(key=lambda m: m.cost_per_1k_input + m.cost_per_1k_output)
        elif strategy == "fastest":
            candidates.sort(key=lambda m: -latency_order.get(m.latency_tier, 0))
        elif strategy == "best_quality":
            candidates.sort(key=lambda m: -quality_order.get(m.quality_tier, 0))
        elif strategy == "best_quality_within_budget":
            candidates.sort(
                key=lambda m: (-quality_order.get(m.quality_tier, 0),
                               -latency_order.get(m.latency_tier, 0))
            )

        return candidates[0] if candidates else None

    def get_policy(self, name: str = "default"):
        return self._config.policies.get(name) or self._config.policies.get("default")

    def resolve_agent_model(
        self, agent_model_str: str, model_ref: str = ""
    ) -> ModelDef | None:
        """Resolve an agent's model string to a ModelDef.

        Resolution order (precise -> heuristic):
        1. model_ref: exact key in models.yaml (preferred, no ambiguity)
        2. agent_model_str: exact match
        3. Prefix match (before space/paren) -- emits a warning
        4. Lowercased prefix match -- emits a warning
        5. Unknown: returns None
        """
        if not agent_model_str and not model_ref:
            return None
        # 1. Try model_ref (precise reference, preferred)
        if model_ref and model_ref in self._config.models:
            return self._config.models[model_ref]
        # 2. Try exact match
        if agent_model_str and agent_model_str in self._config.models:
            return self._config.models[agent_model_str]
        # 3. Try prefix match (before space/paren) -- heuristic, emit warning
        if agent_model_str:
            prefix = agent_model_str.split(" ")[0].split("(")[0].strip()
            if prefix in self._config.models:
                logger.warning(
                    "heuristic prefix match '%s' for agent model '%s'. "
                    "Add model_ref for precise resolution.",
                    prefix, agent_model_str,
                )
                return self._config.models[prefix]
            # 4. Try lowercased
            if prefix.lower() in self._config.models:
                logger.warning(
                    "heuristic lowercased match '%s' for agent model '%s'. "
                    "Add model_ref for precise resolution.",
                    prefix.lower(), agent_model_str,
                )
                return self._config.models[prefix.lower()]
        return None

    def stats(self) -> dict:
        """Return registry statistics for Dashboard."""
        models = list(self._config.models.values())
        providers = list(self._config.providers.values())
        return {
            "total_models": len(models),
            "enabled_models": sum(1 for m in models if m.enabled),
            "total_providers": len(providers),
            "enabled_providers": sum(1 for p in providers if p.enabled),
            "by_quality": {
                q.value: sum(1 for m in models if m.quality_tier == q)
                for q in QualityTier
            },
            "by_latency": {
                tier.value: sum(1 for m in models if m.latency_tier == tier)
                for tier in LatencyTier
            },
            "by_provider": {
                p: sum(1 for m in models if m.provider == p)
                for p in self._config.providers
            },
            "by_protocol": {
                proto.value: sum(1 for p in providers if p.protocol == proto)
                for proto in ProtocolType
            },
            "thinking_capable": sum(1 for m in models if m.thinking.supported),
            "multimodal_models": sum(1 for m in models if m.capability_matrix.multimodal_understanding),
            "tool_calling_models": sum(1 for m in models if m.capability_matrix.tool_calling),
            "streaming_models": sum(1 for m in models if m.capability_matrix.streaming),
            "image_gen_models": sum(1 for m in models if m.capability_matrix.image_generation),
        }

    def add_provider(self, name: str, provider: ProviderDef) -> ProviderDef:
        """Add or replace a provider at runtime."""
        self._config.providers[name] = provider
        self._provider_registry.add(name, provider)
        return provider

    def remove_provider(self, name: str) -> bool:
        """Remove a provider at runtime. Fails if models reference it."""
        models_using = [m.name for m in self._config.models.values() if m.provider == name]
        if models_using:
            raise ValueError(f"Cannot remove provider '{name}': used by models {models_using}")
        del self._config.providers[name]
        self._provider_registry.remove(name)
        return True

    def add_model(self, name: str, model: ModelDef) -> ModelDef:
        """Add or replace a model at runtime."""
        model.name = name
        self._config.models[name] = model
        return model

    def remove_model(self, name: str) -> bool:
        """Remove a model at runtime."""
        if name not in self._config.models:
            return False
        del self._config.models[name]
        return True

    def save(self) -> None:
        """Persist current config back to models.yaml."""
        path = self._root / "config" / "models.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)

        def _serialize(d: dict) -> dict:
            out = {}
            for k, v in d.items():
                if isinstance(v, dict):
                    out[k] = _serialize(v)
                elif hasattr(v, "value") and isinstance(v, type) is not True:
                    out[k] = v.value
                else:
                    out[k] = v
            return out

        def _model_to_dict(name: str, m: ModelDef) -> dict:
            d = m.model_dump(exclude={"name"}, exclude_defaults=True)
            if "type" in d and hasattr(m, "type"):
                pass
            return _serialize(d)

        def _provider_to_dict(p: ProviderDef) -> dict:
            d = p.model_dump(exclude_defaults=True)
            return _serialize(d)

        data = {
            "providers": {
                name: _provider_to_dict(p)
                for name, p in self._config.providers.items()
            },
            "models": {
                name: _model_to_dict(name, m)
                for name, m in self._config.models.items()
            },
            "policies": {
                name: _serialize(p.model_dump(exclude_defaults=True))
                for name, p in self._config.policies.items()
            },
            "budget": _serialize(self._config.budget.model_dump(exclude_defaults=True)),
            "quota": {
                name: _serialize(q.model_dump(exclude_defaults=True))
                for name, q in self._config.quota.items()
            },
        }
        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
        logger.info("[registry] Saved models.yaml to %s", path)
