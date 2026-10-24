"""把 ``LoopResult`` 的失败原因翻译成一句人话，供 ``maop run`` 使用。

为什么单独成文件：``cli.py`` 已 783 行，而 ADR-023 的文件大小门禁是 800 行。
把判定与措辞放在这里，``cli.py`` 只加三行调用，不占配额。

背景（2026-10-24 实测）：``maop run`` 在 agent 找不到时打印
``[stderr] Agent 'codex' not found in config or registry``，随后**仍然**输出
``[OK] run 完成`` 并以 0 退出 —— 假成功。根因是 ``cmd_run`` 只把
``result.block_reason`` 当作失败信号，忽略了 ``LoopResult.success``
（其定义为 ``exec_result is not None and exec_result.is_success() and verify_ok``）。

假成功比失败更糟：失败用户知道要修；假成功会让用户以为任务跑过了，
等发现产出为空时信任已经没了。
"""

from __future__ import annotations

from maop.loop_models import LoopResult

__all__ = ["explain_unsuccessful_run"]


def explain_unsuccessful_run(result: LoopResult) -> str:
    """给出「为什么没成功」的一句话。

    尽量具体；实在没有信息时返回空串，**不编造**原因 —— 调用方会把空串渲染成
    「未提供进一步信息」，而不是替产品猜一个理由。

    判定顺序按「离用户最近的原因优先」：执行层错误 → 执行退出码 → 验证层 → 结构缺失。
    """
    execution = result.execution
    if execution is not None:
        if execution.error:
            return str(execution.error)
        if execution.exit_code:
            return f"agent 退出码 {execution.exit_code}"

    verify = result.verify
    if verify is not None:
        if verify.errored:
            # 验证引擎自己跑不起来，与「任务做错了」是两件事，措辞上要分开。
            return f"验证引擎未能运行（不是任务失败）：{verify.summary or '无摘要'}"
        if not verify.passed:
            detail = verify.summary or verify.feedback
            return f"verify 未通过：{detail}" if detail else "verify 未通过"

    if execution is None:
        return "没有产生执行结果（agent 可能未被解析或未派发）"

    # 执行结果存在、ok 但 success 为假：通常是 verify 缺失，交给调用方兜底措辞。
    return ""
