"""M-X2 E2-a —— orchestration wiring：后端决议 / 双模分发 / 执行体（mock 全链路）.

覆盖：``resolve_backend``（env 收敛 + 显式覆盖 + 未知值显式拒绝）、
``run_workflow`` legacy 透传（flag 关零变化）与 temporal 分发（stub
adapter）、结果映射、``maop_run_executor`` 子进程三形态
（success/failed/timeout，monkeypatch ``subprocess.run``）、
``cmd_workflow`` 未知后端退出码。真连通验收由
``scripts/temporal_m0_demo.py`` / ``maop workflow run m0-demo`` 在 dev
server 上做，不进单测。
"""

from __future__ import annotations

import subprocess
from typing import ClassVar

import pytest

from maop.config.loader import MaopConfig, WorkflowDef, WorkflowStepDef
from maop.orchestration import wiring
from maop.orchestration.base import CompiledWorkflow, WorkflowNode
from maop.orchestration.wiring import maop_run_executor, resolve_backend, run_workflow

# ── resolve_backend ──────────────────────────────────────────────────


def test_resolve_backend_default_legacy(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("MAOP_ORCHESTRATION_BACKEND", raising=False)
    assert resolve_backend() == ""


def test_resolve_backend_env_temporal(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MAOP_ORCHESTRATION_BACKEND", "Temporal")  # 大小写归一
    assert resolve_backend() == "temporal"


def test_resolve_backend_override_wins(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MAOP_ORCHESTRATION_BACKEND", "temporal")
    assert resolve_backend(override="") == "temporal"


def test_resolve_backend_unknown_rejected(monkeypatch: pytest.MonkeyPatch):
    """未知值显式报错（诚实边界），不静默回退 legacy。"""
    monkeypatch.setenv("MAOP_ORCHESTRATION_BACKEND", "airflow")
    with pytest.raises(ValueError, match="unknown MAOP_ORCHESTRATION_BACKEND"):
        resolve_backend()


# ── run_workflow 双模 ────────────────────────────────────────────────


def _config_with(steps: list[WorkflowStepDef], name: str = "wf") -> MaopConfig:
    cfg = MaopConfig()
    cfg.workflows[name] = WorkflowDef(steps=steps, description=name)
    return cfg


async def test_run_workflow_legacy_passthrough(monkeypatch: pytest.MonkeyPatch):
    """flag 关：走 maop_plan.execute_workflow（规划合同），参数原样透传。"""
    calls: list[tuple] = []

    def _fake_execute(name, config, initial_vars):
        calls.append((name, config, initial_vars))
        return "legacy-result"  # type: ignore[return-value]

    monkeypatch.setattr(wiring, "execute_workflow", _fake_execute)
    out = await run_workflow("wf", None, {"k": "v"})
    assert out == "legacy-result"
    assert calls == [("wf", None, {"k": "v"})]


_SINGLE_NODE = (WorkflowNode(step_id="0", agent="a", task="t", timeout_s=5),)


class _StubAdapter:
    def __init__(self) -> None:
        self.compiled_with: list[str] = []
        self.run_with: list[dict] = []

    def compile_workflow(self, workflow, *, name=""):
        self.compiled_with.append(name)
        return CompiledWorkflow(
            backend="temporal", name=name, nodes=_SINGLE_NODE,
            levels=(("0",),), artifact="x",
        )

    async def run(self, compiled, *, executor=None, timeout_s=None):
        self.run_with.append({"executor": executor, "timeout_s": timeout_s})
        return {"workflow": compiled.name, "levels": 1, "nodes": {}}


async def test_run_workflow_temporal_routes_to_adapter():
    adapter = _StubAdapter()
    result = await run_workflow(
        "wf", _config_with([WorkflowStepDef(agent="a", task="t")]),
        backend="temporal", adapter=adapter,
        executor=lambda n: {"step_id": n.step_id, "status": "success"},
        timeout_s=9,
    )
    assert adapter.compiled_with == ["wf"]
    assert adapter.run_with[0]["timeout_s"] == 9
    assert adapter.run_with[0]["executor"] is not None
    assert result.steps_total == 1 and result.steps_planned == 1


async def test_run_workflow_temporal_unknown_name_is_empty_result():
    adapter = _StubAdapter()
    result = await run_workflow("nope", None, backend="temporal", adapter=adapter)
    assert adapter.compiled_with == []  # 未编译即返回
    assert result.workflow_name == "nope" and result.steps_total == 0


async def test_run_workflow_result_mapping():
    """节点结果 → WorkflowResult（计数沿用语义：success 计 completed）。"""
    steps = [
        WorkflowStepDef(agent="a", task="t0"),
        WorkflowStepDef(agent="b", task="t1", depends_on=["0"]),
    ]

    class _PayloadAdapter(_StubAdapter):
        nodes_payload: ClassVar[tuple[WorkflowNode, ...]] = (
            WorkflowNode(step_id="0", agent="a", task="t0", timeout_s=5),
            WorkflowNode(step_id="1", agent="b", task="t1", timeout_s=5),
        )
        levels_payload: ClassVar[tuple[tuple[str, ...], ...]] = (("0",), ("1",))
        run_payload: ClassVar[dict] = {
            "nodes": {
                "0": {"status": "success", "exit_code": 0},
                "1": {"status": "failed", "exit_code": 1},
            }
        }

        def compile_workflow(self, workflow, *, name=""):
            return CompiledWorkflow(
                backend="temporal", name=name, nodes=self.nodes_payload,
                levels=self.levels_payload, artifact="x",
            )

        async def run(self, compiled, *, executor=None, timeout_s=None):
            return self.run_payload

    result = await run_workflow(
        "wf", _config_with(steps), backend="temporal", adapter=_PayloadAdapter(),
    )
    assert (result.steps_total, result.steps_planned, result.steps_completed) == (
        2, 2, 1,
    )
    by_idx = {r.step_index: r for r in result.step_results}
    assert by_idx[0].status == "success" and by_idx[0].agent == "a"
    assert by_idx[1].status == "failed" and by_idx[1].agent == "b"


# ── maop_run_executor（E3-a 子进程执行体） ───────────────────────────


def _node(timeout_s: int = 30) -> WorkflowNode:
    return WorkflowNode(step_id="1", agent="codex", task="do", timeout_s=timeout_s)


def test_executor_success(monkeypatch: pytest.MonkeyPatch):
    recorded: dict = {}

    def _fake_run(command, **kwargs):
        recorded["command"] = command
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(wiring.subprocess, "run", _fake_run)
    out = maop_run_executor(_node())
    assert recorded["command"] == [
        wiring.sys.executable, "-m", "maop", "run",
        "--agent", "codex", "--task", "do",
    ]
    assert out["status"] == "success" and out["exit_code"] == 0


def test_executor_failed_and_timeout(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        wiring.subprocess, "run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 3),
    )
    assert maop_run_executor(_node())["status"] == "failed"

    def _timeout(command, **kwargs):
        raise subprocess.TimeoutExpired(cmd=command, timeout=kwargs.get("timeout", 30))

    monkeypatch.setattr(wiring.subprocess, "run", _timeout)
    out = maop_run_executor(_node(timeout_s=7))
    assert out["status"] == "timed_out" and out["timeout_s"] == 7


# ── CLI 面 ───────────────────────────────────────────────────────────


def test_cmd_workflow_unknown_backend_exit_2():
    from maop.cli_workflow import cmd_workflow

    rc = cmd_workflow(["run", "wf", "--backend", "bogus"])
    assert rc == 2
