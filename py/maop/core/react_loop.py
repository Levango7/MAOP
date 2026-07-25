"""MAOP ReAct Loop — Thought → Action → Observation micro-cycle engine.

Implements the ReAct (Reasoning + Acting) pattern for agent execution:
  1. **Thought**: Agent reasons about the current state and decides what to do
  2. **Action**: Agent invokes a tool or takes an action
  3. **Observation**: The result of the action is observed and fed back

This loop continues until:
  - The agent produces a final answer (no more tool_calls)
  - Maximum iterations are reached
  - A termination condition is met (error, timeout, budget)

The ReAct loop integrates with:
  - FunctionCallBridge for tool execution
  - ConversationManager for message history
  - ChangeTracker for file change monitoring
  - PermissionManager for action approval

F3 (2026-07-22, Phase F): dual-path execution per ADR-013. When
``ReactConfig.enable_llm`` is True and a model is configured, the loop
calls ``LLMProviderFactory.chat_with_fallback()`` directly (LLM main
path). On LLM failure or when disabled, it falls back to the original
``dispatcher.dispatch()`` CLI path (preserving prior behavior). See
``docs/adr/013-agent-llm-direct-cli-fallback.md``.

Usage::

    from maop.core.react_loop import ReactLoop, ReactConfig

    loop = ReactLoop(config=ReactConfig(max_iterations=10))
    result = await loop.run(
        task="Fix the bug in main.py",
        agent="mavis",
        dispatcher=dispatcher,
    )
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from enum import Enum
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from maop.core.error_schema import MaopResult, new_result

if TYPE_CHECKING:
    from maop.core.llm_provider import LLMProviderFactory

logger = logging.getLogger(__name__)


class ReactPhase(str, Enum):
    THOUGHT = "thought"
    ACTION = "action"
    OBSERVATION = "observation"
    FINAL = "final"
    ERROR = "error"


class ReactStep(BaseModel):
    iteration: int = 0
    phase: ReactPhase = ReactPhase.THOUGHT
    content: str = ""
    tool_name: str = ""
    tool_args: dict[str, Any] = Field(default_factory=dict)
    tool_result: Any = None
    tool_error: str = ""
    duration_ms: int = 0
    timestamp: float = 0.0


class ReactResult(BaseModel):
    session_id: str = ""
    task: str = ""
    agent: str = ""
    steps: list[ReactStep] = Field(default_factory=list)
    final_answer: str = ""
    total_iterations: int = 0
    total_tool_calls: int = 0
    total_duration_ms: int = 0
    success: bool = False
    error: str = ""
    token_count: int = 0


class ReactConfig(BaseModel):
    max_iterations: int = 10
    max_tool_calls: int = 30
    max_total_tokens: int = 100000
    timeout_seconds: int = 300
    enable_change_tracking: bool = True
    require_approval_for_write: bool = False
    provider: str = "openai"
    # F3a (2026-07-22, Phase F): LLM direct-call toggle (ADR-013 dual-path).
    # When True + llm_model non-empty + provider_factory available, the
    # loop calls LLMProviderFactory.chat_with_fallback() directly instead
    # of going through dispatcher.dispatch() → CLI subprocess. On LLM
    # failure, automatically falls back to CLI path. Default False
    # preserves prior CLI-only behavior.
    enable_llm: bool = False
    llm_model: str = ""  # model_name key in models.yaml


class ReactLoop:
    """ReAct micro-cycle engine: Thought → Action → Observation.

    This is the inner loop that runs within a single maop_execute call.
    It handles the iterative process of:
      1. Sending the task + conversation to the LLM
      2. Parsing the response for tool calls
      3. Executing tool calls via FunctionCallBridge
      4. Feeding results back into the conversation
      5. Repeating until a final answer or limit is reached
    """

    def __init__(
        self,
        config: ReactConfig | None = None,
        root_dir: str | None = None,
    ) -> None:
        self._config = config or ReactConfig()
        self._root_dir = root_dir
        self._bridge: Any = None
        self._change_tracker: Any = None
        # F3b (2026-07-22, Phase F): Lazily-initialized LLMProviderFactory
        # for direct API calls (ADR-013 dual-path). Kept None until first
        # access so a ReactLoop works without any LLM provider configured.
        # See the ``provider_factory`` property below.
        self._provider_factory: LLMProviderFactory | None = None

    @property
    def config(self) -> ReactConfig:
        return self._config

    @property
    def provider_factory(self) -> LLMProviderFactory | None:
        """Lazily-initialized LLMProviderFactory for direct LLM API calls.

        F3b (2026-07-22, Phase F): constructed on first access so a ReactLoop
        without any LLM provider configured remains fully functional
        (CLI-only path). Returns None if the factory cannot be built
        (config missing / import error); callers must handle None by
        falling back to the CLI dispatcher path. See ADR-013.
        """
        if self._provider_factory is None:
            try:
                from maop.core.llm_provider import LLMProviderFactory
                self._provider_factory = LLMProviderFactory(root_dir=self._root_dir)
            except Exception as exc:
                logger.warning("[react_loop] LLMProviderFactory init failed: %s", exc)
                # Leave _provider_factory as None so subsequent accesses
                # retry. _execute_step treats factory=None as "fall back
                # to CLI dispatcher", so this is a safe degradation path.
        return self._provider_factory

    def _get_bridge(self):
        if self._bridge is None:
            from maop.core.function_call import FunctionCallBridge
            self._bridge = FunctionCallBridge(root_dir=self._root_dir)
        return self._bridge

    def _get_change_tracker(self):
        if self._change_tracker is None and self._root_dir:
            try:
                from maop.core.change_tracker import ChangeTracker
                self._change_tracker = ChangeTracker(root_dir=self._root_dir)
            except Exception:
                pass
        return self._change_tracker

    @staticmethod
    def _estimate_tokens(messages: list[dict[str, Any]]) -> int:
        total = 0
        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, str):
                total += len(content)
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict):
                        total += len(block.get("text", ""))
            total += len(json.dumps(msg.get("tool_calls", []), ensure_ascii=False))
        estimated = max(1, total // 4)
        logger.warning("[react_loop] Token count is heuristic estimate (len//4) — not from provider, cost accuracy is UNKNOWN")
        return estimated

    def _trim_conversation(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if len(messages) <= 2:
            return messages
        estimated = self._estimate_tokens(messages)
        if estimated <= self._config.max_total_tokens:
            return messages
        logger.warning(
            "[react_loop] Conversation %d tokens exceeds limit %d, trimming",
            estimated, self._config.max_total_tokens,
        )
        system_msgs = [m for m in messages if m.get("role") == "system"]
        non_system = [m for m in messages if m.get("role") != "system"]
        if len(non_system) <= 2:
            return messages
        kept = [non_system[0], non_system[-1]]
        summary_msg = {
            "role": "system",
            "content": f"[Conversation trimmed: {len(non_system) - 2} earlier messages summarized to fit context window]",
        }
        return system_msgs + [summary_msg] + kept

    async def _call_llm(
        self,
        conversation: list[dict[str, Any]],
        model_name: str,
        trace_id: str,
        *,
        tools: list[dict] | None = None,
    ) -> MaopResult:
        """F3c (2026-07-22, Phase F): LLM direct call returning a MaopResult.

        Wraps ``LLMProviderFactory.chat_with_fallback()`` so the result
        can flow through the same ``exec_result`` code path as the CLI
        dispatcher. On success, ``stdout`` carries the LLM response text
        and ``duration_ms`` carries the provider-reported latency. On
        failure, returns a failed MaopResult so the caller can decide
        whether to fall back to CLI. See ADR-013.

        Parameters
        ----------
        conversation : list[dict]
            当前对话历史，作为 messages 传给 LLM。
        model_name : str
            目标模型名（models.yaml 中的 key）。
        trace_id : str
            追踪 ID，用于日志关联。
        tools : list[dict] | None
            可用工具的 JSON Schema 列表，会透传给
            ``chat_with_fallback`` → ``provider.chat(tools=...)``，
            使 LLM 知道有哪些工具可调用。None 表示不传工具。
        """
        try:
            factory = self.provider_factory
            if factory is None:
                return new_result(
                    agent="", task="",
                    exit_code=-1, error="LLM provider factory not available",
                    trace_id=trace_id,
                )
            # 构造传给 chat_with_fallback 的 kwargs，仅当 tools 非空时透传，
            # 避免覆盖 provider 默认行为（chat_with_fallback 通过 **kwargs
            # 转发到 provider.chat，OpenAICompatibleProvider 已识别 tools）
            chat_kwargs: dict[str, Any] = {
                "messages": conversation,
                "model_name": model_name,
                "temperature": 0.7,
                "max_tokens": self._config.max_total_tokens,
            }
            if tools:
                chat_kwargs["tools"] = tools
            fb_result = await factory.chat_with_fallback(**chat_kwargs)
            content = fb_result.response.content or ""
            return new_result(
                agent="", task="",
                exit_code=0, stdout=content,
                duration_ms=fb_result.response.latency_ms,
                trace_id=trace_id,
            )
        except Exception as exc:
            logger.warning(
                "[react_loop] LLM direct call failed (model=%s): %s",
                model_name, exc,
            )
            return new_result(
                agent="", task="",
                exit_code=-1, error=f"LLM call failed: {exc}",
                trace_id=trace_id,
            )

    async def run(
        self,
        task: str,
        agent: str,
        dispatcher: Any,
        *,
        workdir: str = "",
        trace_id: str = "",
        session_id: str = "",
        tools: list[dict] | None = None,
        provider: str = "",
    ) -> ReactResult:
        start = time.monotonic()
        if not trace_id:
            trace_id = uuid.uuid4().hex
        if not session_id:
            session_id = f"react-{uuid.uuid4().hex[:8]}"

        prov = provider or self._config.provider
        result = ReactResult(
            session_id=session_id,
            task=task,
            agent=agent,
        )

        conversation: list[dict[str, Any]] = [
            {"role": "user", "content": task},
        ]

        if self._config.enable_change_tracking:
            tracker = self._get_change_tracker()
            if tracker and workdir:
                tracker.snapshot(workdir, label=f"react-start-{session_id}")

        for iteration in range(self._config.max_iterations):
            conversation = self._trim_conversation(conversation)

            step = ReactStep(
                iteration=iteration,
                phase=ReactPhase.THOUGHT,
                timestamp=time.time(),
            )

            try:
                intent = task
                for msg in conversation:
                    if msg.get("role") == "user" and isinstance(msg.get("content"), str):
                        intent = msg["content"]
                        break

                # F3c (2026-07-22, Phase F): Dual-path execution per ADR-013.
                # When enable_llm=True + llm_model configured + factory
                # available, try LLM direct call first. On LLM failure
                # (exception or error response), transparently fall back
                # to the original CLI dispatcher path. When enable_llm
                # is False (default), behavior is identical to prior
                # CLI-only flow.
                exec_result: MaopResult
                used_llm_path = False

                if (
                    self._config.enable_llm
                    and self._config.llm_model
                    and self.provider_factory is not None
                ):
                    # C-5 修复：把 run() 接收的 tools 透传给 _call_llm，
                    # 进而通过 chat_with_fallback → provider.chat(tools=...)
                    # 告知 LLM 可用工具列表
                    llm_result = await self._call_llm(
                        conversation=conversation,
                        model_name=self._config.llm_model,
                        trace_id=trace_id,
                        tools=tools,
                    )
                    if llm_result.is_success():
                        exec_result = llm_result
                        used_llm_path = True
                        logger.debug(
                            "[react_loop] iter=%d used LLM path (model=%s)",
                            iteration, self._config.llm_model,
                        )
                    else:
                        logger.info(
                            "[react_loop] iter=%d LLM path failed (%s), falling back to CLI dispatcher",
                            iteration, llm_result.error,
                        )

                if not used_llm_path:
                    # Original CLI path via dispatcher.dispatch(). Preserved
                    # verbatim from pre-Phase-F behavior so that when
                    # enable_llm=False (default) execution is identical.
                    dispatch_result = await dispatcher.dispatch(
                        agent=agent,
                        task=intent,
                        _react_context=json.dumps(conversation, ensure_ascii=False) if len(conversation) > 1 else None,
                        routing_key="react",
                        workdir=workdir,
                        timeout_seconds=self._config.timeout_seconds,
                        trace_id=trace_id,
                    )
                    exec_result = dispatch_result.result
            except Exception as exc:
                step.phase = ReactPhase.ERROR
                step.content = str(exc)
                result.steps.append(step)
                result.error = f"Dispatch error at iteration {iteration}: {exc}"
                result.success = False
                break

            if not exec_result.is_success():
                step.phase = ReactPhase.ERROR
                step.content = exec_result.error or "Execution failed"
                result.steps.append(step)
                result.error = exec_result.error or "Execution failed"
                result.success = False
                break

            response_text = exec_result.stdout or ""
            step.content = response_text[:500]
            step.duration_ms = exec_result.duration_ms

            try:
                response_json = json.loads(response_text)
            except (json.JSONDecodeError, ValueError):
                step.phase = ReactPhase.FINAL
                result.steps.append(step)
                result.final_answer = response_text
                result.success = True
                break

            bridge = self._get_bridge()
            tool_calls = bridge.parse_response(response_json, provider=prov)

            if not tool_calls:
                step.phase = ReactPhase.FINAL
                result.steps.append(step)
                final_content = response_text
                if isinstance(response_json, dict):
                    choices = response_json.get("choices", [])
                    if choices:
                        msg = choices[0].get("message", {})
                        final_content = msg.get("content", response_text)
                    content_blocks = response_json.get("content", [])
                    if isinstance(content_blocks, list):
                        texts = [b.get("text", "") for b in content_blocks if b.get("type") == "text"]
                        if texts:
                            final_content = "\n".join(texts)
                result.final_answer = final_content
                result.success = True
                break

            step.phase = ReactPhase.ACTION
            result.steps.append(step)

            if prov.lower() in ("openai", "ollama"):
                assistant_msg: dict[str, Any] = {"role": "assistant", "content": None, "tool_calls": []}
                raw_calls = response_json.get("choices", [{}])[0].get("message", {}).get("tool_calls", [])
                assistant_msg["tool_calls"] = raw_calls
                conversation.append(assistant_msg)
            elif prov.lower() == "anthropic":
                conversation.append({"role": "assistant", "content": response_json.get("content", [])})

            for call in tool_calls:
                if result.total_tool_calls >= self._config.max_tool_calls:
                    result.error = f"Max tool calls ({self._config.max_tool_calls}) reached"
                    result.success = False
                    break

                obs_step = ReactStep(
                    iteration=iteration,
                    phase=ReactPhase.OBSERVATION,
                    tool_name=call.name,
                    tool_args=call.arguments,
                    timestamp=time.time(),
                )
                result.steps.append(obs_step)
                result.total_tool_calls += 1

            if result.error and "Max tool calls" in result.error:
                break

            if result.steps:
                import asyncio
                obs_indices = [
                    i for i, s in enumerate(result.steps)
                    if s.phase == ReactPhase.OBSERVATION and s.iteration == iteration
                ]
                if obs_indices:
                    coros = [bridge.execute(tool_calls[j - obs_indices[0]]) for j in obs_indices]
                    outcomes = await asyncio.gather(*coros, return_exceptions=True)
                    for idx, outcome in zip(obs_indices, outcomes):
                        call_idx = idx - obs_indices[0]
                        call = tool_calls[call_idx]
                        obs_step = result.steps[idx]
                        if isinstance(outcome, Exception):
                            obs_step.tool_error = str(outcome)
                            obs_step.phase = ReactPhase.ERROR
                            conversation.append({
                                "role": "tool",
                                "tool_call_id": call.id or call.name,
                                "content": json.dumps({"error": str(outcome)}),
                            })
                        else:
                            call_result = outcome
                            obs_step.tool_result = getattr(call_result, "output", "")
                            obs_step.tool_error = getattr(call_result, "error", "") if not getattr(call_result, "success", True) else ""
                            obs_step.duration_ms = getattr(call_result, "duration_ms", 0)
                            tool_msg = bridge.format_result(call, call_result, provider=prov)
                            conversation.append(tool_msg)

            if result.error and "Max tool calls" in result.error:
                break

        result.total_iterations = iteration + 1 if result.steps else 0
        result.total_duration_ms = int((time.monotonic() - start) * 1000)

        if self._config.enable_change_tracking:
            tracker = self._get_change_tracker()
            if tracker and workdir:
                tracker.snapshot(workdir, label=f"react-end-{session_id}")

        if not result.final_answer and not result.error:
            result.error = f"ReAct loop exhausted {self._config.max_iterations} iterations without final answer"
            result.success = False

        return result
