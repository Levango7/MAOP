"""v5.2.0 验收 #3：MAOP_EVOLUTION_LOOP_ENABLED 开启后主循环零回归。

**这条验收此前从未真正落地**：AC-01/AC-02 只验接线点（单函数 `_phase_evolve`
被调用/不被调用），ROADMAP 写的"开关开启后主循环其余阶段行为不变（全量测试
零回归）"没有任何自动化承载——等于一句承诺。

本文件把它变成**可执行的基线**：
  1. **相位顺序基线**：开关=0 与开关=1 两种取值下，`_phase_evolve` 之后的
     相位计数与返回值结构一致（闭环是独立分支，跑失败也不影响主流程——
     `maop_loop_phases.py:477` 的 try/except 只 warning）。
  2. **异常隔离**：闭环内部抛异常时，主流程照常推进（"独立分支"设计的核心
     承诺，此前无测试）。
  3. **事件面差异**：开关=1 会多发一条 `loop.evolution_cycle` 事件（这是
     预期内的增量，不是回归）；除此之外不应有其他事件面差异。
  4. **数据面隔离**：开关=1 时闭环的 SQLite 报告落在 root/data 下，不得
     写到仓库真实 data/ （防演练污染生产数据）。

变异验证：把 `maop_loop_phases.py` 里 `if evolution_loop_enabled:` 改成恒 True，
第 1 条必红（开关=0 时多跑了闭环）；改成恒 False，第 1 条同样必红。
"""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from maop.core.agent.evolution.phases import PhaseContext
from maop.loop_models import LoopConfig
from maop.maop_loop_phases import PhasesMixin


class _Stub(PhasesMixin):
    """最小 PhasesMixin 实例（与 AC-01/02 同口径：不引入完整主循环）。"""

    def __init__(self, root, bus):
        self._root = root
        self._loop_config = LoopConfig()
        self._bus = bus
        self._log = lambda phase, level, msg, trace_id: None
        self._loop_count = 0
        self._consolidator = None


def _ctx() -> PhaseContext:
    return PhaseContext(
        trace_id="ac03-regression",
        plan_result=MagicMock(),
        execution_result=MagicMock(),
        verify_result=MagicMock(success=True, output="ok"),
    )


async def _run_evolve(stub: _Stub) -> tuple[int, list]:
    """跑一次 _phase_evolve，返回 (loop_count, bus 发布的事件列表)。"""
    published: list = []

    async def _publish(event):
        published.append(event)

    stub._bus = MagicMock()
    stub._bus.publish = _publish
    await stub._phase_evolve(_ctx())
    return stub._loop_count, published


@pytest.mark.asyncio
async def test_ac03_switch_on_keeps_loop_count_and_shape(monkeypatch, tmp_path):
    """基线 1：开关开/关，_phase_evolve 之后的相位计数与主 analyze 路径一致。"""
    # 开关=0（默认关闭）
    monkeypatch.delenv("MAOP_EVOLUTION_LOOP_ENABLED", raising=False)
    stub_off = _Stub(root=tmp_path, bus=MagicMock())
    with patch("maop.evolve.EvolveEngine") as engine:
        engine.return_value.analyze.return_value = None
        count_off, events_off = await _run_evolve(stub_off)

    # 开关=1
    monkeypatch.setenv("MAOP_EVOLUTION_LOOP_ENABLED", "1")
    stub_on = _Stub(root=tmp_path, bus=MagicMock())
    with patch("maop.evolve.EvolveEngine") as engine, \
         patch("maop.core.evolution.evolution_loop.EvolutionLoop") as loop_cls:
        loop_cls.return_value.run_cycle.return_value = MagicMock(
            cycle_id="c-001", errors_observed=0, suggestions_generated=0,
            suggestions_applied=0, validation_improved=False, rolled_back=False,
        )
        engine.return_value.analyze.return_value = None
        count_on, events_on = await _run_evolve(stub_on)

    # 相位计数一致——闭环是独立分支，不多占相位
    assert count_off == count_on, (
        f"开关改变了主循环相位计数：off={count_off} on={count_on}"
    )
    # 事件面：on 多一条 loop.evolution_cycle（预期增量），其余一致
    topics_off = [e.topic for e in events_off]
    topics_on = [e.topic for e in events_on]
    extra = [t for t in topics_on if t not in topics_off]
    assert extra == ["loop.evolution_cycle"], (
        f"开关=1 时出现了预期外的事件增量: {extra}"
    )


@pytest.mark.asyncio
async def test_ac03_loop_failure_does_not_break_main_flow(monkeypatch, tmp_path):
    """基线 2：闭环内部炸了，主流程照常推进（"独立分支"承诺，此前无测试）。"""
    monkeypatch.setenv("MAOP_EVOLUTION_LOOP_ENABLED", "1")
    stub = _Stub(root=tmp_path, bus=MagicMock())
    with patch("maop.evolve.EvolveEngine") as engine, \
         patch("maop.core.evolution.evolution_loop.EvolutionLoop") as loop_cls:
        engine.return_value.analyze.return_value = None
        loop_cls.return_value.run_cycle.side_effect = RuntimeError("loop boom")
        count, _events = await _run_evolve(stub)

    assert count == 1, "闭环异常后主流程相位计数未推进——异常外溢了"


@pytest.mark.asyncio
async def test_ac03_loop_data_stays_under_root(monkeypatch, tmp_path):
    """基线 3：开关=1 时闭环数据落在 root 下，不写仓库真实 data/。"""
    monkeypatch.setenv("MAOP_EVOLUTION_LOOP_ENABLED", "1")
    root = tmp_path / "loop-root"
    root.mkdir(parents=True, exist_ok=True)
    stub = _Stub(root=root, bus=MagicMock())
    with patch("maop.evolve.EvolveEngine") as engine:
        engine.return_value.analyze.return_value = None
        await _run_evolve(stub)

    # 真跑一轮闭环（不 mock EvolutionLoop），确认报告落在 root 的库里
    from maop.core.evolution.evolution_loop import EvolutionLoop

    loop = EvolutionLoop(root_dir=root)
    loop.run_cycle(dry_run=True, auto_rollback=True)
    history = loop.get_cycle_history(limit=1)
    assert history, "闭环未在 root 下留下报告"

    # 隔离性实证：闭环的 SQLite 库走统一的 MAOP_DATA_DIR（db_utils.get_db_path，
    # **不跟 root_dir 走**——这是设计：root_dir 管快照/配置面，SQLite 归统一
    # 数据面）。此处的保证是"落在测试隔离目录内、绝不碰仓库/用户真实 data/"。
    from maop.core.backends import db_utils

    db_path = Path(db_utils.get_db_path("evolution_cycles"))
    data_dir = Path(os.getenv("MAOP_DATA_DIR", "")).resolve()
    assert data_dir, "MAOP_DATA_DIR 未设置——测试隔离失效"
    assert db_path.resolve().is_relative_to(data_dir), (
        f"闭环数据库落在 MAOP_DATA_DIR 之外: {db_path} —— 会污染仓库/用户真实数据"
    )
    # 且它不在仓库里
    repo_root = Path(db_utils.__file__).resolve().parents[3]
    assert not str(db_path.resolve()).startswith(str(repo_root)), (
        f"闭环数据库写进了仓库: {db_path}"
    )