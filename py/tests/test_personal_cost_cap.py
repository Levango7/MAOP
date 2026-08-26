"""P1-1 个人版成本兜底护栏单测。

覆盖三条路径：
  1. 低于阈值放行：cap=$1.0，累计花费 $0.5 → check_new_call() 返回 (True, "")
  2. 软熔断（默认）：cap=$1.0，累计花费 $1.2 → check_new_call() 返回 (False, reason)，
     should_interrupt_running() 返回 False
  3. 硬熔断：cap=$1.0，personal_cost_hard=True，累计花费 $1.2 →
     check_new_call() 返回 (False, reason)，should_interrupt_running() 返回 True

测试通过 CostTracker.record() 注入真实花费数据（gpt-4o 定价 2.50/1M prompt），
不 mock 数据库。DB 隔离由 conftest.py 的 _isolate_data_dir（MAOP_DATA_DIR）保证。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from maop.core.cost_tracker import CostTracker
from maop.core.personal_cost_guard import PersonalCostGuard

# ── 辅助：用 gpt-4o（2.50/1M prompt）注入精确花费 ──────────────────
# 200000 prompt tokens × 2.50/1M = $0.50
# 480000 prompt tokens × 2.50/1M = $1.20
_COST_0_5_TOKENS = 200_000
_COST_1_2_TOKENS = 480_000


def _make_tracker_with_spend(tmp_path: Path, prompt_tokens: int) -> CostTracker:
    """创建 CostTracker 并记录一笔 gpt-4o 调用产生指定花费。"""
    tracker = CostTracker(root_dir=tmp_path)
    tracker.record(
        agent="test-agent",
        model="gpt-4o",
        prompt_tokens=prompt_tokens,
        completion_tokens=0,
    )
    return tracker


# ── 1. 低于阈值放行 ───────────────────────────────────────────────


def test_below_cap_allows_new_call(tmp_path: Path) -> None:
    """cap=$1.0，累计花费 $0.5 → check_new_call() 放行。"""
    tracker = _make_tracker_with_spend(tmp_path, _COST_0_5_TOKENS)
    # 校验注入的花费确实为 $0.5
    assert tracker.summary().total_cost_usd == pytest.approx(0.5)

    guard = PersonalCostGuard(cost_tracker=tracker, cost_cap=1.0)
    allowed, reason = guard.check_new_call()

    assert allowed is True
    assert reason == ""
    # 未熔断，不应中断运行中
    assert guard.should_interrupt_running() is False


# ── 2. 软熔断（默认）────────────────────────────────────────────


def test_soft_breaker_rejects_new_call_but_keeps_running(tmp_path: Path) -> None:
    """cap=$1.0，累计花费 $1.2，软熔断 → 拒绝新调用但不中断运行中。"""
    tracker = _make_tracker_with_spend(tmp_path, _COST_1_2_TOKENS)
    assert tracker.summary().total_cost_usd == pytest.approx(1.2)

    guard = PersonalCostGuard(cost_tracker=tracker, cost_cap=1.0, cost_hard=False)
    allowed, reason = guard.check_new_call()

    assert allowed is False
    # reason 含累计花费 $1.2（格式化为 $1.20，含 $1.2 子串）
    assert "$1.2" in reason
    assert "Personal cost cap exceeded" in reason
    # 软熔断：不中断运行中任务
    assert guard.should_interrupt_running() is False
    # 状态快照
    status = guard.get_status()
    assert status["tripped"] is True
    assert status["mode"] == "soft"
    assert status["hard"] is False


# ── 3. 硬熔断 ─────────────────────────────────────────────────────


def test_hard_breaker_rejects_new_call_and_interrupts_running(tmp_path: Path) -> None:
    """cap=$1.0，hard=True，累计花费 $1.2 → 拒绝新调用且中断运行中。"""
    tracker = _make_tracker_with_spend(tmp_path, _COST_1_2_TOKENS)
    assert tracker.summary().total_cost_usd == pytest.approx(1.2)

    guard = PersonalCostGuard(cost_tracker=tracker, cost_cap=1.0, cost_hard=True)
    allowed, reason = guard.check_new_call()

    assert allowed is False
    assert "$1.2" in reason
    assert "Personal cost cap exceeded" in reason
    # 硬熔断：应中断运行中任务
    assert guard.should_interrupt_running() is True
    # 状态快照
    status = guard.get_status()
    assert status["tripped"] is True
    assert status["mode"] == "hard"
    assert status["hard"] is True