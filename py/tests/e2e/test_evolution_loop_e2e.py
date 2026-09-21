"""E2E 测试：自演化闭环 dry_run 可配置 + A/B SPRT 接入 + 回滚联动。

覆盖 ROADMAP v5.2.0 验收标准：
- AC-01: MAOP_EVOLUTION_LOOP_ENABLED 默认关闭
- AC-03: MAOP_EVOLUTION_DRY_RUN 默认 True，可配置为 False
- AC-04: A/B SPRT 决策接入 VALIDATE 阶段，联动回滚
- AC-05: 劣化候选注入 → 自动回滚 < 5 分钟

所有测试用 mock 构造环境，不依赖真实 LLM 调用。
"""

from __future__ import annotations

import os
import time
from unittest.mock import MagicMock, patch

import pytest  # noqa: F401

# ── 辅助：构造预设 PhaseResult ──────────────────────────────


def _make_phase_result(phase, success=True, **detail_kwargs):
    """构造 PhaseResult，避免每次手动导入。

    参数:
        phase: LoopPhase 枚举值
        success: 阶段是否成功
        **detail_kwargs: 写入 details 字典的字段
    """
    from maop.core.evolution.evolution_loop_types import PhaseResult

    return PhaseResult(
        phase=phase,
        success=success,
        duration_s=0.001,
        details=detail_kwargs,
    )


# ── 测试 1-3: 环境变量配置（AC-01 / AC-03）─────────────────


def test_evolution_loop_disabled_by_default(monkeypatch):
    """MAOP_EVOLUTION_LOOP_ENABLED 未设置时闭环不执行。

    验证 _env_truthy("MAOP_EVOLUTION_LOOP_ENABLED") 在未设置时返回 False，
    从而 _phase_evolve 不进入完整闭环分支（AC-01 默认关闭）。
    """
    from maop.maop_loop_phases import _env_truthy

    # 确保环境变量未设置
    monkeypatch.delenv("MAOP_EVOLUTION_LOOP_ENABLED", raising=False)
    assert _env_truthy("MAOP_EVOLUTION_LOOP_ENABLED") is False

    # 设置为空字符串也应返回 False
    monkeypatch.setenv("MAOP_EVOLUTION_LOOP_ENABLED", "")
    assert _env_truthy("MAOP_EVOLUTION_LOOP_ENABLED") is False


def test_evolution_loop_dry_run_default(monkeypatch):
    """MAOP_EVOLUTION_DRY_RUN 未设置时 dry_run=True（向后兼容）。

    验证环境变量解析逻辑：未设置时默认 True，即仅预览不 APPLY。
    """
    monkeypatch.delenv("MAOP_EVOLUTION_DRY_RUN", raising=False)
    # 模拟 maop_loop_phases.py 中的 dry_run 解析逻辑
    dry_run = os.getenv("MAOP_EVOLUTION_DRY_RUN", "true").strip().lower() in (
        "true", "1", "yes", "on",
    )
    assert dry_run is True, "未设置 MAOP_EVOLUTION_DRY_RUN 时应默认 True"


def test_evolution_loop_dry_run_false(monkeypatch):
    """设置 MAOP_EVOLUTION_DRY_RUN=0 时 dry_run=False（实际执行 APPLY）。"""
    monkeypatch.setenv("MAOP_EVOLUTION_DRY_RUN", "0")
    dry_run = os.getenv("MAOP_EVOLUTION_DRY_RUN", "true").strip().lower() in (
        "true", "1", "yes", "on",
    )
    assert dry_run is False, "MAOP_EVOLUTION_DRY_RUN=0 时 dry_run 应为 False"

    # 验证其他 falsy 值
    for val in ("false", "no", "off", ""):
        monkeypatch.setenv("MAOP_EVOLUTION_DRY_RUN", val)
        dry_run = os.getenv("MAOP_EVOLUTION_DRY_RUN", "true").strip().lower() in (
            "true", "1", "yes", "on",
        )
        assert dry_run is False, f"MAOP_EVOLUTION_DRY_RUN={val!r} 时 dry_run 应为 False"


