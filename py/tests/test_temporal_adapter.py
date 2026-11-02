"""M-X2 M0 —— TemporalAdapter：编译（stub temporalio）+ 缺包报错（无后端）.

``compile_workflow`` 是纯文本生成，不依赖真 temporalio——测试用 stub 注入
（同 operator handlers 的 kopf stub 先例）做产物结构断言 + ast 语法哨兵；
``run`` 的缺包路径单独钉一条（真连通由 scripts/temporal_m0_demo.py 在 dev
server 上验收，不进单测）。
"""

from __future__ import annotations

import ast
import sys
import types

import pytest

from maop.config.loader import WorkflowDef, WorkflowStepDef
from maop.orchestration.temporal_adapter import (
    DEFAULT_SERVER_ADDRESS,
    TemporalAdapter,
    _workflow_class_name,
)


@pytest.fixture()
def stub_temporalio(monkeypatch: pytest.MonkeyPatch):
    """最小 temporalio stub：装饰器为恒等（足以驱动编译与产物 exec 断言）."""
    def _identity_deco(*_a, **_kw):
        def deco(fn):
            return fn
        return deco

    stub = types.ModuleType("temporalio")
    stub.activity = types.SimpleNamespace(defn=_identity_deco)
    stub.workflow = types.SimpleNamespace(defn=_identity_deco)
    monkeypatch.setitem(sys.modules, "temporalio", stub)
    return stub


def _workflow(**kw) -> WorkflowDef:
    steps = [
        WorkflowStepDef(agent="planner", task="产出计划"),
        WorkflowStepDef(agent="worker-a", task="分支 A", depends_on=["0"]),
        WorkflowStepDef(agent="worker-b", task="分支 B", depends_on=["0"]),
    ]
    return WorkflowDef(steps=steps, **kw)


def test_adapter_defaults_no_env_reads():
    """构造参数显式给值，不读 env（config-drift 棘轮零增量的设计前提）。"""
    adapter = TemporalAdapter()
    assert adapter.server_address == DEFAULT_SERVER_ADDRESS
    assert adapter.namespace == "default"
    assert adapter.task_queue == "maop-m0"


def test_compile_branch_workflow(stub_temporalio):
    adapter = TemporalAdapter()
    compiled = adapter.compile_workflow(_workflow(), name="m0-demo")

    assert compiled.backend == "temporal"
    assert compiled.name == "m0-demo"
    assert compiled.levels == (("0",), ("1", "2"))
    assert [n.step_id for n in compiled.nodes] == ["0", "1", "2"]
    # 产物是合法 Python（语法哨兵）且表达同一 DAG
    ast.parse(compiled.artifact)
    assert "@workflow.defn" in compiled.artifact
    assert '"maop_node"' in compiled.artifact
    assert repr((("0",), ("1", "2"))) in compiled.artifact


def test_compile_is_deterministic(stub_temporalio):
    adapter = TemporalAdapter()
    first = adapter.compile_workflow(_workflow(), name="demo")
    second = adapter.compile_workflow(_workflow(), name="demo")
    assert first.artifact == second.artifact


def test_compile_name_fallback_uses_description(stub_temporalio):
    compiled = TemporalAdapter().compile_workflow(_workflow(description="烟测链"))
    assert compiled.name == "烟测链"
    # 类名清洗只保留 [0-9a-zA-Z_]：纯中文名 → 下划线壳 → Maopworkflow
    assert _workflow_class_name(compiled.name) == "Maopworkflow"


def test_compile_rejects_uncompilable_steps(stub_temporalio):
    bad = WorkflowDef(
        steps=[
            WorkflowStepDef(agent="a", task="t", depends_on=["1"]),
            WorkflowStepDef(agent="a", task="t", depends_on=["0"]),
        ]
    )
    with pytest.raises(Exception, match="cycle"):
        TemporalAdapter().compile_workflow(bad, name="x")


def test_run_without_temporalio_raises_with_hint(monkeypatch: pytest.MonkeyPatch):
    """缺 temporalio 的报错带安装指引（kopf 先例），而非裸 ImportError。

    ``setitem(sys.modules, name, None)`` 让后续 ``import`` 直接 ImportError
    ——本机装了真 temporalio 也能模拟缺席（delitem 会从磁盘重装成功）。
    """
    from maop.orchestration.base import CompiledWorkflow

    monkeypatch.setitem(sys.modules, "temporalio", None)
    compiled = CompiledWorkflow(
        backend="temporal", name="x", nodes=(), levels=(), artifact="",
    )
    with pytest.raises(RuntimeError, match="pip install temporalio"):
        import asyncio

        asyncio.run(TemporalAdapter().run(compiled))


def test_workflow_class_name_sanitized():
    assert _workflow_class_name("demo 3-node!") == "Maopdemo_3_node"
    assert _workflow_class_name("42 号链") == "Maop_42"  # 尾部下划线被 strip
    assert _workflow_class_name("") == "Maopworkflow"
