"""Route scoring and cooldown API endpoints.

Provides visibility into the multi-factor route matching process:
- POST /api/routing/match: Preview which route a task would match and why
- GET /api/routing/cooldowns: List agents currently in cooldown
- GET /api/routing/scores: Show all route scores for a given task
"""

from __future__ import annotations

import logging
import os
from typing import Any, cast

from fastapi import APIRouter, HTTPException, Request

from maop.config.loader import load_config
from maop.core.routing.route_scorer import get_route_scorer
from maop.core.security.middleware import require_admin
from maop.dashboard.error_handler import handle_api_errors

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/routing", tags=["routing"])


@router.post("/match")
@handle_api_errors("routing preview match")
async def preview_match(body: dict[str, Any], request: Request) -> dict[str, Any]:
    require_admin(request)
    """Preview route matching for a task description.

    Returns the matched route, agent, score, confidence, and all candidate
    routes with their individual scores.
    """
    task = body.get("task", "")
    if not task:
        raise HTTPException(status_code=400, detail="task is required")

    # M3 修复：统一使用 get_root_dir() 解析根目录（兼容 MAOP_ROOT_DIR / MAOP_ROOT）
    from maop.config.env import get_root_dir

    root = str(get_root_dir(default=os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))))
    config = load_config(root)
    scorer = get_route_scorer(config)
    match = scorer.match(task, adaptive=True)

    if match is None:
        return {
            "task": task,
            "matched": False,
            "message": "No route matched — would fall through to legacy routing",
        }

    # Also compute all candidate scores for transparency
    task_lower = task.lower()
    # 优先使用公开方法 score_route，回退到内部方法 _score_route（通过 getattr 避免直接引用）
    _score_fn = getattr(scorer, "score_route", None) or getattr(scorer, "_score_route")
    all_scores = []
    for rk, route in config.routing.items():
        score, matched_by = _score_fn(task_lower, rk, route)
        if score > 0:
            all_scores.append({
                "routing_key": rk,
                "score": round(score, 4),
                "matched_by": matched_by,
                "primary": route.primary,
                "fallback": route.fallback or None,
                "tertiary": route.tertiary or None,
            })
    all_scores.sort(key=lambda x: cast(float, x["score"]), reverse=True)

    return {
        "task": task,
        "matched": True,
        "routing_key": match.routing_key,
        "agent": match.agent,
        "score": match.score,
        "confidence": match.confidence,
        "matched_by": match.matched_by,
        "all_candidates": all_scores,
    }


@router.get("/cooldowns")
@handle_api_errors("routing cooldowns")
async def get_cooldowns(request: Request) -> dict[str, Any]:
    """Get all agents currently in cooldown (recently failed)."""
    require_admin(request)
    scorer = get_route_scorer()
    cooldowns = scorer.get_cooldown_status()
    return {
        "count": len(cooldowns),
        "cooldowns": cooldowns,
    }


@router.get("/scores")
@handle_api_errors("routing scores")
async def get_route_scores(request: Request, task: str = "") -> dict[str, Any]:
    """Get scores for all routes against a given task."""
    require_admin(request)
    if not task:
        raise HTTPException(status_code=400, detail="task parameter is required")

    # M3 修复：统一使用 get_root_dir() 解析根目录（兼容 MAOP_ROOT_DIR / MAOP_ROOT）
    from maop.config.env import get_root_dir

    root = str(get_root_dir(default=os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))))
    config = load_config(root)
    scorer = get_route_scorer(config)
    task_lower = task.lower()
    # 优先使用公开方法 score_route，回退到内部方法 _score_route（通过 getattr 避免直接引用）
    _score_fn = getattr(scorer, "score_route", None) or getattr(scorer, "_score_route")
    scores = []
    for rk, route in config.routing.items():
        score, matched_by = _score_fn(task_lower, rk, route)
        scores.append({
            "routing_key": rk,
            "score": round(score, 4),
            "matched_by": matched_by or "none",
            "primary": route.primary,
        })
    scores.sort(key=lambda x: cast(float, x["score"]), reverse=True)
    return {"task": task, "scores": scores}
