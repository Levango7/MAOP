"""Self-evolution endpoints for MAOP Dashboard."""

from __future__ import annotations

from typing import Any

import logging

from fastapi import APIRouter, Request

from .state import MAOP_ROOT
from .error_handler import handle_api_errors
from maop.core.middleware import require_admin

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
        try:
            result: Any = eng.apply("") if hasattr(eng, "apply") else eng.analyze()
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
