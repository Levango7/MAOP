"""MAOP Supervisor — proactive patrol / alert / replace / degrade / terminate / upgrade.

Builds on :class:`~maop.core.scheduling.failure_detector.FailurePatternDetector`
(passive sliding-window detector) to add six proactive capabilities that
form a three-layer supervision system:

1. **Patrol (巡检)** — background asyncio task that periodically probes
   every registered agent's health (reachable / metrics / resource),
   complementing the passive ``record_result()`` path so that long-idle
   agents in abnormal states are still detected.
2. **Alert (预警)** — threshold-rule-driven proactive event publishing
   via ``EventBus.publish(Event)``. Levels: info / warning / error /
   critical. Degrade-but-not-drain scenarios emit warnings; drain
   thresholds emit criticals.
3. **Replace (替换)** — switch routing from a failing agent to a
   healthy backup, with audit record and ``agent_replaced`` event.
4. **Degrade (降级)** — continuous weight reduction (``weight *= factor``)
   with optional concurrency / timeout limits. Reversible on recovery.
5. **Terminate (终止)** — stronger than drain: marks ``disabled=True``
   so the dispatch path skips the agent entirely. Audited, requires
   manual review to restore.
6. **Upgrade (升级)** — register a new version, switch traffic (full
   cut in v1; weighted rollout deferred), auto-rollback on regression.

Backward compatibility
----------------------
When no Supervisor is configured (``get_supervisor() is None``), the
Engine and LoopExecutor integration points take the ``None`` branch and
behave exactly as before. The Supervisor inherits all passive detector
APIs (``record_result`` / ``get_weight`` / ``get_stats`` / drain /
recovery), so existing scheduling-path callers are zero-change.

References
----------
- docs/design-supervisor-agent.md (full design)
- docs/design-debate-agent.md §2.2.5 (adjudicate interface)

Implementation note
-------------------
The code below was physically split (for size) into sibling modules —
``supervisor_models`` / ``supervisor_rules`` / ``supervisor_health`` and
four mixins (``supervisor_patrol`` / ``supervisor_action`` /
``supervisor_dispatch`` / ``supervisor_status``). This module remains the
single backward-compatible import surface: it assembles the mixins into
the :class:`Supervisor` class and re-exports every public symbol.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections import deque
from typing import Any

from maop.core.observability.metrics import get_metrics
from maop.core.scheduling.failure_detector import FailurePatternDetector
from maop.core.scheduling.supervisor_action import SupervisorActionMixin
from maop.core.scheduling.supervisor_dispatch import SupervisorDispatchMixin
from maop.core.scheduling.supervisor_health import HealthChecker
from maop.core.scheduling.supervisor_models import (
    _DEFAULT_PATROL_CONCURRENCY,
    _EVOLUTION_TRIGGER_COOLDOWN_S,
    _MAX_ACTION_HISTORY,
    _MAX_PENDING_ALERTS,
    M_SUPERVISOR_ACTIONS_TOTAL,
    M_SUPERVISOR_PATROL_AGENTS,
    M_SUPERVISOR_PATROL_DURATION,
    M_SUPERVISOR_PATROL_ISSUES,
    ActionRecord,
    AgentOperationalStatus,
    AlertLevel,
    DispatchDecision,
    HealthProbe,
    SupervisorAction,
    SupervisorActionRequest,
    SupervisorRule,
    TerminateRefusedError,
    _SupervisorAgentState,
)
from maop.core.scheduling.supervisor_patrol import SupervisorPatrolMixin
from maop.core.scheduling.supervisor_rules import RuleEngine, default_rules
from maop.core.scheduling.supervisor_status import SupervisorStatusMixin

logger = logging.getLogger(__name__)


class Supervisor(
    SupervisorStatusMixin,
    SupervisorDispatchMixin,
    SupervisorActionMixin,
    SupervisorPatrolMixin,
    FailurePatternDetector,
):
    """Proactive supervisor: passive detector + patrol + 6 control actions.

    Architecture layers
    -------------------
    - **Passive (inherited)**: ``record_result`` / ``get_weight`` /
      ``get_stats`` / drain / recovery — unchanged.
    - **Proactive (new)**: ``patrol`` loop / ``warn`` / ``replace`` /
      ``degrade`` / ``terminate`` / ``upgrade``.
    - **Adjudication (new)**: ``adjudicate`` — called by the debate
      orchestrator on stalemate (lazy, no-op when debate is unused).

    Parameters
    ----------
    health_checker : HealthChecker | None
        Probe executor. When None, a default is constructed.
    rules : list[SupervisorRule] | None
        Supervision rules. When None, the built-in default set is used.
    patrol_interval_s : float
        Patrol loop period (default 60s).
    patrol_strategy : str
        Patrol strategy: "full" (v1 only; "sample" / "adaptive" log a
        warning and degrade to "full" per [F-4]).
    patrol_timeout_s : float
        Per-agent probe timeout (default 10s).
    patrol_concurrency : int
        Max parallel probes per patrol round (default 10).
    evolution_cooldown_s : float
        Cooldown for evolution-trigger suggestions (default 600s).
    **kwargs : Any
        Forwarded to FailurePatternDetector (window_size, etc.).
    """

    def __init__(
        self,
        *,
        health_checker: HealthChecker | None = None,
        rules: list[SupervisorRule] | None = None,
        patrol_interval_s: float = 60.0,
        patrol_strategy: str = "full",
        patrol_timeout_s: float = 10.0,
        patrol_concurrency: int = _DEFAULT_PATROL_CONCURRENCY,
        evolution_cooldown_s: float = _EVOLUTION_TRIGGER_COOLDOWN_S,
        agent_registry: Any = None,
        routing_store: Any = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        # Health checker (default uses self as detector for metrics probe).
        self._health_checker = health_checker or HealthChecker(
            detector=self,
            registry=agent_registry,
        )
        # Rule engine.
        self._rule_engine = RuleEngine(rules if rules is not None else default_rules())
        # Patrol config.
        self._patrol_interval_s = float(patrol_interval_s)
        if patrol_strategy not in ("full", "sample", "adaptive"):
            logger.warning(
                "[supervisor] unknown patrol_strategy %r, falling back to 'full'",
                patrol_strategy,
            )
            patrol_strategy = "full"
        if patrol_strategy in ("sample", "adaptive"):
            logger.warning(
                "[supervisor] patrol_strategy %r not yet implemented, "
                "degrading to 'full' (see [F-4])",
                patrol_strategy,
            )
            patrol_strategy = "full"
        self._patrol_strategy = patrol_strategy
        self._patrol_timeout_s = float(patrol_timeout_s)
        self._patrol_concurrency = max(1, int(patrol_concurrency))
        self._evolution_cooldown_s = float(evolution_cooldown_s)
        # External dependencies (lazy / optional).
        self._agent_registry = agent_registry
        self._routing_store = routing_store
        # Patrol loop state.
        self._patrol_task: asyncio.Task[None] | None = None
        self._patrol_stop = asyncio.Event()
        self._last_patrol_at: float = 0.0
        self._last_patrol_duration_s: float = 0.0
        self._patrol_failure_streak: int = 0
        # Per-agent supervisor state (extends _AgentState implicitly).
        self._supervisor_state: dict[str, _SupervisorAgentState] = {}
        # Action history (ring buffer).
        self._actions: deque[ActionRecord] = deque(maxlen=_MAX_ACTION_HISTORY)
        # Pending alerts (ring buffer).
        self._pending_alerts: deque[dict[str, Any]] = deque(maxlen=_MAX_PENDING_ALERTS)
        # Evolution trigger cooldown.
        self._last_evolution_trigger_at: float = 0.0
        # Supervisor-specific metrics.
        self._metrics = get_metrics()
        self._patrol_duration_gauge = self._metrics.collector.gauge(
            M_SUPERVISOR_PATROL_DURATION,
            "Supervisor patrol round duration in seconds",
        )
        self._patrol_agents_gauge = self._metrics.collector.gauge(
            M_SUPERVISOR_PATROL_AGENTS,
            "Number of agents checked in last patrol round",
        )
        self._patrol_issues_gauge = self._metrics.collector.gauge(
            M_SUPERVISOR_PATROL_ISSUES,
            "Number of issues found in last patrol round",
        )
        self._actions_counter = self._metrics.collector.counter(
            M_SUPERVISOR_ACTIONS_TOTAL,
            "Total supervisor control actions executed",
        )
        # Lock guarding supervisor-specific state. RLock so internal
        # helpers (e.g. _get_operational_status) can re-enter while
        # already held by get_retry_strategy / check_before_dispatch.
        self._supervisor_lock = threading.RLock()

    # ── Public config accessors ────────────────────────────────

    @property
    def rules(self) -> list[SupervisorRule]:
        """Return the current rule set."""
        return self._rule_engine.rules

    def set_rules(self, rules: list[SupervisorRule]) -> None:
        """Hot-update the rule set (called by dashboard API)."""
        self._rule_engine.set_rules(rules)
        logger.info("[supervisor] rule set updated (%d rules)", len(rules))


# ── Re-exports for backward compatibility ─────────────────────
# The symbols below are imported at the top of this module both to build
# the Supervisor class and to keep the historical import surface intact:
#
#     from maop.core.scheduling.supervisor import (
#         Supervisor, SupervisorRule, HealthProbe, DispatchDecision,
#         SupervisorActionRequest, RuleEngine, HealthChecker, default_rules,
#         TerminateRefusedError, ActionRecord, SupervisorAction, AlertLevel,
#         AgentOperationalStatus,
#     )
#
# ``_AgentState`` and ``_SupervisorAgentState`` are also available (used
# internally by the mixins); ``_AgentState`` is re-exported from
# ``failure_detector`` via the top import above for mypy resolution.

__all__ = [
    "ActionRecord",
    "AgentOperationalStatus",
    "AlertLevel",
    "DispatchDecision",
    "HealthChecker",
    "HealthProbe",
    "RuleEngine",
    "Supervisor",
    "SupervisorAction",
    "SupervisorActionRequest",
    "SupervisorRule",
    "TerminateRefusedError",
    "default_rules",
]