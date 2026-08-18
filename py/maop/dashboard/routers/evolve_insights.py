"""Self-evolution endpoints for MAOP Dashboard."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Request

from maop.core.security.middleware import require_admin
from maop.dashboard.error_handler import handle_api_errors

from .state import MAOP_ROOT

logger = logging.getLogger(__name__)

router = APIRouter()

@router.get("/api/evolve/status")
@handle_api_errors("Evolve status", error_value={"status": "error", "error": "Evolve status unavailable"})
async def api_evolve_status() -> dict[str, Any]:
    from maop.evolve import EvolveEngine
    eng = EvolveEngine(root_dir=str(MAOP_ROOT))
    data: Any = eng.status()
    if hasattr(data, 'model_dump'):
        data = data.model_dump()
    elif hasattr(data, 'dict') and not isinstance(data, dict):
        data = data.dict()
    return {"status": "ok", "data": data}


@router.get("/api/evolve/metrics")
@handle_api_errors("Evolve metrics", error_value={"timeseries": [], "heatmap": [], "lineage": []})
async def api_evolve_metrics() -> dict[str, Any]:
    """演化指标聚合（时间序列 / 热力图 / 世系）。

    TODO(P1): 当前后端无演化指标数据源，返回空结构以对齐前端契约。
    待演化历史数据接入后从 evolve 引擎聚合真实时间序列/热力图/世系。
    """
    return {"timeseries": [], "heatmap": [], "lineage": []}


@router.post("/api/evolve/analyze")
@handle_api_errors("Evolve analyze", error_value={"status": "error", "error": "Evolve analyze unavailable"})
async def api_evolve_analyze(request: Request) -> dict[str, Any]:
    require_admin(request)
    from maop.evolve import EvolveEngine
    eng = EvolveEngine(root_dir=str(MAOP_ROOT))
    action = ""
    try:
        body = await request.json()
        action = body.get("action", "")
    except Exception:
        logger.debug("Failed to parse request body", exc_info=True)
    if action == "apply":
        suggestion_id = body.get("suggestion_id", "")
        try:
            result: Any = eng.apply(suggestion_id) if hasattr(eng, "apply") else eng.analyze()
        except TypeError:
            result = eng.apply() if hasattr(eng, "apply") else eng.analyze()
        if hasattr(result, 'model_dump'):
            result = result.model_dump()
        return {"status": "ok", "action": "apply", "suggestions": result}
    elif action == "reset":
        if hasattr(eng, '_suggestions_file'):
            sf = eng._suggestions_file
            if sf and sf.exists():
                sf.unlink()
        return {"status": "ok", "action": "reset", "msg": "Suggestions cleared"}
    elif action == "auto_evolve":
        try:
            hours = body.get("hours", 24)
            result = eng.auto_evolve(hours=hours) if hasattr(eng, "auto_evolve") else eng.analyze()
        except Exception as exc:
            logger.warning("auto_evolve failed: %s", exc)
            result = {"error": str(exc)}
        return {"status": "ok", "action": "auto_evolve", "result": result}
    else:
        analyze_result: Any = eng.analyze()
        if hasattr(analyze_result, 'model_dump'):
            analyze_result = analyze_result.model_dump()
        elif hasattr(analyze_result, 'dict') and not isinstance(analyze_result, dict):
            analyze_result = analyze_result.dict()
        return {"status": "ok", "action": "analyze", "suggestions": analyze_result}

@router.get("/api/evolve/suggestions")
@handle_api_errors("Evolve suggestions", error_value={"status": "error", "error": "Evolve suggestions unavailable", "suggestions": {"stats": {"by_agent": []}}})
async def api_evolve_suggestions() -> dict[str, Any]:
    from maop.evolve import EvolveEngine
    eng = EvolveEngine(root_dir=str(MAOP_ROOT))
    s: Any = eng.suggest() if hasattr(eng, "suggest") else {}
    if hasattr(s, 'model_dump'):
        s = s.model_dump()
    elif hasattr(s, 'dict') and not isinstance(s, dict):
        s = s.dict()
    if not isinstance(s, dict):
        s = {"action": "suggest", "stats": {"by_agent": []}}
    if "stats" not in s:
        s = {"action": "suggest", "stats": s if isinstance(s, dict) else {"by_agent": []}}
    if "by_agent" not in s.get("stats", {}):
        try:
            status = eng.status()
            if hasattr(status, 'get'):
                s["stats"]["by_agent"] = status.get("stats", {}).get("by_agent", [])
            if hasattr(status, 'model_dump'):
                s["stats"]["by_agent"] = status.model_dump().get("stats", {}).get("by_agent", [])
        except Exception:
            s["stats"]["by_agent"] = []
    return {"status": "ok", "suggestions": s}

@router.get("/api/evolve/report")
@handle_api_errors("Evolve report", error_value={"performance": [], "error": "Evolve report unavailable"})
async def api_evolve_report_v4() -> dict[str, Any]:
    from .state import get_bridge
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
        perf.append({"agent": a.get("name", a.get("agent", "")),
            "success_rate": sr * 100 if sr <= 1 else sr,
            "avg_latency_ms": a.get("avg_latency_ms", a.get("avg_duration_ms", 0)) or 0,
            "fail_count": fail, "total_count": total,
            "tags": ",".join(a.get("tags", [])) if isinstance(a.get("tags"), list) else ""})
    return {"performance": perf}


@router.get("/api/evolve/strategies")
@handle_api_errors("Evolve strategies", error_value={"status": "error", "strategies": []})
async def api_evolve_strategies() -> dict[str, Any]:
    """返回可用进化策略列表。"""
    from maop.core.evolution.evolution_strategies import STRATEGY_MAP
    strategies = [
        {"name": name, "description": cls.__doc__ or cls.__name__}
        for name, cls in STRATEGY_MAP.items()
    ]
    return {"status": "ok", "strategies": strategies}

@router.get("/api/evolve/history")
@handle_api_errors("Evolve history", error_value={"status": "error", "history": []})
async def api_evolve_history() -> dict[str, Any]:
    """返回进化循环历史。"""
    try:
        from maop.core.evolution.evolution_loop import EvolutionLoop
        loop = EvolutionLoop(root_dir=str(MAOP_ROOT))
        history = loop.get_cycle_history(limit=20)
        stats = loop.get_stats()
        return {
            "status": "ok",
            "history": [h.model_dump() for h in history],
            "stats": stats,
        }
    except Exception:
        return {"status": "ok", "history": [], "stats": {}}

@router.get("/api/evolve/suggestions-list")
@handle_api_errors("Evolve suggestions list", error_value={"status": "error", "suggestions": []})
async def api_evolve_suggestions_list() -> dict[str, Any]:
    """返回所有进化建议列表 (含已应用状态)。"""
    from maop.evolve import EvolveEngine
    eng = EvolveEngine(root_dir=str(MAOP_ROOT))
    suggestions = eng._load_suggestions()
    return {
        "status": "ok",
        "suggestions": [s.model_dump() for s in suggestions],
        "total": len(suggestions),
        "applied": sum(1 for s in suggestions if s.applied),
    }

@router.post("/api/evolve/apply-suggestion")
@handle_api_errors("Evolve apply suggestion", error_value={"status": "error", "error": "Apply failed"})
async def api_evolve_apply_suggestion(request: Request) -> dict[str, Any]:
    """手动应用指定进化建议。"""
    require_admin(request)
    from maop.evolve import EvolveEngine
    eng = EvolveEngine(root_dir=str(MAOP_ROOT))
    body = await request.json()
    suggestion_id = body.get("suggestion_id", "")
    result: Any = eng.apply(suggestion_id)
    if hasattr(result, 'model_dump'):
        result = result.model_dump()
    return {"status": "ok", "result": result}