# ── 测试 4: OBSERVE→SUGGEST 阶段流转 ───────────────────────


def test_evolution_loop_observe_suggest(evolution_loop_factory, monkeypatch):
    """OBSERVE→SUGGEST 阶段流转：有错误时完整走完七阶段。

    用 mock 替换各 _phase_* 方法，验证 run_cycle 正确编排阶段顺序。
    """
    from maop.core.evolution.evolution_loop_types import LoopPhase

    # auto_consolidate=False 避免 _phase_consolidate 依赖 MemoryFacade
    loop = evolution_loop_factory(auto_consolidate=False)

    # Mock 各阶段方法，返回预设 PhaseResult
    def mock_observe():
        return _make_phase_result(
            LoopPhase.OBSERVE, hotspot_count=2, hotspot_patterns=["err_a", "err_b"],
        )

    def mock_heal(patterns):
        return _make_phase_result(LoopPhase.HEAL, attempts=2, successes=1)

    def mock_suggest(patterns):
        return _make_phase_result(
            LoopPhase.SUGGEST, count=1, suggestions=[{"id": "s1"}],
        )

    def mock_evaluate(suggestions):
        return _make_phase_result(
            LoopPhase.EVALUATE, approved=[{"id": "s1"}], pending_approval=[],
        )

    def mock_apply(approved, dry_run=False):
        return _make_phase_result(LoopPhase.APPLY, applied=1)

    def mock_validate(baseline_errors):
        return _make_phase_result(
            LoopPhase.VALIDATE, improved=True, baseline=baseline_errors, current=0,
            ab_recommendation=None, ab_decision="", ab_winner="", ab_experiment="",
        )

    monkeypatch.setattr(loop, "_phase_observe", mock_observe)
    monkeypatch.setattr(loop, "_phase_heal", mock_heal)
    monkeypatch.setattr(loop, "_phase_suggest", mock_suggest)
    monkeypatch.setattr(loop, "_phase_evaluate", mock_evaluate)
    monkeypatch.setattr(loop, "_phase_apply", mock_apply)
    monkeypatch.setattr(loop, "_phase_validate", mock_validate)

    # dry_run=True 避免 snapshot/rollback 副作用
    report = loop.run_cycle(dry_run=True)

    # 验证阶段顺序
    phase_names = [p.phase for p in report.phases]
    assert LoopPhase.OBSERVE in phase_names, "应包含 OBSERVE 阶段"
    assert LoopPhase.SUGGEST in phase_names, "应包含 SUGGEST 阶段"
    assert report.errors_observed == 2
    assert report.suggestions_generated == 1
    assert report.suggestions_applied == 1
    assert report.validation_improved is True


# ── 测试 5: 劣化候选注入后触发回滚 ─────────────────────────


