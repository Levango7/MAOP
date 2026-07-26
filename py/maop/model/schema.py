"""Model schema definitions — Pydantic models for models.yaml.

Extended with protocol types, capability matrix, and thinking mode configuration.
Thinking mode levels standard:
  low    — ~500-1000 reasoning tokens, +10-20% latency. Simple chat, quick codegen.
  medium — ~2000-4000 reasoning tokens, +30-50% latency. Planning, review, refactor.
  high   — ~8000-16000 reasoning tokens, +100-200% latency. Architecture, security, deep debug.

API parameter mapping by protocol:
  openai_responses:  reasoning_effort = "low"|"medium"|"high"
  openai_completions: extra_body = {"reasoning_effort": "low"|"medium"|"high"}
  claude_code:       thinking_budget = 1024|4096|16384
  custom:            thinking = "low"|"medium"|"high" (provider-specific)
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

# ── Enums ─────────────────────────────────────────────────────

class ProviderType(str, Enum):
    OPENAI_COMPATIBLE = "openai-compatible"
    CUSTOM = "custom"
    BUILTIN = "builtin"
    OLLAMA = "ollama"


class ProtocolType(str, Enum):
    """API protocol used to communicate with the provider."""
    OPENAI_RESPONSES = "openai_responses"       # /v1/responses endpoint (newer OpenAI)
    OPENAI_COMPLETIONS = "openai_completions"   # /v1/chat/completions (standard)
    CLAUDE_CODE = "claude_code"                 # Anthropic Claude Code protocol
    OLLAMA_CHAT = "ollama_chat"                 # Ollama /api/chat endpoint
    CUSTOM = "custom"                           # Provider-specific protocol


class LatencyTier(str, Enum):
    INSTANT = "instant"
    FAST = "fast"
    MEDIUM = "medium"
    SLOW = "slow"


class QualityTier(str, Enum):
    EXCELLENT = "excellent"
    GOOD = "good"
    FAIR = "fair"
    POOR = "poor"


class SelectionStrategy(str, Enum):
    CHEAPEST = "cheapest"
    FASTEST = "fastest"
    BEST_QUALITY = "best_quality"
    BEST_QUALITY_WITHIN_BUDGET = "best_quality_within_budget"


class ThinkingLevel(str, Enum):
    """Thinking mode intensity levels.

    Standard:
      low:    ~500-1000 reasoning tokens, +10-20% latency.
              Use for: simple chat, quick codegen, formatting, straightforward tasks.
      medium: ~2000-4000 reasoning tokens, +30-50% latency.
              Use for: planning, code review, refactoring, moderate debugging.
      high:   ~8000-16000 reasoning tokens, +100-200% latency.
              Use for: architecture decisions, security analysis, complex debugging,
                       multi-step reasoning, cross-file analysis.
    """
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


# ── Capability matrix ────────────────────────────────────────

class CapabilityMatrix(BaseModel):
    """Structured capability declaration for a model.

    Replaces the flat capabilities list with explicit booleans.
    The legacy capabilities list is kept for backward compatibility.
    """
    text_chat: bool = True              # Basic text conversation
    multimodal_understanding: bool = False  # Image/audio/video input understanding
    image_generation: bool = False      # Can generate images
    tool_calling: bool = False          # Supports function/tool calling
    streaming: bool = True              # Supports streaming responses
    code_execution: bool = False        # Can execute code (sandboxed)
    web_search: bool = False            # Built-in web search capability
    long_context: bool = False          # Supports >100K context window


# ── Thinking mode config ─────────────────────────────────────

class ThinkingModeConfig(BaseModel):
    """Thinking mode configuration for a model.

    Defines whether the model supports extended thinking/reasoning,
    which levels are available, and the default level.
    """
    supported: bool = False             # Whether this model supports thinking mode
    default_level: ThinkingLevel = ThinkingLevel.MEDIUM  # Level when thinking is enabled but unspecified
    available_levels: list[ThinkingLevel] = Field(
        default_factory=lambda: [ThinkingLevel.LOW, ThinkingLevel.MEDIUM, ThinkingLevel.HIGH]
    )
    # Token budgets per level (provider-specific, used for cost estimation)
    token_budget_low: int = 512
    token_budget_medium: int = 3072
    token_budget_high: int = 12288


# ── Provider ──────────────────────────────────────────────────

class ProviderDef(BaseModel):
    """A model provider definition.

    Extended with protocol type, direct API key, and extra headers.
    """
    type: ProviderType = ProviderType.OPENAI_COMPATIBLE
    protocol: ProtocolType = ProtocolType.OPENAI_COMPLETIONS  # API protocol
    base_url: str = ""
    api_key_env: str = ""               # Environment variable name for API key
    api_key: str = ""                   # Direct API key (takes precedence over env)
    timeout_s: int = 120
    max_retries: int = 3
    health_check_url: str = ""
    extra_headers: dict[str, str] = Field(default_factory=dict)  # Custom headers per request
    enabled: bool = True


# ── Model ─────────────────────────────────────────────────────

class ModelDef(BaseModel):
    """A model definition in the registry.

    Extended with model_id, capability matrix, thinking mode, and temperature config.
    """
    model_config = ConfigDict(protected_namespaces=())
    name: str = ""                      # Populated from dict key (registry name)
    model_id: str = ""                  # Actual model ID passed to the API (defaults to name)
    provider: str = ""
    family: str = ""
    context_window: int = 32768
    max_output: int = 8192
    cost_per_1k_input: float = 0.0
    cost_per_1k_output: float = 0.0
    # Legacy flat capability list (kept for backward compat)
    capabilities: list[str] = Field(default_factory=list)
    # Structured capability matrix
    capability_matrix: CapabilityMatrix = Field(default_factory=CapabilityMatrix)
    # Thinking mode configuration
    thinking: ThinkingModeConfig = Field(default_factory=ThinkingModeConfig)
    # Temperature defaults
    default_temperature: float = 0.7
    max_temperature: float = 2.0
    # Tiers
    latency_tier: LatencyTier = LatencyTier.MEDIUM
    quality_tier: QualityTier = QualityTier.GOOD
    enabled: bool = True


# ── Policy ────────────────────────────────────────────────────

class ModelPolicy(BaseModel):
    """A model selection policy."""
    strategy: SelectionStrategy = SelectionStrategy.BEST_QUALITY_WITHIN_BUDGET
    max_cost_per_task: float = 0.05
    prefer_low_latency: bool = False
    fallback_on_error: bool = True
    fallback_on_timeout: bool = True
    fallback_on_quota_exceeded: bool = True


# ── Budget ────────────────────────────────────────────────────

class BudgetConfig(BaseModel):
    """Budget configuration."""
    daily_limit: float = 5.0
    monthly_limit: float = 100.0
    alert_threshold: float = 0.8
    hard_stop: bool = True


# ── Quota ─────────────────────────────────────────────────────

class QuotaConfig(BaseModel):
    """Per-provider quota configuration."""
    requests_per_minute: int = 60
    tokens_per_minute: int = 100000


# ── Top-level config ──────────────────────────────────────────

class ModelRegistryConfig(BaseModel):
    """Top-level models.yaml configuration."""
    providers: dict[str, ProviderDef] = Field(default_factory=dict)
    models: dict[str, ModelDef] = Field(default_factory=dict)
    policies: dict[str, ModelPolicy] = Field(default_factory=dict)
    budget: BudgetConfig = Field(default_factory=BudgetConfig)
    quota: dict[str, QuotaConfig] = Field(default_factory=dict)
    default_provider: str = ""
    """When set, ModelSelector prefers models from this provider as primary.
    Empty string = no preference (use legacy strategy-based selection)."""
    default_model: str = ""
    """When set, used as the fallback model of last resort if all else fails.
    Empty string = no default model."""


# ── Effective model (runtime) ─────────────────────────────────

class EffectiveModel(BaseModel):
    """The resolved model to use for a specific dispatch.

    This is what Dispatcher receives after ModelSelector picks a model.
    Extended with protocol, capability matrix, and thinking mode info.
    """
    model_config = ConfigDict(protected_namespaces=())
    model_name: str
    provider: str
    model_id: str = ""                  # Actual API model ID
    protocol: ProtocolType = ProtocolType.OPENAI_COMPLETIONS
    cli_model_arg: str = ""  # the string to pass as --model to CLI
    cost_estimate: float = 0.0
    fallback_chain: list[str] = Field(default_factory=list)
    policy_name: str = "default"
    capability_matrix: CapabilityMatrix = Field(default_factory=CapabilityMatrix)
    thinking: ThinkingModeConfig = Field(default_factory=ThinkingModeConfig)
    base_url: str = ""
    api_key: str = ""                   # Resolved API key (from env or direct)


# ── Thinking level -> API parameter mapping (Standard) ───────

THINKING_TOKEN_BUDGETS: dict[ThinkingLevel, int] = {
    ThinkingLevel.LOW: 512,
    ThinkingLevel.MEDIUM: 3072,
    ThinkingLevel.HIGH: 12288,
}

THINKING_LATENCY_MULTIPLIER: dict[ThinkingLevel, float] = {
    ThinkingLevel.LOW: 1.15,       # +15%
    ThinkingLevel.MEDIUM: 1.40,    # +40%
    ThinkingLevel.HIGH: 2.00,      # +100%
}


def thinking_to_api_params(
    level: ThinkingLevel,
    protocol: ProtocolType,
    config: ThinkingModeConfig | None = None,
) -> dict[str, object]:
    """Map a thinking level to provider-specific API parameters.

    Standard mapping:
      openai_responses:   {"reasoning_effort": "low"|"medium"|"high"}
      openai_completions: {"extra_body": {"reasoning_effort": "low"|"medium"|"high"}}
      claude_code:        {"thinking_budget": 1024|4096|16384}
      custom:             {"thinking": "low"|"medium"|"high"}
    """
    if config:
        budgets = {
            ThinkingLevel.LOW: config.token_budget_low,
            ThinkingLevel.MEDIUM: config.token_budget_medium,
            ThinkingLevel.HIGH: config.token_budget_high,
        }
    else:
        budgets = THINKING_TOKEN_BUDGETS

    if protocol == ProtocolType.OPENAI_RESPONSES:
        return {"reasoning_effort": level.value}

    if protocol == ProtocolType.OPENAI_COMPLETIONS:
        return {"extra_body": {"reasoning_effort": level.value}}

    if protocol == ProtocolType.CLAUDE_CODE:
        claude_budgets = {
            ThinkingLevel.LOW: 1024,
            ThinkingLevel.MEDIUM: 4096,
            ThinkingLevel.HIGH: 16384,
        }
        return {"thinking_budget": claude_budgets.get(level, budgets.get(level, 4096))}

    if protocol == ProtocolType.OLLAMA_CHAT:
        return {"options": {"num_predict": budgets.get(level, 3072)}}

    return {"thinking": level.value}
