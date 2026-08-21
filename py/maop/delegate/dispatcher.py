"""MAOP Delegate Dispatcher — Config-driven agent execution.

Agent dispatch and driver resolution.: resolves agent config from YAML,
dispatches to the appropriate driver (cli/wrapper/powershell/cmd),
with circuit-breaker check, timeout, and unified MaopResult.

Architecture (split for maintainability):
  - models.py:          AgentConfig, DispatchResult, security helpers
  - drivers.py:         5 async driver implementations + DRIVERS table
  - dispatch_tools.py:  retry helper + lazy-subsystem factories
  - dispatch_core.py:   Dispatcher class + decision-record helper
  - dispatcher.py (this file): re-export shim for backward compatibility

This module is a thin re-export layer: the implementation now lives in
``dispatch_core.py`` and ``dispatch_tools.py``. All public (and
previously patch-able private) symbols are re-exported here so that
existing imports such as::

    from maop.delegate.dispatcher import Dispatcher, DispatchResult
    from maop.delegate.dispatcher import _DRIVERS, _retry_with_backoff
    from maop.delegate import dispatcher as disp_mod   # disp_mod._DRIVERS[...]

and test patches such as::

    patch("maop.delegate.dispatcher.Dispatcher")
    patch("maop.delegate.dispatcher._get_subagent_manager")

continue to work without any caller-side changes.
"""

from __future__ import annotations

import logging

# ── Re-export Dispatcher class + decision-record helper ──
from maop.delegate.dispatch_core import (  # noqa: F401
    Dispatcher,
    _record_dispatcher_decision,
    otel_span,
)

# ── Re-export tool / lazy-subsystem helpers ──
from maop.delegate.dispatch_tools import (  # noqa: F401
    _get_load_balancer,
    _get_runtime,
    _get_sandbox_manager,
    _get_subagent_manager,
    _retry_with_backoff,
)

# ── Re-export the driver registry (same dict object as drivers.DRIVERS) ──
# Tests mutate `disp_mod._DRIVERS["cli"]` in place; importing the same
# object preserves that mutation-visible behaviour for Dispatcher (which
# reads `_DRIVERS` from dispatch_core).
from maop.delegate.drivers import DRIVERS as _DRIVERS  # noqa: F401

# ── Re-export models (backward compatibility) ──
from maop.delegate.models import (  # noqa: F401
    AgentConfig,
    DispatchResult,
    _escape_for_cmd,
    _escape_for_ps_command,
)

# Module-level logger pinned to the canonical name so that any caller
# that does `logging.getLogger("maop.delegate.dispatcher")` (or accesses
# `disp_mod.logger`) gets the same logger object as the implementation
# modules.
logger = logging.getLogger(__name__)