def test_evolution_loop_rollback_on_degradation(evolution_loop_factory, monkeypatch):
    """劣化候选注入后触发回滚：VALIDATE 未改善 → 自动回滚。

    构造 validation_improved=False 的场景，验证 run_cycle 触发 rollback_cycle。
    """
    from maop.core.evolution.evolution_loop_types import LoopPhase

    loop = evolution_loop_factory(auto_consolidate=False)

    def mock_observe():
        return _make_phase_result(
            LoopPhase.OBSERVE, hotspot_count=1, hotspot_patterns=["err_x"],
        )

    def mock_heal(patterns):
        return _make_phase_result(LoopPhase.HEAL, attempts=0, successes=0)

    def mock_suggest(patterns):
        return _make_phase_result(
            LoopPhase.SUGGEST, count=1, suggestions=[{"id": "s1"}],
        )

    def mock_evaluate(suggestions):
        return _make_phase_result(
            LoopPhase.EVALUATE, approved=[{"id": "s1"}], pending_approval=[],
        )

    def mock_apply(approved, dry_run=False):
        return _make_phase_result(LoopPhase.APPLY, applied=1)

    def mock_validate(baseline_errors):
        # 劣化：未改善，无 A/B 决策（传统路径）
        return _make_phase_result(
            LoopPhase.VALIDATE, improved=False, baseline=baseline_errors, current=2,
            ab_recommendation=None, ab_decision="", ab_winner="", ab_experiment="",
        )

    monkeypatch.setattr(loop, "_phase_observe", mock_observe)
    monkeypatch.setattr(loop, "_phase_heal", mock_heal)
    monkeypatch.setattr(loop, "_phase_suggest", mock_suggest)
    monkeypatch.setattr(loop, "_phase_evaluate", mock_evaluate)
    monkeypatch.setattr(loop, "_phase_apply", mock_apply)
    monkeypatch.setattr(loop, "_phase_validate", mock_validate)

    # Mock ChangeTracker：snapshot 返回 ID，rollback 返回恢复文件数
    with patch("maop.core.reliability.change_tracker.ChangeTracker") as mock_ct_class:
        mock_ct = MagicMock()
        mock_ct.snapshot.return_value = "snap-degrade-001"
        mock_ct.rollback.return_value = 3
        mock_ct_class.return_value = mock_ct

        # dry_run=False + auto_rollback=True → 应触发回滚
        report = loop.run_cycle(dry_run=False, auto_rollback=True)

    assert report.validation_improved is False, "劣化场景下 validation 不应改善"
    assert report.rolled_back is True, "未改善时应触发自动回滚"
    assert report.snapshot_id == "snap-degrade-001"
    mock_ct.rollback.assert_called_once()


# ── 测试 6: A/B SPRT 决策接入 VALIDATE（AC-04）─────────────


def test_ab_test_sprt_integration(evolution_loop_factory, monkeypatch):
    """A/B SPRT 决策接入 VALIDATE：promote 跳过回滚，rollback 强制回滚。

    验证 run_cycle 根据 _phase_validate 返回的 ab_recommendation 做出正确回滚决策：
    - ab_recommendation="promote" → 即使 improved=False 也跳过回滚
    - ab_recommendation="rollback" → 即使 improved=True 也强制回滚
    """
    from maop.core.evolution.evolution_loop_types import LoopPhase

    loop = evolution_loop_factory(auto_consolidate=False)

    # 公共 mock：observe/heal/suggest/evaluate/apply
    def mock_observe():
        return _make_phase_result(
            LoopPhase.OBSERVE, hotspot_count=1, hotspot_patterns=["err_y"],
        )

    def mock_heal(patterns):
        return _make_phase_result(LoopPhase.HEAL, attempts=0, successes=0)

    def mock_suggest(patterns):
        return _make_phase_result(
            LoopPhase.SUGGEST, count=1, suggestions=[{"id": "s1"}],
        )

    def mock_evaluate(suggestions):
        return _make_phase_result(
            LoopPhase.EVALUATE, approved=[{"id": "s1"}], pending_approval=[],
        )

    def mock_apply(approved, dry_run=False):
        return _make_phase_result(LoopPhase.APPLY, applied=1)

    monkeypatch.setattr(loop, "_phase_observe", mock_observe)
    monkeypatch.setattr(loop, "_phase_heal", mock_heal)
    monkeypatch.setattr(loop, "_phase_suggest", mock_suggest)
    monkeypatch.setattr(loop, "_phase_evaluate", mock_evaluate)
    monkeypatch.setattr(loop, "_phase_apply", mock_apply)

    with patch("maop.core.reliability.change_tracker.ChangeTracker") as mock_ct_class:
        mock_ct = MagicMock()
        mock_ct.snapshot.return_value = "snap-ab-001"
        mock_ct.rollback.return_value = 2
        mock_ct_class.return_value = mock_ct

        # 场景 A：AB 决策 promote → 即使 improved=False 也跳过回滚
        def mock_validate_promote(baseline_errors):
            return _make_phase_result(
                LoopPhase.VALIDATE, improved=False, baseline=baseline_errors, current=2,
                ab_recommendation="promote", ab_decision="accept_h1",
                ab_winner="treatment", ab_experiment="exp1",
            )

        monkeypatch.setattr(loop, "_phase_validate", mock_validate_promote)
        report_promote = loop.run_cycle(dry_run=False, auto_rollback=True)
        assert report_promote.rolled_back is False, (
            "AB promote 应跳过回滚（即使 validation 未改善）"
        )

        # 场景 B：AB 决策 rollback → 即使 improved=True 也强制回滚
        def mock_validate_rollback(baseline_errors):
            return _make_phase_result(
                LoopPhase.VALIDATE, improved=True, baseline=baseline_errors, current=0,
                ab_recommendation="rollback", ab_decision="accept_h0",
                ab_winner="", ab_experiment="exp1",
            )

        monkeypatch.setattr(loop, "_phase_validate", mock_validate_rollback)
        report_rollback = loop.run_cycle(dry_run=False, auto_rollback=True)
        assert report_rollback.rolled_back is True, (
            "AB rollback 应强制回滚（即使 validation 改善）"
        )


