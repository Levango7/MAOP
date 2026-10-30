"""M-T3.3 Operator M0 —— mock 全链路测试（不需要 k8s/集群）.

三条章程验收的落点：
1. **mock 链路（MaopTask → 子进程 → status 回写）全绿** —— 本文件的
   controller 用例（FakeGateway + FakeRunner 注入）；
2. **结构测试翻真** —— ``test_spec_matches_crd_schema`` 把 spec 校验规则与
   CRD YAML 机器对齐（此前只有文件存在性断言）；
3. operator 镜像构建由 CI docker 作业的 build-only 腿验证（不入本文件）。

kopf 不在测试依赖里：handlers 的注册用显式 stub 注入；缺 kopf 的报错信息
单独钉一条（镜像内才装 kopf）。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import yaml

from maop.operator import handlers
from maop.operator.controller import OperatorController
from maop.operator.gateway import RunOutcome
from maop.operator.reconcile import PHASES, plan_finish, plan_launch
from maop.operator.spec import InvalidSpecError, MaopTaskSpec, command_for, parse_task_spec

REPO = Path(__file__).resolve().parents[2]
CRD = REPO / "deploy" / "k8s" / "operator" / "crds" / "maoptask.yaml"


def _body(**spec_overrides) -> dict:
    spec = {"agent": "support-bot", "prompt": "检查订单服务健康", "maxTurns": 5}
    spec.update(spec_overrides)
    return {"metadata": {"name": "task-1", "namespace": "maop-system"}, "spec": spec}


class FakeGateway:
    def __init__(self) -> None:
        self.patches: list[tuple[str, str, dict]] = []

    async def patch_status(self, *, name: str, namespace: str, patch: dict) -> None:
        self.patches.append((name, namespace, patch))


class FakeRunner:
    def __init__(self, outcome: RunOutcome) -> None:
        self.outcome = outcome
        self.commands: list[list[str]] = []

    async def run(self, command: list[str], *, timeout_s: float | None = None) -> RunOutcome:
        self.commands.append(list(command))
        return self.outcome


class CancellingRunner:
    async def run(self, command: list[str], *, timeout_s: float | None = None) -> RunOutcome:
        raise asyncio.CancelledError


def _controller(gateway: FakeGateway, runner) -> OperatorController:
    return OperatorController(gateway=gateway, runner=runner)


# ── spec 校验与命令构造 ──────────────────────────────────────────────


def test_parse_valid_spec():
    spec = parse_task_spec(_body(), name="task-1", namespace="maop-system")
    assert isinstance(spec, MaopTaskSpec)
    assert (spec.name, spec.namespace) == ("task-1", "maop-system")
    assert (spec.agent, spec.prompt) == ("support-bot", "检查订单服务健康")
    assert spec.max_turns == 5
    assert spec.tenant == ""


def test_parse_requires_agent_and_prompt():
    with pytest.raises(InvalidSpecError, match="spec.agent"):
        parse_task_spec(_body(agent=""))
    with pytest.raises(InvalidSpecError, match="spec.prompt"):
        parse_task_spec(_body(prompt=None))
    with pytest.raises(InvalidSpecError, match="spec is required"):
        parse_task_spec({"metadata": {"name": "t"}})


def test_parse_rejects_bad_max_turns_and_tools():
    with pytest.raises(InvalidSpecError, match="maxTurns"):
        parse_task_spec(_body(maxTurns=0))
    with pytest.raises(InvalidSpecError, match="maxTurns"):
        parse_task_spec(_body(maxTurns="5"))
    with pytest.raises(InvalidSpecError, match="tools"):
        parse_task_spec(_body(tools="mcp.search"))


def test_command_for_runs_cli():
    spec = parse_task_spec(_body())
    assert command_for(spec) == ["maop", "run", "检查订单服务健康"]


def test_spec_matches_crd_schema():
    """机器对齐：spec 校验规则与 CRD YAML 必填/枚举一致（防漂移哨兵）。"""
    crd = yaml.safe_load(CRD.read_text(encoding="utf-8"))
    version = crd["spec"]["versions"][0]
    schema = version["schema"]["openAPIV3Schema"]
    spec_schema = schema["properties"]["spec"]
    assert tuple(spec_schema["required"]) == ("agent", "prompt")
    assert spec_schema["properties"]["maxTurns"]["default"] == 20
    # phase 枚举在顶层 status.properties.phase —— reconcile 的相位必须是子集
    phase_enum = set(schema["properties"]["status"]["properties"]["phase"]["enum"])
    assert PHASES <= phase_enum


# ── reconcile 状态机 ─────────────────────────────────────────────────


def test_plan_launch_shape():
    t = plan_launch(when="2026-10-30T00:00:00+00:00")
    assert t.phase == "Running"
    assert t.patch["startTime"] == "2026-10-30T00:00:00+00:00"
    assert t.patch["conditions"][0]["type"] == "Launched"


def test_plan_finish_transitions():
    ok = plan_finish(exit_code=0)
    assert ok.phase == "Succeeded"
    bad = plan_finish(exit_code=1)
    assert bad.phase == "Failed"
    assert "exited with code 1" in bad.patch["conditions"][0]["message"]
    timeout = plan_finish(timed_out=True)
    assert timeout.phase == "TimedOut"
    cancel = plan_finish(cancelled=True)
    assert cancel.phase == "Cancelled"
    invalid = plan_finish(invalid="spec.agent is required")
    assert invalid.phase == "Failed"
    assert invalid.patch["conditions"][0]["reason"] == "InvalidSpec"


# ── controller 全链路（mock） ────────────────────────────────────────


async def test_controller_happy_path():
    gateway, runner = FakeGateway(), FakeRunner(RunOutcome(exit_code=0))
    result = await _controller(gateway, runner).handle_task(
        name="task-1", namespace="maop-system", body=_body()
    )
    assert result == {"phase": "Succeeded", "launched": True}
    assert runner.commands == [["maop", "run", "检查订单服务健康"]]
    phases = [p["phase"] for _, _, p in gateway.patches]
    assert phases == ["Running", "Succeeded"]
    assert gateway.patches[0][0] == "task-1" and gateway.patches[0][1] == "maop-system"
    assert gateway.patches[1][2]["completionTime"]


async def test_controller_marks_failed_on_nonzero_exit():
    gateway, runner = FakeGateway(), FakeRunner(RunOutcome(exit_code=3))
    result = await _controller(gateway, runner).handle_task(
        name="task-1", body=_body()
    )
    assert result["phase"] == "Failed"
    assert [p["phase"] for _, _, p in gateway.patches] == ["Running", "Failed"]


async def test_controller_marks_timed_out():
    gateway = FakeGateway()
    result = await _controller(gateway, FakeRunner(RunOutcome(timed_out=True))).handle_task(
        name="task-1", body=_body()
    )
    assert result["phase"] == "TimedOut"
    assert [p["phase"] for _, _, p in gateway.patches] == ["Running", "TimedOut"]


async def test_controller_invalid_spec_fails_without_launch():
    gateway, runner = FakeGateway(), FakeRunner(RunOutcome(exit_code=0))
    result = await _controller(gateway, runner).handle_task(
        name="task-1", body=_body(agent="")
    )
    assert result["phase"] == "Failed"
    assert result["launched"] is False
    assert result["reason"] == "InvalidSpec"
    # 未拉起：只有一条 Failed 补丁
    assert [p["phase"] for _, _, p in gateway.patches] == ["Failed"]
    assert runner.commands == []


async def test_controller_is_noop_on_terminal_phase():
    gateway, runner = FakeGateway(), FakeRunner(RunOutcome(exit_code=0))
    result = await _controller(gateway, runner).handle_task(
        name="task-1", body=_body(), status_phase="Succeeded"
    )
    assert result == {"phase": "Succeeded", "launched": False}
    assert gateway.patches == []
    assert runner.commands == []


async def test_controller_marks_cancelled_and_reraises():
    gateway = FakeGateway()
    ctrl = _controller(gateway, CancellingRunner())
    with pytest.raises(asyncio.CancelledError):
        await ctrl.handle_task(name="task-1", body=_body())
    assert [p["phase"] for _, _, p in gateway.patches] == ["Running", "Cancelled"]


# ── kopf 层 ──────────────────────────────────────────────────────────


def test_register_with_stub_kopf():
    """stub 对齐真实 kopf API 形状：注册走 ``kopf.on.event(group, version)``。"""
    recorded = {}

    class _On:
        def event(self, event: str, *, group: str, version: str):
            def deco(fn):
                recorded[(group, version, event)] = fn
                return fn

            return deco

    class StubKopf:
        on = _On()

    handlers.register(StubKopf(), controller=_controller(FakeGateway(), FakeRunner(RunOutcome())))
    assert ("maop.io", "v1alpha1", "maoptasks") in recorded


def test_require_kopf_raises_with_guidance_when_missing(monkeypatch):
    """kopf 未安装时报带安装指引的 RuntimeError（镜像内才装）。"""
    import builtins

    real_import = builtins.__import__

    def _no_kopf(name, *args, **kwargs):
        if name == "kopf":
            raise ImportError("No module named 'kopf'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _no_kopf)
    with pytest.raises(RuntimeError, match="pip install kopf"):
        handlers._require_kopf()


# ── 镜像构建静态守卫 ─────────────────────────────────────────────────


def test_operator_dockerfile_build_invariants():
    """镜像构建不变量（2026-10-30 本地复刻 CI 构建两处真红的静态钉）。

    PR 上 docker 腿不跑（``if: push && (main||master)``），静态守卫是 PR 期唯一防线。

    1. **config/ 拷贝**：``py/hatch_build.py`` 把 ``../config`` 当必需构建资产
       （``dashboard/dist-enterprise`` 才可跳过），缺则 wheel 构元数据阶段
       ``metadata-generation-failed``。要求 ``COPY config/ /app/config/`` 且
       早于 ``pip install /app/py``（``/app/py/../config`` 由此可解析）。
    2. **useradd 复用既有组**：基镜像（Debian slim）自带 ``operator`` 组
       （GID 37），裸 ``useradd`` 会因同名组已存在直接 exit 9 —— 必须带
       ``--gid``。
    """
    dockerfile = REPO / "deploy" / "k8s" / "operator" / "Dockerfile"
    lines = [ln.strip() for ln in dockerfile.read_text(encoding="utf-8").splitlines()]

    copy_config = [
        i for i, ln in enumerate(lines) if ln.startswith("COPY config/") and "/app/config" in ln
    ]
    pip_install = [i for i, ln in enumerate(lines) if "pip install" in ln and "/app/py" in ln]
    assert copy_config, "operator Dockerfile 必须 COPY config/ 到 /app/config（wheel 必需资产）"
    assert pip_install, "operator Dockerfile 必须在 /app/py 上 pip install"
    assert copy_config[0] < pip_install[0], "COPY config/ 必须早于 pip install /app/py"

    useradd = [ln for ln in lines if ln.startswith("RUN useradd")]
    assert useradd, "operator Dockerfile 应有 useradd（非 root 运行）"
    assert all("--gid" in ln for ln in useradd), (
        "useradd 必须带 --gid 复用基镜像既有 operator 组（同名组裸建 exit 9）"
    )
