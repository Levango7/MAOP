"""System router subpackage.

Aggregates five sub-routers (framework / agent_admin / overview / workflow /
v4_misc) into a single ``router`` so the public import path is unchanged:

    from maop.dashboard.routers.system import router

Shared state (``MAOP_ROOT`` / ``active_jobs`` / ``start_time`` / ``get_bridge`` /
``init_subsystems`` / ``get_subsystems``) and utility functions
(``_run_subprocess`` / ``_dir_size_mb`` / ``_pct`` / ``_get_allowed_packages`` /
``_HARDENED_ALLOWED_PACKAGES`` / ``_ALLOWED_PIP_PACKAGES``) live in ``._deps``
and are re-exported here for backward compatibility.  Sub-routers always call
them through ``_deps.xxx`` so monkeypatching
``maop.dashboard.routers.system._deps.xxx`` takes effect regardless of which
sub-router an endpoint lives in.
"""

from __future__ import annotations

from fastapi import APIRouter

# Re-export shared deps for backward compatibility (tests / external code
# that reference maop.dashboard.routers.system.xxx).
from ._deps import (
    MAOP_ROOT,
    MAOP_VERSION,
    _ALLOWED_PIP_PACKAGES,
    _HARDENED_ALLOWED_PACKAGES,
    _count_file_lines,
    _dir_size_mb,
    _get_allowed_packages,
    _pct,
    _run_subprocess,
    active_jobs,
    get_bridge,
    get_db_path,
    get_subsystems,
    handle_api_errors,
    init_subsystems,
    logger,
    require_admin,
    start_time,
)

# Sub-routers — each declares its own routes with full paths (no prefix
# doubling) so include_router below just merges them.
from . import agent_admin, framework, overview, v4_misc, workflow

# Re-export overview caches for backward compatibility (tests clear them
# between runs via maop.dashboard.routers.system._overview_cache).
from .overview import _file_counts_cache, _overview_cache

router = APIRouter()
# Flatten sub-router routes into the parent so ``router.routes`` contains
# actual Route objects (not deferred _IncludedRouter wrappers).  This keeps
# backward compatibility with tests that iterate ``system.router.routes``.
for _sub in (framework.router, agent_admin.router, overview.router, workflow.router, v4_misc.router):
    for _route in _sub.routes:
        router.routes.append(_route)

__all__ = [
    "router",
    "logger",
    "MAOP_ROOT",
    "MAOP_VERSION",
    "active_jobs",
    "start_time",
    "get_bridge",
    "get_subsystems",
    "init_subsystems",
    "get_db_path",
    "handle_api_errors",
    "require_admin",
    "_count_file_lines",
    "_run_subprocess",
    "_HARDENED_ALLOWED_PACKAGES",
    "_ALLOWED_PIP_PACKAGES",
    "_get_allowed_packages",
    "_dir_size_mb",
    "_pct",
    "_overview_cache",
    "_file_counts_cache",
]