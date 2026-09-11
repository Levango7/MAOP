"""F2-01 Agent 自演化闭环 — FastAPI 路由。

暴露 PerformanceEvaluator / ImprovementSuggester / ABTestFramework(SPRT)
/ AutoDeployer / PerformanceEvolutionLoop 的能力给前端
EvolutionHistory.vue 消费。

端点分组：
  - /api/evolution/evaluate         — 评估一组 trace 的性能指标
  - /api/evolution/suggest          — LLM/规则生成候选改进
  - /api/evolution/ab/*             — AB 实验 + SPRT
  - /api/evolution/deploy/*         — 自动提升 / 回滚
  - /api/evolution/cycles           — 性能演化循环历史
  - /api/evolution/run              — 触发一轮循环
  - /api/evolution/approve          — 人工 gate 批准提升
  - /api/evolution/pending          — 待批准列表
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from maop.core.security.middleware import require_admin
from maop.dashboard.error_handler import handle_api_errors

from .state import MAOP_ROOT

logger = logging.getLogger(__name__)

router = APIRouter()


# ── Pydantic 请求模型 ──────────────────────────────────────────────
class EvolutionEvaluateRequest(BaseModel):
    """评估一组 trace 性能指标的请求体。"""
    traces: list[Any] = Field(default_factory=list)
    baseline: list[Any] | None = None


class EvolutionSuggestRequest(BaseModel):
    """生成改进建议的请求体。"""
    metrics: dict[str, Any] = Field(default_factory=dict)
    agent_name: str = Field(default="", max_length=256)
    enable_llm: bool = True


class EvolutionABCreateRequest(BaseModel):
    """创建 AB 实验的请求体。"""
    name: str = Field(..., min_length=1, max_length=256)
    variants: list[str] = Field(default_factory=list)
    sprt: dict[str, Any] | None = None


class EvolutionABRecordRequest(BaseModel):
    """记录 AB 实验样本的请求体。"""
    experiment: str = Field(..., min_length=1, max_length=256)
    variant: str = Field(..., min_length=1, max_length=256)
    entity_id: str = Field(default="", max_length=256)
    success: bool = False


class EvolutionDeployPromoteRequest(BaseModel):
    """提升部署的请求体。"""
    experiment: str = Field(..., min_length=1, max_length=256)
    winner: str = Field(..., min_length=1, max_length=256)
    config: dict[str, Any] | None = None


class EvolutionDeployRollbackRequest(BaseModel):
    """回滚部署的请求体。"""
    experiment: str = Field(..., min_length=1, max_length=256)
    snapshot_id: str = Field(default="", max_length=256)


class EvolutionRunRequest(BaseModel):
    """触发演化循环的请求体。"""
    baseline_traces: list[Any] = Field(default_factory=list)
    candidate_traces: list[Any] = Field(default_factory=list)
    experiment: str = Field(..., min_length=1, max_length=256)
    agent_name: str = Field(default="", max_length=256)
    candidate_config: dict[str, Any] | None = None


class EvolutionApproveRequest(BaseModel):
    """批准提升的请求体。"""
    experiment: str = Field(..., min_length=1, max_length=256)
    candidate_config: dict[str, Any] | None = None


class EvolutionSkillCompositeRequest(BaseModel):
    """保存 composite Skill 的请求体。"""
    name: str = Field(default="", max_length=256)
    version: str = Field(default="1.0.0", max_length=64)
    description: str = Field(default="", max_length=10000)
    source: str = Field(default="manual", max_length=64)
    tags: list[str] = Field(default_factory=list)
    steps: list[dict[str, Any]] = Field(default_factory=list)
    content: str | None = None


def _perf_loop() -> Any:
    from maop.core.evolution.evolution_perf_loop import PerformanceEvolutionLoop

    return PerformanceEvolutionLoop(root_dir=str(MAOP_ROOT))


def _ab_fw() -> Any:
    from maop.core.evolution.ab_test import ABTestFramework

    return ABTestFramework(root_dir=str(MAOP_ROOT))


def _deployer() -> Any:
    from maop.core.evolution.auto_deployer import AutoDeployer

    return AutoDeployer(root_dir=str(MAOP_ROOT))


def _evaluator() -> Any:
    from maop.core.evolution.evaluator import PerformanceEvaluator

    return PerformanceEvaluator()


# ── 评估 ───────────────────────────────────────────────────────


@router.post("/api/evolution/evaluate")
@handle_api_errors("evolution evaluate", error_value={"status": "error", "metrics": {}})
async def api_evolution_evaluate(body: EvolutionEvaluateRequest, request: Request) -> dict[str, Any]:
    """评估一组 trace 的性能指标。

    Body: {"traces": [...], "baseline": [...] (可选)}
    返回 metrics（+ delta 当提供 baseline）。
    """
    require_admin(request)
    traces = body.traces
    evaluator = _evaluator()
    metrics = evaluator.evaluate(traces)
    result: dict[str, Any] = {"status": "ok", "metrics": metrics.to_dict()}
    if body.baseline is not None:
        delta = evaluator.compare(body.baseline, traces)
        result["delta"] = delta.to_dict()
    return result


# ── 建议生成 ───────────────────────────────────────────────────


@router.post("/api/evolution/suggest")
@handle_api_errors("evolution suggest", error_value={"status": "error", "suggestions": []})
async def api_evolution_suggest(body: EvolutionSuggestRequest, request: Request) -> dict[str, Any]:
    """基于指标生成候选改进建议。

    Body: {"metrics": {...}, "agent_name": "...", "enable_llm": true}
    """
    require_admin(request)
    from maop.core.evolution.evaluator import PerformanceMetrics
    from maop.core.evolution.suggester import ImprovementSuggester, SuggestionContext

    metrics = PerformanceMetrics.model_validate(body.metrics)
    enable_llm = body.enable_llm
    suggester = ImprovementSuggester(root_dir=str(MAOP_ROOT), enable_llm=enable_llm)
    ctx = SuggestionContext(agent_name=body.agent_name)
    suggestions = suggester.suggest_sync(metrics, ctx)
    return {
        "status": "ok",
        "suggestions": [s.model_dump() for s in suggestions],
        "count": len(suggestions),
    }


# ── AB 实验 + SPRT ─────────────────────────────────────────────


@router.post("/api/evolution/ab/create")
@handle_api_errors("evolution ab create", error_value={"status": "error"})
async def api_ab_create(body: EvolutionABCreateRequest, request: Request) -> dict[str, Any]:
    require_admin(request)
    from maop.core.evolution.ab_test import SPRTConfig

    sprt_cfg = None
    if body.sprt:
        sprt_cfg = SPRTConfig.model_validate(body.sprt)
    fw = _ab_fw()
    config = fw.create_experiment(
        name=body.name,
        variants=body.variants,
        sprt_config=sprt_cfg,
    )
    return {"status": "ok", "experiment": config.model_dump()}


@router.post("/api/evolution/ab/record")
@handle_api_errors("evolution ab record", error_value={"status": "error"})
async def api_ab_record(body: EvolutionABRecordRequest, request: Request) -> dict[str, Any]:
    """记录一个样本并返回当前 SPRT 状态。

    Body: {"experiment": "...", "variant": "...", "entity_id": "...", "success": true}
    """
    require_admin(request)
    fw = _ab_fw()
    state = fw.record(
        body.experiment, body.variant, body.entity_id, body.success,
    )
    return {"status": "ok", "sprt": state.model_dump()}


@router.get("/api/evolution/ab/evaluate/{experiment}")
@handle_api_errors("evolution ab evaluate", error_value={"status": "error"})
async def api_ab_evaluate(request: Request, experiment: str) -> dict[str, Any]:
    require_admin(request)
    fw = _ab_fw()
    result = fw.evaluate_sprt(experiment)
    return {"status": "ok", "result": result.model_dump()}


@router.get("/api/evolution/ab/list")
@handle_api_errors("evolution ab list", error_value={"status": "error", "experiments": []})
async def api_ab_list(request: Request) -> dict[str, Any]:
    require_admin(request)
    fw = _ab_fw()
    return {"status": "ok", "experiments": fw.list_experiments()}


# ── 部署 ───────────────────────────────────────────────────────


@router.post("/api/evolution/deploy/promote")
@handle_api_errors("evolution promote", error_value={"status": "error"})
async def api_deploy_promote(body: EvolutionDeployPromoteRequest, request: Request) -> dict[str, Any]:
    require_admin(request)
    deployer = _deployer()
    result = deployer.promote(
        body.experiment, body.winner, config=body.config,
    )
    return {"status": "ok", "result": result.model_dump()}


@router.post("/api/evolution/deploy/rollback")
@handle_api_errors("evolution rollback", error_value={"status": "error"})
async def api_deploy_rollback(body: EvolutionDeployRollbackRequest, request: Request) -> dict[str, Any]:
    require_admin(request)
    deployer = _deployer()
    result = deployer.rollback(
        body.experiment, snapshot_id=body.snapshot_id,
    )
    return {"status": "ok", "result": result.model_dump()}


@router.get("/api/evolution/deploy/history")
@handle_api_errors("evolution deploy history", error_value={"status": "error", "history": []})
async def api_deploy_history(request: Request) -> dict[str, Any]:
    experiment = request.query_params.get("experiment", "")
    deployer = _deployer()
    history = deployer.get_history(experiment=experiment, limit=100)
    return {"status": "ok", "history": [h.model_dump() for h in history]}


# ── 性能演化循环 ───────────────────────────────────────────────


@router.post("/api/evolution/run")
@handle_api_errors("evolution run cycle", error_value={"status": "error"})
async def api_evolution_run(body: EvolutionRunRequest, request: Request) -> dict[str, Any]:
    """触发一轮性能演化循环。

    Body: {
        "baseline_traces": [...],
        "candidate_traces": [...],
        "experiment": "...",
        "agent_name": "...",
        "candidate_config": {...} (可选)
    }
    """
    require_admin(request)
    loop = _perf_loop()
    report = loop.run_evolution_cycle(
        body.baseline_traces,
        body.candidate_traces,
        experiment=body.experiment,
        agent_name=body.agent_name,
        candidate_config=body.candidate_config,
    )
    return {"status": "ok", "report": report.model_dump()}


@router.get("/api/evolution/cycles")
@handle_api_errors("evolution cycles", error_value={"status": "error", "cycles": []})
async def api_evolution_cycles(request: Request, limit: int = Query(50, ge=1, le=1000)) -> dict[str, Any]:
    experiment = request.query_params.get("experiment", "")
    # P1-20: 使用 FastAPI Query 参数校验替代 int(request.query_params.get(...))，
    # 自动处理异常值并限制范围 [1, 1000]
    loop = _perf_loop()
    cycles = loop.get_cycle_history(experiment=experiment, limit=limit)
    return {"status": "ok", "cycles": [c.model_dump() for c in cycles]}


@router.get("/api/evolution/pending")
@handle_api_errors("evolution pending", error_value={"status": "error", "pending": []})
async def api_evolution_pending(request: Request) -> dict[str, Any]:
    """人工 gate：返回待批准的提升列表。"""
    require_admin(request)
    loop = _perf_loop()
    pending = loop.get_pending_approvals()
    return {"status": "ok", "pending": [c.model_dump() for c in pending]}


@router.post("/api/evolution/approve")
@handle_api_errors("evolution approve", error_value={"status": "error"})
async def api_evolution_approve(body: EvolutionApproveRequest, request: Request) -> dict[str, Any]:
    """人工 gate：批准指定实验的提升。"""
    require_admin(request)
    loop = _perf_loop()
    result = loop.approve_and_promote(
        body.experiment, candidate_config=body.candidate_config,
    )
    return {"status": "ok", "result": result}


# ── Skill 编辑器 ────────────────────────────────────────────────


@router.get("/api/evolution/skills")
@handle_api_errors("evolution skills", error_value={"skills": []})
async def api_evolution_skills(request: Request) -> dict[str, Any]:
    """列出已保存的 Skill 原子（从 SkillVersionManager 加载）。"""
    require_admin(request)
    from maop.core.evolution.skill_version import SkillVersionManager

    try:
        mgr = SkillVersionManager(root_dir=str(MAOP_ROOT))
        skills_meta = mgr.list_skills()
        skills = [m.model_dump() for m in skills_meta]
        return {"skills": skills}
    except Exception:
        logger.debug("[evo/skills] list failed", exc_info=True)
        return {"skills": []}


@router.post("/api/evolution/skills/composite")
@handle_api_errors("evolution skill composite", error_value={"status": "error"})
async def api_evolution_skill_composite(body: EvolutionSkillCompositeRequest, request: Request) -> dict[str, Any]:
    """保存 composite Skill（接入 SkillVersionManager）。"""
    require_admin(request)
    from maop.core.evolution.skill_version import SkillMeta, SkillStep, SkillVersionManager

    name = (body.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="skill name is required")

    steps_raw = body.steps or []
    steps = [SkillStep(**(s if isinstance(s, dict) else {})) for s in steps_raw]

    meta = SkillMeta(
        name=name,
        version=body.version,
        description=body.description,
        source=body.source,
        tags=body.tags,
        steps=steps,
    )

    import json as _json
    content = body.content if body.content is not None else _json.dumps(meta.model_dump(), ensure_ascii=False)

    try:
        mgr = SkillVersionManager(root_dir=str(MAOP_ROOT))
        mgr.save_skill(name, content=content, metadata={
            "source": "dashboard",
            "steps": steps_raw,
            "description": meta.description,
            "tags": meta.tags,
        })
        return {"status": "ok", "saved": True, "name": name, "version": meta.version}
    except Exception as exc:
        logger.exception("[evo/skills] save_skill failed for %s", name)
        raise HTTPException(status_code=500, detail="Failed to save skill") from exc


# ── 演化案例叙事 ───────────────────────────────────────────────


def _evo_loop() -> Any:
    """EvolutionLoop 实例（ErrorLedger 驱动的七段闭环，写入 evolution_cycles 表）。"""
    from maop.core.evolution.evolution_loop import EvolutionLoop

    return EvolutionLoop(root_dir=str(MAOP_ROOT))


def _find_cycle_report(loop: Any, cycle_id: str) -> Any:
    """从演化历史中查找指定 cycle_id 的 LoopReport，未命中返回 None。"""
    for report in loop.get_cycle_history(limit=1000):
        if report.cycle_id == cycle_id:
            return report
    return None


@router.get("/api/evolution/narrative/{cycle_id}")
@handle_api_errors("evolution narrative", error_value={"status": "error"})
async def api_evolution_narrative(cycle_id: str, request: Request) -> dict[str, Any]:
    """获取指定演化周期的人类可读叙事。

    Path params:
        cycle_id — EvolutionLoop 周期 ID
    Query params:
        format — "markdown"（默认）或 "json"

    Returns:
        format=markdown → ``{"status": "ok", "format": "markdown", "markdown": "..."}``
        format=json     → ``{"status": "ok", "format": "json", "narrative": {...}}``
    """
    fmt = request.query_params.get("format", "markdown").lower()
    loop = _evo_loop()
    report = _find_cycle_report(loop, cycle_id)
    if report is None:
        raise HTTPException(
            status_code=404,
            detail=f"evolution cycle '{cycle_id}' not found",
        )
    from maop.core.evolution.narrative import EvolutionNarrative

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