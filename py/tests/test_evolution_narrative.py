"""Tests for EvolutionNarrative — LoopReport → 人类可读演化叙事。

覆盖：
  - 完整报告的 Markdown 输出（含七段阶段、建议、指标、验证、下一步）
  - 完整报告的 JSON 输出结构
  - 空报告（无阶段、无建议）
  - 错误报告（含失败阶段、错误信息）
  - dry-run 模式
  - 自动回滚场景
"""

from __future__ import annotations

import pytest

from maop.core.evolution.evolution_loop_types import (
    EvolutionSuggestion,
    LoopPhase,
    LoopReport,
    PhaseResult,
)
from maop.core.evolution.narrative import EvolutionNarrative

# ── 测试夹具构造 ──────────────────────────────────────────────


def _build_suggestions() -> list[EvolutionSuggestion]:
    return [
        EvolutionSuggestion(
            source="error_ledger",
            category="error",
            mutation_type="error_pattern_rule",
            severity="HIGH",
            description="Recurring pattern 'timeout' (count=5) → auto-promoted rule",
            auto_applicable=True,
            target_type="system",
            target_name="timeout",
            mutation_params={"threshold": 5},
        ),
        EvolutionSuggestion(
            source="error_ledger",
            category="routing",
            mutation_type="change_routing",
            severity="MEDIUM",
            description="Unhealed routing pattern needs config adjustment",
            auto_applicable=False,
            target_type="routing",
            target_name="route_x",
        ),
    ]


def _build_full_report() -> LoopReport:
    """构造包含全部七段阶段的完整 LoopReport。"""
    suggestions = _build_suggestions()
    suggestion_dicts = [s.model_dump() for s in suggestions]
    phases = [
        PhaseResult(
            phase=LoopPhase.OBSERVE,
            success=True,
            duration_s=0.12,
            details={
                "hotspot_count": 3,
                "hotspot_patterns": ["timeout", "route_x"],
                "top_patterns": [
                    {"pattern": "timeout", "count": 5},
                    {"pattern": "route_x", "count": 3},
                ],
            },
        ),
        PhaseResult(
            phase=LoopPhase.HEAL,
            success=True,
            duration_s=0.34,
            details={"attempts": 2, "successes": 1},
        ),
        PhaseResult(
            phase=LoopPhase.SUGGEST,
            success=True,
            duration_s=0.05,
            details={"count": 2, "suggestions": suggestion_dicts},
        ),
        PhaseResult(
            phase=LoopPhase.EVALUATE,
            success=True,
            duration_s=0.08,
            details={
                "approved": [
                    {
                        "suggestion_id": suggestions[0].id,
                        "type": "error_pattern_rule",
                        "severity": "HIGH",
                        "reason": "auto-applicable",
                    }
                ],
                "total": 2,
                "approved_count": 1,
            },
        ),
        PhaseResult(
            phase=LoopPhase.APPLY,
            success=True,
            duration_s=0.15,
            details={"applied": 1, "total": 1},
        ),
        PhaseResult(
            phase=LoopPhase.VALIDATE,
            success=True,
            duration_s=0.22,
            details={"improved": True, "baseline": 3, "current": 1},
        ),
        PhaseResult(
            phase=LoopPhase.CONSOLIDATE,
            success=True,
            duration_s=0.40,
            details={"candidates": 5, "consolidated": 2, "errors": 0},
        ),
    ]
    return LoopReport(
        cycle_id="test_cycle_001",
        started_at=1700000000.0,
        finished_at=1700000001.5,
        total_duration_s=1.5,
        phases=phases,
        errors_observed=3,
        heal_attempts=2,
        heal_successes=1,
        suggestions_generated=2,
        suggestions_applied=1,
        validation_improved=True,
        consolidated=2,
        dry_run=False,
        snapshot_id="snap_abc",
        rolled_back=False,
    )


@pytest.fixture
def narrative() -> EvolutionNarrative:
    return EvolutionNarrative()


