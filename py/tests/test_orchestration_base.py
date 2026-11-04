"""M-X2 M0 —— orchestration base：DAG 拓扑分层与边界校验（纯逻辑，无后端）.

M0 章程"adapter 接口先行"的测试面：``plan_levels`` 与
``engine_utils._topological_sort`` 同契约（steps 按索引串寻址），但编译期
即拒绝环 / 未知依赖 / condition——**编译期失败优于运行期失败**。
"""

from __future__ import annotations

import pytest

from maop.config.loader import WorkflowStepDef
from maop.orchestration.base import (
    CompiledWorkflow,
    CompileError,
    WorkflowNode,
    nodes_from_steps,
    plan_levels,
)


def _step(**kw) -> WorkflowStepDef:
    return WorkflowStepDef(**{"agent": "a", "task": "t", **kw})


def test_plan_levels_chain():
    steps = [_step(), _step(depends_on=["0"]), _step(depends_on=["1"])]
    assert plan_levels(steps) == (("0",), ("1",), ("2",))


def test_plan_levels_branch_is_parallel_level():
    steps = [_step(), _step(depends_on=["0"]), _step(depends_on=["0"])]
    assert plan_levels(steps) == (("0",), ("1", "2"))


def test_plan_levels_independent_single_level():
    assert plan_levels([_step(), _step(), _step()]) == (("0", "1", "2"),)


def test_plan_levels_rejects_cycle():
    steps = [_step(depends_on=["1"]), _step(depends_on=["0"])]
    with pytest.raises(CompileError, match="cycle"):
        plan_levels(steps)


def test_plan_levels_rejects_unknown_dep():
    with pytest.raises(CompileError, match="unknown step id '9'"):
        plan_levels([_step(depends_on=["9"])])


def test_plan_levels_rejects_condition_step():
    """条件执行语义是 M0 明示边界——宁拒不昧（静默恒执行会改变语义）。"""
    with pytest.raises(CompileError, match="M0 scope"):
        plan_levels([_step(condition='steps.0.status == "success"')])


def test_nodes_from_steps_projection():
    nodes = nodes_from_steps([_step(agent="x", task="do", timeout_s=7)])
    assert nodes == (
        WorkflowNode(step_id="0", agent="x", task="do", timeout_s=7),
    )


def test_compiled_workflow_shape():
    wf = CompiledWorkflow(
        backend="temporal", name="demo",
        nodes=nodes_from_steps([_step()]), levels=(("0",),), artifact="x",
    )
    assert wf.backend == "temporal" and wf.name == "demo"
