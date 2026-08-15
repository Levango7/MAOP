"""Agents router subpackage.

Aggregates four sub-routers (crud / evolution / memory / routes) into a
single ``router`` so the public import path is unchanged:

    from maop.dashboard.routers.agents import router

Shared lazy factories (``_get_registry`` / ``_get_scanner`` / ...) and
the module-level ``_instance_cache`` live in ``._deps`` and are
re-exported here for backward compatibility. Sub-routers always call
them through ``_deps._get_*()`` so monkeypatching
``maop.dashboard.routers.agents._deps._get_*`` takes effect regardless
of which sub-router an endpoint lives in.
"""

from __future__ import annotations

from fastapi import APIRouter

# Sub-routers — each declares its own prefix="/api/agents" so include_router
# below does not double-prefix.
from . import crud, evolution, memory, routes

# Re-export shared deps for backward compatibility (tests / external code
# that reference maop.dashboard.routers.agents._get_registry etc.).
from ._deps import (
    MAOP_ROOT,
    _get_agent_config,
    _get_evolution,
    _get_matcher,
    _get_memory,
    _get_registry,
    _get_repair,
    _get_scanner,
    _instance_cache,
)

router = APIRouter()
# Order matters: static single-segment paths (e.g. /routes) must be
# registered before the dynamic /{name} catch-all in crud.router so
# FastAPI matches them first. routes.router only owns /routes.
router.include_router(routes.router)
router.include_router(crud.router)
router.include_router(evolution.router)
router.include_router(memory.router)

__all__ = [
    "MAOP_ROOT",
    "_get_agent_config",
    "_get_evolution",
    "_get_matcher",
    "_get_memory",
    "_get_registry",
    "_get_repair",
    "_get_scanner",
    "_instance_cache",
    "router",
]