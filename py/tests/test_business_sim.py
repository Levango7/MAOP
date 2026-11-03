"""M-SIM M0 harness tests — stub determinism + one real-path scenario.

CI-safe: the stub driver is local (no network/LLM); the end-to-end test runs
3 nodes through ``maop.cli run`` with an isolated MAOP_ROOT_DIR (~10s).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

SIM_DIR = Path(__file__).resolve().parents[1] / "scripts" / "business_sim"
sys.path.insert(0, str(SIM_DIR))

import run_sim  # scripts 目录非包，路径注入后导入

STUB = SIM_DIR / "stub_business_agent.py"


def _stub(task: str, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(STUB), "--task", task, *extra],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,  # 退出码本身就是断言对象
    )


def test_stub_is_deterministic() -> None:
    a = _stub("确定性探测任务")
    b = _stub("确定性探测任务")
    assert a.returncode == 0
    assert a.stdout == b.stdout
    assert "[USAGE] turns=" in a.stdout
    assert "source=estimated" in a.stdout


def test_stub_fail_on_injects_failure() -> None:
    p = _stub("这个任务包含 MAGIC_MARK 字样", "--fail-on", "MAGIC_MARK")
    assert p.returncode == 2
    assert "FAIL-INJECTED" in p.stderr


def test_topo_order_rejects_cycle() -> None:
    nodes = [
        {"id": "a", "agent": "x", "task": "t", "depends_on": ["b"]},
        {"id": "b", "agent": "x", "task": "t", "depends_on": ["a"]},
    ]
    with pytest.raises(ValueError, match="cycle"):
        run_sim.topo_order(nodes)


@pytest.mark.integration
def test_scenario_end_to_end_through_production_entry(tmp_path: Path) -> None:
    """三节点工单剧本经 ``maop.cli run`` 生产路径全 success + USAGE 可解析。"""
    home = run_sim.build_sim_home(tmp_path)
    py_dir = Path(__file__).resolve().parents[1]
    result = run_sim.run_scenario(home, py_dir, "ticket_triage")
    assert result["failed"] == 0
    assert result["success"] == result["executions"] == 3
    for r in result["runs"]:
        assert r["usage"] is not None, r["stdout_tail"]
        assert r["usage"]["source"] in {"estimated", "measured"}
        assert r["usage"]["turns"] >= 1
    # 隔离性：所有落库都在 sim home 内（cost/alert 表随 MAOP_ROOT_DIR）
    assert (home / "config" / "agents.yaml").exists()
