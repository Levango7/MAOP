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

from fastapi import APIRouter, HTTPException, Request

from maop.core.security.middleware import require_admin
from maop.dashboard.error_handler import handle_api_errors

from .state import MAOP_ROOT

logger = logging.getLogger(__name__)

router = APIRouter()


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
async def api_evolution_evaluate(request: Request) -> dict[str, Any]:
    """评估一组 trace 的性能指标。

    Body: {"traces": [...], "baseline": [...] (可选)}
    返回 metrics（+ delta 当提供 baseline）。
    """
    body = await request.json()
    traces = body.get("traces", [])
    evaluator = _evaluator()
    metrics = evaluator.evaluate(traces)
    result: dict[str, Any] = {"status": "ok", "metrics": metrics.to_dict()}
    if "baseline" in body:
        delta = evaluator.compare(body["baseline"], traces)
        result["delta"] = delta.to_dict()
    return result


# ── 建议生成 ───────────────────────────────────────────────────


@router.post("/api/evolution/suggest")
@handle_api_errors("evolution suggest", error_value={"status": "error", "suggestions": []})
async def api_evolution_suggest(request: Request) -> dict[str, Any]:
    """基于指标生成候选改进建议。

    Body: {"metrics": {...}, "agent_name": "...", "enable_llm": true}
    """
    body = await request.json()
    from maop.core.evolution.evaluator import PerformanceMetrics
    from maop.core.evolution.suggester import ImprovementSuggester, SuggestionContext

    metrics = PerformanceMetrics.model_validate(body.get("metrics", {}))
    enable_llm = body.get("enable_llm", True)
    suggester = ImprovementSuggester(root_dir=str(MAOP_ROOT), enable_llm=enable_llm)
    ctx = SuggestionContext(agent_name=body.get("agent_name", ""))
    suggestions = suggester.suggest_sync(metrics, ctx)
    return {
        "status": "ok",
        "suggestions": [s.model_dump() for s in suggestions],
        "count": len(suggestions),
    }


# ── AB 实验 + SPRT ─────────────────────────────────────────────


@router.post("/api/evolution/ab/create")
@handle_api_errors("evolution ab create", error_value={"status": "error"})
async def api_ab_create(request: Request) -> dict[str, Any]:
    require_admin(request)
    body = await request.json()
    from maop.core.evolution.ab_test import SPRTConfig

    sprt_cfg = None
    if body.get("sprt"):
        sprt_cfg = SPRTConfig.model_validate(body["sprt"])
    fw = _ab_fw()
    config = fw.create_experiment(
        name=body["name"],
        variants=body["variants"],
        sprt_config=sprt_cfg,
    )
    return {"status": "ok", "experiment": config.model_dump()}


@router.post("/api/evolution/ab/record")
@handle_api_errors("evolution ab record", error_value={"status": "error"})
async def api_ab_record(request: Request) -> dict[str, Any]:
    """记录一个样本并返回当前 SPRT 状态。

    Body: {"experiment": "...", "variant": "...", "entity_id": "...", "success": true}
    """
    body = await request.json()
    fw = _ab_fw()
    state = fw.record(
        body["experiment"], body["variant"], body.get("entity_id", ""), body.get("success", False),
    )
    return {"status": "ok", "sprt": state.model_dump()}


@router.get("/api/evolution/ab/evaluate/{experiment}")
@handle_api_errors("evolution ab evaluate", error_value={"status": "error"})
async def api_ab_evaluate(experiment: str) -> dict[str, Any]:
    fw = _ab_fw()
    result = fw.evaluate_sprt(experiment)
    return {"status": "ok", "result": result.model_dump()}


@router.get("/api/evolution/ab/list")
@handle_api_errors("evolution ab list", error_value={"status": "error", "experiments": []})
async def api_ab_list() -> dict[str, Any]:
    fw = _ab_fw()
    return {"status": "ok", "experiments": fw.list_experiments()}


# ── 部署 ───────────────────────────────────────────────────────


@router.post("/api/evolution/deploy/promote")
@handle_api_errors("evolution promote", error_value={"status": "error"})
async def api_deploy_promote(request: Request) -> dict[str, Any]:
    require_admin(request)
    body = await request.json()
    deployer = _deployer()
    result = deployer.promote(
        body["experiment"], body["winner"], config=body.get("config"),
    )
    return {"status": "ok", "result": result.model_dump()}


@router.post("/api/evolution/deploy/rollback")
@handle_api_errors("evolution rollback", error_value={"status": "error"})
async def api_deploy_rollback(request: Request) -> dict[str, Any]:
    require_admin(request)
    body = await request.json()
    deployer = _deployer()
    result = deployer.rollback(
        body["experiment"], snapshot_id=body.get("snapshot_id", ""),
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
async def api_evolution_run(request: Request) -> dict[str, Any]:
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
    body = await request.json()
    loop = _perf_loop()
    report = loop.run_evolution_cycle(
        body["baseline_traces"],
        body["candidate_traces"],
        experiment=body["experiment"],
        agent_name=body.get("agent_name", ""),
        candidate_config=body.get("candidate_config"),
    )
    return {"status": "ok", "report": report.model_dump()}


@router.get("/api/evolution/cycles")
@handle_api_errors("evolution cycles", error_value={"status": "error", "cycles": []})
async def api_evolution_cycles(request: Request) -> dict[str, Any]:
    experiment = request.query_params.get("experiment", "")
    limit = int(request.query_params.get("limit", "50"))
    loop = _perf_loop()
    cycles = loop.get_cycle_history(experiment=experiment, limit=limit)
    return {"status": "ok", "cycles": [c.model_dump() for c in cycles]}


@router.get("/api/evolution/pending")
@handle_api_errors("evolution pending", error_value={"status": "error", "pending": []})
async def api_evolution_pending() -> dict[str, Any]:
    """人工 gate：返回待批准的提升列表。"""
    loop = _perf_loop()
    pending = loop.get_pending_approvals()
    return {"status": "ok", "pending": [c.model_dump() for c in pending]}


@router.post("/api/evolution/approve")
@handle_api_errors("evolution approve", error_value={"status": "error"})
async def api_evolution_approve(request: Request) -> dict[str, Any]:
    """人工 gate：批准指定实验的提升。"""
    require_admin(request)
    body = await request.json()
    loop = _perf_loop()
    result = loop.approve_and_promote(
        body["experiment"], candidate_config=body.get("candidate_config"),
    )
    return {"status": "ok", "result": result}


# ── Skill 编辑器 ────────────────────────────────────────────────
# TODO(P1): 后端尚无 Skill 原子/composite 的持久化数据源，以下端点返回空
# 结构以对齐前端 SkillEditor.vue 契约（避免 404），待 skill 系统落地后接入。


@router.get("/api/evolution/skills")
@handle_api_errors("evolution skills", error_value={"skills": []})
async def api_evolution_skills() -> dict[str, Any]:
    """列出 Skill 原子（当前为空，Skill 系统待落地）。"""
    return {"skills": []}


@router.post("/api/evolution/skills/composite")
@handle_api_errors("evolution skill composite", error_value={"status": "error"})
async def api_evolution_skill_composite(request: Request) -> dict[str, Any]:
    """保存 composite Skill。

    Skill 持久化后端尚未落地，此前返回 ``"status": "ok", "saved": False`` 属于
    假成功；现如实返回 501，待 Skill 系统后端接入后再实现。
    """
    require_admin(request)
    raise HTTPException(
        status_code=501,
        detail="skill persistence not implemented; Skill system backend pending",
    )