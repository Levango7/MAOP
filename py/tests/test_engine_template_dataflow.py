"""DAG 模板数据流：上游输出必须注入下游 agent step 的 task。

回归缺陷（多厂商编队实测发现，两次 verify 步骤均收不到内联报告）：
``_execute_step`` 开头的 ``resolved_task`` 此前只在 PLAN 分解分支使用，
AGENT 分支仍把原始 ``step.task`` 交给 executor ⇒ ``{{step_id}}`` 占位符
对普通 agent step 从不替换，DAG 的「上游输出 → 下游提示词」数据流断。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from maop.engine import Engine
from maop.engine_types import StepType, WorkflowStep


class _RecordingExecutor:
    """记录每个 step 实际收到的 task，并返回确定性输出。"""

    def __init__(self, output_text: str = "") -> None:
        self.tasks: dict[str, str] = {}
        self._output_text = output_text

    async def __call__(self, step: Any, context: Any, workdir: Any, trace_id: Any) -> Any:
        self.tasks[step.id] = step.task
        return SimpleNamespace(
            output=self._output_text or f"output-of-{step.id}",
            exit_code=0,
            error="",
        )


async def test_upstream_output_reaches_downstream_task() -> None:
    recorder = _RecordingExecutor()
    steps = [
        WorkflowStep(id="upstream", type=StepType.AGENT, agent="a", task="do first"),
        WorkflowStep(
            id="downstream", type=StepType.AGENT, agent="b",
            task="cross-review this report: {{upstream}}",
            depends_on=["upstream"],
        ),
    ]
    result = await Engine(step_executor=recorder).run(steps=steps, context={}, workdir="")
    assert result.success is True
    # 下游收到的 task 必须含上游真实输出，而非字面占位符
    assert "output-of-upstream" in recorder.tasks["downstream"]
    assert "{{upstream}}" not in recorder.tasks["downstream"]


async def test_placeholder_without_context_key_is_left_untouched() -> None:
    recorder = _RecordingExecutor()
    steps = [
        WorkflowStep(
            id="solo", type=StepType.AGENT, agent="a",
            task="keep this literal: {{missing_key}}",
        ),
    ]
    await Engine(step_executor=recorder).run(steps=steps, context={}, workdir="")
    assert recorder.tasks["solo"] == "keep this literal: {{missing_key}}"


async def test_oversized_upstream_output_is_truncated_for_cli_safety() -> None:
    """上游超长输出注入下游 task 时必须截断（头+尾保留结论）。

    实战缺陷：修复模板替换后，约 10KB 的上游报告经 CLI argv 传递撞
    Windows CreateProcess 命令行上限（The command line is too long），
    verify 步骤 exit_code=1。模板注入须有上下文预算。
    """
    recorder = _RecordingExecutor(output_text="x" * 10000)
    steps = [
        WorkflowStep(id="upstream", type=StepType.AGENT, agent="a", task="do first"),
        WorkflowStep(
            id="downstream", type=StepType.AGENT, agent="b",
            task="review: {{upstream}}",
            depends_on=["upstream"],
        ),
    ]
    await Engine(step_executor=recorder).run(steps=steps, context={}, workdir="")
    task = recorder.tasks["downstream"]
    assert "truncated" in task
    assert len(task) < 7000


async def test_substituted_output_is_not_reexpanded() -> None:
    """上游输出里若含 {{...}} 字样，注入下游后不得二次展开（防二阶注入）。"""
    recorder = _RecordingExecutor(output_text="literal {{upstream}} inside output")
    steps = [
        WorkflowStep(id="upstream", type=StepType.AGENT, agent="a", task="do first"),
        WorkflowStep(
            id="downstream", type=StepType.AGENT, agent="b",
            task="review: {{upstream}}",
            depends_on=["upstream"],
        ),
    ]
    await Engine(step_executor=recorder).run(steps=steps, context={}, workdir="")
    task = recorder.tasks["downstream"]
    assert "literal {{upstream}} inside output" in task
    # 若发生二次展开，{{upstream}} 会被再次替换成 output 全文形成自相似放大
    assert task.count("inside output") == 1
