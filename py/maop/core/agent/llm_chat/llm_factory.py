"""LLMProviderFactory and helper functions for the MAOP LLM provider layer.

Extracted from ``llm_provider.py``. This module sits at the top of the
provider dependency chain and pulls together the models
(``llm_models``) and provider implementations (``llm_providers``).

Public symbols:
  - LLMProviderFactory
  - _is_error_response
  - _record_cost
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from maop.core.agent.llm_chat.llm_models import (
    FallbackResult,
    LLMResponse,
    ModelConfig,
    ProviderConfig,
)
from maop.core.agent.llm_chat.llm_providers import (
    AnthropicProvider,
    BaseLLMProvider,
    OllamaProvider,
    OpenAICompatibleProvider,
)

logger = logging.getLogger(__name__)


class LLMProviderFactory:
    """Factory for creating LLM providers from models.yaml configuration.

    Loads provider and model definitions from models.yaml, creates the
    appropriate provider instance, and caches them for reuse.
    """

    def __init__(
        self,
        root_dir: str | Path | None = None,
        vault: Any = None,
    ) -> None:
        self._root = Path(root_dir) if root_dir else Path(".")
        self._providers: dict[str, BaseLLMProvider] = {}
        self._provider_configs: dict[str, ProviderConfig] = {}
        self._model_configs: dict[str, ModelConfig] = {}
        self._default_provider: str = ""
        self._default_model: str = ""
        self._loaded = False
        # Vault is optional; if not supplied we try to construct one lazily
        # from root_dir so that dashboard-stored keys are picked up.
        self._vault = vault

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
                api_key=pdata.get("api_key", ""),
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
        # Inject API key from vault if env var is not set and no static key
        # is configured in YAML. This closes the gap between the dashboard
        # key-store endpoint (which writes to ApiKeyVault) and the runtime
        # provider (which previously only read environment variables).
        if cfg.api_key_env and not os.environ.get(cfg.api_key_env) and not cfg.api_key:
            vault = self._get_vault()
            if vault is not None:
                try:
                    stored = vault.retrieve(cfg.name)
                except Exception as exc:
                    logger.debug(
                        "[llm_provider] vault.retrieve(%s) failed: %s",
                        cfg.name, exc,
                    )
                    stored = None
                if stored:
                    cfg.api_key = stored
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

    def _get_vault(self) -> Any:
        """Lazily construct an ApiKeyVault rooted at the project root.

        Returns ``None`` if the vault cannot be initialised (e.g. the
        ``cryptography`` package is missing). The instance is cached on
        ``self._vault`` so subsequent calls reuse it.
        """
        if self._vault is not None:
            return self._vault
        try:
            from maop.core.security.api_key_vault import ApiKeyVault
            self._vault = ApiKeyVault(root_dir=str(self._root))
        except Exception as exc:
            logger.debug("[llm_provider] ApiKeyVault init failed: %s", exc)
            self._vault = None
        return self._vault

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
                await _record_cost(resp, kwargs)
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

        await _record_cost(resp, kwargs)
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


async def _record_cost(resp: LLMResponse, kwargs: dict[str, Any]) -> None:
    """Auto-record LLM call metrics to CostTracker (best-effort, async).

    P2-P3 fix (M5): 改为 async，通过 record_async() 避免阻塞事件循环。
    若 tracker 无 record_async 方法则回退到同步 record()（向后兼容）。

    Extracts session_id/agent from kwargs when callers pass them.
    Failures are logged as warnings but never break the LLM call.
    """
    try:
        from maop.core.cost_tracker import get_cost_tracker
        tracker = get_cost_tracker()
        if hasattr(tracker, "record_async"):
            await tracker.record_async(
                model=resp.model,
                prompt_tokens=resp.prompt_tokens,
                completion_tokens=resp.completion_tokens,
                total_tokens=resp.total_tokens,
                latency_ms=resp.latency_ms,
                session_id=str(kwargs.get("session_id", "")),
                agent=str(kwargs.get("agent", "")),
                metadata={"provider": resp.provider} if resp.provider else None,
            )
        else:
            # Fallback: 同步调用（tracker 无 async 版本时兼容）
            tracker.record(
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