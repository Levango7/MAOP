"""Info router subpackage.

Aggregates three sub-routers (meta / activity / admin) into a single
``router`` so the public import path is unchanged:

    from maop.dashboard.routers.info import router

Sub-routers each declare ``prefix="/api/info"`` so include_router below
just merges them without double-prefixing.
"""

from __future__ import annotations

from fastapi import APIRouter

from . import activity, admin, meta

router = APIRouter()
# Flatten sub-router routes into the parent so ``router.routes`` contains
# actual Route objects (not deferred _IncludedRouter wrappers).
for _sub in (meta.router, activity.router, admin.router):
    for _route in _sub.routes:
        router.routes.append(_route)

__all__ = ["router"]