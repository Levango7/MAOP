"""Propagate delegation depth across process boundaries.

The in-process ``max_self_ref_depth`` check only sees one call chain. A
CLI agent that re-enters MAOP (the self-referential ``MAOP`` entry in
``config/agents.yaml``) starts a fresh process with a fresh counter, so
without an inherited marker a task can fork itself until the machine
runs out of memory. ``MAOP_DELEGATION_DEPTH`` is that marker: every
spawned agent inherits parent depth + 1, and ``maop run`` refuses to
start at the cap.
"""

from __future__ import annotations

import os

DEPTH_ENV = "MAOP_DELEGATION_DEPTH"
MAX_SELF_DELEGATION_DEPTH = 3


def current_depth() -> int:
    """Return the delegation depth inherited from the parent process."""
    try:
        return max(0, int(os.environ.get(DEPTH_ENV, "0")))
    except ValueError:
        return 0


def next_depth_env(base: dict[str, str] | None = None) -> dict[str, str]:
    """Return an environment for a child agent one level deeper."""
    env = dict(os.environ if base is None else base)
    env[DEPTH_ENV] = str(current_depth() + 1)
    return env
