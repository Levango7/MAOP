"""MAOP Execute — Dispatch task to agent via Dispatcher.

Task execution wrapper with guardrails.: wraps Dispatcher.dispatch() with
guardrail pre/post checks, timeout, and observability hooks.

v3.6.0: Added ``tools`` / ``provider`` / ``max_tool_rounds`` parameters
for function-calling support.  When the agent response contains
tool_calls, the FunctionCallBridge executes them and re-dispatches
with the tool results injected back into the conversation.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any

from pydantic import BaseModel

from maop.core.reliability.error_schema import MaopResult, new_result
from maop.core.security.guardrail import Guardrail
from maop.delegate.dispatcher import Dispatcher

logger = logging.getLogger(__name__)


# ── P1-13: Agent token-stream event emission ──────────────────────────
# Helpers that publish agent execution tokens to the global EventBus so
# the /api/stream/agent/{execution_id} SSE endpoint can forward them to
# the frontend in real time. All helpers are fire-and-forget and never
# raise — emitter failures are swallowed to keep the orchestration main
# flow intact (mirrors DagProgressEmitter semantics in
# maop/core/agent/dag/dag_progress_emitter.py).


def _emit_agent_event(
    execution_id: str,
    event_type: str,
    data: dict[str, Any],
) -> None:
    """Publish an agent token-stream event to the global EventBus.

    Emits on topic ``agent.{execution_id}.{event_type}`` so the
    ``/api/stream/agent/{execution_id}`` SSE endpoint can subscribe via
    the ``agent.{execution_id}.*`` wildcard and forward tokens to the
    frontend in real time.

    Parameters
    ----------
    execution_id : str
        The execution/trace id scoping the stream.
    event_type : str
        Event subtype: ``"meta"``, ``"token"``, ``"done"``, ``"error"``.
    data : dict
        Event payload. For token events: ``{"content": ..., "type": "token"}``.
    """
    try:
        from maop.core.reliability.event_bus import Event, get_event_bus

        bus = get_event_bus()
        bus.publish_sync(Event(
            topic=f"agent.{execution_id}.{event_type}",
            data=data,
        ))
    except Exception:
        logger.debug(
            "[execute] agent event emit failed for %s/%s",
            execution_id, event_type, exc_info=True,
        )


def _make_token_line_callback(execution_id: str) -> Any:
    """Build a SubprocessStreamer line_callback that emits each stdout line as a token event.

    P1-13: bridges subprocess stdout (the agent's streamed output) to the
    EventBus so /api/stream/agent/{execution_id} receives real tokens
    instead of only the final result after the process exits.
    """
    def _on_line(line: str) -> None:
        if not line:
            return
        _emit_agent_event(
            execution_id, "token", {"content": line, "type": "token"},
        )
    return _on_line


class Observability(BaseModel):
    """Observability hooks for execution."""
    trace_id: str = ""
    span_id: str = ""
    start_time: float = 0.0
    duration_ms: int = 0


class Delegate(BaseModel):
    """Delegate parameters for execution."""
    agent: str
    task: str
    routing_key: str = ""
    workdir: str = ""
    timeout_seconds: int = 120
    trace_id: str = ""
    tools: list[dict] | None = None
    provider: str = "openai"
    max_tool_rounds: int = 5
    react_mode: bool = False
    react_max_iterations: int = 10


async def _check_permission_and_hooks(
    agent: str,
    task: str,
    routing_key: str,
    trace_id: str,
    permission_manager: Any = None,
) -> MaopResult | None:
    """Run permission check and pre_dispatch hook before dispatch.

    Returns ``None`` if dispatch may proceed, or a ``MaopResult``
    (exit_code=126) if permission/hook denies the call.

    P0 fix: extracted so both the ``react_mode`` branch and the normal
    dispatch branch go through the same security gate. Previously
    (maop_execute.py:202-286) the ``react_mode`` branch returned before
    reaching the permission check and pre_dispatch hook, allowing a
    caller to bypass all security gates by setting ``react_mode=True``.
    """
    # Permission check — consult PermissionManager before dispatch
    try:
        from pathlib import Path as _Path

        from maop.core.security.permission import PermissionManager
        _root = _Path(__file__).resolve().parent.parent.parent
        pm = permission_manager if permission_manager is not None else PermissionManager(root_dir=str(_root))
        perm = pm.check(agent=agent, action=routing_key or "execute")
        if perm.decision == "deny":
            return new_result(
                agent=agent, task=task,
                exit_code=126,
                error=f"Permission denied: {perm.reason or 'rule=' + perm.matched_rule}",
                trace_id=trace_id, routing_key=routing_key,
            )
        if perm.decision == "ask":
            from maop.core.agent.delegation.human_proxy import HumanProxy
            hp = HumanProxy(root_dir=str(_root))
            req_id = hp.request(
                task=task, agent=agent,
                priority="high", reason=f"Permission check: agent={agent} action={routing_key or 'execute'}",
                metadata={"routing_key": routing_key, "trace_id": trace_id},
            )
            logger.warning("[execute] Permission=ask, request %s pending human approval — denying until approved", req_id)
            return new_result(
                agent=agent, task=task,
                exit_code=126,
                error=f"Permission pending human approval (request={req_id}): {perm.reason or 'agent=' + agent}",
                trace_id=trace_id, routing_key=routing_key,
            )
    except Exception as exc:
        logger.error("[execute] Permission check failed (fail-closed): %s", exc)
        return new_result(
            agent=agent, task=task,
            exit_code=126,
            error=f"Permission check failed: {exc}",
            trace_id=trace_id, routing_key=routing_key,
        )

    # Hook: agent.pre_dispatch — hooks can veto dispatch by returning decision="deny"
    try:
        from maop.core.agent.plugins_hooks.hook_manager import LifecycleEvent, get_hook_manager
        mgr = get_hook_manager()
        hook_results = await mgr.trigger(LifecycleEvent.AGENT_PRE_DISPATCH, {
            "agent": agent, "task": task, "routing_key": routing_key, "trace_id": trace_id,
        })
        for hr in hook_results:
            if hr.decision == "deny":
                return new_result(
                    agent=agent, task=task,
                    exit_code=126,
                    error=f"Hook vetoed dispatch: hook={hr.hook_id} reason={hr.error or 'denied'}",
                    trace_id=trace_id, routing_key=routing_key,
                )
    except Exception as exc:
        logger.error("[execute] Hook pre_dispatch failed (fail-closed): %s", exc)
        return new_result(
            agent=agent, task=task,
            exit_code=126,
            error=f"Hook pre_dispatch error (fail-closed): {exc}",
            trace_id=trace_id, routing_key=routing_key,
        )

    return None


async def maop_execute(
    delegate: Delegate | None = None,
    *,
    agent: str = "",
    task: str = "",
    routing_key: str = "",
    workdir: str = "",
    timeout_seconds: int = 120,
    trace_id: str = "",
    dispatcher: Dispatcher | None = None,
    guardrail: Guardrail | None = None,
    tools: list[dict] | None = None,
    provider: str = "openai",
    max_tool_rounds: int = 5,
    react_mode: bool = False,
    react_max_iterations: int = 10,
    permission_manager: Any = None,
) -> MaopResult:
    """Execute a task by dispatching to an agent.

    Parameters
    ----------
    agent : str
        Agent name to dispatch to.
    task : str
        Task description.
    routing_key : str
        Routing key for context.
    workdir : str
        Working directory.
    timeout_seconds : int
        Timeout in seconds.
    trace_id : str
        Trace ID for observability.
    dispatcher : Dispatcher | None
        Dispatcher instance (creates default if None).
    guardrail : Guardrail | None
        Guardrail for pre/post checks.

    Returns
    -------
    MaopResult
        Execution result.
    """
    if delegate is not None:
        agent = delegate.agent
        task = delegate.task
        routing_key = delegate.routing_key
        workdir = delegate.workdir
        timeout_seconds = delegate.timeout_seconds
        trace_id = delegate.trace_id
        if delegate.tools is not None:
            tools = delegate.tools
        provider = delegate.provider
        max_tool_rounds = delegate.max_tool_rounds
        react_mode = delegate.react_mode
        react_max_iterations = delegate.react_max_iterations

    if not trace_id:
        trace_id = uuid.uuid4().hex

    if dispatcher is None:
        dispatcher = Dispatcher()

    if guardrail is None:
        guardrail = Guardrail()

    # P1-1: 个人版成本兜底护栏 — 在发起 LLM 调用前检查全局累计花费。
    # 软/硬熔断均拒绝新调用；硬熔断的中断运行中语义由 should_interrupt_running()
    # 提供，供上层 loop 按需集成。此处 fail-open：guard 检查自身异常时不阻断
    # 主流程（成本护栏是 best-effort 保护，非安全检查）。
    try:
        from maop.core.personal_cost_guard import PersonalCostGuard
        cost_guard = PersonalCostGuard()
        cost_allowed, cost_reason = cost_guard.check_new_call()
        if not cost_allowed:
            return new_result(
                agent=agent, task=task,
                exit_code=126,
                error=f"[Cost Cap Exceeded] 个人版累计花费已达上限。{cost_reason}"
                      " 如需更高限额，请升级到企业版（MAOS）。",
                trace_id=trace_id, routing_key=routing_key,
            )
    except Exception as exc:
        # P2-1 fix: 成本护栏检查自身异常时 fail-open 不阻断主流程，但原 logger.debug
        # 级别在生产日志中几乎不可见，导致护栏静默失效无法察觉。提升为 logger.warning
        # 以便运维能从常规日志发现成本护栏检查异常（仍保持 fail-open 语义）。
        logger.warning("[execute] Personal cost guard check failed (fail-open): %s", exc)

    # P0 fix: permission check + pre_dispatch hook must run BEFORE both
    # the react_mode branch and the normal dispatch branch, otherwise
    # react_mode bypasses all security gates (was maop_execute.py:202-286).
    perm_hook_result = await _check_permission_and_hooks(
        agent=agent, task=task, routing_key=routing_key,
        trace_id=trace_id, permission_manager=permission_manager,
    )
    if perm_hook_result is not None:
        return perm_hook_result

    start = time.monotonic()

    # ReAct mode: delegate to ReactLoop for Thought→Action→Observation cycling
    if react_mode:
        # P1-13: emit meta event for react-mode execution stream.
        _emit_agent_event(trace_id, "meta", {"agent": agent, "type": "meta", "mode": "react"})
        try:
            from maop.core.agent.llm_chat.react_loop import ReactConfig, ReactLoop
            react_config = ReactConfig(
                max_iterations=react_max_iterations,
                provider=provider,
                timeout_seconds=timeout_seconds,
            )
            react_loop = ReactLoop(config=react_config, root_dir=None)
            react_result = await react_loop.run(
                task=task, agent=agent, dispatcher=dispatcher,
                workdir=workdir, trace_id=trace_id,
                tools=tools, provider=provider,
            )
            duration_ms = int((time.monotonic() - start) * 1000)
            # P1-13: emit done event with react result summary.
            _emit_agent_event(trace_id, "done", {
                "content_length": len(react_result.final_answer or ""),
                "tokens": react_result.token_count,
                "iterations": react_result.total_iterations,
                "success": react_result.success,
            })
            return new_result(
                agent=agent, task=task,
                exit_code=0 if react_result.success else 1,
                stdout=react_result.final_answer,
                error=react_result.error or None,
                duration_ms=duration_ms,
                trace_id=trace_id, routing_key=routing_key,
                structured_output={
                    "react_session_id": react_result.session_id,
                    "react_iterations": react_result.total_iterations,
                    "react_tool_calls": react_result.total_tool_calls,
                    "react_steps": [s.model_dump() for s in react_result.steps],
                },
            )
        except Exception as exc:
            # P1-SSE fix: 对外 SSE error event 只发送通用错误消息，详细异常
            # （可能含堆栈跟踪、连接字符串、文件路径等）仅记录到 logger，避免信息泄露。
            logger.error("[execute] ReAct loop error (trace_id=%s): %s", trace_id, exc, exc_info=True)
            _emit_agent_event(trace_id, "error", {"error": "Internal server error"})
            return new_result(
                agent=agent, task=task,
                exit_code=-1, error=f"ReAct loop error: {exc}",
                trace_id=trace_id, routing_key=routing_key,
            )


    # Pre-guardrail check
    try:
        pre_check = guardrail.check(task)
        if not pre_check.passed:
            return new_result(
                agent=agent, task=task,
                exit_code=126,  # Permission denied analog
                error=f"Guardrail blocked: {getattr(pre_check, 'reason', str(pre_check))}",
                trace_id=trace_id, routing_key=routing_key,
            )
    except Exception as exc:
        # P3-1 fix: 删除函数内重复 import logging，直接用模块级 logger
        logger.error("[execute] Guardrail pre-check failed (fail-closed): %s", exc)
        return new_result(
            agent=agent, task=task,
            exit_code=126,
            error=f"Guardrail check error (fail-closed): {exc}",
            trace_id=trace_id, routing_key=routing_key,
        )

    # Dispatch
    # P1-13: emit meta event so /api/stream/agent/{trace_id} subscribers
    # receive a stream-start signal before the first token.
    _emit_agent_event(trace_id, "meta", {"agent": agent, "type": "meta"})
    try:
        from maop.core.reliability.streaming import SubprocessStreamer, get_stream_registry
        token_callback = _make_token_line_callback(trace_id)
        streamer = SubprocessStreamer(
            trace_id=trace_id, line_callback=token_callback,
        )
        registry = get_stream_registry()
        registry.register(trace_id, streamer)

        try:
            dispatch_result = await dispatcher.dispatch(
                agent=agent, task=task,
                routing_key=routing_key, workdir=workdir,
                timeout_seconds=timeout_seconds, trace_id=trace_id,
                streamer=streamer,
            )
            result = dispatch_result.result
            result.trace_id = trace_id
            # P1-13: emit done event with final content length / token count.
            _emit_agent_event(trace_id, "done", {
                "content_length": len(result.stdout or ""),
                "tokens": len(result.stdout or "") // 4,
                "exit_code": result.exit_code,
            })
        finally:
            # P1-4 fix: 将 unregister 移入 finally 块，确保 dispatch 抛异常时
            # streamer 不会从 registry 泄漏（原代码仅在成功路径调用 unregister）。
            registry.unregister(trace_id)
    except Exception as exc:
        result = new_result(
            agent=agent, task=task,
            exit_code=-1, error=f"Dispatch error: {exc}",
            trace_id=trace_id, routing_key=routing_key,
        )
        # P1-SSE fix: 对外 SSE error event 只发送通用错误消息，详细异常
        # （可能含堆栈跟踪、连接字符串、文件路径等）仅记录到 logger，避免信息泄露。
        logger.error("[execute] Dispatch error (trace_id=%s): %s", trace_id, exc, exc_info=True)
        _emit_agent_event(trace_id, "error", {"error": "Internal server error"})

    # Function-call loop: if agent returned tool_calls, execute and re-dispatch
    if result.is_success() and tools is not None and result.stdout:
        result = await _handle_function_calls(
            result=result,
            agent=agent, task=task,
            routing_key=routing_key, workdir=workdir,
            timeout_seconds=timeout_seconds, trace_id=trace_id,
            dispatcher=dispatcher, tools=tools,
            provider=provider, max_tool_rounds=max_tool_rounds,
            guardrail=guardrail,
        )

    # Post-guardrail check (on output)
    if result.is_success() and result.stdout:
        try:
            post_check = guardrail.check(result.stdout)
            if not post_check.passed:
                result = new_result(
                    agent=agent, task=task,
                    exit_code=127,
                    error=f"Output guardrail: {getattr(post_check, 'reason', str(post_check))}",
                    stdout=result.stdout,
                    trace_id=trace_id, routing_key=routing_key,
                )
        except Exception as exc:
            # P3-1 fix: 删除函数内重复 import logging，直接用模块级 logger
            logger.error("[execute] Post-guardrail check failed (fail-closed): %s", exc)
            result = new_result(
                agent=agent, task=task,
                exit_code=127,
                error=f"Post-guardrail check error (fail-closed): {exc}",
                stdout=result.stdout,
                trace_id=trace_id, routing_key=routing_key,
            )

    # Add duration
    duration_ms = int((time.monotonic() - start) * 1000)
    result.duration_ms = duration_ms

    # Hook: agent.post_dispatch / agent.on_error / agent.on_timeout
    try:
        from maop.core.agent.plugins_hooks.hook_manager import LifecycleEvent, get_hook_manager
        mgr = get_hook_manager()
        if result.is_success():
            await mgr.trigger(LifecycleEvent.AGENT_POST_DISPATCH, {
                "agent": agent, "task": task, "trace_id": trace_id,
                "exit_code": result.exit_code, "duration_ms": duration_ms,
            })
        elif "TIMEOUT" in (result.error or ""):
            await mgr.trigger(LifecycleEvent.AGENT_ON_TIMEOUT, {
                "agent": agent, "task": task, "trace_id": trace_id, "error": result.error,
            })
        else:
            await mgr.trigger(LifecycleEvent.AGENT_ON_ERROR, {
                "agent": agent, "task": task, "trace_id": trace_id,
                "exit_code": result.exit_code, "error": result.error,
            })
    except Exception as exc:
        logger.warning("[execute] Post-dispatch hook failed: %s", exc)

    return result


async def _handle_function_calls(
    result: MaopResult,
    *,
    agent: str,
    task: str,
    routing_key: str,
    workdir: str,
    timeout_seconds: int,
    trace_id: str,
    dispatcher: Dispatcher,
    tools: list[dict],
    provider: str,
    max_tool_rounds: int,
    guardrail: Guardrail | None = None,
) -> MaopResult:
    """Handle function-call loop: parse tool_calls, execute, re-dispatch.

    When the LLM response contains tool_calls / function_calls, this
    function executes each call via FunctionCallBridge, appends the
    results as tool messages, and re-dispatches the task with the
    enriched conversation until the LLM produces a final text answer
    or ``max_tool_rounds`` is exhausted.
    """
    from maop.core.agent.llm_chat.function_call import FunctionCallBridge

    bridge = FunctionCallBridge(root_dir=None)
    conversation: list[dict[str, object]] = [
        {"role": "user", "content": task},
    ]

    # P1-12 fix: 校验 agent 输出，防止恶意/被入侵 agent 注入非法 tool_calls
    _MAX_STDOUT_LEN = 1_000_000  # 1MB 最大 stdout 长度，防止超大输出导致 OOM
    _MAX_TOOL_CALLS = 20  # 单轮最大工具调用数
    allowed_tool_names = {t.get("name") for t in tools if t.get("name")}

    for round_idx in range(max_tool_rounds):
        try:
            # P1-12 fix: 截断超长 stdout，防止 OOM
            raw_stdout = result.stdout or ""
            if len(raw_stdout) > _MAX_STDOUT_LEN:
                logger.warning(
                    "[execute] Agent stdout truncated: %d chars > %d limit",
                    len(raw_stdout), _MAX_STDOUT_LEN,
                )
                raw_stdout = raw_stdout[:_MAX_STDOUT_LEN]
            response = json.loads(raw_stdout)
        except (json.JSONDecodeError, ValueError):
            break

        tool_calls = bridge.parse_response(response, provider=provider)
        if not tool_calls:
            break

        # P1-12 fix: 限制单轮工具调用数量
        if len(tool_calls) > _MAX_TOOL_CALLS:
            logger.warning(
                "[execute] Too many tool calls: %d > %d limit, truncating",
                len(tool_calls), _MAX_TOOL_CALLS,
            )
            tool_calls = tool_calls[:_MAX_TOOL_CALLS]
        # P1-12 fix: 过滤非白名单工具调用，拒绝执行未声明的工具
        # 兼容 ToolCall 对象（.name 属性）和 dict（["name"] 键）两种形式
        def _tool_name(c: Any) -> str:
            return getattr(c, "name", None) or (c.get("name") if isinstance(c, dict) else None) or ""
        valid_calls = [c for c in tool_calls if _tool_name(c) in allowed_tool_names]
        if len(valid_calls) < len(tool_calls):
            rejected = len(tool_calls) - len(valid_calls)
            logger.warning(
                "[execute] Rejected %d tool calls with non-whitelisted names",
                rejected,
            )
            tool_calls = valid_calls
        if not tool_calls:
            break

        logger.info(
            "[fn_call] Round %d: %d tool calls from agent=%s",
            round_idx + 1, len(tool_calls), agent,
        )

        if provider.lower() == "openai" or provider.lower() == "ollama":
            assistant_msg: dict[str, Any] = {"role": "assistant", "content": None, "tool_calls": []}
            for tc in response.get("choices", [{}])[0].get("message", {}).get("tool_calls", []):
                assistant_msg["tool_calls"].append(tc)
            if assistant_msg["tool_calls"]:
                conversation.append(assistant_msg)
        elif provider.lower() == "anthropic":
            conversation.append({"role": "assistant", "content": response.get("content", [])})

        for call in tool_calls:
            call_result = await bridge.execute(call)
            tool_msg = bridge.format_result(call, call_result, provider=provider)
            conversation.append(tool_msg)

        enriched_task = json.dumps(conversation, ensure_ascii=False)

        dispatch_result = await dispatcher.dispatch(
            agent=agent, task=enriched_task,
            routing_key=routing_key, workdir=workdir,
            timeout_seconds=timeout_seconds, trace_id=trace_id,
        )
        result = dispatch_result.result
        result.trace_id = trace_id

        if not result.is_success():
            break

        # P1-guardrail fix: 每轮 re-dispatch 后重新检查 guardrail，防止多轮
        # 迭代绕过护栏限制。若 guardrail 检查失败则终止循环而非继续下一轮。
        # 复用与 post-guardrail check 相同的 check(result.stdout) 模式。
        if guardrail is not None and result.stdout:
            try:
                round_post_check = guardrail.check(result.stdout)
                if not round_post_check.passed:
                    logger.warning(
                        "[execute] Guardrail blocked output in re-dispatch round %d: %s",
                        round_idx + 1, getattr(round_post_check, 'reason', str(round_post_check)),
                    )
                    result = new_result(
                        agent=agent, task=task,
                        exit_code=127,
                        error=f"Output guardrail (round {round_idx + 1}): "
                              f"{getattr(round_post_check, 'reason', str(round_post_check))}",
                        stdout=result.stdout,
                        trace_id=trace_id, routing_key=routing_key,
                    )
                    break
            except Exception as exc:
                logger.error(
                    "[execute] Guardrail check in re-dispatch round %d failed (fail-closed): %s",
                    round_idx + 1, exc,
                )
                result = new_result(
                    agent=agent, task=task,
                    exit_code=127,
                    error=f"Guardrail check error in re-dispatch (fail-closed): {exc}",
                    stdout=result.stdout,
                    trace_id=trace_id, routing_key=routing_key,
                )
                break

    return result