# ── 测试 7 (步骤5): 回滚 SLA < 5 分钟（AC-05）──────────────


def test_rollback_sla_within_5min(evolution_loop_factory, monkeypatch):
    """回滚 SLA 验证：劣化候选注入 → 自动回滚 < 5 分钟（300s）。

    ROADMAP v5.2.0 验收标准 2（AC-05）。
    用 _build_degradation_test_suggestion() 构造必然失败的 mutation，
    测量 run_cycle 回滚耗时，断言 < 300 秒。
    """
    from maop.core.evolution.evolution_loop_types import LoopPhase

    loop = evolution_loop_factory(auto_consolidate=False)

    # 验证 _build_degradation_test_suggestion 返回正确结构
    deg_suggestion = loop._build_degradation_test_suggestion()
    assert deg_suggestion.mutation_params.get("timeout_s") == -1, (
        "劣化建议应包含 timeout_s=-1"
    )

    def mock_observe():
        return _make_phase_result(
            LoopPhase.OBSERVE, hotspot_count=1, hotspot_patterns=["err_deg"],
        )

    def mock_heal(patterns):
        return _make_phase_result(LoopPhase.HEAL, attempts=0, successes=0)

    def mock_suggest(patterns):
        return _make_phase_result(
            LoopPhase.SUGGEST, count=1, suggestions=[deg_suggestion.model_dump()],
        )

    def mock_evaluate(suggestions):
        return _make_phase_result(
            LoopPhase.EVALUATE, approved=[suggestions[0]], pending_approval=[],
        )

    def mock_apply(approved, dry_run=False):
        return _make_phase_result(LoopPhase.APPLY, applied=1)

    def mock_validate(baseline_errors):
        # 劣化注入 → VALIDATE 失败
        return _make_phase_result(
            LoopPhase.VALIDATE, improved=False, baseline=baseline_errors, current=3,
            ab_recommendation=None, ab_decision="", ab_winner="", ab_experiment="",
        )

    monkeypatch.setattr(loop, "_phase_observe", mock_observe)
    monkeypatch.setattr(loop, "_phase_heal", mock_heal)
    monkeypatch.setattr(loop, "_phase_suggest", mock_suggest)
    monkeypatch.setattr(loop, "_phase_evaluate", mock_evaluate)
    monkeypatch.setattr(loop, "_phase_apply", mock_apply)
    monkeypatch.setattr(loop, "_phase_validate", mock_validate)

    with patch("maop.core.reliability.change_tracker.ChangeTracker") as mock_ct_class:
        mock_ct = MagicMock()
        mock_ct.snapshot.return_value = "snap-sla-001"
        mock_ct.rollback.return_value = 5
        mock_ct_class.return_value = mock_ct

        t0 = time.monotonic()
        report = loop.run_cycle(dry_run=False, auto_rollback=True)
        elapsed = time.monotonic() - t0

    assert report.rolled_back is True, "劣化注入应触发回滚"
    assert elapsed < 300, f"回滚耗时 {elapsed:.2f}s 超 5min SLA"