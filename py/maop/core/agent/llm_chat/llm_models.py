"""Pydantic models for the MAOP LLM provider abstraction.

Extracted from ``llm_provider.py`` to keep model definitions independent of
the provider implementations and factory logic, avoiding circular imports.

Public symbols:
  - LLMResponse
  - FallbackResult
  - ProviderConfig
  - ModelConfig
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class LLMResponse(BaseModel):
    content: str = ""
    model: str = ""
    finish_reason: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    latency_ms: int = 0
    provider: str = ""
    # 工具调用结果（OpenAI 兼容格式），供 ReactLoop 等编排器消费
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    # P1 fix: 异常信息结构化字段，调用方可据此判断失败（而非嗅探 content 前缀）
    error: str = ""


class FallbackResult(BaseModel):
    """Result of a chat_with_fallback call, including fallback chain info."""
    response: LLMResponse
    used_model: str = ""
    original_model: str = ""
    fallback_chain: list[str] = Field(default_factory=list)
    fell_back: bool = False


class ProviderConfig(BaseModel):
    name: str = ""
    provider_type: str = "openai-compatible"
    protocol: str = "openai_completions"
    base_url: str = ""
    api_key_env: str = ""
    api_key: str = ""  # 直接配置的 key（如 omniroute 的 "dummy"），优先级低于 env 和 vault
    timeout_s: int = 120
    max_retries: int = 3
    enabled: bool = True
    extra_headers: dict[str, str] = Field(default_factory=dict)


class ModelConfig(BaseModel):
    model_config = ConfigDict(protected_namespaces=())
    name: str = ""
    provider: str = ""
    model_id: str = ""
    context_window: int = 32768
    max_output: int = 4096
    default_temperature: float = 0.7
    max_temperature: float = 2.0
    streaming: bool = True
    multimodal_understanding: bool = False
    tool_calling: bool = True
    enabled: bool = True
    cost_per_1k_input: float = 0.0
    cost_per_1k_output: float = 0.0
    fallback_model: str = ""