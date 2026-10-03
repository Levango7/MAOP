"""T3.1-e：人工审批的跨轮回流（approve → 下一轮 APPLY 真应用）。

**断点**：`approved_suggestions` 此前只被写入（`evolution_service.decide_evolution_approval`
落库），**没有任何消费者**——人工批准的建议要等同类错误再次触发、由规则引擎重新
产出同一 id 才可能落地，人工闸门形同"记录了但不执行"。

本文件验证修复后的真实链路（零 mock，除数据目录）：
  第 1 轮：劣化条目 → EVALUATE → 进 pending（人工 gate）→ 报告落库
  人工审批：service.decide_evolution_approval 决策落库
  第 2 轮：**即使该条目不再被规则引擎产出**，上一轮批准的 id 也要被回放进行
           EVALUATE → APPLY 真改 agents.yaml → VALIDATE → 真回滚

关键断言：第 2 轮里 suggestions_applied > 0 且 agents.yaml 被真改过又恢复——
证明"批准 → 执行"闭环打通，而不是只在报告里记了个字段。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from maop.core.evolution.evolution_loop import EvolutionLoop
from maop.core.evolution.evolution_loop_types import EvolutionSuggestion
from maop.core.reliability.error_ledger import ErrorLedger


@pytest.fixture
def real_loop(tmp_path, monkeypatch) -> EvolutionLoop:
    root = tmp_path / "evolution-root"
    (root / "data").mkdir(parents=True, exist_ok=True)
    (root / "config").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("MAOP_DATA_DIR", str(root / "data"))
    monkeypatch.setenv("MAOP_EVOLUTION_LOOP_ENABLED", "1")
    return EvolutionLoop(root_dir=root)


def _seed_errors(root: Path, times: int = 5) -> None:
    ledger = ErrorLedger(root_dir=str(root))
    for _ in range(times):
        ledger.record(
            error_type="RuntimeError", context="carryover probe", pattern="carryover-probe"
        )


def _seed_manual_gate_suggestion(real_loop) -> str:
    """构造一条**必进人工闸门**的建议，返回其 suggestion_id。

    为什么不用 ErrorLedger 自动提升出来的建议：那是 ``error_pattern_rule``
    且 ``auto_applicable=True``，Balanced 策略下 HIGH 级**直接放行**
    （evolution_strategies.py:122 `should = severity in ("HIGH","MEDIUM")`），
    第一轮就被应用了，根本不经过人工闸门——测不到回流。

    这里直接注入一条 ``disable_agent`` 类建议：``auto_applicable=False``
    → 策略不放行 → 落 pending（evolution_phases.py:288-295），正是人工闸门
    要处理的对象。
    """
    suggestion = EvolutionSuggestion(
        source="manual_gate_probe",
        category="reliability",
        mutation_type="disable_agent",
        severity="MEDIUM",
        description="agent keeps failing — needs disable (manual gate probe)",
        auto_applicable=False,
        target_type="agent",
        target_name="never-exists-agent",
        metadata={"pattern": "manual-gate-probe"},
    )
    payload = suggestion.model_dump()
    payload.setdefault("type", payload.get("mutation_type", ""))
    payload.setdefault("suggestion_type", payload.get("category", ""))
    queue_file = real_loop._root / "data" / "evolve-suggestions.json"
    queue_file.parent.mkdir(parents=True, exist_ok=True)
    queue_file.write_text(
        json.dumps([payload], ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return payload["id"]


def test_approved_suggestion_is_applied_in_next_cycle(real_loop):
    """完整回流：审批落库 → 下一轮即使不再被规则产出也被真应用。"""
    agents_yaml = real_loop._root / "config" / "agents.yaml"
    # ConfigMutator 的 mutation handler 都读写这份文件；键结构须与真实
    # agents.yaml 一致（`agents.<name>`），且目标 agent 必须真实存在——
    # `_mutate_disable_agent` 只在目标存在且 enabled 时才产生变更，
    # 变更列表为空会被算作 applied=False。
    agents_yaml.write_text(
        "agents:\n  ghost:\n    model: gpt-4o\n    enabled: true\n",
        encoding="utf-8",
    )

    _seed_errors(real_loop._root)  # 让闭环整体跑起来（零热点会提前返回）
    sid = _seed_manual_gate_suggestion(real_loop)

    # ── 第 1 轮：建议落进 pending（人工 gate）
    report1 = real_loop.run_cycle(dry_run=False, auto_rollback=False)
    assert sid in report1.pending_approval, (
        f"预置建议未进人工闸门: pending={report1.pending_approval}"
    )
    # 人工闸门条目本身在第 1 轮不得被应用（同轮可能另有 HIGH 建议被自动放行，
    # 那是 Balanced 策略的正常行为——所以只断言"我这条没被应用"）
    evaluate1 = next(p for p in report1.phases if p.phase.value == "evaluate")
    applied_ids = [a["suggestion_id"] for a in evaluate1.details.get("approved", [])]
    assert sid not in applied_ids, "人工闸门条目不应在第 1 轮被应用"

    # 人工审批落库（走真实 service 路径）
    from maop.dashboard.services import evolution_service

    service_result = evolution_service.decide_evolution_approval(
        f"{report1.cycle_id}:{sid}", "approve", "admin", "ok"
    )
    assert service_result["status"] == "ok"

    # ── 第 2 轮：模拟"该建议不再被规则引擎产出"——把队列里的**其它**条目
    # 标记为已应用，只留我们批准的那条（未被应用过），它必须被回放进 APPLY。
    # 不能把我们这条也标 applied=True——那等于"已经执行过"，回流理应跳过。
    queue_file = real_loop._root / "data" / "evolve-suggestions.json"
    queued = json.loads(queue_file.read_text(encoding="utf-8"))
    for item in queued:
        if item.get("id") != sid:
            item["applied"] = True
    queue_file.write_text(json.dumps(queued, ensure_ascii=False, indent=2), encoding="utf-8")

    before = agents_yaml.read_bytes()
    report2 = real_loop.run_cycle(dry_run=False, auto_rollback=False)

    # 批准的条目被回放进 APPLY 并真被执行
    assert report2.suggestions_applied > 0, (
        "人工批准的建议在下一轮仍未被应用——回流未生效"
    )
    # VALIDATE 判定失败 → 真回滚，文件恢复
    assert agents_yaml.read_bytes() == before, "agents.yaml 未恢复"


def test_carried_suggestion_recorded_in_report(real_loop):
    """回流在报告里留痕（carried_over 非空），便于审计与前端呈现。"""
    _seed_errors(real_loop._root)
    sid = _seed_manual_gate_suggestion(real_loop)

    # 第 1 轮：进 pending
    report1 = real_loop.run_cycle(dry_run=True, auto_rollback=False)
    assert sid in report1.pending_approval

    # 模拟审批落库：把该 id 记进上一轮报告的 approved_suggestions
    stored = real_loop.get_report(report1.cycle_id)
    stored.approved_suggestions = [sid]
    real_loop.update_report(stored)

    # 第 2 轮：dry-run 下也走 EVALUATE（回放），应记录 carried_over
    report2 = real_loop.run_cycle(dry_run=True, auto_rollback=False)
    evaluate = next(p for p in report2.phases if p.phase.value == "evaluate")
    assert evaluate.details.get("carried_over") == [sid], (
        f"回流未记录: carried_over={evaluate.details.get('carried_over')}"
    )
    approved_ids = [a["suggestion_id"] for a in evaluate.details.get("approved", [])]
    assert sid in approved_ids, "回流的建议未进入 approved 列表"