"""Domain submodules split out of ``data_service`` (file-size ratchet).

Two of the largest, most self-contained blocks moved here:

* :mod:`.logs` — raw log tail reading, log-name validation, log analysis
* :mod:`.analysis` — the six deep-analysis endpoints (cost/trends/etc.)

``maop.dashboard.services.data_service`` still forwards every public name
below, so existing imports and ``data_service.<name>`` monkeypatching keep
working. New code may import from here directly.
"""

from __future__ import annotations

from maop.dashboard.services.data.analysis import (
    _analysis_meta,
    analysis_agent_efficiency,
    analysis_cost_breakdown,
    analysis_performance_bottlenecks,
    analysis_resource_utilization,
    analysis_summary,
    analysis_task_trends,
)
from maop.dashboard.services.data.logs import (
    _LOG_RE,
    _read_log_tail,
    _validate_log_name,
    get_logs,
    get_logs_analysis,
    get_logs_checker,
    get_logs_delegations,
)

__all__ = [
    "_LOG_RE",
    "_analysis_meta",
    "_read_log_tail",
    "_validate_log_name",
    "analysis_agent_efficiency",
    "analysis_cost_breakdown",
    "analysis_performance_bottlenecks",
    "analysis_resource_utilization",
    "analysis_summary",
    "analysis_task_trends",
    "get_logs",
    "get_logs_analysis",
    "get_logs_checker",
    "get_logs_delegations",
]