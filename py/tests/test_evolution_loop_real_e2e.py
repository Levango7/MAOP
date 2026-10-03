"""T3.1 真实自演化闭环 E2E —— 替掉 AC-05 的 mock 版（v5.2.0 验收 #1/#2）。

**为什么要真跑**：AC-05 的既有测试全部基于 mock——`rollback_cycle` 测的是
mock 出来的 `ChangeTracker.rollback`，<5min SLA 测的是"mock 快照 + 空调用
耗时"，劣化注入的入口只存在于测试代码里（"生产无入口"）。于是 v5.2.0 的两条
验收（E2E 全链路跑通 / 劣化注入自动回滚 <5min）一直是空的。

本文件让闭环真跑：**除 LLM/网络边界外，全部用真实组件**——
  ErrorLedger（真 SQLite 写入 → auto_promote 真阈值判定）
  → SelfHealEngine（真实自愈尝试）
  → _phase_apply（真实 mutation 执行）
  → _phase_validate（真实校验）
  → ChangeTracker（真实文件快照 → 真实回滚落盘）
数据面隔离到 tmp_path（conftest 的 _isolate_data_dir 已设 MAOP_DATA_DIR，
本文件再用 monkeypatch 兜一层显式 root_dir），不碰仓库真实数据。

LLM 边界：闭环的 ErrorLedger 驱动路径本就不调 LLM（建议来自本地规则引擎），
因此无需 mock 模型。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from maop.core.evolution.evolution_loop import EvolutionLoop
from maop.core.evolution.evolution_loop_types import LoopPhase
from maop.core.reliability.change_tracker import ChangeTracker
from maop.core.reliability.error_ledger import ErrorLedger

# AC-05 SLA：劣化注入到自动回滚 <5 分钟（spec-v5.2.0 §16）
SLA_SECONDS = 300


@pytest.fixture
def real_loop(tmp_path, monkeypatch) -> EvolutionLoop:
    """真实 EvolutionLoop，数据面全部落在 tmp_path。

    与 ``evolution_loop_factory`` 的区别：不重置任何单例、不 mock 组件——
    这个 fixture 的存在意义就是"什么都不拦"。

    ``MAOP_DATA_DIR`` 必须**显式指到 root_dir/data**：conftest 的
    ``_isolate_data_dir`` 会把环境变量指向另一处 tmp 目录，导致播种错误用的
    ErrorLedger 与闭环内部阶段各自创建的 ledger 落到两个库（表现为
    applied=0 的假象——第一次实测就踩了这个坑）。
    """
    root = tmp_path / "evolution-root"
    (root / "data").mkdir(parents=True, exist_ok=True)
    (root / "config").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("MAOP_DATA_DIR", str(root / "data"))
    monkeypatch.setenv("MAOP_EVOLUTION_LOOP_ENABLED", "1")
    return EvolutionLoop(root_dir=root)


def _seed_errors(root: Path, pattern: str = "e2e-degradation-probe", times: int = 5) -> None:
    """往真 ErrorLedger 写入重复错误 —— 触发 auto_promote 的真实阈值路径。

    times 必须 ≥ EvolutionLoop 的 _suggest_threshold（默认 3），
    否则 SUGGEST 阶段不产建议，链路会在 EVALUATE 之前自然终止。
    """
    ledger = ErrorLedger(root_dir=str(root))
    for _ in range(times):
        ledger.record(
            error_type="RuntimeError",
            context="e2e degradation probe",
            output="boom",
            expected="ok",
            pattern=pattern,
        )


def test_e2e_full_cycle_runs_real_phases(real_loop, tmp_path):
    """验收 #1：observe→suggest→evaluate→apply→validate→consolidate 真实跑通一轮。

    不 mock 任何 _phase_*：错误从真 ErrorLedger 进来，建议由真规则引擎产出，
    APPLY 执行真 mutation，VALIDATE 真校验，报告落真 SQLite。
    """
    _seed_errors(real_loop._root, times=5)

    report = real_loop.run_cycle(dry_run=True, auto_rollback=True)

    # OBSERVE 真读到了我们播下的错误——注意语义：errors_observed 是**热点数**
    # （get_hotspots 的 distinct pattern 数），不是总次数。同一 pattern 记 5 次
    # 仍是 1 个热点；run_cycle 只在热点为 0 时提前跳过整轮。
    assert report.errors_observed >= 1, (
        f"OBSERVE 未读到 ErrorLedger 数据（errors_observed={report.errors_observed}）"
    )
    # 阶段序列完整（dry_run 下不产生文件快照/回滚）
    phases = [p.phase for p in report.phases]
    assert phases[0] == LoopPhase.OBSERVE
    assert LoopPhase.SUGGEST in phases
    assert LoopPhase.EVALUATE in phases
    # 建议确实被产出（SUGGEST 走 auto_promote 真阈值：recurrence 5 ≥ threshold 3）
    assert report.suggestions_generated >= 1, (
        f"SUGGEST 未产出建议（count={report.suggestions_generated}）——"
        "auto_promote 阈值或 ErrorLedger 写入链路有问题"
    )
    # 报告真落库，可回读
    stored = real_loop.get_report(report.cycle_id)
    assert stored is not None and stored.cycle_id == report.cycle_id


def test_e2e_degradation_injection_auto_rollback_within_sla(real_loop):
    """验收 #2：劣化注入 → VALIDATE 失败 → **真回滚**，全程 <5min（SLA 实测）。

    这条替换 AC-05 的 mock 版 ``test_ac05_sla_within_5min_mocked``：
    那里测的是 mock ChangeTracker 的空调用耗时，不是真实回滚。

    走的是运维演练同一条真实路径：
      inject_degradation_suggestion()（落盘建议队列，T3.1-a 新增）
      → run_cycle：EVALUATE 放行 HIGH 级建议（Balanced 策略）
      → APPLY 真改 config/agents.yaml（adjust_timeout）
      → VALIDATE 判定失败（负 timeout 被拒）
      → ChangeTracker 真快照回滚，agents.yaml 恢复原状

    读代码确认的三个前提（不是猜的）：
      - run_cycle 的回滚条件含 ``applied > 0`` —— 必须有建议真被应用；
      - EVALUATE 按 StrategyEngine 分流，HIGH+ 且 cooldown==0 自动放行；
      - 注入条目显式 ``auto_applicable=True``，否则 ConfigMutator 拒绝。
    """
    # 演练起点：一份真实的 agents.yaml（APPLY 会改它，回滚必须把它恢复）
    agents_yaml = real_loop._root / "config" / "agents.yaml"
    agents_yaml.write_text(
        "agents:\n  maop:\n    model: gpt-4o\n    timeout: 30\n",
        encoding="utf-8",
    )
    original_bytes = agents_yaml.read_bytes()

    # 劣化注入：落盘到建议队列（公开入口，此前只在测试代码里）
    suggestion = real_loop.inject_degradation_suggestion()
    assert suggestion.mutation_params.get("timeout_s") == -1

    # 闭环前提：OBSERVE 必须先看到错误，否则 run_cycle 按设计提前返回整轮
    # （evolution_loop.py:158-163 "No errors observed, skipping cycle"）——
    # 真实演练也发生在故障处置期间，不是空转。
    _seed_errors(real_loop._root, times=5)

    t0 = time.monotonic()
    report = real_loop.run_cycle(dry_run=False, auto_rollback=True)
    elapsed = time.monotonic() - t0

    # SLA：spec 要求劣化 → 回滚 <5 分钟
    assert elapsed < SLA_SECONDS, (
        f"劣化→自动回滚耗时 {elapsed:.1f}s，超 {SLA_SECONDS}s SLA"
    )

    # 真有建议被应用（否则回滚分支按设计不触发）
    assert report.suggestions_applied > 0, (
        f"无建议被应用（applied={report.suggestions_applied}）——"
        "注入的劣化条目没走到 APPLY，演练链路有断点"
    )
    # 非 dry_run 才做快照；有快照才有回滚能力
    assert report.snapshot_id, "dry_run=False 时应生成 pre-APPLY 快照"

    # 劣化场景：VALIDATE 判定未改善 → 必须真回滚
    assert not report.validation_improved, (
        "劣化场景下 VALIDATE 不应判定改善（若判定改善则本用例前提不成立）"
    )
    assert report.rolled_back, "未改善但 rolled_back=False —— auto_rollback 分支未触发"

    # 真回滚的落点：agents.yaml 被 APPLY 改过，回滚后应恢复原状
    assert agents_yaml.read_bytes() == original_bytes, (
        "自动回滚后 agents.yaml 未恢复 —— ChangeTracker 真实回滚链路损坏"
    )


def test_e2e_degradation_entry_is_public_not_test_only(real_loop):
    """回归守卫：劣化注入入口必须是公开 API（不再是"生产无入口"）。

    防退化：若有人把 inject_degradation_suggestion 改回私有/删除，
    `maop evolution inject-degradation` CLI 会断，验收 #2 的演练入口消失
    ——这里钉住公开契约，并验证注入确实落盘（能被下一轮消费）。
    """
    assert hasattr(EvolutionLoop, "build_degradation_suggestion")
    assert hasattr(EvolutionLoop, "inject_degradation_suggestion")

    suggestion = real_loop.inject_degradation_suggestion()
    # 必然失败：VALIDATE 拒绝负 timeout
    assert suggestion.mutation_type == "adjust_timeout"
    assert suggestion.mutation_params["timeout_s"] < 0

    # 真落盘（CLI 的"queued"措辞成立，下一轮 run_cycle 能消费）
    queue_file = real_loop._root / "data" / "evolve-suggestions.json"
    assert queue_file.exists(), "注入的劣化建议未落盘——演练入口名不副实"
    queued = json.loads(queue_file.read_text(encoding="utf-8"))
    assert any(item.get("id") == suggestion.id for item in queued)
    assert any(item.get("auto_applicable") for item in queued)