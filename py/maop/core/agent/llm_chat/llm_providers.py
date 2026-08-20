"""LLM provider implementations (OpenAI-compatible, Anthropic, Ollama).

Extracted from ``llm_provider.py``. These classes depend only on the pydantic
models in ``llm_models.py``, keeping the dependency graph acyclic:

    llm_models  ←  llm_providers  ←  llm_factory  ←  llm_provider (re-export)

Public symbols:
  - BaseLLMProvider
  - OpenAICompatibleProvider
  - AnthropicProvider
  - OllamaProvider
"""

from __future__ import annotations

import json
import logging
import os
import time
from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator
from typing import Any

import httpx

from maop.core.agent.llm_chat.llm_models import LLMResponse, ProviderConfig

logger = logging.getLogger(__name__)


class BaseLLMProvider(ABC):
    """Abstract base class for LLM providers.

    API Key resolution order (highest priority first):
      1. Environment variable named by ``api_key_env``
      2. ``api_key`` field on ProviderConfig (set from YAML or injected by vault)
      3. Empty string (provider marked as not configured)

    The vault injection happens in ``LLMProviderFactory._create_provider``:
    it retrieves the encrypted key from ``ApiKeyVault`` and writes it into
    ``ProviderConfig.api_key`` before constructing the provider, so this
    constructor only needs to fall back to that field.
    """

    def __init__(self, provider_config: ProviderConfig) -> None:
        self._config = provider_config
        env_key = os.environ.get(provider_config.api_key_env, "")
        self._api_key = env_key or provider_config.api_key
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
                    logger.warning("[llm] %s request failed after %d attempts: %s", self._config.name, attempt + 1, exc)
                    return LLMResponse(
                        content=f"[LLM Error] {exc}",
                        provider=self._config.name,
                        latency_ms=int((time.perf_counter() - start) * 1000),
                        error=str(exc),
                    )
                await self._backoff(attempt)
            except Exception as exc:
                logger.warning("[llm] %s request unexpected error: %s", self._config.name, exc)
                return LLMResponse(
                    content=f"[LLM Error] {exc}",
                    provider=self._config.name,
                    latency_ms=int((time.perf_counter() - start) * 1000),
                    error=str(exc),
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