@pytest.fixture
def full_report() -> LoopReport:
    return _build_full_report()


# ── Markdown 输出 ─────────────────────────────────────────────


class TestNarrativeMarkdown:
    def test_contains_title_and_cycle_id(self, narrative, full_report):
        md = narrative.to_markdown(full_report)
        assert "# 演化周期叙事 — test_cycle_001" in md

    def test_contains_cycle_summary(self, narrative, full_report):
        md = narrative.to_markdown(full_report)
        assert "## 周期摘要" in md
        assert "**周期 ID**" in md
        assert "**执行模式**" in md
        assert "正式执行" in md
        assert "**总体状态**" in md

    def test_contains_all_seven_phases(self, narrative, full_report):
        md = narrative.to_markdown(full_report)
        assert "## 阶段详情" in md
        for cn in [
            "观察（OBSERVE）",
            "自愈（HEAL）",
            "建议（SUGGEST）",
            "评估（EVALUATE）",
            "应用（APPLY）",
            "验证（VALIDATE）",
            "巩固（CONSOLIDATE）",
        ]:
            assert cn in md, f"缺少阶段：{cn}"

    def test_phase_shows_status_and_duration(self, narrative, full_report):
        md = narrative.to_markdown(full_report)
        assert "✅ 成功" in md
        assert "**耗时**" in md

    def test_contains_suggestions_list(self, narrative, full_report):
        md = narrative.to_markdown(full_report)
        assert "## 建议列表" in md
        assert "[HIGH]" in md
        assert "[MEDIUM]" in md
        assert "auto-promoted rule" in md
        assert "可自动应用" in md
        assert "需人工批准" in md

    def test_contains_metrics_section(self, narrative, full_report):
        md = narrative.to_markdown(full_report)
        assert "## 关键指标变化" in md
        assert "**错误热点数**" in md
        assert "改善" in md
        assert "**自愈成功率**" in md
        assert "**建议应用率**" in md

    def test_contains_apply_result(self, narrative, full_report):
        md = narrative.to_markdown(full_report)
        assert "## 应用结果" in md
        assert "**应用条数**" in md
        assert "1/1" in md

    def test_contains_validation_conclusion(self, narrative, full_report):
        md = narrative.to_markdown(full_report)
        assert "## 验证结论" in md
        assert "✅ 检测到改进" in md
        assert "3 → 1" in md

    def test_contains_next_steps(self, narrative, full_report):
        md = narrative.to_markdown(full_report)
        assert "## 下一步建议" in md
        assert "改进已验证" in md

    def test_contains_snapshot_id(self, narrative, full_report):
        md = narrative.to_markdown(full_report)
        assert "snap_abc" in md


# ── JSON 输出 ─────────────────────────────────────────────────


