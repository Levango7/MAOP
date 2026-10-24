"""``explain_unsuccessful_run`` 的判定顺序单测。

这个函数存在的理由：``maop run`` 曾经在 agent 找不到时仍打印 ``[OK] run 完成``
（假成功）。修复后 ``cmd_run`` 用 ``LoopResult.success`` 判定失败，并把
「为什么没成功」交给这里。所以这里的每条用例都对应一种"如果措辞错了，
用户会被误导到错误方向"的场景。
"""

from __future__ import annotations

import pytest

from maop.cli_run_outcome import explain_unsuccessful_run
from maop.core.reliability.error_schema import MaopResult
from maop.loop_models import LoopResult
from maop.maop_verify import VerifyResult


def _exec(
    *,
    error: str | None = None,
    exit_code: int = 0,
) -> MaopResult:
    """构造执行结果。

    显式参数而非 ``**kwargs``：``MaopResult(**dict[str, str])`` 会让 mypy 报
    arg-type（dict 值类型不足以匹配 bool/int 形参）。
    """
    return MaopResult(
        agent="codex", task="say hello", error=error, exit_code=exit_code
    )


def _result(execution=None, verify=None, success: bool = False) -> LoopResult:
    return LoopResult(
        task="say hello", execution=execution, verify=verify, success=success
    )


# ── 执行层 ────────────────────────────────────────────────────────


def test_execution_error_takes_priority():
    """agent 解析失败这类错误最贴近用户，应优先展示。"""
    r = _result(_exec(error="Agent 'codex' not found in config or registry"))
    assert "not found" in explain_unsuccessful_run(r)


def test_execution_exit_code_used_when_no_error_message():
    r = _result(_exec(exit_code=2))
    assert "退出码 2" in explain_unsuccessful_run(r)


def test_execution_error_beats_exit_code():
    """两者同时存在时，错误信息比退出码更有解释力。"""
    r = _result(_exec(exit_code=2, error="permission denied"))
    assert "permission denied" in explain_unsuccessful_run(r)


# ── 验证层 ────────────────────────────────────────────────────────


def test_verify_errored_is_worded_as_not_task_failure():
    """验证引擎自己跑不起来 ≠ 任务做错了，措辞必须区分开。"""
    r = _result(_exec(), VerifyResult(errored=True, summary="engine crashed"))
    msg = explain_unsuccessful_run(r)
    assert "不是任务失败" in msg
    assert "engine crashed" in msg


def test_verify_failed_uses_summary():
    r = _result(_exec(), VerifyResult(passed=False, summary="tests still failing"))
    assert "verify 未通过" in explain_unsuccessful_run(r)
    assert "tests still failing" in explain_unsuccessful_run(r)


def test_verify_failed_falls_back_to_feedback():
    r = _result(_exec(), VerifyResult(passed=False, feedback="retry with -x"))
    assert "retry with -x" in explain_unsuccessful_run(r)


def test_verify_failed_without_detail_still_says_failed():
    r = _result(_exec(), VerifyResult(passed=False))
    assert explain_unsuccessful_run(r) == "verify 未通过"


# ── 结构缺失 / 兜底 ───────────────────────────────────────────────


def test_missing_execution_is_explained():
    """execution 为 None 时说明「agent 可能未被解析或未派发」。"""
    r = _result(None, None)
    assert "没有产生执行结果" in explain_unsuccessful_run(r)


def test_returns_empty_string_when_nothing_to_say():
    """执行成功但 verify 缺失 —— 没有信息就不编造理由，交给调用方兜底。"""
    r = _result(_exec(), None)
    assert explain_unsuccessful_run(r) == ""


def test_explain_never_raises_on_minimal_result():
    """最小构造的 LoopResult 也必须能安全调用（防御未来字段变化）。"""
    assert isinstance(explain_unsuccessful_run(LoopResult(task="t")), str)


@pytest.mark.parametrize(
    ("error", "exit_code", "expected"),
    [
        ("boom", 0, "boom"),
        (None, 3, "退出码 3"),
    ],
)
def test_priority_matrix(error: str | None, exit_code: int, expected: str) -> None:
    r = _result(_exec(error=error, exit_code=exit_code))
    assert expected in explain_unsuccessful_run(r)
