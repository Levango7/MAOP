"""AC-01 + AC-02 验收：MAOP_EVOLUTION_LOOP_ENABLED 开关行为。

基于 spec-v5.2.0-evolution-loop.md §9 AC-01/AC-02 + §5 接线点 #1。

AC-01：开关未设 / =0 → 现有 _phase_evolve 行为完全一致（仅 analyze），全量测试零回归
AC-02：开关 =1 → _phase_evolve 调用 EvolutionLoop.run_cycle() 并返回 LoopReport

注：直接调 _phase_evolve() 验证，不依赖完整主循环。
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


@pytest.fixture
def loop_phases(monkeypatch, tmp_path):
    """构造一个最小可用的 PhasesMixin 实例用于 _phase_evolve 单元测试。

    不依赖真实 MaopLoop（避免引入循环 / 业务副作用），只 mock 出 _phase_evolve
    真正用到的属性：_root / _loop_config / _bus / _log / _loop_count / _consolidator。
    """
    from maop.maop_loop_phases import PhasesMixin
    from maop.loop_models import LoopConfig

    class _Stub(PhasesMixin):
        def __init__(self, root, bus, consolidator=None):
            self._root = root
            self._loop_config = LoopConfig()
            self._bus = bus
            self._log = lambda phase, level, msg, trace_id: None  # noqa: E731
            self._loop_count = 0
            self._consolidator = consolidator

    bus = MagicMock()
    return _Stub(root=tmp_path, bus=bus)


@pytest.fixture
def phase_ctx():
    """构造一个 verify_result=True 的 PhaseContext 让 _phase_evolve 进入分支。"""
    from maop.core.agent.evolution.phases import PhaseContext
    return PhaseContext(
        trace_id="ac-test-001",
        plan_result=MagicMock(),
        execution_result=MagicMock(),  # 字段名是 execution_result，不是 exec_result
        verify_result=MagicMock(success=True, output="ok"),  # non-None → 进 analyze 分支
    )


def test_ac01_evolution_loop_disabled_keeps_only_analyze(
    monkeypatch, loop_phases, phase_ctx
):
    """AC-01：MAOP_EVOLUTION_LOOP_ENABLED 未设 → 仅调 EvolveEngine.analyze，不调 run_cycle。"""
    monkeypatch.delenv("MAOP_EVOLUTION_LOOP_ENABLED", raising=False)

    # 模拟 EvolveEngine.analyze 返回带 1 个建议的结果
    fake_suggestion = MagicMock()
    fake_suggestion.model_dump.return_value = {"id": "s1", "type": "adjust_timeout"}
    fake_analyze_result = MagicMock(suggestions=[fake_suggestion])
    fake_engine_instance = MagicMock()
    fake_engine_instance.analyze.return_value = fake_analyze_result

    # mock EvolutionLoop 类，验证 AC-01 下**不**被实例化
    fake_evo_loop_class = MagicMock()

    with patch("maop.evolve.EvolveEngine", return_value=fake_engine_instance) as mock_ee, \
         patch("maop.core.evolution.evolution_loop.EvolutionLoop", fake_evo_loop_class):
        result = asyncio.run(loop_phases._phase_evolve(phase_ctx))

    assert result.ok is True
    mock_ee.assert_called_once()  # analyze 路径执行
    fake_evo_loop_class.assert_not_called()  # ★ 闭环路径不执行（AC-01 关键断言）


def test_ac02_evolution_loop_enabled_invokes_run_cycle(
    monkeypatch, loop_phases, phase_ctx
):
    """AC-02：MAOP_EVOLUTION_LOOP_ENABLED=1 → _phase_evolve 调用 EvolutionLoop.run_cycle()。"""
    monkeypatch.setenv("MAOP_EVOLUTION_LOOP_ENABLED", "1")

    # mock EvolveEngine.analyze 走主路径
    fake_analyze_result = MagicMock(suggestions=[])
    fake_engine_instance = MagicMock()
    fake_engine_instance.analyze.return_value = fake_analyze_result

    # mock EvolutionLoop.run_cycle 返回带 cycle_id 的 LoopReport
    fake_loop_report = MagicMock(
        cycle_id="abc123",
        errors_observed=0,
        suggestions_generated=2,
        suggestions_applied=1,
        validation_improved=True,
        rolled_back=False,
    )
    fake_loop_instance = MagicMock()
    fake_loop_instance.run_cycle.return_value = fake_loop_report

    with patch("maop.evolve.EvolveEngine", return_value=fake_engine_instance), \
         patch("maop.core.evolution.evolution_loop.EvolutionLoop", return_value=fake_loop_instance) as mock_evo_cls:
        result = asyncio.run(loop_phases._phase_evolve(phase_ctx))

    assert result.ok is True
    mock_evo_cls.assert_called_once()  # ★ 闭环类被实例化
    fake_loop_instance.run_cycle.assert_called_once()
    call_kwargs = fake_loop_instance.run_cycle.call_args.kwargs
    assert call_kwargs.get("dry_run") is True  # 默认 dry_run=True（AC-02 安全第一）
    assert call_kwargs.get("auto_rollback") is True


def test_ac01_truthy_values_for_disabled_env():
    """AC-01 边界：环境变量 = 'false' / '0' / '' 都判为关闭（与默认一致）。"""
    from maop.maop_loop_phases import _env_truthy
    for val in ("0", "false", "False", "FALSE", "", "no", "off", "  "):
        os.environ["TEST_EVO_FLAG"] = val
        assert _env_truthy("TEST_EVO_FLAG") is False, f"val={val!r} should be False"


def test_ac02_truthy_values_for_enabled_env():
    """AC-02 边界：环境变量 = '1' / 'true' / 'yes' / 'on'（任意大小写）都判为开启。"""
    from maop.maop_loop_phases import _env_truthy
    for val in ("1", "true", "True", "TRUE", "yes", "YES", "on", "On", "ON"):
        os.environ["TEST_EVO_FLAG"] = val
        assert _env_truthy("TEST_EVO_FLAG") is True, f"val={val!r} should be True"
    # 清理
    os.environ.pop("TEST_EVO_FLAG", None)
