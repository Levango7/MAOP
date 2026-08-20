"""Evolution narrative — 将 LoopReport 转化为人类可读的演化故事。

纯数据处理模块，不依赖 LLM 或外部服务。输出 Markdown / JSON 双格式，
供 dashboard ``/api/evolution/narrative/{cycle_id}`` 端点消费。

叙事章节：
  1. 周期摘要      — cycle_id / 时间戳 / dry_run / 总体状态
  2. 阶段详情      — 七段闭环每阶段的状态、耗时、关键发现
  3. 关键指标变化  — 错误热点、自愈率、建议应用率的前后对比
  4. 建议列表      — LLM/规则生成的改进建议（类型/严重度/目标/参数）
  5. 应用结果      — 哪些建议被批准/拒绝/应用，是否触发回滚
  6. 验证结论      — VALIDATE 阶段是否检测到改进或回归
  7. 下一步建议    — 基于验证结论的推荐行动
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from maop.core.evolution.evolution_loop_types import (
    LoopPhase,
    LoopReport,
    PhaseResult,
)

# 七段闭环的阶段中文名映射，用于叙事标题。
_PHASE_CN: dict[LoopPhase, str] = {
    LoopPhase.OBSERVE: "观察（OBSERVE）",
    LoopPhase.HEAL: "自愈（HEAL）",
    LoopPhase.SUGGEST: "建议（SUGGEST）",
    LoopPhase.EVALUATE: "评估（EVALUATE）",
    LoopPhase.APPLY: "应用（APPLY）",
    LoopPhase.VALIDATE: "验证（VALIDATE）",
    LoopPhase.CONSOLIDATE: "巩固（CONSOLIDATE）",
}


class EvolutionNarrative:
    """将 LoopReport 转化为人类可读的演化叙事。

    纯数据处理：所有格式化方法仅读取 ``LoopReport`` / ``PhaseResult``
    的字段与 ``details`` 字典，不触发任何 I/O 或外部调用。
    """

    # ── 公开 API ───────────────────────────────────────────────

    def to_markdown(self, report: LoopReport) -> str:
        """生成 Markdown 格式的演化叙事。"""
        lines: list[str] = [f"# 演化周期叙事 — {report.cycle_id}", ""]
        lines.append(self._format_cycle_summary(report))
        lines.append("## 阶段详情")
        lines.append("")
        if not report.phases:
            lines.append("_无阶段记录_。")
            lines.append("")
        else:
            for phase in report.phases:
                lines.append(self._format_phase_summary(phase))
                lines.append("")
        lines.append("## 关键指标变化")
        lines.append("")
        lines.append(self._format_metrics_section(report))
        lines.append("## 建议列表")
        lines.append("")
        lines.append(self._format_suggestions_section(report))
        lines.append("## 应用结果")
        lines.append("")
        lines.append(self._format_apply_section(report))
        lines.append("## 验证结论")
        lines.append("")
        lines.append(self._format_validate_section(report))
        lines.append("## 下一步建议")
        lines.append("")
        lines.append(self._format_next_steps(report))
        return "\n".join(lines)

    def to_json(self, report: LoopReport) -> dict[str, Any]:
        """生成结构化 JSON 叙事。"""
        return {
            "cycle_id": report.cycle_id,
            "summary": self._cycle_summary_dict(report),
            "phases": [self._phase_to_dict(p) for p in report.phases],
            "metrics": self._metrics_dict(report),
            "suggestions": self._suggestions_list(report),
            "apply_result": self._apply_dict(report),
            "validation": self._validation_dict(report),
            "next_steps": self._next_steps(report),
        }

    # ── Markdown 内部格式化 ─────────────────────────────────────

    def _format_cycle_summary(self, report: LoopReport) -> str:
        """格式化周期摘要章节。"""
        ts = self._format_timestamp(report.started_at)
        mode = "dry-run（预览）" if report.dry_run else "正式执行"
        if not report.phases:
            overall = "无阶段"
        elif all(p.success for p in report.phases):
            overall = "成功"
        else:
            overall = "含失败阶段"
        lines: list[str] = [
            "## 周期摘要",
            "",
            f"- **周期 ID**：`{report.cycle_id}`",
            f"- **开始时间**：{ts}",
            f"- **执行模式**：{mode}",
            f"- **总体状态**：{overall}",
            f"- **总耗时**：{report.total_duration_s:.2f}s",
            f"- **错误观察数**：{report.errors_observed}",
            f"- **自愈尝试**：{report.heal_successes}/{report.heal_attempts}",
            f"- **建议生成数**：{report.suggestions_generated}",
            f"- **建议应用数**：{report.suggestions_applied}",
            f"- **验证改进**：{'是' if report.validation_improved else '否'}",
            f"- **巩固条目数**：{report.consolidated}",
        ]
        if report.rolled_back:
            snap = report.snapshot_id or "N/A"
            lines.append(f"- **自动回滚**：已触发（snapshot=`{snap}`）")
        elif report.snapshot_id:
            lines.append(f"- **快照 ID**：`{report.snapshot_id}`")
        lines.append("")
        return "\n".join(lines)

    def _format_phase_summary(self, phase: PhaseResult) -> str:
        """格式化单个阶段摘要。"""
        name = _PHASE_CN.get(phase.phase, phase.phase.value)
        status = "✅ 成功" if phase.success else "❌ 失败"
        lines: list[str] = [
            f"### {name}",
            "",
            f"- **状态**：{status}",
            f"- **耗时**：{phase.duration_s:.3f}s",
        ]
        findings = self._phase_findings(phase)
        if findings:
            lines.append("- **关键发现**：")
            for item in findings:
                lines.append(f"  - {item}")
        if phase.error:
            lines.append(f"- **错误信息**：`{phase.error}`")
        lines.append("")
        return "\n".join(lines)

    def _format_suggestions(self, suggestions: list[Any]) -> str:
        """格式化建议列表。"""
        if not suggestions:
            return "_本周期未生成建议_。"
        lines: list[str] = []
        for i, s in enumerate(suggestions, 1):
            if isinstance(s, dict):
                lines.extend(self._format_one_suggestion(i, s))
            else:
                lines.append(f"{i}. {s!r}")
        return "\n".join(lines)

    def _format_one_suggestion(self, idx: int, s: dict[str, Any]) -> list[str]:
        """格式化单条建议（dict 形式）。"""
        sid = s.get("id", "?")
        category = s.get("category") or s.get("suggestion_type", "")
        mtype = s.get("mutation_type") or s.get("type", "")
        severity = s.get("severity", "")
        desc = s.get("description", "")
        target_type = s.get("target_type", "")
        target_name = s.get("target_name", "")
        auto = "可自动应用" if s.get("auto_applicable") else "需人工批准"
        applied = "已应用" if s.get("applied") else "未应用"
        out: list[str] = [f"{idx}. **`{sid}`** — [{severity}] {desc}"]
        out.append(f"   - 类别：`{category}` / 动作：`{mtype}`")
        if target_type or target_name:
            out.append(f"   - 目标：`{target_type}` / `{target_name}`")
        out.append(f"   - 状态：{auto}，{applied}")
        params = s.get("mutation_params") or {}
        if params:
            out.append(f"   - 参数：`{params}`")
        return out

    def _format_metrics_delta(self, baseline: Any, current: Any) -> str:
        """格式化指标变化。"""
        try:
            b = float(baseline)
            c = float(current)
        except (TypeError, ValueError):
            return f"- **变化**：{baseline} → {current}（无法计算差值）"
        delta = c - b
        if delta < 0:
            arrow = f"↓ {abs(delta):.2f}（改善）"
        elif delta > 0:
            arrow = f"↑ {delta:.2f}（恶化）"
        else:
            arrow = "→ 0（无变化）"
        return f"- **错误热点数**：{b:.0f} → {c:.0f}，{arrow}"

    def _format_metrics_section(self, report: LoopReport) -> str:
        """格式化关键指标变化章节。"""
        lines: list[str] = []
        validate = self._find_phase(report, LoopPhase.VALIDATE)
        if validate is not None:
            base = validate.details.get("baseline")
            cur = validate.details.get("current")
            if base is not None and cur is not None:
                lines.append(self._format_metrics_delta(base, cur))
        else:
            lines.append("_无验证阶段数据_。")
        if report.heal_attempts > 0:
            rate = report.heal_successes / report.heal_attempts * 100
            lines.append(
                f"- **自愈成功率**：{rate:.1f}%"
                f"（{report.heal_successes}/{report.heal_attempts}）"
            )
        if report.suggestions_generated > 0:
            rate = report.suggestions_applied / report.suggestions_generated * 100
            lines.append(
                f"- **建议应用率**：{rate:.1f}%"
                f"（{report.suggestions_applied}/{report.suggestions_generated}）"
            )
        if not lines:
            lines.append("_无可用指标_。")
        lines.append("")
        return "\n".join(lines)

    def _format_suggestions_section(self, report: LoopReport) -> str:
        """格式化建议列表章节。"""
        suggest_phase = self._find_phase(report, LoopPhase.SUGGEST)
        if suggest_phase is None:
            return "_无建议阶段数据_。"
        suggestions = suggest_phase.details.get("suggestions") or []
        return self._format_suggestions(suggestions)

    def _format_apply_section(self, report: LoopReport) -> str:
        """格式化应用结果章节。"""
        apply_phase = self._find_phase(report, LoopPhase.APPLY)
        if apply_phase is None:
            return "_无应用阶段数据_。"
        d = apply_phase.details
        applied = d.get("applied", 0)
        total = d.get("total", 0)
        lines: list[str] = [f"- **应用条数**：{applied}/{total}"]
        if d.get("dry_run"):
            lines.append("- **模式**：dry-run（仅预览，未实际写入）")
            proposed = d.get("proposed") or []
            if proposed:
                lines.append("- **预览变更**：")
                for p in proposed:
                    sid = p.get("suggestion_id", "?")
                    ptype = p.get("type", "")
                    reason = p.get("reason", "")
                    lines.append(f"  - `{sid}` ({ptype}): {reason}")
        if report.rolled_back:
            snap = report.snapshot_id or "N/A"
            lines.append(f"- **自动回滚**：已触发（snapshot=`{snap}`）")
        lines.append("")
        return "\n".join(lines)

    def _format_validate_section(self, report: LoopReport) -> str:
        """格式化验证结论章节。"""
        validate = self._find_phase(report, LoopPhase.VALIDATE)
        if validate is None:
            return "_无验证阶段数据_。"
        d = validate.details
        improved = d.get("improved", False)
        base = d.get("baseline", 0)
        cur = d.get("current", 0)
        if improved:
            lines = [
                f"- **结论**：✅ 检测到改进（错误热点 {base} → {cur}）",
                "- 本次演化有效，建议保留应用结果。",
            ]
        else:
            lines = [
                f"- **结论**：⚠️ 未检测到改进（错误热点 {base} → {cur}）",
                "- 本次演化未带来改善，建议回滚或重新评估。",
            ]
        lines.append("")
        return "\n".join(lines)

    def _format_next_steps(self, report: LoopReport) -> str:
        """格式化下一步建议章节。"""
        steps = self._next_steps(report)
        if not steps:
            return "_无推荐行动_。"
        return "\n".join(f"{i}. {s}" for i, s in enumerate(steps, 1))

    # ── 阶段关键发现提取 ─────────────────────────────────────────

    def _phase_findings(self, phase: PhaseResult) -> list[str]:
        """从 PhaseResult.details 提取阶段关键发现。"""
        d = phase.details
        findings: list[str] = []
        if phase.phase == LoopPhase.OBSERVE:
            count = d.get("hotspot_count", 0)
            findings.append(f"未愈错误热点数：{count}")
            top = d.get("top_patterns") or []
            if top:
                pats = ", ".join(
                    f"`{p.get('pattern', '?')}`×{p.get('count', 0)}" for p in top[:5]
                )
                findings.append(f"Top 模式：{pats}")
        elif phase.phase == LoopPhase.HEAL:
            findings.append(
                f"尝试 {d.get('attempts', 0)} 次，成功 {d.get('successes', 0)} 次"
            )
        elif phase.phase == LoopPhase.SUGGEST:
            findings.append(f"生成建议 {d.get('count', 0)} 条")
        elif phase.phase == LoopPhase.EVALUATE:
            total = d.get("total", 0)
            approved = d.get("approved_count", len(d.get("approved", [])))
            findings.append(f"评估 {total} 条，批准 {approved} 条")
        elif phase.phase == LoopPhase.APPLY:
            applied = d.get("applied", 0)
            total = d.get("total", 0)
            if d.get("dry_run"):
                findings.append(
                    f"dry-run 模式：预览 {applied}/{total} 条变更（未实际写入）"
                )
            else:
                findings.append(f"应用 {applied}/{total} 条变更")
        elif phase.phase == LoopPhase.VALIDATE:
            base = d.get("baseline", 0)
            cur = d.get("current", 0)
            improved = d.get("improved", False)
            arrow = "↓ 改进" if improved else "→ 无改进"
            findings.append(f"错误热点 {base} → {cur}（{arrow}）")
        elif phase.phase == LoopPhase.CONSOLIDATE:
            findings.append(
                f"候选 {d.get('candidates', 0)}，"
                f"巩固 {d.get('consolidated', 0)}，"
                f"错误 {d.get('errors', 0)}"
            )
        return findings

    # ── JSON 内部结构化 ─────────────────────────────────────────

    def _cycle_summary_dict(self, report: LoopReport) -> dict[str, Any]:
        """生成周期摘要字典。"""
        all_success = bool(report.phases) and all(p.success for p in report.phases)
        return {
            "cycle_id": report.cycle_id,
            "started_at": self._format_timestamp(report.started_at),
            "finished_at": self._format_timestamp(report.finished_at),
            "total_duration_s": report.total_duration_s,
            "dry_run": report.dry_run,
            "overall_success": all_success,
            "errors_observed": report.errors_observed,
            "heal_attempts": report.heal_attempts,
            "heal_successes": report.heal_successes,
            "suggestions_generated": report.suggestions_generated,
            "suggestions_applied": report.suggestions_applied,
            "validation_improved": report.validation_improved,
            "consolidated": report.consolidated,
            "rolled_back": report.rolled_back,
            "snapshot_id": report.snapshot_id,
        }

    def _phase_to_dict(self, phase: PhaseResult) -> dict[str, Any]:
        """生成单个阶段的结构化字典。"""
        return {
            "phase": phase.phase.value,
            "phase_cn": _PHASE_CN.get(phase.phase, phase.phase.value),
            "success": phase.success,
            "duration_s": phase.duration_s,
            "findings": self._phase_findings(phase),
            "details": phase.details,
            "error": phase.error,
        }

    def _metrics_dict(self, report: LoopReport) -> dict[str, Any]:
        """生成关键指标字典。"""
        result: dict[str, Any] = {}
        validate = self._find_phase(report, LoopPhase.VALIDATE)
        if validate is not None:
            result["error_hotspots"] = {
                "baseline": validate.details.get("baseline"),
                "current": validate.details.get("current"),
                "improved": validate.details.get("improved", False),
            }
        if report.heal_attempts > 0:
            result["heal_rate"] = round(
                report.heal_successes / report.heal_attempts, 4
            )
        if report.suggestions_generated > 0:
            result["apply_rate"] = round(
                report.suggestions_applied / report.suggestions_generated, 4
            )
        return result

    def _suggestions_list(self, report: LoopReport) -> list[dict[str, Any]]:
        """提取建议列表。"""
        suggest_phase = self._find_phase(report, LoopPhase.SUGGEST)
        if suggest_phase is None:
            return []
        raw = suggest_phase.details.get("suggestions") or []
        return [s for s in raw if isinstance(s, dict)]

    def _apply_dict(self, report: LoopReport) -> dict[str, Any]:
        """生成应用结果字典。"""
        apply_phase = self._find_phase(report, LoopPhase.APPLY)
        if apply_phase is None:
            return {}
        d = apply_phase.details
        return {
            "applied": d.get("applied", 0),
            "total": d.get("total", 0),
            "dry_run": d.get("dry_run", False),
            "proposed": d.get("proposed", []),
            "rolled_back": report.rolled_back,
            "snapshot_id": report.snapshot_id,
        }

    def _validation_dict(self, report: LoopReport) -> dict[str, Any]:
        """生成验证结论字典。"""
        validate = self._find_phase(report, LoopPhase.VALIDATE)
        if validate is None:
            return {}
        return {
            "improved": validate.details.get("improved", False),
            "baseline": validate.details.get("baseline"),
            "current": validate.details.get("current"),
        }

    def _next_steps(self, report: LoopReport) -> list[str]:
        """基于验证结论生成推荐行动。"""
        steps: list[str] = []
        if not report.phases:
            steps.append("本周期未执行任何阶段，建议检查 EvolutionLoop 初始化与触发条件。")
            return steps
        if report.rolled_back:
            steps.append(
                "已自动回滚：建议分析 VALIDATE 阶段失败原因，"
                "调整建议生成策略后重试。"
            )
        elif report.validation_improved:
            steps.append(
                "改进已验证：建议将本次成功模式写入 Semantic Memory 供后续周期复用。"
            )
        else:
            steps.append(
                "未检测到改进：建议检查 APPLY 是否实际生效，或回滚至快照。"
            )
        if report.suggestions_generated > report.suggestions_applied:
            gap = report.suggestions_generated - report.suggestions_applied
            steps.append(
                f"有 {gap} 条建议未应用，"
                "建议人工复核 EVALUATE 阶段的拒绝原因。"
            )
        if report.dry_run:
            steps.append(
                "本次为 dry-run 预览：确认建议合理后，"
                "以 dry_run=False 重新执行以实际应用。"
            )
        if report.errors_observed == 0:
            steps.append("未观察到错误热点：本周期可跳过，下次按计划触发即可。")
        return steps

    # ── 辅助 ─────────────────────────────────────────────────────

    @staticmethod
    def _find_phase(report: LoopReport, phase: LoopPhase) -> PhaseResult | None:
        """在 report.phases 中查找指定阶段的 PhaseResult。"""
        for p in report.phases:
            if p.phase == phase:
                return p
        return None

    @staticmethod
    def _format_timestamp(ts: float) -> str:
        """将 Unix 时间戳格式化为 ISO8601 字符串。"""
        if ts <= 0:
            return "未记录"
        try:
            return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
        except (OSError, ValueError, OverflowError):
            return f"ts={ts}"