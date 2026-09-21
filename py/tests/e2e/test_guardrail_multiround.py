"""E2E 测试：Guardrail 多轮 re-dispatch 检查（第五轮 P0 修复）。

验证在 function-call 多轮 re-dispatch 循环中，guardrail 每轮都被检查。
若第二轮 guardrail 检查失败，执行应立即终止，不会继续第三轮。

修复背景（maop_execute.py P1-guardrail fix）：
  原实现仅在首尾做 guardrail 检查，多轮 re-dispatch 中间轮次可绕过护栏。
  修复后每轮 re-dispatch 后都重新检查 guardrail，检查失败则 break。

测试策略：
  - 直接调用 _handle_function_calls（guardrail 多轮检查核心逻辑）
  - mock FunctionCallBridge（parse_response/execute/format_result）
  - mock dispatcher.dispatch 每轮返回成功结果（含 tool_calls，使循环继续）
  - mock guardrail.check：第一轮通过，第二轮不通过
  - 断言 dispatcher.dispatch 恰好被调用 2 次（非 3 次）
  - 断言最终 result 包含 guardrail 阻止信息，exit_code == 127
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from maop.core.reliability.error_schema import MaopResult, new_result
from maop.core.security.guardrail import CheckResult, Violation
from maop.delegate.models import DispatchResult

# -- 辅助：构造成功结果（含 tool_calls JSON，使循环继续） ----------


def _make_success_result_with_tool_calls(agent: str, task: str) -> MaopResult:
    """构造一个成功结果，stdout 为含 tool_calls 的 JSON。

    _handle_function_calls 循环会 json.loads(result.stdout)，
    然后调用 bridge.parse_response 解析 tool_calls。
    只要 parse_response 返回非空列表，循环就会继续。
    """
    fake_response = {
        "choices": [
            {
                "message": {
                    "content": None,
                    "tool_calls": [
                        {"id": "call_1", "function": {"name": "search", "arguments": "{}"}}
                    ],
                }
            }
        ]
    }
    return new_result(
        agent=agent, task=task,
        exit_code=0,
        stdout=json.dumps(fake_response),
        trace_id="test-guardrail-multiround",
    )


def _make_tool_call():
    """构造一个模拟的 tool_call 对象。"""
    return {"name": "search", "arguments": "{}"}


# -- Fixtures -------------------------------------------------------


@pytest.fixture
def mock_function_call_bridge():
    """创建 mock FunctionCallBridge。

    - parse_response 总是返回非空 tool_calls 列表（使循环继续）
    - execute 返回模拟结果
    - format_result 返回模拟 tool 消息
    """
    bridge = MagicMock()
    bridge.parse_response = MagicMock(return_value=[_make_tool_call()])
    bridge.execute = AsyncMock(return_value={"result": "ok"})
    bridge.format_result = MagicMock(
        return_value={"role": "tool", "content": "tool result", "tool_call_id": "call_1"}
    )
    return bridge


@pytest.fixture
def mock_guardrail_two_rounds():
    """创建 mock guardrail，第一轮通过，第二轮阻止。

    check() 的 side_effect：
      第 1 次调用 → passed=True（第一轮 re-dispatch 后检查通过）
      第 2 次调用 → passed=False（第二轮 re-dispatch 后检查阻止）
    """
    guardrail = MagicMock()
    guardrail.check = MagicMock(side_effect=[
        # 第一轮 re-dispatch 后：通过
        CheckResult(passed=True, violations=[], summary="PASS"),
        # 第二轮 re-dispatch 后：阻止
        CheckResult(
            passed=False,
            violations=[Violation(
                rule="sensitive-patterns",
                severity="block",
                message="sensitive content detected",
                action="block",
            )],
            summary="BLOCKED",
        ),
    ])
    return guardrail


# -- 测试：Guardrail 多轮检查 --------------------------------------


class TestGuardrailMultiround:
    """验证多轮 re-dispatch 中 guardrail 每轮都被检查。"""

    async def test_guardrail_blocks_on_second_round(
        self, mock_function_call_bridge, mock_guardrail_two_rounds,
    ):
        """第二轮 guardrail 检查失败时，执行在第二轮终止。

        场景：
          - 第一轮 re-dispatch → guardrail.check() 通过 → 继续第二轮
          - 第二轮 re-dispatch → guardrail.check() 阻止 → break
          - 不应进入第三轮

        验证：
          - dispatcher.dispatch 恰好被调用 2 次
          - 最终 result.exit_code == 127（guardrail 阻止）
          - result.error 包含 guardrail 阻止信息
        """
        agent = "test-agent"
        task = "执行工具调用"
        trace_id = "test-guardrail-multiround"

        # 初始 result：成功且含 tool_calls JSON
        initial_result = _make_success_result_with_tool_calls(agent, task)

        # mock dispatcher：每次返回成功结果（含 tool_calls，使循环继续）
        dispatcher = MagicMock()
        dispatcher.dispatch = AsyncMock(
            side_effect=[
                DispatchResult(result=_make_success_result_with_tool_calls(agent, task))
                for _ in range(5)  # 提供足够多的返回值
            ]
        )

        with patch(
            "maop.core.agent.llm_chat.function_call.FunctionCallBridge",
            return_value=mock_function_call_bridge,
        ):
            from maop.maop_execute import _handle_function_calls

            result = await _handle_function_calls(
                result=initial_result,
                agent=agent, task=task,
                routing_key="", workdir="",
                timeout_seconds=30, trace_id=trace_id,
                dispatcher=dispatcher,
                tools=[{"name": "search"}],
                provider="openai",
                max_tool_rounds=5,
                guardrail=mock_guardrail_two_rounds,
            )

        # 核心断言 1：dispatcher 恰好被调用 2 次（第一轮 + 第二轮），不是 3 次
        assert dispatcher.dispatch.call_count == 2, (
            f"期望 dispatch 被调用 2 次（第二轮 guardrail 阻止后终止），"
            f"实际 {dispatcher.dispatch.call_count} 次"
        )

        # 核心断言 2：guardrail.check 恰好被调用 2 次
        assert mock_guardrail_two_rounds.check.call_count == 2, (
            f"期望 guardrail.check 被调用 2 次，"
            f"实际 {mock_guardrail_two_rounds.check.call_count} 次"
        )

        # 核心断言 3：最终 result 被 guardrail 阻止
        assert result.exit_code == 127, (
            f"期望 exit_code=127（guardrail 阻止），实际 {result.exit_code}"
        )

        # 核心断言 4：error 包含 guardrail 阻止信息
        assert result.error is not None, "guardrail 阻止后 error 不应为 None"
        assert "guardrail" in result.error.lower() or "round" in result.error.lower(), (
            f"error 应包含 guardrail 阻止信息，实际: {result.error}"
        )

    async def test_guardrail_not_bypassed_between_rounds(
        self, mock_function_call_bridge,
    ):
        """guardrail 在每轮 re-dispatch 后都被检查，不会被跳过。

        如果 guardrail 每轮都通过，循环应继续直到 max_tool_rounds 或
        agent 不再返回 tool_calls。此测试验证 guardrail.check 在每轮
        都被调用（而非仅在首尾）。
        """
        agent = "test-agent"
        task = "执行工具调用"
        trace_id = "test-guardrail-every-round"

        initial_result = _make_success_result_with_tool_calls(agent, task)

        dispatcher = MagicMock()
        max_rounds = 3
        dispatcher.dispatch = AsyncMock(
            side_effect=[
                DispatchResult(result=_make_success_result_with_tool_calls(agent, task))
                for _ in range(max_rounds)
            ]
        )

        # guardrail 每轮都通过
        guardrail = MagicMock()
        guardrail.check = MagicMock(
            return_value=CheckResult(passed=True, violations=[], summary="PASS")
        )

        with patch(
            "maop.core.agent.llm_chat.function_call.FunctionCallBridge",
            return_value=mock_function_call_bridge,
        ):
            from maop.maop_execute import _handle_function_calls

            await _handle_function_calls(
                result=initial_result,
                agent=agent, task=task,
                routing_key="", workdir="",
                timeout_seconds=30, trace_id=trace_id,
                dispatcher=dispatcher,
                tools=[{"name": "search"}],
                provider="openai",
                max_tool_rounds=max_rounds,
                guardrail=guardrail,
            )

        # guardrail.check 应在每轮 re-dispatch 后都被调用
        # 3 轮 re-dispatch → 3 次 guardrail.check
        assert guardrail.check.call_count == max_rounds, (
            f"期望 guardrail.check 被调用 {max_rounds} 次（每轮一次），"
            f"实际 {guardrail.check.call_count} 次。"
            f"若少于 {max_rounds} 次，说明中间轮次跳过了 guardrail 检查。"
        )

    async def test_guardrail_blocks_on_first_round(
        self, mock_function_call_bridge,
    ):
        """第一轮 guardrail 检查失败时，立即终止，不进入第二轮。

        边界场景：guardrail 在第一轮 re-dispatch 后就阻止。
        """
        agent = "test-agent"
        task = "执行工具调用"
        trace_id = "test-guardrail-first-round"

        initial_result = _make_success_result_with_tool_calls(agent, task)

        dispatcher = MagicMock()
        dispatcher.dispatch = AsyncMock(
            side_effect=[
                DispatchResult(result=_make_success_result_with_tool_calls(agent, task))
                for _ in range(5)
            ]
        )

        # guardrail 第一轮就阻止
        guardrail = MagicMock()
        guardrail.check = MagicMock(
            return_value=CheckResult(
                passed=False,
                violations=[Violation(
                    rule="blocked-agents",
                    severity="block",
                    message="agent blocked",
                    action="block",
                )],
                summary="BLOCKED",
            )
        )

        with patch(
            "maop.core.agent.llm_chat.function_call.FunctionCallBridge",
            return_value=mock_function_call_bridge,
        ):
            from maop.maop_execute import _handle_function_calls

            result = await _handle_function_calls(
                result=initial_result,
                agent=agent, task=task,
                routing_key="", workdir="",
                timeout_seconds=30, trace_id=trace_id,
                dispatcher=dispatcher,
                tools=[{"name": "search"}],
                provider="openai",
                max_tool_rounds=5,
                guardrail=guardrail,
            )

        # 只调用 1 次 dispatch，第一轮后就终止
        assert dispatcher.dispatch.call_count == 1, (
            f"期望 dispatch 被调用 1 次（第一轮 guardrail 阻止），"
            f"实际 {dispatcher.dispatch.call_count} 次"
        )
        assert result.exit_code == 127, (
            f"期望 exit_code=127，实际 {result.exit_code}"
        )