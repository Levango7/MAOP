"""MAOP Chat Engine — LLM interaction with three-layer memory injection.

Orchestrates:
  1. Build context from MemoryManager (L1+L2+L3)
  2. Call LLM via LLMProvider (direct API) with Dispatcher fallback
  3. Stream response tokens via SSE (true token-level streaming)
  4. Store exchange back to memory

Usage::

    from maop.core.chat_engine import ChatEngine

    engine = ChatEngine(root_dir="/path/to/MAOP")
    async for token in engine.chat(session_id="s1", message="Fix the bug"):
        print(token, end="")
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from maop.core.llm_provider import LLMProviderFactory

from pydantic import BaseModel, Field

from maop.memory.manager import MemoryManager, MemoryManagerConfig

logger = logging.getLogger(__name__)


class ContentPart(BaseModel):
    type: str = "text"
    text: str = ""
    image_url: str = ""
    image_id: str = ""


class ChatMessage(BaseModel):
    role: str = "user"
    content: str | list[ContentPart] = ""


class ChatRequest(BaseModel):
    session_id: str = ""
    message: str = ""
    images: list[str] = Field(default_factory=list)
    agent: str = ""
    model: str = ""
    system_prompt: str = ""
    stream: bool = True
    max_tokens: int = 4096
    temperature: float = 0.7


class ChatResponse(BaseModel):
    session_id: str = ""
    message_id: str = ""
    content: str = ""
    agent: str = ""
    model: str = ""
    tokens_used: int = 0
    latency_ms: int = 0
    memory_context_tokens: int = 0
    finish_reason: str = ""


class ChatEngine:
    """Chat engine with memory injection and streaming support.

    Uses MemoryManager for three-layer context. LLM calls go through
    LLMProviderFactory for direct API access when possible, with
    Dispatcher fallback for CLI-based agents.
    """

    def __init__(
        self,
        root_dir: str | Path,
        config: MemoryManagerConfig | None = None,
        *,
        default_agent: str = "mavis",
        default_model: str = "",
        default_system_prompt: str = "",
    ) -> None:
        self._root = Path(root_dir)
        self._memory_mgr = MemoryManager(root_dir=root_dir, config=config)
        self._default_agent = default_agent
        self._default_model = default_model
        self._default_system_prompt = default_system_prompt or (
            "You are MAOP, an intelligent multi-agent orchestration assistant. "
            "You help users with coding, debugging, architecture, and project management tasks. "
            "Use the provided memory context to give informed, contextual responses."
        )
        self._provider_factory: LLMProviderFactory | None = None

    @property
    def memory(self) -> MemoryManager:
        return self._memory_mgr

    @property
    def provider_factory(self) -> LLMProviderFactory:
        if self._provider_factory is None:
            from maop.core.llm_provider import LLMProviderFactory
            self._provider_factory = LLMProviderFactory(root_dir=self._root)
        return self._provider_factory

    async def chat(self, request: ChatRequest) -> ChatResponse:
        """Non-streaming chat: send message, get full response."""
        session_id = request.session_id or f"chat-{uuid.uuid4().hex[:8]}"
        agent = request.agent or self._default_agent
        start = time.perf_counter()

        # Build context with memory injection
        messages = self._memory_mgr.get_messages_for_llm(
            session_id=session_id,
            query=request.message,
            system_prompt=request.system_prompt or self._default_system_prompt,
        )

        # Build multimodal user message if images present
        user_content = self._build_user_content(request)
        self._memory_mgr.conversation.add_message(
            session_id=session_id,
            role="user",
            content=request.message,
            metadata={"has_images": len(request.images) > 0},
        )

        # Add multimodal message to LLM context
        if request.images:
            messages.append({"role": "user", "content": user_content})
        else:
            messages.append({"role": "user", "content": request.message})

        # Call LLM via dispatcher
        content = await self._call_llm(agent, messages, request)
        latency_ms = int((time.perf_counter() - start) * 1000)

        # Store assistant response
        msg_id = self._memory_mgr.conversation.add_message(
            session_id=session_id,
            role="assistant",
            content=content,
        )

        # Store to L2 memory
        self._memory_mgr.add_exchange(
            session_id=session_id,
            user_msg=request.message,
            assistant_msg=content,
            agent=agent,
        )

        return ChatResponse(
            session_id=session_id,
            message_id=msg_id,
            content=content,
            agent=agent,
            latency_ms=latency_ms,
        )

    async def chat_stream(self, request: ChatRequest) -> AsyncGenerator[str, None]:
        """Streaming chat: yield SSE-formatted tokens."""
        session_id = request.session_id or f"chat-{uuid.uuid4().hex[:8]}"
        agent = request.agent or self._default_agent

        # Build context
        messages = self._memory_mgr.get_messages_for_llm(
            session_id=session_id,
            query=request.message,
            system_prompt=request.system_prompt or self._default_system_prompt,
        )

        # Store user message
        self._memory_mgr.conversation.add_message(
            session_id=session_id,
            role="user",
            content=request.message,
        )

        # Yield session info first
        yield _sse_event("session", {"session_id": session_id, "agent": agent})

        # Stream LLM response
        full_content = []
        try:
            async for token in self._stream_llm(agent, messages, request):
                full_content.append(token)
                yield _sse_event("token", {"content": token})
        except Exception as exc:
            yield _sse_event("error", {"error": str(exc)})
            return

        content = "".join(full_content)
        token_count = len(content) // 4
        model_name = request.model or self._default_model or ""

        # Store assistant response
        self._memory_mgr.conversation.add_message(
            session_id=session_id,
            role="assistant",
            content=content,
            metadata={"model": model_name, "tokens": token_count},
        )

        # Store to L2 memory
        self._memory_mgr.add_exchange(
            session_id=session_id,
            user_msg=request.message,
            assistant_msg=content,
            agent=agent,
        )

        yield _sse_event("done", {"session_id": session_id, "content_length": len(content), "tokens": token_count, "model": model_name})

    async def _call_llm(
        self,
        agent: str,
        messages: list[dict[str, Any]],
        request: ChatRequest,
    ) -> str:
        """Call LLM via LLMProvider (direct API) with Dispatcher fallback."""
        model_name = request.model or self._default_model
        provider = self.provider_factory.get_provider(model_name) if model_name else None

        if provider and provider.is_configured:
            try:
                # 统一走 chat_with_fallback 以触发 _record_cost 成本记录与 fallback 链
                result = await self.provider_factory.chat_with_fallback(
                    messages, model_name,
                    temperature=request.temperature,
                    max_tokens=request.max_tokens,
                    agent=agent,
                )
                response = result.response
                if response.content and not response.content.startswith("["):
                    return response.content
                logger.warning("[chat_engine] Provider returned error, falling back: %s", response.content)
            except Exception as exc:
                logger.warning("[chat_engine] Provider call failed, falling back: %s", exc)

        return await self._call_llm_fallback(agent, messages, request)

    async def _call_llm_fallback(
        self,
        agent: str,
        messages: list[dict[str, Any]],
        request: ChatRequest,
    ) -> str:
        """Fallback: call LLM via the Dispatcher/CLI system."""
        try:
            # Intentional lazy import: maop.delegate depends on maop.core at
            # module load time, so importing it at the top of this module would
            # create a circular import. The delayed binding keeps the strict
            # downward dependency direction intact (core never top-level
            # imports delegate); reviewed per audit item 4.5.
            from maop.config.loader import ConfigLoader
            from maop.delegate.dispatcher import Dispatcher

            loader = ConfigLoader()
            config = loader.load()
            dispatcher = Dispatcher(MAOP_config=config)

            task_text = messages[-1].get("content", "") if messages else request.message
            result = await dispatcher.dispatch(
                agent=agent,
                task=task_text,
                routing_key="chat",
            )

            if result.result and result.result.is_success():
                return getattr(result.result, "output", None) or result.result.error or "No response"
            return result.result.error if result.result else "Dispatch failed"  # type: ignore[return-value]
        except Exception as exc:
            logger.warning("[chat_engine] LLM call failed: %s", exc)
            return f"[MAOP] Unable to reach agent '{agent}': {exc}"

    async def _stream_llm(
        self,
        agent: str,
        messages: list[dict[str, Any]],
        request: ChatRequest,
    ) -> AsyncGenerator[str, None]:
        """Stream LLM response token by token via Provider, with Dispatcher fallback."""
        model_name = request.model or self._default_model
        provider = self.provider_factory.get_provider(model_name) if model_name else None

        if provider and provider.is_configured:
            model_cfg = self.provider_factory.get_model_config(model_name)
            model_id = model_cfg.model_id if model_cfg else model_name
            try:
                async for token in provider.chat_stream(
                    messages=messages,
                    model=model_id,
                    temperature=request.temperature,
                    max_tokens=request.max_tokens,
                ):
                    yield token
                return
            except Exception as exc:
                logger.warning("[chat_engine] Provider stream failed, falling back: %s", exc)

        async for token in self._stream_llm_fallback(agent, messages, request):
            yield token

    async def _stream_llm_fallback(
        self,
        agent: str,
        messages: list[dict[str, Any]],
        request: ChatRequest,
    ) -> AsyncGenerator[str, None]:
        """Fallback: stream via Dispatcher (simulated chunking)."""
        try:
            from maop.config.loader import ConfigLoader
            from maop.delegate.dispatcher import Dispatcher

            loader = ConfigLoader()
            config = loader.load()
            dispatcher = Dispatcher(MAOP_config=config)

            task_text = messages[-1].get("content", "") if messages else request.message
            result = await dispatcher.dispatch(
                agent=agent,
                task=task_text,
                routing_key="chat",
            )

            if result.result and result.result.is_success():
                output = getattr(result.result, "output", "") or ""
                chunk_size = max(1, len(output) // 20)
                for i in range(0, len(output), chunk_size):
                    yield output[i:i + chunk_size]
                    await asyncio.sleep(0.01)
            else:
                error = result.result.error if result.result else "Dispatch failed"
                yield error or "Dispatch failed"
        except Exception as exc:
            yield f"[MAOP] Error: {exc}"


    def _build_user_content(self, request: ChatRequest) -> str | list[dict[str, Any]]:
        """Build multimodal user content for LLM API."""
        if not request.images:
            return request.message

        parts: list[dict[str, Any]] = [{"type": "text", "text": request.message}]
        for img_ref in request.images:
            if img_ref.startswith(("data:", "http")):
                parts.append({
                    "type": "image_url",
                    "image_url": {"url": img_ref},
                })
            else:
                try:
                    from maop.core.image_store import ImageStore
                    store = ImageStore(root_dir=self._root)
                    b64 = store.get_base64(img_ref)
                    if b64:
                        meta = store.get_meta(img_ref)
                        mime = meta.content_type if meta else "image/png"
                        parts.append({
                            "type": "image_url",
                            "image_url": {"url": f"data:{mime};base64,{b64}"},
                        })
                except Exception as exc:
                    logger.warning("[chat_engine] Failed to load image %s: %s", img_ref, exc)
        return parts


def _sse_event(event: str, data: dict[str, Any]) -> str:
    """Format an SSE event."""
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"
