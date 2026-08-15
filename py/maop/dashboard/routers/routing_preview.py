"""Route scoring and cooldown API endpoints.

Provides visibility into the multi-factor route matching process:
- POST /api/routing/match: Preview which route a task would match and why
- GET /api/routing/cooldowns: List agents currently in cooldown
- GET /api/routing/scores: Show all route scores for a given task
"""

from __future__ import annotations

import logging
from typing import Any, cast

from fastapi import APIRouter, Request

from maop.config.loader import load_config
from maop.core.routing.route_scorer import get_route_scorer
from maop.core.security.middleware import require_admin

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/routing", tags=["routing"])


@router.post("/match")
async def preview_match(body: dict[str, Any], request: Request) -> dict[str, Any]:
    require_admin(request)
    """Preview route matching for a task description.

    Returns the matched route, agent, score, confidence, and all candidate
    routes with their individual scores.
    """
    task = body.get("task", "")
    if not task:
        return {"error": "task is required"}

    import os
    root = os.environ.get("MAOP_ROOT_DIR", os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))))
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
    all_scores = []
    for rk, route in config.routing.items():
        score, matched_by = scorer._score_route(task_lower, rk, route)
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
async def get_route_scores(request: Request, task: str = "") -> dict[str, Any]:
    """Get scores for all routes against a given task."""
    require_admin(request)
    if not task:
        return {"error": "task parameter is required"}

    import os
    root = os.environ.get("MAOP_ROOT_DIR", os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))))
    config = load_config(root)
    scorer = get_route_scorer(config)
    task_lower = task.lower()
    scores = []
    for rk, route in config.routing.items():
        score, matched_by = scorer._score_route(task_lower, rk, route)
        scores.append({
            "routing_key": rk,
            "score": round(score, 4),
            "matched_by": matched_by or "none",
            "primary": route.primary,
        })
    scores.sort(key=lambda x: cast(float, x["score"]), reverse=True)
    return {"task": task, "scores": scores}
