"""Response models for ``/api/model/*`` — the 8 endpoints the dashboard calls.

Note on the ``status`` defaults: every success path is built as
``{"status": "ok", **service_result}``, while several
``@handle_api_errors(error_value=...)`` fallbacks omit ``status`` entirely
(e.g. ``{"agents": [], "count": 0, "error": "..."}``). Those fallback dicts are
returned as plain dicts, so they ARE validated by ``response_model`` — a
``status = "ok"`` default would label an error response as a success. Defaulting
to ``"error"`` keeps the wire truthful on both paths: success always sets
``ok`` explicitly, absence means the fallback branch.

Enums are declared as ``str`` on purpose: the service flattens them via
``.value`` (``model_service.list_models``), and a bad YAML tier would then
degrade to a string instead of a 500.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ModelAgentRow(BaseModel):
    name: str = ""
    cli: str = ""
    driver: str = ""
    model: str = ""
    timeout_s: int = 120
    capabilities: list[str] = []
    description: str = ""
    cli_available: bool = False
    cli_path: str = ""


class ModelAgentsEnvelope(BaseModel):
    status: str = "error"
    agents: list[ModelAgentRow] = []
    count: int = 0
    error: str = ""


class QuotaAgentRow(BaseModel):
    #: The producer's key is ``agent`` (not ``name``) — Models.vue reads it as
    #: an agent, and a ``name`` here would silently blank the column.
    agent: str = ""
    cli: str = ""
    model: str = ""
    available: bool = False
    cli_path: str = ""
    driver: str = ""


class ModelQuotaEnvelope(BaseModel):
    status: str = "error"
    agents: list[QuotaAgentRow] = []
    count: int = 0
    error: str = ""


class RegistryStats(BaseModel):
    """``ModelRegistry.stats()`` — the ``by_*`` maps are keyed by provider name."""

    total_models: int = 0
    enabled_models: int = 0
    total_providers: int = 0
    enabled_providers: int = 0
    by_quality: dict[str, int] = Field(default_factory=dict)
    by_latency: dict[str, int] = Field(default_factory=dict)
    by_provider: dict[str, int] = Field(default_factory=dict)
    by_protocol: dict[str, int] = Field(default_factory=dict)
    thinking_capable: int = 0
    multimodal_models: int = 0
    tool_calling_models: int = 0
    streaming_models: int = 0
    image_gen_models: int = 0


class RegistryEnvelope(BaseModel):
    status: str = "error"
    stats: RegistryStats | None = None
    error: str = ""


class ModelRow(BaseModel):
    name: str = ""
    provider: str = ""
    family: str = ""
    context_window: int = 0
    max_output: int = 0
    cost_per_1k_input: float = 0.0
    cost_per_1k_output: float = 0.0
    capabilities: list[str] = []
    latency_tier: str = ""
    quality_tier: str = ""
    enabled: bool = True
    provider_healthy: bool = True


class ModelListEnvelope(BaseModel):
    status: str = "error"
    models: list[ModelRow] = []
    count: int = 0
    error: str = ""


class ProviderRow(BaseModel):
    """``api_key`` / ``api_key_env`` / ``extra_headers`` are never emitted."""

    name: str = ""
    type: str = ""
    enabled: bool = True
    protocol: str = ""
    healthy: bool = True
    has_api_key: bool = False
    base_url: str = ""


class ProviderListEnvelope(BaseModel):
    status: str = "error"
    providers: list[ProviderRow] = []
    error: str = ""


class PolicyRow(BaseModel):
    #: ``ModelPolicy.fallback_on_quota_exceeded`` is deliberately absent — the
    #: service never emits it, so declaring it would invent a default on the wire.
    name: str = ""
    strategy: str = ""
    max_cost_per_task: float = 0.0
    prefer_low_latency: bool = False
    fallback_on_error: bool = True
    fallback_on_timeout: bool = True


class PolicyListEnvelope(BaseModel):
    status: str = "error"
    policies: list[PolicyRow] = []
    count: int = 0
    error: str = ""


class BudgetStats(BaseModel):
    daily_spend: float = 0.0
    daily_limit: float = 0.0
    monthly_spend: float = 0.0
    monthly_limit: float = 0.0
    alert_threshold: float = 0.0
    hard_stop: bool = False
    daily_remaining: float = 0.0
    daily_utilization: float = 0.0
    alerted: bool = False


class BudgetEnvelope(BaseModel):
    status: str = "error"
    budget: BudgetStats | None = None
    error: str = ""


class SwitchEnvelope(BaseModel):
    status: str = "error"
    agent: str = ""
    model: str = ""
    error: str = ""


class QuotaUsageRow(BaseModel):
    """One provider's rolling window (``QuotaEnforcer.usage``)."""

    provider: str = ""
    requests_used: int = 0
    requests_limit: int = 0
    tokens_used: int = 0
    tokens_limit: int = 0
    window_s: int = 60


class QuotaStatusEnvelope(BaseModel):
    #: ``usage_all()`` returns a **list**, not the dict an old contract test claimed.
    status: str = "error"
    quotas: list[QuotaUsageRow] = []
    error: str = ""


class EffectiveModelPublic(BaseModel):
    """``EffectiveModel`` minus the resolved credential.

    ``EffectiveModel.api_key`` holds a live key (env or inline) for the
    dispatcher; the dashboard never needs it, so the HTTP boundary drops it and
    reports presence instead — the same shape ``/api/model/providers`` uses.
    """

    model_config = ConfigDict(protected_namespaces=())

    model_name: str = ""
    provider: str = ""
    model_id: str = ""
    protocol: str = ""
    cli_model_arg: str = ""
    cost_estimate: float = 0.0
    fallback_chain: list[str] = []
    policy_name: str = "default"
    capability_matrix: dict[str, Any] = Field(default_factory=dict)
    thinking: dict[str, Any] = Field(default_factory=dict)
    base_url: str = ""
    has_api_key: bool = False

    @model_validator(mode="before")
    @classmethod
    def _redact_credential(cls, data: Any) -> Any:
        """Replace ``api_key`` with ``has_api_key`` instead of defaulting it.

        Dropping the field silently would leave ``has_api_key`` false even when
        a key is configured, which is its own kind of wrong answer.
        """
        if isinstance(data, dict) and "api_key" in data:
            out = dict(data)
            out["has_api_key"] = bool(out.pop("api_key"))
            return out
        return data


class ModelSelectEnvelope(BaseModel):
    status: str = "error"
    effective_model: EffectiveModelPublic | None = None
    error: str = ""