class TestNarrativeJson:
    def test_top_level_keys(self, narrative, full_report):
        j = narrative.to_json(full_report)
        assert j["cycle_id"] == "test_cycle_001"
        for key in [
            "summary",
            "phases",
            "metrics",
            "suggestions",
            "apply_result",
            "validation",
            "next_steps",
        ]:
            assert key in j, f"缺少顶层键：{key}"

    def test_summary_structure(self, narrative, full_report):
        s = narrative.to_json(full_report)["summary"]
        assert s["cycle_id"] == "test_cycle_001"
        assert s["dry_run"] is False
        assert s["overall_success"] is True
        assert s["errors_observed"] == 3
        assert s["heal_attempts"] == 2
        assert s["heal_successes"] == 1
        assert s["suggestions_generated"] == 2
        assert s["suggestions_applied"] == 1
        assert s["validation_improved"] is True
        assert s["snapshot_id"] == "snap_abc"
        assert s["rolled_back"] is False

    def test_phases_count(self, narrative, full_report):
        phases = narrative.to_json(full_report)["phases"]
        assert len(phases) == 7
        assert phases[0]["phase"] == "observe"
        assert phases[0]["phase_cn"] == "观察（OBSERVE）"
        assert phases[0]["success"] is True
        assert isinstance(phases[0]["findings"], list)

    def test_suggestions_count(self, narrative, full_report):
        suggestions = narrative.to_json(full_report)["suggestions"]
        assert len(suggestions) == 2
        assert suggestions[0]["severity"] == "HIGH"
        assert suggestions[1]["severity"] == "MEDIUM"

    def test_metrics_structure(self, narrative, full_report):
        m = narrative.to_json(full_report)["metrics"]
        assert m["error_hotspots"]["baseline"] == 3
        assert m["error_hotspots"]["current"] == 1
        assert m["error_hotspots"]["improved"] is True
        assert m["heal_rate"] == 0.5
        assert m["apply_rate"] == 0.5

    def test_apply_result_structure(self, narrative, full_report):
        a = narrative.to_json(full_report)["apply_result"]
        assert a["applied"] == 1
        assert a["total"] == 1
        assert a["dry_run"] is False
        assert a["rolled_back"] is False

    def test_validation_structure(self, narrative, full_report):
        v = narrative.to_json(full_report)["validation"]
        assert v["improved"] is True
        assert v["baseline"] == 3
        assert v["current"] == 1

    def test_next_steps_non_empty(self, narrative, full_report):
        steps = narrative.to_json(full_report)["next_steps"]
        assert isinstance(steps, list)
        assert len(steps) >= 1


# ── 空报告 ───────────────────────────────────────────────────


class TestNarrativeEmpty:
    @pytest.fixture
    def empty_report(self) -> LoopReport:
        return LoopReport(cycle_id="empty_cycle")

    def test_empty_markdown_no_crash(self, narrative, empty_report):
        md = narrative.to_markdown(empty_report)
        assert "# 演化周期叙事 — empty_cycle" in md
        assert "_无阶段记录_" in md
        assert "_无验证阶段数据_" in md
        assert "_无建议阶段数据_" in md
        assert "_无应用阶段数据_" in md

    def test_empty_json_structure(self, narrative, empty_report):
        j = narrative.to_json(empty_report)
        assert j["cycle_id"] == "empty_cycle"
        assert j["phases"] == []
        assert j["suggestions"] == []
        assert j["apply_result"] == {}
        assert j["validation"] == {}
        assert j["summary"]["overall_success"] is False

    def test_empty_next_steps(self, narrative, empty_report):
        steps = narrative.to_json(empty_report)["next_steps"]
        assert len(steps) == 1
        assert "未执行任何阶段" in steps[0]


# ── 错误报告 ─────────────────────────────────────────────────


class TestNarrativeErrors:
    @pytest.fixture
    def error_report(self) -> LoopReport:
        return LoopReport(
            cycle_id="err_cycle",
            phases=[
                PhaseResult(
                    phase=LoopPhase.OBSERVE,
                    success=True,
                    duration_s=0.1,
                    details={"hotspot_count": 2, "hotspot_patterns": ["p1"], "top_patterns": []},
                ),
                PhaseResult(
                    phase=LoopPhase.HEAL,
                    success=False,
                    duration_s=0.0,
                    error="SelfHealEngine boom",
                    details={"attempts": 0, "successes": 0},
                ),
                PhaseResult(
                    phase=LoopPhase.VALIDATE,
                    success=True,
                    duration_s=0.1,
                    details={"improved": False, "baseline": 2, "current": 2},
                ),
            ],
            errors_observed=2,
            heal_attempts=0,
            heal_successes=0,
        )

    def test_error_phase_markdown(self, narrative, error_report):
        md = narrative.to_markdown(error_report)
        assert "❌ 失败" in md
        assert "SelfHealEngine boom" in md
        assert "**总体状态**" in md
        assert "含失败阶段" in md

    def test_error_phase_json(self, narrative, error_report):
        j = narrative.to_json(error_report)
        heal_phase = j["phases"][1]
        assert heal_phase["success"] is False
        assert heal_phase["error"] == "SelfHealEngine boom"
        assert j["summary"]["overall_success"] is False

    def test_no_improvement_conclusion(self, narrative, error_report):
        md = narrative.to_markdown(error_report)
        assert "⚠️ 未检测到改进" in md
        assert "2 → 2" in md

    def test_no_improvement_next_steps(self, narrative, error_report):
        steps = narrative.to_json(error_report)["next_steps"]
        assert any("未检测到改进" in s for s in steps)


