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

    from maop.core.llm_provider import LLMProviderFactory

    factory = LLMProviderFactory(root_dir="/path/to/MAOP")
    provider = factory.get_provider("yi-large")

    # Non-streaming
    response = await provider.chat(messages=[...], model="yi-large")

    # Streaming
    async for token in provider.chat_stream(messages=[...], model="yi-large"):
        print(token, end="")
"""

from __future__ import annotations

import json
import logging
import os
import time
from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)


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


class BaseLLMProvider(ABC):
    """Abstract base class for LLM providers."""

    def __init__(self, provider_config: ProviderConfig) -> None:
        self._config = provider_config
        self._api_key = os.environ.get(provider_config.api_key_env, "")
        self._client: httpx.AsyncClient | None = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=self._config.timeout_s,
                headers=self._get_headers(),
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    @abstractmethod
    async def chat(
        self,
        messages: list[dict[str, Any]],
        model: str = "",
        *,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        **kwargs: Any,
    ) -> LLMResponse:
        ...

    @abstractmethod
    def chat_stream(
        self,
        messages: list[dict[str, Any]],
        model: str = "",
        *,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        **kwargs: Any,
    ) -> AsyncGenerator[str, None]:
        ...

    def _get_headers(self) -> dict[str, str]:
        return {}

    @property
    def name(self) -> str:
        return self._config.name

    @property
    def is_configured(self) -> bool:
        return bool(self._api_key) or self._config.provider_type == "ollama"


class OpenAICompatibleProvider(BaseLLMProvider):
    """Provider for OpenAI-compatible APIs (OpenAI, DeepSeek, StepFun, Aliyun, etc.)."""

    def _get_headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._api_key}",
        }
        headers.update(self._config.extra_headers)
        return headers

    async def chat(
        self,
        messages: list[dict[str, Any]],
        model: str = "",
        *,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        **kwargs: Any,
    ) -> LLMResponse:
        url = f"{self._config.base_url}/chat/completions"
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if kwargs.get("tools"):
            payload["tools"] = kwargs["tools"]

        start = time.perf_counter()
        client = self._get_client()
        for attempt in range(self._config.max_retries):
            try:
                resp = await client.post(url, json=payload)
                resp.raise_for_status()
                data = resp.json()
                break
            except (httpx.HTTPStatusError, httpx.TimeoutException) as exc:
                if attempt == self._config.max_retries - 1:
                    return LLMResponse(
                        content=f"[LLM Error] {exc}",
                        provider=self._config.name,
                        latency_ms=int((time.perf_counter() - start) * 1000),
                    )
                await self._backoff(attempt)
            except Exception as exc:
                return LLMResponse(
                    content=f"[LLM Error] {exc}",
                    provider=self._config.name,
                    latency_ms=int((time.perf_counter() - start) * 1000),
                )

        latency_ms = int((time.perf_counter() - start) * 1000)
        choice = data.get("choices", [{}])[0]
        usage = data.get("usage", {})

        return LLMResponse(
            content=choice.get("message", {}).get("content", ""),
            model=data.get("model", model),
            finish_reason=choice.get("finish_reason", ""),
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            total_tokens=usage.get("total_tokens", 0),
            latency_ms=latency_ms,
            provider=self._config.name,
        )

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        model: str = "",
        *,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        **kwargs: Any,
    ) -> AsyncGenerator[str, None]:
        url = f"{self._config.base_url}/chat/completions"
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }

        client = self._get_client()
        for attempt in range(self._config.max_retries):
            try:
                async with client.stream(
                    "POST", url, json=payload,
                ) as resp:
                    resp.raise_for_status()
                    async for line in resp.aiter_lines():
                        if not line.startswith("data: "):
                            continue
                        data_str = line[6:].strip()
                        if data_str == "[DONE]":
                            return
                        try:
                            chunk = json.loads(data_str)
                            delta = chunk.get("choices", [{}])[0].get("delta", {})
                            token = delta.get("content", "")
                            if token:
                                yield token
                        except json.JSONDecodeError:
                            continue
                    return
            except (httpx.HTTPStatusError, httpx.TimeoutException) as exc:
                if attempt == self._config.max_retries - 1:
                    yield f"[LLM Stream Error] {exc}"
                    return
                await self._backoff(attempt)
            except Exception as exc:
                yield f"[LLM Stream Error] {exc}"
                return

    @staticmethod
    async def _backoff(attempt: int) -> None:
        import asyncio
        await asyncio.sleep(min(0.5 * (2 ** attempt), 10))


class AnthropicProvider(BaseLLMProvider):
    """Provider for Anthropic Claude API."""

    def _get_headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "x-api-key": self._api_key,
            "anthropic-version": self._config.extra_headers.get("anthropic-version", "2023-06-01"),
        }

    async def chat(
        self,
        messages: list[dict[str, Any]],
        model: str = "",
        *,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        url = f"{self._config.base_url}/messages"
        system_content = ""
        chat_messages = []
        for m in messages:
            if m.get("role") == "system":
                system_content = m.get("content", "")
            else:
                chat_messages.append(m)

        payload: dict[str, Any] = {
            "model": model,
            "messages": chat_messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if system_content:
            payload["system"] = system_content

        # 转换 OpenAI 工具格式为 Anthropic 格式并注入 payload
        if tools:
            anthropic_tools: list[dict[str, Any]] = []
            for t in tools:
                if t.get("type") == "function":
                    fn = t.get("function", {})
                    anthropic_tools.append({
                        "name": fn.get("name", ""),
                        "description": fn.get("description", ""),
                        "input_schema": fn.get("parameters", {"type": "object", "properties": {}}),
                    })
                else:
                    # 已经是简化格式的工具定义
                    anthropic_tools.append({
                        "name": t.get("name", ""),
                        "description": t.get("description", ""),
                        "input_schema": t.get("input_schema") or t.get("parameters", {"type": "object", "properties": {}}),
                    })
            payload["tools"] = anthropic_tools

        start = time.perf_counter()
        client = self._get_client()
        for attempt in range(self._config.max_retries):
            try:
                resp = await client.post(url, json=payload)
                resp.raise_for_status()
                data = resp.json()
                break
            except (httpx.HTTPStatusError, httpx.TimeoutException) as exc:
                if attempt == self._config.max_retries - 1:
                    return LLMResponse(
                        content=f"[Claude Error] {exc}",
                        provider=self._config.name,
                        latency_ms=int((time.perf_counter() - start) * 1000),
                    )
                await OpenAICompatibleProvider._backoff(attempt)
            except Exception as exc:
                return LLMResponse(
                    content=f"[Claude Error] {exc}",
                    provider=self._config.name,
                    latency_ms=int((time.perf_counter() - start) * 1000),
                )

        latency_ms = int((time.perf_counter() - start) * 1000)
        content_blocks = data.get("content", [])
        content = "".join(b.get("text", "") for b in content_blocks if b.get("type") == "text")
        usage = data.get("usage", {})

        # 解析 Anthropic 响应中的 tool_use blocks，转换为 OpenAI 兼容格式
        tool_calls: list[dict[str, Any]] = []
        for block in content_blocks:
            if block.get("type") == "tool_use":
                tool_calls.append({
                    "id": block.get("id", ""),
                    "type": "function",
                    "function": {
                        "name": block.get("name", ""),
                        "arguments": json.dumps(block.get("input", {})),
                    },
                })

        return LLMResponse(
            content=content,
            model=data.get("model", model),
            finish_reason=data.get("stop_reason", ""),
            prompt_tokens=usage.get("input_tokens", 0),
            completion_tokens=usage.get("output_tokens", 0),
            total_tokens=usage.get("input_tokens", 0) + usage.get("output_tokens", 0),
            latency_ms=latency_ms,
            provider=self._config.name,
            tool_calls=tool_calls,
        )

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        model: str = "",
        *,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        **kwargs: Any,
    ) -> AsyncGenerator[str, None]:
        url = f"{self._config.base_url}/messages"
        system_content = ""
        chat_messages = []
        for m in messages:
            if m.get("role") == "system":
                system_content = m.get("content", "")
            else:
                chat_messages.append(m)

        payload: dict[str, Any] = {
            "model": model,
            "messages": chat_messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": True,
        }
        if system_content:
            payload["system"] = system_content

        client = self._get_client()
        for attempt in range(self._config.max_retries):
            try:
                async with client.stream(
                    "POST", url, json=payload,
                ) as resp:
                    resp.raise_for_status()
                    async for line in resp.aiter_lines():
                        if not line.startswith("data: "):
                            continue
                        data_str = line[6:].strip()
                        try:
                            event = json.loads(data_str)
                            if event.get("type") == "content_block_delta":
                                delta = event.get("delta", {})
                                if delta.get("type") == "text_delta":
                                    yield delta.get("text", "")
                            elif event.get("type") == "message_stop":
                                return
                        except json.JSONDecodeError:
                            continue
                    return
            except (httpx.HTTPStatusError, httpx.TimeoutException) as exc:
                if attempt == self._config.max_retries - 1:
                    yield f"[Claude Stream Error] {exc}"
                    return
                await OpenAICompatibleProvider._backoff(attempt)
            except Exception as exc:
                yield f"[Claude Stream Error] {exc}"
                return


class OllamaProvider(BaseLLMProvider):
    """Provider for local Ollama models."""

    async def chat(
        self,
        messages: list[dict[str, Any]],
        model: str = "",
        *,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        **kwargs: Any,
    ) -> LLMResponse:
        url = f"{self._config.base_url}/api/chat"
        payload = {
            "model": model,
            "messages": messages,
            "stream": False,
            "options": {"temperature": temperature, "num_predict": max_tokens},
        }

        start = time.perf_counter()
        client = self._get_client()
        try:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            return LLMResponse(
                content=f"[Ollama Error] {exc}",
                provider=self._config.name,
                latency_ms=int((time.perf_counter() - start) * 1000),
            )

        latency_ms = int((time.perf_counter() - start) * 1000)
        content = data.get("message", {}).get("content", "")

        return LLMResponse(
            content=content,
            model=data.get("model", model),
            finish_reason="stop" if data.get("done") else "",
            latency_ms=latency_ms,
            provider=self._config.name,
        )

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        model: str = "",
        *,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        **kwargs: Any,
    ) -> AsyncGenerator[str, None]:
        url = f"{self._config.base_url}/api/chat"
        payload = {
            "model": model,
            "messages": messages,
            "stream": True,
            "options": {"temperature": temperature, "num_predict": max_tokens},
        }

        client = self._get_client()
        try:
            async with client.stream("POST", url, json=payload) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        chunk = json.loads(line)
                        token = chunk.get("message", {}).get("content", "")
                        if token:
                            yield token
                        if chunk.get("done"):
                            return
                    except json.JSONDecodeError:
                        continue
        except Exception as exc:
            yield f"[Ollama Stream Error] {exc}"


class LLMProviderFactory:
    """Factory for creating LLM providers from models.yaml configuration.

    Loads provider and model definitions from models.yaml, creates the
    appropriate provider instance, and caches them for reuse.
    """

    def __init__(self, root_dir: str | Path | None = None) -> None:
        self._root = Path(root_dir) if root_dir else Path(".")
        self._providers: dict[str, BaseLLMProvider] = {}
        self._provider_configs: dict[str, ProviderConfig] = {}
        self._model_configs: dict[str, ModelConfig] = {}
        self._default_provider: str = ""
        self._default_model: str = ""
        self._loaded = False

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        try:
            from maop.config.loader import ConfigLoader
            loader = ConfigLoader(project_root=self._root)
            config = loader.load()
            models_yaml = config._raw_models
            if models_yaml:
                self._parse_models_yaml(models_yaml)
                return
            models_path = self._root / "config" / "models.yaml"
            if models_path.exists():
                import yaml
                with open(models_path, encoding="utf-8") as f:
                    models_yaml = yaml.safe_load(f) or {}
                self._parse_models_yaml(models_yaml)
        except Exception as exc:
            logger.warning("[llm_provider] Failed to load models.yaml: %s", exc)

    def _parse_models_yaml(self, data: dict) -> None:
        for name, pdata in data.get("providers", {}).items():
            self._provider_configs[name] = ProviderConfig(
                name=name,
                provider_type=pdata.get("type", "openai-compatible"),
                protocol=pdata.get("protocol", "openai_completions"),
                base_url=pdata.get("base_url", ""),
                api_key_env=pdata.get("api_key_env", ""),
                timeout_s=pdata.get("timeout_s", 120),
                max_retries=pdata.get("max_retries", 3),
                enabled=pdata.get("enabled", True),
                extra_headers=pdata.get("extra_headers", {}),
            )

        for name, mdata in data.get("models", {}).items():
            cap_matrix = mdata.get("capability_matrix", {})
            self._model_configs[name] = ModelConfig(
                name=name,
                provider=mdata.get("provider", ""),
                model_id=mdata.get("model_id", name),
                context_window=mdata.get("context_window", 32768),
                max_output=mdata.get("max_output", 4096),
                default_temperature=mdata.get("default_temperature", 0.7),
                max_temperature=mdata.get("max_temperature", 2.0),
                streaming=cap_matrix.get("streaming", True),
                multimodal_understanding=cap_matrix.get("multimodal_understanding", False),
                tool_calling=cap_matrix.get("tool_calling", False),
                enabled=mdata.get("enabled", True),
                cost_per_1k_input=mdata.get("cost_per_1k_input", 0.0),
                cost_per_1k_output=mdata.get("cost_per_1k_output", 0.0),
                fallback_model=mdata.get("fallback_model", ""),
            )

        # Phase 2: OmniRoute default exit — top-level default_provider/default_model
        self._default_provider = str(data.get("default_provider") or "")
        self._default_model = str(data.get("default_model") or "")

    def get_provider(self, model_name: str) -> BaseLLMProvider | None:
        """Get a provider instance for the given model name."""
        self._ensure_loaded()

        model_cfg = self._model_configs.get(model_name)
        if model_cfg is None:
            return None

        provider_name = model_cfg.provider
        if provider_name in self._providers:
            return self._providers[provider_name]

        provider_cfg = self._provider_configs.get(provider_name)
        if provider_cfg is None:
            return None

        provider = self._create_provider(provider_cfg)
        if provider:
            self._providers[provider_name] = provider
        return provider

    def get_model_config(self, model_name: str) -> ModelConfig | None:
        self._ensure_loaded()
        return self._model_configs.get(model_name)

    def list_models(self, enabled_only: bool = True) -> list[ModelConfig]:
        self._ensure_loaded()
        models = list(self._model_configs.values())
        if enabled_only:
            models = [m for m in models if m.enabled]
        return models

    def list_providers(self, enabled_only: bool = True) -> list[ProviderConfig]:
        self._ensure_loaded()
        providers = list(self._provider_configs.values())
        if enabled_only:
            providers = [p for p in providers if p.enabled]
        return providers

    def _create_provider(self, cfg: ProviderConfig) -> BaseLLMProvider | None:
        ptype = cfg.provider_type
        if ptype in ("openai-compatible", "custom"):
            if cfg.protocol == "claude_code":
                return AnthropicProvider(cfg)
            return OpenAICompatibleProvider(cfg)
        if ptype == "ollama":
            return OllamaProvider(cfg)
        if ptype == "builtin":
            return None
        logger.warning("[llm_provider] Unknown provider type: %s", ptype)
        return None

    def _get_default_model(self) -> str:
        """Return the configured default model name, if any.

        Used as the fallback model of last resort when no model is
        specified and all other selection logic fails.
        """
        self._ensure_loaded()
        return self._default_model

    async def aclose(self) -> None:
        for provider in self._providers.values():
            await provider.aclose()
        self._providers.clear()

    async def chat_with_fallback(
        self,
        messages: list[dict[str, Any]],
        model_name: str,
        *,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        max_fallback_depth: int = 5,
        **kwargs: Any,
    ) -> FallbackResult:
        """Chat with automatic fallback chain on failure.

        If the primary model returns an error response (content starts with
        ``[LLM Error]``, ``[Claude Error]``, or ``[Ollama Error]``), the
        factory walks the fallback_model chain until a successful response
        or the chain is exhausted.

        Parameters
        ----------
        messages : list[dict]
            Chat messages.
        model_name : str
            Primary model to try.
        temperature : float
            Sampling temperature.
        max_tokens : int
            Max output tokens.
        max_fallback_depth : int
            Maximum fallback steps to prevent infinite loops.
        **kwargs
            Extra args forwarded to ``provider.chat()``.

        Returns
        -------
        FallbackResult
        """
        # Phase 2: If no model specified, try the configured default
        if not model_name:
            default_model = self._get_default_model()
            if default_model:
                model_name = default_model
                logger.debug("[llm-provider] No model specified, using default: %s", model_name)

        chain: list[str] = [model_name]
        original_model = model_name
        current = model_name

        for _ in range(max_fallback_depth):
            provider = self.get_provider(current)
            if provider is None:
                logger.warning("[fallback] No provider for model '%s'", current)
                break

            model_cfg = self.get_model_config(current)
            model_id = model_cfg.model_id if model_cfg else current

            try:
                resp = await provider.chat(
                    messages, model=model_id,
                    temperature=temperature, max_tokens=max_tokens, **kwargs,
                )
            except Exception as exc:
                logger.warning("[fallback] Exception from '%s': %s", current, exc)
                resp = LLMResponse(
                    content=f"[LLM Error] {exc}",
                    provider=getattr(provider, "name", current),
                )

            if not _is_error_response(resp):
                _record_cost(resp, kwargs)
                return FallbackResult(
                    response=resp,
                    used_model=current,
                    original_model=original_model,
                    fallback_chain=chain,
                    fell_back=(current != original_model),
                )

            logger.info(
                "[fallback] Model '%s' failed, trying fallback...",
                current,
            )

            next_model = ""
            if model_cfg and model_cfg.fallback_model:
                next_model = model_cfg.fallback_model
            if not next_model or next_model in chain:
                break
            chain.append(next_model)
            current = next_model

        last_provider = self.get_provider(current)
        last_model_cfg = self.get_model_config(current)
        last_model_id = last_model_cfg.model_id if last_model_cfg else current

        if last_provider is not None:
            resp = await last_provider.chat(
                messages, model=last_model_id,
                temperature=temperature, max_tokens=max_tokens, **kwargs,
            )
        else:
            resp = LLMResponse(content="[LLM Error] All providers exhausted", provider="none")

        _record_cost(resp, kwargs)
        return FallbackResult(
            response=resp,
            used_model=current,
            original_model=original_model,
            fallback_chain=chain,
            fell_back=(current != original_model),
        )


def _is_error_response(resp: LLMResponse) -> bool:
    """Check if an LLMResponse indicates a provider error."""
    if not resp.content:
        return True
    return resp.content.startswith(("[LLM Error]", "[Claude Error]", "[Ollama Error]"))

def _record_cost(resp: LLMResponse, kwargs: dict[str, Any]) -> None:
    """Auto-record LLM call metrics to CostTracker (best-effort).

    Extracts session_id/agent from kwargs when callers pass them.
    Failures are logged as warnings but never break the LLM call.
    """
    try:
        from maop.core.cost_tracker import get_cost_tracker
        get_cost_tracker().record(
            model=resp.model,
            prompt_tokens=resp.prompt_tokens,
            completion_tokens=resp.completion_tokens,
            total_tokens=resp.total_tokens,
            latency_ms=resp.latency_ms,
            session_id=str(kwargs.get("session_id", "")),
            agent=str(kwargs.get("agent", "")),
            metadata={"provider": resp.provider} if resp.provider else None,
        )
    except Exception as exc:
        logger.warning("[llm_provider] CostTracker record failed: %s", exc)
