"""Tests for Iteration A: LLM Provider abstraction + ChatEngine integration."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from maop.core.chat_engine import ChatEngine, ChatRequest
from maop.core.llm_provider import (
    AnthropicProvider,
    BaseLLMProvider,
    FallbackResult,
    LLMProviderFactory,
    LLMResponse,
    ModelConfig,
    OllamaProvider,
    OpenAICompatibleProvider,
    ProviderConfig,
)

# ═══════════════════════════════════════════════════════════════════
# Pydantic Model Tests
# ═══════════════════════════════════════════════════════════════════

class TestLLMResponse:
    def test_defaults(self):
        r = LLMResponse()
        assert r.content == ""
        assert r.model == ""
        assert r.finish_reason == ""
        assert r.prompt_tokens == 0
        assert r.completion_tokens == 0
        assert r.total_tokens == 0
        assert r.latency_ms == 0
        assert r.provider == ""

    def test_with_data(self):
        r = LLMResponse(content="hello", model="gpt-4", total_tokens=100, provider="openai")
        assert r.content == "hello"
        assert r.total_tokens == 100


class TestProviderConfig:
    def test_defaults(self):
        c = ProviderConfig()
        assert c.provider_type == "openai-compatible"
        assert c.protocol == "openai_completions"
        assert c.timeout_s == 120
        assert c.max_retries == 3
        assert c.enabled is True

    def test_custom(self):
        c = ProviderConfig(name="test", base_url="https://api.test.com/v1", api_key_env="TEST_KEY")
        assert c.name == "test"
        assert c.base_url == "https://api.test.com/v1"


class TestModelConfig:
    def test_defaults(self):
        m = ModelConfig()
        assert m.context_window == 32768
        assert m.max_output == 4096
        assert m.streaming is True
        assert m.multimodal_understanding is False

    def test_custom(self):
        m = ModelConfig(name="yi-large", provider="stepfun", model_id="yi-large")
        assert m.name == "yi-large"
        assert m.provider == "stepfun"


# ═══════════════════════════════════════════════════════════════════
# Provider Tests (with mocked httpx)
# ═══════════════════════════════════════════════════════════════════

def _openai_config() -> ProviderConfig:
    return ProviderConfig(
        name="stepfun",
        provider_type="openai-compatible",
        protocol="openai_completions",
        base_url="https://api.stepfun.com/v1",
        api_key_env="STEPFUN_API_KEY",
    )

def _anthropic_config() -> ProviderConfig:
    return ProviderConfig(
        name="anthropic",
        provider_type="custom",
        protocol="claude_code",
        base_url="https://api.anthropic.com/v1",
        api_key_env="ANTHROPIC_API_KEY",
        extra_headers={"anthropic-version": "2024-01-01"},
    )

def _ollama_config() -> ProviderConfig:
    return ProviderConfig(
        name="ollama",
        provider_type="ollama",
        protocol="ollama_chat",
        base_url="http://localhost:11434",
        api_key_env="",
    )


class TestOpenAICompatibleProvider:
    def test_headers(self):
        with patch.dict("os.environ", {"STEPFUN_API_KEY": "sk-test-123"}):
            p = OpenAICompatibleProvider(_openai_config())
            headers = p._get_headers()
            assert headers["Authorization"] == "Bearer sk-test-123"
            assert headers["Content-Type"] == "application/json"

    def test_is_configured_with_key(self):
        with patch.dict("os.environ", {"STEPFUN_API_KEY": "sk-test"}):
            p = OpenAICompatibleProvider(_openai_config())
            assert p.is_configured is True

    def test_is_configured_without_key(self):
        with patch.dict("os.environ", {}, clear=True):
            p = OpenAICompatibleProvider(_openai_config())
            assert p.is_configured is False

    @pytest.mark.asyncio
    async def test_chat_success(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "Hello!"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            "model": "yi-large",
        }
        mock_response.raise_for_status = MagicMock()

        with patch.dict("os.environ", {"STEPFUN_API_KEY": "sk-test"}):
            provider = OpenAICompatibleProvider(_openai_config())
            with patch("httpx.AsyncClient") as mock_client_cls:
                mock_client = AsyncMock()
                mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client.__aexit__ = AsyncMock(return_value=None)
                mock_client.post = AsyncMock(return_value=mock_response)
                mock_client_cls.return_value = mock_client

                result = await provider.chat(
                    messages=[{"role": "user", "content": "Hi"}],
                    model="yi-large",
                )
                assert result.content == "Hello!"
                assert result.model == "yi-large"
                assert result.total_tokens == 15
                assert result.provider == "stepfun"

    @pytest.mark.asyncio
    async def test_chat_error(self):
        import httpx
        mock_response = MagicMock()
        mock_response.status_code = 429
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "rate limited", request=MagicMock(), response=mock_response,
        )

        with patch.dict("os.environ", {"STEPFUN_API_KEY": "sk-test"}):
            provider = OpenAICompatibleProvider(_openai_config())
            with patch("httpx.AsyncClient") as mock_client_cls:
                mock_client = AsyncMock()
                mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client.__aexit__ = AsyncMock(return_value=None)
                mock_client.post = AsyncMock(return_value=mock_response)
                mock_client_cls.return_value = mock_client

                result = await provider.chat(
                    messages=[{"role": "user", "content": "Hi"}],
                    model="yi-large",
                )
                assert "[LLM Error]" in result.content


class TestAnthropicProvider:
    def test_headers(self):
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-ant-test"}):
            p = AnthropicProvider(_anthropic_config())
            headers = p._get_headers()
            assert headers["x-api-key"] == "sk-ant-test"
            assert headers["anthropic-version"] == "2024-01-01"

    @pytest.mark.asyncio
    async def test_chat_separates_system(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "content": [{"type": "text", "text": "Claude says hi"}],
            "usage": {"input_tokens": 20, "output_tokens": 10},
            "model": "claude-sonnet-4",
            "stop_reason": "end_turn",
        }
        mock_response.raise_for_status = MagicMock()

        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-ant-test"}):
            provider = AnthropicProvider(_anthropic_config())
            with patch("httpx.AsyncClient") as mock_client_cls:
                mock_client = AsyncMock()
                mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client.__aexit__ = AsyncMock(return_value=None)
                mock_client.post = AsyncMock(return_value=mock_response)
                mock_client_cls.return_value = mock_client

                result = await provider.chat(
                    messages=[
                        {"role": "system", "content": "You are helpful"},
                        {"role": "user", "content": "Hi"},
                    ],
                    model="claude-sonnet-4",
                )
                assert result.content == "Claude says hi"
                assert result.prompt_tokens == 20
                assert result.completion_tokens == 10

                call_args = mock_client.post.call_args
                payload = call_args.kwargs.get("json") or call_args[1].get("json")
                assert "system" in payload
                assert payload["system"] == "You are helpful"


class TestOllamaProvider:
    def test_is_configured_without_key(self):
        p = OllamaProvider(_ollama_config())
        assert p.is_configured is True

    @pytest.mark.asyncio
    async def test_chat_success(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "message": {"content": "Local model response"},
            "model": "llama3",
            "done": True,
        }
        mock_response.raise_for_status = MagicMock()

        provider = OllamaProvider(_ollama_config())
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            result = await provider.chat(
                messages=[{"role": "user", "content": "Hi"}],
                model="llama3",
            )
            assert result.content == "Local model response"
            assert result.finish_reason == "stop"


# ═══════════════════════════════════════════════════════════════════
# LLMProviderFactory Tests
# ═══════════════════════════════════════════════════════════════════

class TestLLMProviderFactory:
    def test_parse_models_yaml(self):
        factory = LLMProviderFactory()
        factory._parse_models_yaml({
            "providers": {
                "stepfun": {
                    "type": "openai-compatible",
                    "protocol": "openai_completions",
                    "base_url": "https://api.stepfun.com/v1",
                    "api_key_env": "STEPFUN_API_KEY",
                    "timeout_s": 120,
                    "max_retries": 3,
                    "enabled": True,
                },
                "ollama": {
                    "type": "ollama",
                    "protocol": "ollama_chat",
                    "base_url": "http://localhost:11434",
                    "api_key_env": "",
                    "timeout_s": 300,
                    "max_retries": 1,
                    "enabled": True,
                },
            },
            "models": {
                "yi-large": {
                    "provider": "stepfun",
                    "model_id": "yi-large",
                    "context_window": 32768,
                    "max_output": 8192,
                    "default_temperature": 0.7,
                    "max_temperature": 2.0,
                    "enabled": True,
                    "capability_matrix": {"streaming": True},
                    "cost_per_1k_input": 0.004,
                    "cost_per_1k_output": 0.012,
                },
            },
        })
        assert "stepfun" in factory._provider_configs
        assert "yi-large" in factory._model_configs
        assert factory._model_configs["yi-large"].provider == "stepfun"

    def test_get_provider_openai_compatible(self):
        factory = LLMProviderFactory()
        factory._parse_models_yaml({
            "providers": {
                "stepfun": {
                    "type": "openai-compatible",
                    "protocol": "openai_completions",
                    "base_url": "https://api.stepfun.com/v1",
                    "api_key_env": "STEPFUN_API_KEY",
                },
            },
            "models": {
                "yi-large": {"provider": "stepfun", "model_id": "yi-large"},
            },
        })
        provider = factory.get_provider("yi-large")
        assert isinstance(provider, OpenAICompatibleProvider)
        assert provider.name == "stepfun"

    def test_get_provider_anthropic(self):
        factory = LLMProviderFactory()
        factory._parse_models_yaml({
            "providers": {
                "anthropic": {
                    "type": "custom",
                    "protocol": "claude_code",
                    "base_url": "https://api.anthropic.com/v1",
                    "api_key_env": "ANTHROPIC_API_KEY",
                    "extra_headers": {"anthropic-version": "2024-01-01"},
                },
            },
            "models": {
                "claude-sonnet-4": {"provider": "anthropic", "model_id": "claude-sonnet-4"},
            },
        })
        provider = factory.get_provider("claude-sonnet-4")
        assert isinstance(provider, AnthropicProvider)

    def test_get_provider_ollama(self):
        factory = LLMProviderFactory()
        factory._parse_models_yaml({
            "providers": {
                "ollama": {
                    "type": "ollama",
                    "protocol": "ollama_chat",
                    "base_url": "http://localhost:11434",
                    "api_key_env": "",
                },
            },
            "models": {
                "llama3": {"provider": "ollama", "model_id": "llama3"},
            },
        })
        provider = factory.get_provider("llama3")
        assert isinstance(provider, OllamaProvider)

    def test_get_provider_builtin_returns_none(self):
        factory = LLMProviderFactory()
        factory._parse_models_yaml({
            "providers": {
                "local": {
                    "type": "builtin",
                    "protocol": "custom",
                    "base_url": "",
                    "api_key_env": "",
                },
            },
            "models": {
                "built-in": {"provider": "local", "model_id": "built-in"},
            },
        })
        provider = factory.get_provider("built-in")
        assert provider is None

    def test_get_provider_unknown_model(self):
        factory = LLMProviderFactory()
        factory._parse_models_yaml({"providers": {}, "models": {}})
        assert factory.get_provider("nonexistent") is None

    def test_get_provider_caches(self):
        factory = LLMProviderFactory()
        factory._parse_models_yaml({
            "providers": {
                "stepfun": {
                    "type": "openai-compatible",
                    "protocol": "openai_completions",
                    "base_url": "https://api.stepfun.com/v1",
                    "api_key_env": "STEPFUN_API_KEY",
                },
            },
            "models": {
                "yi-large": {"provider": "stepfun", "model_id": "yi-large"},
                "step-3.7-flash": {"provider": "stepfun", "model_id": "step-3.7-flash"},
            },
        })
        p1 = factory.get_provider("yi-large")
        p2 = factory.get_provider("step-3.7-flash")
        assert p1 is p2

    def test_list_models(self):
        factory = LLMProviderFactory()
        factory._loaded = True
        factory._parse_models_yaml({
            "providers": {},
            "models": {
                "a": {"provider": "x", "model_id": "a", "enabled": True},
                "b": {"provider": "y", "model_id": "b", "enabled": False},
            },
        })
        enabled = factory.list_models(enabled_only=True)
        assert len(enabled) == 1
        all_models = factory.list_models(enabled_only=False)
        assert len(all_models) == 2

    def test_list_providers(self):
        factory = LLMProviderFactory()
        factory._loaded = True
        factory._parse_models_yaml({
            "providers": {
                "a": {"type": "openai-compatible", "enabled": True},
                "b": {"type": "ollama", "enabled": False},
            },
            "models": {},
        })
        enabled = factory.list_providers(enabled_only=True)
        assert len(enabled) == 1
        all_p = factory.list_providers(enabled_only=False)
        assert len(all_p) == 2


# ═══════════════════════════════════════════════════════════════════
# ChatEngine Integration Tests (Provider path)
# ═══════════════════════════════════════════════════════════════════

class TestChatEngineProviderIntegration:
    @pytest.mark.asyncio
    async def test_call_llm_uses_provider(self, tmp_path):
        engine = ChatEngine(root_dir=tmp_path, default_model="yi-large")

        mock_provider = AsyncMock(spec=BaseLLMProvider)
        mock_provider.is_configured = True

        mock_factory = MagicMock()
        mock_factory.get_provider.return_value = mock_provider
        # _call_llm 现在统一走 chat_with_fallback，需 mock 为 AsyncMock
        mock_factory.chat_with_fallback = AsyncMock(return_value=FallbackResult(
            response=LLMResponse(
                content="Provider response", model="yi-large", provider="stepfun",
            ),
            used_model="yi-large",
            original_model="yi-large",
        ))
        engine._provider_factory = mock_factory

        request = ChatRequest(message="Hi", model="yi-large")
        result = await engine._call_llm("mavis", [{"role": "user", "content": "Hi"}], request)
        assert result == "Provider response"
        mock_factory.chat_with_fallback.assert_called_once()

    @pytest.mark.asyncio
    async def test_call_llm_fallback_on_provider_error(self, tmp_path):
        engine = ChatEngine(root_dir=tmp_path, default_model="yi-large")

        mock_provider = AsyncMock(spec=BaseLLMProvider)
        mock_provider.is_configured = True

        mock_factory = MagicMock()
        mock_factory.get_provider.return_value = mock_provider
        # _call_llm 现在统一走 chat_with_fallback；返回错误响应触发 Dispatcher fallback
        mock_factory.chat_with_fallback = AsyncMock(return_value=FallbackResult(
            response=LLMResponse(
                content="[LLM Error] timeout", provider="stepfun",
            ),
        ))
        engine._provider_factory = mock_factory

        with patch("maop.delegate.dispatcher.Dispatcher") as MockDispatcher:
            mock_result = MagicMock()
            mock_result.result.is_success.return_value = True
            mock_result.result.output = "Fallback response"
            MockDispatcher.return_value.dispatch = AsyncMock(return_value=mock_result)

            request = ChatRequest(message="Hi", model="yi-large")
            result = await engine._call_llm("mavis", [{"role": "user", "content": "Hi"}], request)
            assert result == "Fallback response"

    @pytest.mark.asyncio
    async def test_call_llm_no_model_uses_fallback(self, tmp_path):
        engine = ChatEngine(root_dir=tmp_path, default_model="")

        with patch("maop.delegate.dispatcher.Dispatcher") as MockDispatcher:
            mock_result = MagicMock()
            mock_result.result.is_success.return_value = True
            mock_result.result.output = "Dispatch response"
            MockDispatcher.return_value.dispatch = AsyncMock(return_value=mock_result)

            request = ChatRequest(message="Hi")
            result = await engine._call_llm("mavis", [{"role": "user", "content": "Hi"}], request)
            assert result == "Dispatch response"

    @pytest.mark.asyncio
    async def test_stream_llm_uses_provider(self, tmp_path):
        engine = ChatEngine(root_dir=tmp_path, default_model="yi-large")

        async def fake_stream(**kwargs):
            for token in ["Hello", " world", "!"]:
                yield token

        mock_provider = AsyncMock(spec=BaseLLMProvider)
        mock_provider.is_configured = True
        mock_provider.chat_stream = fake_stream

        mock_factory = MagicMock()
        mock_factory.get_provider.return_value = mock_provider
        mock_factory.get_model_config.return_value = ModelConfig(
            name="yi-large", provider="stepfun", model_id="yi-large",
        )
        engine._provider_factory = mock_factory

        request = ChatRequest(message="Hi", model="yi-large")
        tokens = []
        async for token in engine._stream_llm("mavis", [{"role": "user", "content": "Hi"}], request):
            tokens.append(token)
        assert tokens == ["Hello", " world", "!"]

    @pytest.mark.asyncio
    async def test_stream_llm_fallback(self, tmp_path):
        engine = ChatEngine(root_dir=tmp_path, default_model="")

        with patch("maop.delegate.dispatcher.Dispatcher") as MockDispatcher:
            mock_result = MagicMock()
            mock_result.result.is_success.return_value = True
            mock_result.result.output = "Chunked response here"
            MockDispatcher.return_value.dispatch = AsyncMock(return_value=mock_result)

            request = ChatRequest(message="Hi")
            tokens = []
            async for token in engine._stream_llm("mavis", [{"role": "user", "content": "Hi"}], request):
                tokens.append(token)
            assert "".join(tokens) == "Chunked response here"

    @pytest.mark.asyncio
    async def test_chat_request_model_field(self):
        req = ChatRequest(message="Hi", model="yi-large")
        assert req.model == "yi-large"

        req2 = ChatRequest(message="Hi")
        assert req2.model == ""

    def test_provider_factory_lazy_init(self, tmp_path):
        engine = ChatEngine(root_dir=tmp_path)
        assert engine._provider_factory is None
        factory = engine.provider_factory
        assert factory is not None
        assert isinstance(factory, LLMProviderFactory)
