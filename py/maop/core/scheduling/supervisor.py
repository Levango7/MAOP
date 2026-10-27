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
from maop.core.scheduling.failure_detector import (
    FailurePatternDetector,
    _AgentState,
    get_failure_detector,
)
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
):
    """Proactive supervisor: passive detector + patrol + 6 control actions.

    Architecture layers
    -------------------
    - **Passive (composed via ``self._detector``)**: ``record_result`` /
      ``get_weight`` / ``get_stats`` / drain / recovery — unchanged,
      delegated to the composed :class:`FailurePatternDetector`.
    - **Proactive (new)**: ``patrol`` loop / ``warn`` / ``replace`` /
      ``degrade`` / ``terminate`` / ``upgrade``.
    - **Adjudication (new)**: ``adjudicate`` — called by the debate
      orchestrator on stalemate (lazy, no-op when debate is unused).

    组合（阶段2 P2，继承改为组合）
    --------------------------
    ``Supervisor`` **不再继承** ``FailurePatternDetector``，而是持有一个
    被动检测器字段 ``self._detector``，并委派 ``record_result`` /
    ``get_weight`` / ``get_stats`` 等被动检测 API。四个 mixin
   （``supervisor_action`` / ``supervisor_status`` / ``supervisor_patrol`` /
    ``supervisor_dispatch``）不再直接访问 detector 私有属性（`_agents`、
    `_lock`、`_agent_health_locked`），而是通过公共 API 或组合字段访问。

    被动检测器默认由 ``get_failure_detector()`` 提供（进程级单例），也可
    通过构造参数 ``detector`` 注入（便于测试）。

    向后兼容：
    - 被动检测 API（record_result / get_weight / get_stats / get_agent_health）
      在 Supervisor 上直接可用，既有调用方零改动。
    - ``get_supervisor()`` / ``set_supervisor()`` 单例槽位不变（各自独立槽位）。

    Parameters
    ----------
    detector : FailurePatternDetector | None
        被动检测器实例。为 None 时使用 ``get_failure_detector()`` 单例。
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
        Reserved (forward-compatible; no longer forwarded to detector,
        detector is constructed independently).
    """

    def __init__(
        self,
        *,
        detector: FailurePatternDetector | None = None,
        health_checker: HealthChecker | None = None,
        rules: list[SupervisorRule] | None = None,
        patrol_interval_s: float = 60.0,
        patrol_strategy: str = "full",
        patrol_timeout_s: float = 10.0,
        patrol_concurrency: int = _DEFAULT_PATROL_CONCURRENCY,
        evolution_cooldown_s: float = _EVOLUTION_TRIGGER_COOLDOWN_S,
        agent_registry: Any = None,
        routing_store: Any = None,
    ) -> None:
        # Passive detector (composed, not inherited). Defaults to the
        # process-wide failure-detector singleton.
        self._detector: FailurePatternDetector = detector or get_failure_detector()

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

        # ── Detector-config passthrough (for get_supervisor_status) ──
        # 原由 FailurePatternDetector.__init__ 设置的配置字段，现在由
        # Supervisor 直接持有（不再继承 detector），以保持
        # get_supervisor_status() 返回完整 config。
        self._window_size = self._detector._window_size
        self._failure_rate_threshold = self._detector._failure_rate_threshold
        self._timeout_threshold = self._detector._timeout_threshold
        self._recovery_consecutive_successes = (
            self._detector._recovery_consecutive_successes
        )

        # ── Backward-compat proxy for mixin internal access ──────────
        # 四个 mixin 直接访问 detector 私有属性（_agents / _lock /
        # _agent_health_locked / _event_bus）。Supervisor 改为组合后，
        # 将 detector 的这些内部状态代理为 Supervisor 的受保护属性，
        # mixin 无需改动（dict 与锁是共享引用，修改立即生效）。
        self._agents = self._detector._agents
        self._lock = self._detector._lock
        self._agent_health_locked = self._detector._agent_health_locked
        self._event_bus = self._detector._event_bus

    # ── Public config accessors ────────────────────────────────

    @property
    def rules(self) -> list[SupervisorRule]:
        """Return the current rule set."""
        return self._rule_engine.rules

    def set_rules(self, rules: list[SupervisorRule]) -> None:
        """Hot-update the rule set (called by dashboard API)."""
        self._rule_engine.set_rules(rules)
        logger.info("[supervisor] rule set updated (%d rules)", len(rules))

    # ── Passive-detector delegation (向后兼容：调用方零改动) ─────────

    def record_result(
        self, agent_id: str, success: bool, latency: float = 0.0
    ) -> None:
        """Record one task outcome (delegated to composed detector)."""
        self._detector.record_result(agent_id, success=success, latency=latency)

    def get_weight(self, agent_id: str) -> float:
        """Return current scheduling weight (delegated to composed detector)."""
        return self._detector.get_weight(agent_id)

    def get_stats(self) -> dict[str, Any]:
        """Return all-agent health snapshot (delegated to composed detector)."""
        return self._detector.get_stats()

    def get_agent_health(self, agent_id: str):
        """Return single-agent health snapshot (delegated to composed detector)."""
        return self._detector.get_agent_health(agent_id)

    def reset(self, agent_id: str | None = None) -> None:
        """Clear recorded state (delegated to composed detector)."""
        self._detector.reset(agent_id)


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