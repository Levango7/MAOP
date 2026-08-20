"""MAOP LLM Provider — Unified LLM API abstraction layer.

Supports:
  - OpenAI-compatible APIs (OpenAI, DeepSeek, StepFun, Aliyun, etc.)
  - Anthropic Claude API
  - Ollama local models
  - Automatic provider selection from models.yaml
  - True token-level streaming via SSE
  - Multimodal content (text + images)
  - Cost tracking integration
  - Retry with exponential backoff

Usage::

    from maop.core.agent.llm_chat.llm_provider import LLMProviderFactory

    factory = LLMProviderFactory(root_dir="/path/to/MAOP")
    provider = factory.get_provider("yi-large")

    # Non-streaming
    response = await provider.chat(messages=[...], model="yi-large")

    # Streaming
    async for token in provider.chat_stream(messages=[...], model="yi-large"):
        print(token, end="")

---

This module is a thin re-export facade kept for backward compatibility.
The implementation has been split into three sub-modules to avoid a
monolithic file and to make the dependency graph acyclic:

    llm_models.py      — pydantic models (LLMResponse, FallbackResult,
                          ProviderConfig, ModelConfig)
    llm_providers.py   — provider classes (BaseLLMProvider,
                          OpenAICompatibleProvider, AnthropicProvider,
                          OllamaProvider)
    llm_factory.py     — LLMProviderFactory + _is_error_response +
                          _record_cost

All public names below remain importable from this module unchanged.
"""

from __future__ import annotations

from maop.core.agent.llm_chat.llm_factory import (  # noqa: F401, RUF100
    LLMProviderFactory,
    _is_error_response,
    _record_cost,
)
from maop.core.agent.llm_chat.llm_models import (  # noqa: F401, RUF100
    FallbackResult,
    LLMResponse,
    ModelConfig,
    ProviderConfig,
)
from maop.core.agent.llm_chat.llm_providers import (  # noqa: F401, RUF100
    AnthropicProvider,
    BaseLLMProvider,
    OllamaProvider,
    OpenAICompatibleProvider,
)

__all__ = [
    "AnthropicProvider",
    "BaseLLMProvider",
    "FallbackResult",
    "LLMProviderFactory",
    "LLMResponse",
    "ModelConfig",
    "OllamaProvider",
    "OpenAICompatibleProvider",
    "ProviderConfig",
    "_is_error_response",
    "_record_cost",
]
