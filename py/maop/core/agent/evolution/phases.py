"""Pipeline phases for MaopLoop.run() decomposition."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class PhaseContext:
    """Shared context passed between phases."""

    task: str = ""
    original_task: str = ""
    agent: str = ""
    # F6a (2026-07-22, Phase F): when non-empty, the caller explicitly
    # pinned the agent (e.g. via A2A dispatch_task). _phase_plan must
    # respect this and must NOT override it with plan_result or
    # load_balancer selections. See ADR-013.
    forced_agent: str = ""
    routing_key: str = ""
    plan: Any = None
    plan_result: Any = None
    execution_result: Any = None
    verify_result: Any = None
    feedback: str = ""
    trace_id: str = ""
    streamer: Any = None
    analysis_result: Any = None
    analysis_dict: dict[str, Any] = field(default_factory=dict)
    fallback_chain: list[str] = field(default_factory=list)
    feedback_cycles: int = 0
    block_reason: str = ""
    parallel_executed: bool = False
    timeout: float = 0.0
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class PhaseResult:
    """Result from a single phase execution."""

    ok: bool = True
    error: str = ""
    data: Any = None
    skip_remaining: bool = False
