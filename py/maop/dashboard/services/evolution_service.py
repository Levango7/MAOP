"""Evolution & evolve-insights service layer.

Encapsulates business logic for the self-evolution closed loop
(``evolution_experiment.py``) and the evolve insights endpoints
(``evolve_insights.py``). Extracted from the router layer so routers
only do parameter parsing + auth + service call + response formatting.

The service is intentionally framework-agnostic: it does not import
FastAPI and can be unit-tested or invoked without an HTTP context.
Shared runtime state (``MAOP_ROOT``) is imported from
``maop.dashboard.routers.state`` so the service operates on the same
singleton the dashboard initialised.

``state.py`` remains the single source of truth for shared constants
and is intentionally left unchanged.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import time
from typing import Any

# Shared runtime state — accessed via the ``state`` module so that tests
# which monkeypatch ``state.MAOP_ROOT`` etc. take effect here too.
# Using ``from state import X`` would bind a snapshot of the reference
# at import time and miss monkeypatch reassignment.
from maop.dashboard.routers import state

logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════════════════════
# Core class factories (lazy singletons keyed by MAOP_ROOT)
# ════════════════════════════════════════════════════════════════════════════

def _perf_loop() -> Any:
    """PerformanceEvolutionLoop 实例（性能演化闭环）。"""
    from maop.core.evolution.evolution_perf_loop import PerformanceEvolutionLoop

    return PerformanceEvolutionLoop(root_dir=str(state.MAOP_ROOT))


def _ab_fw() -> Any:
    """ABTestFramework 实例（AB 实验 + SPRT）。"""
    from maop.core.evolution.ab_test import ABTestFramework

    return ABTestFramework(root_dir=str(state.MAOP_ROOT))


def _deployer() -> Any:
    """AutoDeployer 实例（自动提升 / 回滚）。"""
    from maop.core.evolution.auto_deployer import AutoDeployer

    return AutoDeployer(root_dir=str(state.MAOP_ROOT))


def _evaluator() -> Any:
    """PerformanceEvaluator 实例（无状态）。"""
    from maop.core.evolution.evaluator import PerformanceEvaluator

    return PerformanceEvaluator()


def _evo_loop() -> Any:
    """EvolutionLoop 实例（ErrorLedger 驱动的七段闭环，写入 evolution_cycles 表）。"""
    from maop.core.evolution.evolution_loop import EvolutionLoop

    return EvolutionLoop(root_dir=str(state.MAOP_ROOT))


def _evolve_engine() -> Any:
    """EvolveEngine 实例（旧版演化引擎）。"""
    from maop.evolve import EvolveEngine

    return EvolveEngine(root_dir=str(state.MAOP_ROOT))


# ════════════════════════════════════════════════════════════════════════════
# evolution_experiment: 评估 / 建议
# ════════════════════════════════════════════════════════════════════════════

def evaluate_traces(traces: list[Any], baseline: list[Any] | None) -> dict[str, Any]:
    """评估一组 trace 的性能指标。

    返回 ``{"status": "ok", "metrics": {...}}``，当提供 ``baseline``
    时附加 ``delta`` 字段。
    """
    evaluator = _evaluator()
    metrics = evaluator.evaluate(traces)
    result: dict[str, Any] = {"status": "ok", "metrics": metrics.to_dict()}
    if baseline is not None:
        delta = evaluator.compare(baseline, traces)
        result["delta"] = delta.to_dict()
    return result


def suggest_improvements(
    metrics: dict[str, Any],
    agent_name: str,
    enable_llm: bool,
) -> dict[str, Any]:
    """基于指标生成候选改进建议。"""
    from maop.core.evolution.evaluator import PerformanceMetrics
    from maop.core.evolution.suggester import ImprovementSuggester, SuggestionContext

    pm = PerformanceMetrics.model_validate(metrics)
    suggester = ImprovementSuggester(root_dir=str(state.MAOP_ROOT), enable_llm=enable_llm)
    ctx = SuggestionContext(agent_name=agent_name)
    suggestions = suggester.suggest_sync(pm, ctx)
    return {
        "status": "ok",
        "suggestions": [s.model_dump() for s in suggestions],
        "count": len(suggestions),
    }


# ════════════════════════════════════════════════════════════════════════════
# evolution_experiment: AB 实验 + SPRT
# ════════════════════════════════════════════════════════════════════════════

def create_ab_experiment(
    name: str,
    variants: list[str],
    sprt: dict[str, Any] | None,
) -> dict[str, Any]:
    """创建 AB 实验。"""
    from maop.core.evolution.ab_test import SPRTConfig

    sprt_cfg = None
    if sprt:
        sprt_cfg = SPRTConfig.model_validate(sprt)
    fw = _ab_fw()
    config = fw.create_experiment(
        name=name,
        variants=variants,
        sprt_config=sprt_cfg,
    )
    return {"status": "ok", "experiment": config.model_dump()}


def record_ab_sample(
    experiment: str,
    variant: str,
    entity_id: str,
    success: bool,
) -> dict[str, Any]:
    """记录一个 AB 样本并返回当前 SPRT 状态。"""
    fw = _ab_fw()
    ab_state = fw.record(experiment, variant, entity_id, success)
    return {"status": "ok", "sprt": ab_state.model_dump()}


def evaluate_ab_experiment(experiment: str) -> dict[str, Any]:
    """评估指定 AB 实验的 SPRT 状态。"""
    fw = _ab_fw()
    result = fw.evaluate_sprt(experiment)
    return {"status": "ok", "result": result.model_dump()}


def list_ab_experiments() -> dict[str, Any]:
    """列出所有 AB 实验。"""
    fw = _ab_fw()
    return {"status": "ok", "experiments": fw.list_experiments()}


# ════════════════════════════════════════════════════════════════════════════
# evolution_experiment: 部署
# ════════════════════════════════════════════════════════════════════════════

def promote_deployment(
    experiment: str,
    winner: str,
    config: dict[str, Any] | None,
) -> dict[str, Any]:
    """提升部署。"""
    deployer = _deployer()
    result = deployer.promote(experiment, winner, config=config)
    return {"status": "ok", "result": result.model_dump()}


def rollback_deployment(
    experiment: str,
    snapshot_id: str,
) -> dict[str, Any]:
    """回滚部署。"""
    deployer = _deployer()
    result = deployer.rollback(experiment, snapshot_id=snapshot_id)
    return {"status": "ok", "result": result.model_dump()}


def get_deploy_history(experiment: str, limit: int = 100) -> dict[str, Any]:
    """获取部署历史。"""
    deployer = _deployer()
    history = deployer.get_history(experiment=experiment, limit=limit)
    return {"status": "ok", "history": [h.model_dump() for h in history]}


# ════════════════════════════════════════════════════════════════════════════
# evolution_experiment: 性能演化循环
# ════════════════════════════════════════════════════════════════════════════

def run_evolution_cycle(
    baseline_traces: list[Any],
    candidate_traces: list[Any],
    experiment: str,
    agent_name: str,
    candidate_config: dict[str, Any] | None,
) -> dict[str, Any]:
    """触发一轮性能演化循环。"""
    loop = _perf_loop()
    report = loop.run_evolution_cycle(
        baseline_traces,
        candidate_traces,
        experiment=experiment,
        agent_name=agent_name,
        candidate_config=candidate_config,
    )
    return {"status": "ok", "report": report.model_dump()}


def get_cycle_history(experiment: str, limit: int) -> dict[str, Any]:
    """获取性能演化循环历史。"""
    loop = _perf_loop()
    cycles = loop.get_cycle_history(experiment=experiment, limit=limit)
    return {"status": "ok", "cycles": [c.model_dump() for c in cycles]}


def get_pending_approvals() -> dict[str, Any]:
    """人工 gate：返回待批准的提升列表。"""
    loop = _perf_loop()
    pending = loop.get_pending_approvals()
    return {"status": "ok", "pending": [c.model_dump() for c in pending]}


def approve_and_promote(
    experiment: str,
    candidate_config: dict[str, Any] | None,
) -> dict[str, Any]:
    """人工 gate：批准指定实验的提升。"""
    loop = _perf_loop()
    result = loop.approve_and_promote(experiment, candidate_config=candidate_config)
    return {"status": "ok", "result": result}


# ════════════════════════════════════════════════════════════════════════════
# evolution_experiment: Skill 编辑器
# ════════════════════════════════════════════════════════════════════════════

def list_skills() -> dict[str, Any]:
    """列出已保存的 Skill 原子（从 SkillVersionManager 加载）。"""
    from maop.core.evolution.skill_version import SkillVersionManager

    try:
        mgr = SkillVersionManager(root_dir=str(state.MAOP_ROOT))
        skills_meta = mgr.list_skills()
        skills = [m.model_dump() for m in skills_meta]
        return {"skills": skills}
    except Exception:
        logger.debug("[evo/skills] list failed", exc_info=True)
        return {"skills": []}


def save_composite_skill(
    name: str,
    version: str,
    description: str,
    source: str,
    tags: list[str],
    steps: list[dict[str, Any]],
    content: str | None,
) -> dict[str, Any]:
    """保存 composite Skill（接入 SkillVersionManager）。

    Raises ``ValueError`` if ``name`` is empty.
    Raises the underlying exception on save failure (router maps to 500).
    """
    from maop.core.evolution.skill_version import SkillMeta, SkillStep, SkillVersionManager

    clean_name = (name or "").strip()
    if not clean_name:
        raise ValueError("skill name is required")

    steps_objs = [SkillStep(**(s if isinstance(s, dict) else {})) for s in (steps or [])]

    meta = SkillMeta(
        name=clean_name,
        version=version,
        description=description,
        source=source,
        tags=tags,
        steps=steps_objs,
    )

    final_content = (
        content if content is not None
        else json.dumps(meta.model_dump(), ensure_ascii=False)
    )

    mgr = SkillVersionManager(root_dir=str(state.MAOP_ROOT))
    mgr.save_skill(clean_name, content=final_content, metadata={
        "source": "dashboard",
        "steps": steps or [],
        "description": meta.description,
        "tags": meta.tags,
    })
    return {"status": "ok", "saved": True, "name": clean_name, "version": meta.version}


# ════════════════════════════════════════════════════════════════════════════
# evolution_experiment: 演化案例叙事
# ════════════════════════════════════════════════════════════════════════════

def _find_cycle_report(loop: Any, cycle_id: str) -> Any:
    """从演化历史中查找指定 cycle_id 的 LoopReport，未命中返回 None。"""
    for report in loop.get_cycle_history(limit=1000):
        if report.cycle_id == cycle_id:
            return report
    return None


def get_evolution_narrative(cycle_id: str, fmt: str) -> dict[str, Any]:
    """获取指定演化周期的人类可读叙事。

    Raises ``KeyError`` if the cycle is not found (router maps to 404).
    """
    from maop.core.evolution.narrative import EvolutionNarrative

    loop = _evo_loop()
    report = _find_cycle_report(loop, cycle_id)
    if report is None:
        raise KeyError(cycle_id)

    narrative = EvolutionNarrative()
    if fmt == "json":
        return {
            "status": "ok",
            "cycle_id": cycle_id,
            "format": "json",
            "narrative": narrative.to_json(report),
        }
    return {
        "status": "ok",
        "cycle_id": cycle_id,
        "format": "markdown",
        "markdown": narrative.to_markdown(report),
    }


# ════════════════════════════════════════════════════════════════════════════
# evolve_insights: status / metrics / analyze / suggestions / report
# ════════════════════════════════════════════════════════════════════════════

def _normalize_model(data: Any) -> Any:
    """将 Pydantic model / .dict() 对象规范化为 dict。"""
    if hasattr(data, "model_dump"):
        return data.model_dump()
    if hasattr(data, "dict") and not isinstance(data, dict):
        return data.dict()
    return data


def get_evolve_status() -> dict[str, Any]:
    """EvolveEngine 状态查询。"""
    eng = _evolve_engine()
    data = _normalize_model(eng.status())
    return {"status": "ok", "data": data}


def get_evolve_metrics() -> dict[str, Any]:
    """演化指标聚合（时间序列 / 热力图 / 世系）。

    从 evolution_cycles 表聚合真实数据；优先 EvolutionLoop，空回退 EvolveEngine。
    """
    try:
        loop = _evo_loop()
        history = loop.get_cycle_history(limit=50)
    except Exception:
        logger.debug("[evolve/metrics] EvolutionLoop init failed, trying EvolveEngine", exc_info=True)
        return _evolve_metrics_fallback()

    if not history:
        return {"status": "ok", "timeseries": [], "heatmap": [], "lineage": []}

    timeseries = [
        {
            "timestamp": h.started_at,
            "errors": h.errors_observed,
            "heals": h.heal_successes,
            "suggestions": h.suggestions_generated,
            "duration_s": h.total_duration_s,
        }
        for h in history
    ]

    lineage = [
        {
            "cycle_id": h.cycle_id,
            "started_at": h.started_at,
            "errors_observed": h.errors_observed,
            "heal_successes": h.heal_successes,
            "validation_improved": h.validation_improved,
        }
        for h in history
    ]

    heatmap: list[dict[str, Any]] = []
    agent_counts: dict[str, dict[str, Any]] = {}

    for h in history:
        agent = ""
        with contextlib.suppress(Exception):
            rpt = json.loads(h.model_dump_json()) if h else {}
            agent = rpt.get("agent", "") or rpt.get("agent_name", "") or ""
        if not agent:
            continue
        if agent not in agent_counts:
            agent_counts[agent] = {"cycles": 0, "errors": 0, "improvement_rate": 0.0}
        agent_counts[agent]["cycles"] += 1
        agent_counts[agent]["errors"] += h.errors_observed
        if agent_counts[agent]["cycles"] > 0:
            agent_counts[agent]["improvement_rate"] = round(
                1.0 - (agent_counts[agent]["errors"] / max(1, agent_counts[agent]["cycles"] * 10)), 3
            )

    heatmap = [{"agent": k, **v} for k, v in agent_counts.items()]

    return {"status": "ok", "timeseries": timeseries, "heatmap": heatmap, "lineage": lineage}


def _evolve_metrics_fallback() -> dict[str, Any]:
    """EvolutionLoop 不可用时的 EvolveEngine 回退路径。"""
    try:
        eng = _evolve_engine()
        raw = eng.status()
        timeseries: list[Any] = []
        heatmap: list[Any] = []
        lineage: list[Any] = []
        if isinstance(raw, dict):
            timeseries = raw.get("timeseries", [])
            heatmap = raw.get("heatmap", [])
            lineage = raw.get("lineage", [])
        elif hasattr(raw, "model_dump"):
            d = raw.model_dump()
            timeseries = d.get("timeseries", [])
            heatmap = d.get("heatmap", [])
            lineage = d.get("lineage", [])
        return {"status": "ok", "timeseries": timeseries, "heatmap": heatmap, "lineage": lineage}
    except Exception:
        return {"status": "ok", "timeseries": [], "heatmap": [], "lineage": []}


def analyze_evolve(
    action: str,
    suggestion_id: str,
    hours: int,
) -> dict[str, Any]:
    """EvolveEngine 分析 / 应用 / 重置 / auto_evolve。"""
    eng = _evolve_engine()
    if action == "apply":
        try:
            result: Any = eng.apply(suggestion_id) if hasattr(eng, "apply") else eng.analyze()
        except TypeError:
            result = eng.apply() if hasattr(eng, "apply") else eng.analyze()
        result = _normalize_model(result)
        return {"status": "ok", "action": "apply", "suggestions": result}
    elif action == "reset":
        if hasattr(eng, "_suggestions_file"):
            sf = eng._suggestions_file
            if sf and sf.exists():
                sf.unlink()
        return {"status": "ok", "action": "reset", "msg": "Suggestions cleared"}
    elif action == "auto_evolve":
        try:
            result = eng.auto_evolve(hours=hours) if hasattr(eng, "auto_evolve") else eng.analyze()
        except Exception as exc:
            logger.warning("auto_evolve failed: %s", exc, exc_info=True)
            result = {"error": "auto_evolve failed, please try again later"}
        return {"status": "ok", "action": "auto_evolve", "result": result}
    else:
        analyze_result: Any = eng.analyze()
        analyze_result = _normalize_model(analyze_result)
        return {"status": "ok", "action": "analyze", "suggestions": analyze_result}


def get_evolve_suggestions() -> dict[str, Any]:
    """返回 EvolveEngine 建议聚合。"""
    eng = _evolve_engine()
    s: Any = eng.suggest() if hasattr(eng, "suggest") else {}
    s = _normalize_model(s)
    if not isinstance(s, dict):
        s = {"action": "suggest", "stats": {"by_agent": []}}
    if "stats" not in s:
        s = {"action": "suggest", "stats": s if isinstance(s, dict) else {"by_agent": []}}
    if "by_agent" not in s.get("stats", {}):
        try:
            eng_status = eng.status()
            if hasattr(eng_status, "get"):
                s["stats"]["by_agent"] = eng_status.get("stats", {}).get("by_agent", [])
            if hasattr(eng_status, "model_dump"):
                s["stats"]["by_agent"] = eng_status.model_dump().get("stats", {}).get("by_agent", [])
        except Exception:
            s["stats"]["by_agent"] = []
    return {"status": "ok", "suggestions": s}


async def get_evolve_report() -> dict[str, Any]:
    """返回 agent 性能报告（基于 DataProxy bridge）。"""
    from maop.dashboard.routers.state import get_bridge

    b = get_bridge()
    agents = await b.agent_stats()
    agent_list = agents.get("agents", []) if isinstance(agents, dict) else (agents if isinstance(agents, list) else [])
    perf = []
    for a in agent_list:
        if not isinstance(a, dict):
            continue
        sr = a.get("success_rate", 0) or 0
        total = a.get("total_delegations", a.get("total", 0)) or 0
        success = a.get("successes", a.get("success", 0)) or 0
        fail = total - success
        perf.append({
            "agent": a.get("name", a.get("agent", "")),
            "success_rate": sr * 100 if sr <= 1 else sr,
            "avg_latency_ms": a.get("avg_latency_ms", a.get("avg_duration_ms", 0)) or 0,
            "fail_count": fail,
            "total_count": total,
            "tags": ",".join(a.get("tags", [])) if isinstance(a.get("tags"), list) else "",
        })
    return {"status": "ok", "performance": perf}


# ════════════════════════════════════════════════════════════════════════════
# evolve_insights: strategies / history / suggestions-list / apply-suggestion
# ════════════════════════════════════════════════════════════════════════════

def list_evolve_strategies() -> dict[str, Any]:
    """返回可用进化策略列表。"""
    from maop.core.evolution.evolution_strategies import STRATEGY_MAP

    strategies = [
        {"name": name, "description": cls.__doc__ or cls.__name__}
        for name, cls in STRATEGY_MAP.items()
    ]
    return {"status": "ok", "strategies": strategies}


def get_evolve_history() -> dict[str, Any]:
    """返回进化循环历史。"""
    try:
        loop = _evo_loop()
        history = loop.get_cycle_history(limit=20)
        stats = loop.get_stats()
        return {
            "status": "ok",
            "history": [h.model_dump() for h in history],
            "stats": stats,
        }
    except Exception:
        return {"status": "ok", "history": [], "stats": {}}


def list_evolve_suggestions() -> dict[str, Any]:
    """返回所有进化建议列表 (含已应用状态)。"""
    eng = _evolve_engine()
    suggestions = eng._load_suggestions()
    return {
        "status": "ok",
        "suggestions": [s.model_dump() for s in suggestions],
        "total": len(suggestions),
        "applied": sum(1 for s in suggestions if s.applied),
    }


def apply_evolve_suggestion(suggestion_id: str) -> dict[str, Any]:
    """手动应用指定进化建议。"""
    eng = _evolve_engine()
    result: Any = eng.apply(suggestion_id)
    result = _normalize_model(result)
    return {"status": "ok", "result": result}


# ════════════════════════════════════════════════════════════════════════════
# evolve_insights: F2-01 Evolution Loop AC-07 Dashboard Endpoints
# ════════════════════════════════════════════════════════════════════════════

def get_evolution_loop_status() -> dict[str, Any]:
    """闭环状态机当前状态 + 最近 cycle 摘要 (AC-07).

    Raises on failure so the router can map to HTTP 500.
    """
    try:
        loop = _evo_loop()
        history = loop.get_cycle_history(limit=5)
        stats = loop.get_stats()
        evolution_enabled = os.getenv("MAOP_EVOLUTION_LOOP_ENABLED", "").strip().lower() in ("1", "true", "yes", "on")

        # 确定当前状态机状态
        loop_state = "idle"
        if history:
            latest = history[0]
            if latest.rolled_back:
                loop_state = "rolled_back"
            elif latest.validation_improved:
                loop_state = "validated"
            elif latest.pending_approval:
                loop_state = "pending_approval"
            elif latest.suggestions_applied > 0:
                loop_state = "applying"
            else:
                loop_state = "evaluating"

        return {
            "status": "ok",
            "state": loop_state,
            "evolution_loop_enabled": evolution_enabled,
            "recent_cycles": [h.model_dump() for h in history],
            "stats": stats,
            "pending_approval_count": len(history[0].pending_approval) if history else 0,
            "pending_approval_ids": history[0].pending_approval if history else [],
        }
    except Exception as exc:
        logger.warning("Evolution loop status failed: %s", exc, exc_info=True)
        raise


async def trigger_evolution_loop(dry_run: bool) -> dict[str, Any]:
    """手动触发一轮闭环（支持 dry_run）（AC-07）.

    Raises on failure so the router can map to HTTP 500.
    """
    try:
        loop = _evo_loop()
        report = await loop.run_cycle(dry_run=dry_run, auto_rollback=True)
        return {"status": "ok", "report": report.model_dump()}
    except Exception as exc:
        logger.warning("Evolution loop trigger failed: %s", exc, exc_info=True)
        raise


def list_evolution_approvals() -> dict[str, Any]:
    """待审批改进列表 (AC-07).

    Raises on failure so the router can map to HTTP 500.
    """
    try:
        loop = _evo_loop()
        history = loop.get_cycle_history(limit=10)

        approvals = []
        for h in history:
            if h.pending_approval:
                approvals.append({
                    "cycle_id": h.cycle_id,
                    "started_at": h.started_at,
                    "pending_approval_ids": h.pending_approval,
                    "approval_state": h.approval_state,
                    "errors_observed": h.errors_observed,
                    "suggestions_generated": h.suggestions_generated,
                    "validation_improved": h.validation_improved,
                })

        return {"status": "ok", "approvals": approvals, "total": len(approvals)}
    except Exception as exc:
        logger.warning("Evolution approvals failed: %s", exc, exc_info=True)
        raise


def decide_evolution_approval(
    approval_id: str,
    decision: str,
    approved_by: str,
    reason: str,
) -> dict[str, Any]:
    """审批通过 / 拒绝 (AC-07).

    ``approval_id`` 格式：``cycle_id:suggestion_id``。
    Raises ``ValueError`` for invalid ``approval_id`` format or unknown
    ``decision`` value (router maps to 400).
    Raises ``KeyError`` if the cycle is not found (router maps to 404).
    Raises the underlying exception on persistence failure (router maps to 500).
    """
    try:
        cycle_id, suggestion_id = approval_id.split(":", 1)
    except ValueError as exc:
        raise ValueError("Invalid approval_id format (cycle_id:suggestion_id)") from exc

    normalized_decision = decision.lower()
    if normalized_decision not in ("approve", "reject"):
        raise ValueError("decision must be 'approve' or 'reject'")

    loop = _evo_loop()
    # M5: _load_report 是 EvolutionLoop 内部协调接口，非公开 API；
    # 此处通过内部方法读取循环报告以支持审批决策持久化。
    report = loop._load_report(cycle_id)
    if not report:
        raise KeyError(cycle_id)

    if normalized_decision == "approve":
        # 将 suggestion_id 从 pending_approval 积到 approved 列表
        # 这里简化：更新 approval_state
        pass  # 实际需更新 DB

    report.approval_state = "approved" if normalized_decision == "approve" else "rejected"
    report.approved_by = approved_by
    report.approved_at = time.time()
    # M5: _save_report 是 EvolutionLoop 内部协调接口，非公开 API；
    # 此处通过内部方法持久化审批后的循环报告。
    loop._save_report(report)

    return {
        "status": "ok",
        "decision": normalized_decision,
        "approval_id": approval_id,
        "cycle_id": cycle_id,
    }


def get_evolution_ab_results(cycle_id: str) -> dict[str, Any]:
    """A/B 结果与显著性检验数据 (AC-06/AC-07).

    Raises on failure so the router can map to HTTP 500.
    """
    from maop.core.evolution.ab_test import ABTestManager

    try:
        loop = _evo_loop()
        ab_manager = ABTestManager(root_dir=str(state.MAOP_ROOT))

        # 查找该 cycle 的 A/B 实验
        # 简化：查找 experiment 名称为 "evo-{cycle_id}" 的实验
        exp_name = f"evo-{cycle_id}"
        try:
            result = ab_manager.evaluate(exp_name)
            ab_result = {
                "experiment": exp_name,
                "p_value": result.p_value,
                "significant": result.p_value < 0.05,
                "control": {"success_rate": result.control_rate, "samples": result.control_count},
                "treatment": {"success_rate": result.treatment_rate, "samples": result.treatment_count},
            }
        except Exception:
            ab_result = None

        return {
            "status": "ok",
            "cycle_id": cycle_id,
            "ab_result": ab_result,
        }
    except Exception as exc:
        logger.warning("Evolution A/B results failed: %s", exc, exc_info=True)
        raise


def rollback_evolution_loop(cycle_id: str, snapshot_id: str) -> dict[str, Any]:
    """手动触发回滚 (AC-05).

    Raises ``ValueError`` if ``cycle_id`` is empty (router maps to 400).
    Raises the underlying exception on failure (router maps to 500).
    """
    if not cycle_id:
        raise ValueError("cycle_id required")

    loop = _evo_loop()
    restored = loop.rollback_cycle(cycle_id, snapshot_id=snapshot_id)
    return {"status": "ok", "restored_files": restored, "cycle_id": cycle_id}