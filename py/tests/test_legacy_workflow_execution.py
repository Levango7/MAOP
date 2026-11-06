"""GAP-1 收口：legacy ``run_workflow`` 必须**真实执行**节点（不再只规划）.

根因（M-SIM M0 演练发现）：``wiring.run_workflow`` 在默认（legacy）后端下走
``maop_plan.execute_workflow``——**纯规划**，一个 agent 都不跑；但生产入口
（``maop workflow run``、dashboard ``POST /api/workflows/{name}/run``）都指
向它，表现为「提交成功、零节点执行」。

本组测试钉死收口后的合同：
- 节点按编译层序真实 dispatch（每节点走 ``maop run`` 子进程）；
- ``steps_completed`` 反映真实成功数；
- 节点事件回写 ``run_events``（``run.workflow.step.started/completed``），
  面板可沿 trace_id 看工作流与子 agent 事件同轴。
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

from maop.config.loader import load_config
from maop.core.backends.db_utils import get_db_path
from maop.orchestration.wiring import run_workflow_sync

# 确定性 stub agent（零外部 LLM/网络）：回显输入并打印 [USAGE] 协议行。
_STUB = Path(__file__).resolve().parents[1] / "scripts" / "business_sim" / "stub_business_agent.py"


def _write_sim_home(tmp_path: Path) -> Path:
    home = tmp_path / "home"
    (home / "config").mkdir(parents=True, exist_ok=True)
    stub = str(_STUB).replace("\\", "/")
    py = sys.executable.replace("\\", "/")
    agents = ""
    for name, role in (("sim-triage", "triage"), ("sim-worker", "worker")):
        agents += (
            f"  {name}:\n"
            f"    capabilities: [chat, planning]\n"
            f"    cli: {py}\n"
            f'    cli_args: {stub} --role {role} --task "{{task}}"\n'
            f"    driver: cli\n"
            f"    model: auto\n"
            f"    timeout_s: 120\n"
        )
    workflows = (
        "workflows:\n"
        "  ticket:\n"
        "    description: 'GAP-1 legacy execution test: triage -> worker.'\n"
        "    steps:\n"
        "      - agent: sim-triage\n"
        "        task: '工单 1042 分诊：登录令牌失效'\n"
        "        timeout_s: 120\n"
        "      - agent: sim-worker\n"
        "        task: '工单 1042 处置：重置会话'\n"
        "        depends_on: ['0']\n"
        "        timeout_s: 120\n"
    )
    (home / "config" / "agents.yaml").write_text(
        "agents:\n" + agents + workflows, encoding="utf-8"
    )
    return home


@pytest.mark.integration
def test_legacy_run_workflow_really_executes_nodes(tmp_path: Path) -> None:
    """legacy 后端真实执行两节点：steps_completed=2（修复前恒为 0）。"""
    home = _write_sim_home(tmp_path)
    config = load_config(str(home))
    result = run_workflow_sync("ticket", config=config, initial_vars={})
    assert result.steps_total == 2
    assert result.steps_planned == 2
    assert result.steps_completed == 2, result.step_results
    assert [r.status for r in result.step_results] == ["success", "success"]
    assert [r.agent for r in result.step_results] == ["sim-triage", "sim-worker"]


@pytest.mark.integration
def test_legacy_run_workflow_writes_step_events(tmp_path: Path) -> None:
    """节点事件回写 run_events（started/completed 各一条）——面板同轴可查。"""
    home = _write_sim_home(tmp_path)
    config = load_config(str(home))
    run_workflow_sync("ticket", config=config, initial_vars={})
    db = get_db_path("run_events", legacy_fallback="maop")
    with sqlite3.connect(str(db)) as conn:
        kinds = [
            r[0] for r in conn.execute(
                "SELECT kind FROM run_events WHERE trace_id = ? ORDER BY seq",
                ("ticket",),
            ).fetchall()
        ]
    assert "run.workflow.step.started" in kinds
    assert "run.workflow.step.success" in kinds


@pytest.mark.integration
def test_legacy_run_workflow_missing_workflow_is_noop(tmp_path: Path) -> None:
    """未知 workflow：不执行任何节点（诚实空结果，非静默假成功）。"""
    home = _write_sim_home(tmp_path)
    config = load_config(str(home))
    result = run_workflow_sync("no-such-workflow", config=config)
    assert result.steps_total == 0
    assert result.steps_completed == 0