# ── dry-run 模式 ─────────────────────────────────────────────


class TestNarrativeDryRun:
    @pytest.fixture
    def dry_run_report(self) -> LoopReport:
        suggestions = _build_suggestions()
        return LoopReport(
            cycle_id="dry_run_cycle",
            dry_run=True,
            phases=[
                PhaseResult(
                    phase=LoopPhase.OBSERVE,
                    success=True,
                    duration_s=0.1,
                    details={"hotspot_count": 1, "hotspot_patterns": ["p"], "top_patterns": []},
                ),
                PhaseResult(
                    phase=LoopPhase.SUGGEST,
                    success=True,
                    duration_s=0.0,
                    details={"count": 1, "suggestions": [suggestions[0].model_dump()]},
                ),
                PhaseResult(
                    phase=LoopPhase.APPLY,
                    success=True,
                    duration_s=0.0,
                    details={
                        "applied": 1,
                        "total": 1,
                        "dry_run": True,
                        "proposed": [
                            {
                                "suggestion_id": suggestions[0].id,
                                "type": "error_pattern_rule",
                                "severity": "HIGH",
                                "reason": "preview",
                            }
                        ],
                    },
                ),
                PhaseResult(
                    phase=LoopPhase.VALIDATE,
                    success=True,
                    duration_s=0.0,
                    details={"improved": False, "baseline": 1, "current": 1},
                ),
            ],
            errors_observed=1,
            suggestions_generated=1,
        )

    def test_dry_run_markdown(self, narrative, dry_run_report):
        md = narrative.to_markdown(dry_run_report)
        assert "dry-run（预览）" in md
        assert "dry-run 模式：预览" in md
        assert "仅预览，未实际写入" in md

    def test_dry_run_json(self, narrative, dry_run_report):
        j = narrative.to_json(dry_run_report)
        assert j["summary"]["dry_run"] is True
        assert j["apply_result"]["dry_run"] is True
        assert len(j["apply_result"]["proposed"]) == 1

    def test_dry_run_next_steps(self, narrative, dry_run_report):
        steps = narrative.to_json(dry_run_report)["next_steps"]
        assert any("dry-run 预览" in s for s in steps)


# ── 自动回滚 ─────────────────────────────────────────────────


class TestNarrativeRollback:
    @pytest.fixture
    def rollback_report(self) -> LoopReport:
        return LoopReport(
            cycle_id="rb_cycle",
            dry_run=False,
            snapshot_id="snap_rb",
            rolled_back=True,
            phases=[
                PhaseResult(
                    phase=LoopPhase.OBSERVE,
                    success=True,
                    duration_s=0.1,
                    details={"hotspot_count": 2, "hotspot_patterns": [], "top_patterns": []},
                ),
                PhaseResult(
                    phase=LoopPhase.APPLY,
                    success=True,
                    duration_s=0.1,
                    details={"applied": 2, "total": 2},
                ),
                PhaseResult(
                    phase=LoopPhase.VALIDATE,
                    success=True,
                    duration_s=0.1,
                    details={"improved": False, "baseline": 2, "current": 3},
                ),
            ],
            errors_observed=2,
            suggestions_generated=2,
            suggestions_applied=2,
        )

    def test_rollback_markdown(self, narrative, rollback_report):
        md = narrative.to_markdown(rollback_report)
        assert "**自动回滚**" in md
        assert "snap_rb" in md
        assert "⚠️ 未检测到改进" in md

    def test_rollback_next_steps(self, narrative, rollback_report):
        steps = narrative.to_json(rollback_report)["next_steps"]
        assert any("已自动回滚" in s for s in steps)