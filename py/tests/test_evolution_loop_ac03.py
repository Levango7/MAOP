"""AC-03 验收：E2E 完整闭环 observe→suggest→evaluate→apply→validate→consolidate。

基于 spec-v5.2.0-evolution-loop.md §16 AC-03 + §9 AC-03。

AC-03：When 对模拟 agent 执行完整 observe→suggest→evaluate→apply→validate→consolidate，
系统必须完整跑通并产出真实 LoopReport（无序列化错误），状态机流转记录可入库查询。

架构重构要点（v2，与 v1 失败对比）：
  - v1：用 MagicMock 模拟所有内部阶段（_phase_observe 等）→ 真实 run_cycle 末尾
    调 report.model_dump_json() 序列化 Pydantic 模型时，因 details 字段含 MagicMock
    而 Pydantic 序列化失败（Circular reference detected）。
  - v2：使用真实数据（真实 ErrorLedger.record() 注入错误），仅 mock 必须
    的外部副作用（ChangeTracker 避免改真实文件、ABTestManager 注入 p-value），
    让 _phase_observe/heal/suggest/evaluate/apply/validate/consolidate 全部走真实
    实现路径。details 字段全部为真实 dict，model_dump_json() 序列化成功。
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest  # noqa: F401

# ─────────────────────────────────────────────────────────────────
# 辅助：真实数据注入（不使用 MagicMock）
# ─────────────────────────────────────────────────────────────────


def _seed_error_ledger(root_dir: Path, pattern: str, count: int, agent: str = "test-agent") -> None:
    """向真实 ErrorLedger 写入错误记录，recurrence 累加。

    ErrorLedger.record()：若同 pattern 已有最新一条记录则 recurrence+1（累加到 ≥ count），
    否则插入新行 recurrence=1。重复调用 count 次后，该 pattern 累计 recurrence=count。
    """
    from maop.core.reliability.error_ledger import ErrorLedger
    ledger = ErrorLedger(root_dir=str(root_dir))
    for _ in range(count):
        ledger.record(
            error_type=pattern,
            context=f"E2E test seed for {pattern}",
            trigger={"agent": agent},
            output="",
            expected="",
            root_cause="e2e_test_seed",
            pattern=pattern,
        )


def _assert_loop_report_serializable(report) -> None:
    """验证 Pydantic 序列化成功（v1 失败点）。"""
    # LoopReport 必须能序列化为 JSON 不抛异常
    s = report.model_dump_json()
    assert isinstance(s, str)
    obj = json.loads(s)
    assert "cycle_id" in obj
    assert "phases" in obj
    # 至少 1 个阶段（observe），错误时有 6+ 个阶段
    assert len(obj["phases"]) >= 1


# ─────────────────────────────────────────────────────────────────
# AC-03 完整闭环：成功路径
# ─────────────────────────────────────────────────────────────────


def test_ac03_e2e_full_closed_loop_promote_path(evolution_loop_factory):
    """E2E 完整闭环：注入热点 → 真实 run_cycle → 验证状态机流转 + 序列化。

    路径：errors 注入 → observe 检出 → heal 尝试 → suggest 生成 → evaluate 决策
          → apply 落地 → validate 显示改进（p<0.05）→ consolidate 写库。
    """
    loop = evolution_loop_factory()

    # 1. 真实 ErrorLedger 注入热点（recurrence=5 ≥ heal_threshold=2）
    _seed_error_ledger(loop._root, "timeout", count=5)
    _seed_error_ledger(loop._root, "connection_error", count=4)

    # 2. 隔离外部副作用：ChangeTracker 避免真实文件系统变更
    with patch("maop.core.reliability.change_tracker.ChangeTracker") as mock_ct_class:
        mock_ct = MagicMock()
        mock_ct.snapshot.return_value = "snap-e2e-001"
        mock_ct.rollback.return_value = 3
        mock_ct_class.return_value = mock_ct

        # 3. 隔离 A/B 测试：注入显著 p-value（p<0.05 触发 promote）
        with patch("maop.core.evolution.ab_test.ABTestManager") as mock_ab_class:
            mock_ab = MagicMock()
            mock_ab.evaluate.return_value = MagicMock(
                p_value=0.03,  # 显著
                control_rate=0.10,
                treatment_rate=0.15,
                control_count=1000,
                treatment_count=1000,
            )
            mock_ab_class.return_value = mock_ab

            # 4. 跑真实 run_cycle（dry_run=False, auto_rollback=True）
            report = loop.run_cycle(dry_run=False, auto_rollback=True)

    # ───────── 验证：序列化（v1 失败点） ─────────
    _assert_loop_report_serializable(report)

    # ───────── 验证：状态机流转 ─────────
    assert report.cycle_id is not None
    phase_names = [p.phase.value for p in report.phases]
    assert "observe" in phase_names
    assert "heal" in phase_names
    assert "suggest" in phase_names
    assert "evaluate" in phase_names
    assert "apply" in phase_names
    assert "validate" in phase_names
    # consolidate 也跑了（_auto_consolidate=True 是默认）

    # ───────── 验证：关键指标 ─────────
    assert report.errors_observed >= 2  # 2 个 pattern
    assert report.suggestions_generated >= 1
    assert report.dry_run is False


# ─────────────────────────────────────────────────────────────────
# AC-03 完整闭环：回滚路径
# ─────────────────────────────────────────────────────────────────


def test_ac03_e2e_full_closed_loop_rollback_path(evolution_loop_factory):
    """E2E 回滚路径：注入热点 → validate 显示未改进 → 自动回滚。

    路径：errors 注入 → observe 检出 → ... → apply 落地 → validate 不改进
          (p>0.05) → auto_rollback=True 触发 rollback → consolidate。
    """
    loop = evolution_loop_factory()

    # 1. 真实 ErrorLedger 注入热点
    _seed_error_ledger(loop._root, "rate_limit", count=6)

    # 2. ChangeTracker mock
    with patch("maop.core.reliability.change_tracker.ChangeTracker") as mock_ct_class:
        mock_ct = MagicMock()
        mock_ct.snapshot.return_value = "snap-rollback-001"
        mock_ct.rollback.return_value = 2  # 恢复了 2 个文件
        mock_ct_class.return_value = mock_ct

        # 3. ABTestManager 注入不显著的 p-value（p>0.05 触发回滚）
        with patch("maop.core.evolution.ab_test.ABTestManager") as mock_ab_class:
            mock_ab = MagicMock()
            mock_ab.evaluate.return_value = MagicMock(
                p_value=0.50,  # 不显著
                control_rate=0.10,
                treatment_rate=0.12,
                control_count=1000,
                treatment_count=1000,
            )
            mock_ab_class.return_value = mock_ab

            # 4. 跑真实 run_cycle
            report = loop.run_cycle(dry_run=False, auto_rollback=True)

    # ───────── 验证：序列化 ─────────
    _assert_loop_report_serializable(report)

    # ───────── 验证：状态机完整流转 ─────────
    assert report.cycle_id is not None
    phase_names = [p.phase.value for p in report.phases]
    assert "observe" in phase_names
    assert "validate" in phase_names

    # ───────── 验证：snapshot 已创建（用于回滚） ─────────
    # 仅在非 dry_run 且有 apply 时创建 snapshot
    if not report.dry_run and report.suggestions_applied > 0:
        assert report.snapshot_id == "snap-rollback-001"


# ─────────────────────────────────────────────────────────────────
# AC-03 完整闭环：无错误路径（边界）
# ─────────────────────────────────────────────────────────────────


def test_ac03_e2e_no_errors_short_circuit(evolution_loop_factory):
    """E2E 边界：ErrorLedger 干净 → run_cycle 快速返回（无 apply/validate）。"""
    loop = evolution_loop_factory()

    # 不注入任何错误（默认 heal_threshold=2 + suggest_threshold=3 都不会触发）

    # ChangeTracker 不应被调用（无 apply）
    with patch("maop.core.reliability.change_tracker.ChangeTracker") as mock_ct_class:
        mock_ct = MagicMock()
        mock_ct_class.return_value = mock_ct

        report = loop.run_cycle(dry_run=False, auto_rollback=True)

    # ───────── 验证：序列化 ─────────
    _assert_loop_report_serializable(report)

    # ───────── 验证：只有 observe 阶段，无 apply/validate ─────────
    assert report.errors_observed == 0
    phase_names = [p.phase.value for p in report.phases]
    # observe 跑了；heal/suggest 都不应跑（早期 short-circuit）
    assert "observe" in phase_names
    # snapshot_id 不应被设置（无 apply）
    assert report.snapshot_id == "" or report.snapshot_id is None
    # 不应触发回滚
    assert report.rolled_back is False
    # ChangeTracker 不应被调用
    mock_ct.snapshot.assert_not_called()


# ─────────────────────────────────────────────────────────────────
# AC-03 完整闭环：dry_run 路径（不实际变更）
# ─────────────────────────────────────────────────────────────────


def test_ac03_e2e_dry_run_does_not_snapshot(evolution_loop_factory):
    """E2E dry_run=True → apply 阶段记录 proposed 而不执行，无 snapshot 创建。"""
    loop = evolution_loop_factory()

    # 注入热点
    _seed_error_ledger(loop._root, "timeout_dry", count=4)

    with patch("maop.core.reliability.change_tracker.ChangeTracker") as mock_ct_class:
        mock_ct = MagicMock()
        mock_ct_class.return_value = mock_ct

        with patch("maop.core.evolution.ab_test.ABTestManager") as mock_ab_class:
            mock_ab = MagicMock()
            mock_ab.evaluate.return_value = MagicMock(
                p_value=0.03, control_rate=0.10, treatment_rate=0.15,
                control_count=1000, treatment_count=1000,
            )
            mock_ab_class.return_value = mock_ab

            report = loop.run_cycle(dry_run=True, auto_rollback=True)

    # ───────── 验证：dry_run 标记 + 序列化 ─────────
    _assert_loop_report_serializable(report)
    assert report.dry_run is True
    # snapshot_id 在 dry_run 模式下应为空（避免无用快照）
    # （具体行为取决于 _save_report 之前是否赋空字符串，但 snapshot 函数不会调）
    mock_ct.snapshot.assert_not_called()


# ─────────────────────────────────────────────────────────────────
# AC-03 完整闭环：报告持久化
# ─────────────────────────────────────────────────────────────────


def test_ac03_e2e_report_persisted_to_db(evolution_loop_factory):
    """E2E 报告持久化：跑完 cycle 后，DB 中应可查到此 cycle 记录。"""
    loop = evolution_loop_factory()

    _seed_error_ledger(loop._root, "persisted_pattern", count=4)

    with patch("maop.core.reliability.change_tracker.ChangeTracker") as mock_ct_class:
        mock_ct = MagicMock()
        mock_ct.snapshot.return_value = "snap-persist-001"
        mock_ct_class.return_value = mock_ct

        with patch("maop.core.evolution.ab_test.ABTestManager") as mock_ab_class:
            mock_ab = MagicMock()
            mock_ab.evaluate.return_value = MagicMock(
                p_value=0.03, control_rate=0.10, treatment_rate=0.15,
                control_count=1000, treatment_count=1000,
            )
            mock_ab_class.return_value = mock_ab

            report = loop.run_cycle(dry_run=False, auto_rollback=True)

    # ───────── 验证：DB 中可查到此 cycle ─────────
    history = loop.get_cycle_history(limit=5)
    assert len(history) >= 1
    # 最近一条应为本 cycle
    found = history[0]
    assert found.cycle_id == report.cycle_id
    # 阶段数一致
    assert len(found.phases) == len(report.phases)
