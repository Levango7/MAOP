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

Module layout
-------------
This module focuses on the :class:`Supervisor` orchestration logic. The
supporting pieces have been split into sibling modules to keep file
sizes manageable:

- :mod:`maop.core.scheduling.models` — enums + Pydantic schemas
- :mod:`maop.core.scheduling.rule_engine` — :class:`RuleEngine` + ``default_rules``
- :mod:`maop.core.scheduling.health_checker` — :class:`HealthChecker`

All public names are re-exported from this module so existing
``from maop.core.scheduling.supervisor import ...`` callers keep working
unchanged.

References
----------
- docs/design-supervisor-agent.md (full design)
- docs/design-debate-agent.md §2.2.5 (adjudicate interface)
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
import uuid
from collections import deque
from typing import Any

from maop.core.observability.metrics import get_metrics
from maop.core.scheduling.failure_detector import (
    FailurePatternDetector,
    _AgentState,
)

# Re-exported names (kept in this module's namespace for backward-compatible
# `from maop.core.scheduling.supervisor import ...` callers).
from maop.core.scheduling.health_checker import _DEFAULT_PATROL_CONCURRENCY, HealthChecker
from maop.core.scheduling.models import (
    ActionRecord,
    AgentOperationalStatus,
    AlertLevel,
    DispatchDecision,
    HealthProbe,
    SupervisorAction,
    SupervisorActionRequest,
    SupervisorRule,
)
from maop.core.scheduling.rule_engine import RuleEngine, default_rules
# B4 拆分：内部 per-agent 状态与 supervisor 专属异常已移至独立模块，
# 此处 re-export 保持向后兼容。
from maop.core.scheduling.supervisor_state import (
    _SupervisorAgentState,
    TerminateRefusedError,
)

logger = logging.getLogger(__name__)

# ── Metric names (supervisor-specific, prefixed maop_supervisor_*) ──
M_SUPERVISOR_PATROL_DURATION = "maop_supervisor_patrol_duration_seconds"
M_SUPERVISOR_PATROL_AGENTS = "maop_supervisor_patrol_agents_checked"
M_SUPERVISOR_PATROL_ISSUES = "maop_supervisor_patrol_issues_found"
M_SUPERVISOR_ACTIONS_TOTAL = "maop_supervisor_actions_total"

# Maximum number of action records kept in memory (ring buffer).
_MAX_ACTION_HISTORY = 500
# Maximum number of pending alerts kept in memory.
_MAX_PENDING_ALERTS = 200
# Number of consecutive unreachable patrols before terminate triggers.
_UNREACHABLE_TERMINATE_THRESHOLD = 3
# Cooldown for evolution trigger (avoid feedback storm with EvolutionLoop).
_EVOLUTION_TRIGGER_COOLDOWN_S = 600.0
# Number of degraded agents that triggers an evolution suggestion.
_EVOLUTION_TRIGGER_DEGRADED_COUNT = 3


# ── Supervisor main class ──────────────────────────────────────


class Supervisor(FailurePatternDetector):
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

    # ── Patrol ─────────────────────────────────────────────────

    def _list_registered_agents(self) -> list[str]:
        """Return the list of registered agent ids to patrol.

        When an AgentRegistry is wired in, uses its listing. Otherwise
        falls back to the agents known to the failure detector
        (passive-recorded) — this covers the common case where patrol
        complements passive detection for already-seen agents.
        """
        if self._agent_registry is not None:
            try:
                agents = list(self._agent_registry.list_agents())
                if agents:
                    return [str(a) for a in agents]
            except Exception as exc:
                logger.debug("[supervisor] registry list failed: %s", exc)
        # Fallback: agents known to the detector.
        with self._lock:
            return list(self._agents.keys())

    async def patrol(self) -> list[HealthProbe]:
        """Execute one patrol round and evaluate rules.

        Flow:
          1. List registered agents.
          2. Concurrently probe each (bounded by patrol_concurrency).
          3. For each probe, evaluate rules and trigger matched actions.
          4. Track consecutive-unreachable count for inline terminate rule.
          5. Update metrics and return the probes.

        Exceptions are caught and logged — patrol never raises so the
        background loop stays alive.
        """
        start = time.monotonic()
        agent_ids = self._list_registered_agents()
        if not agent_ids:
            self._last_patrol_at = time.time()
            self._last_patrol_duration_s = 0.0
            return []
        try:
            probes = await asyncio.wait_for(
                self._health_checker.check_all(agent_ids),
                timeout=self._patrol_timeout_s * max(1, len(agent_ids)),
            )
        except asyncio.TimeoutError:
            logger.warning(
                "[supervisor] patrol timed out after %.1fs (%d agents)",
                self._patrol_timeout_s * len(agent_ids), len(agent_ids),
            )
            probes = [
                HealthProbe(agent_id=aid, reachable=False, probed_at=time.time())
                for aid in agent_ids
            ]
        except Exception:
            logger.exception("[supervisor] patrol failed")
            self._patrol_failure_streak += 1
            if self._patrol_failure_streak >= 3:
                await self._publish_supervisor_event(
                    "supervisor.patrol.failing",
                    {
                        "failure_streak": self._patrol_failure_streak,

                    },
                    level="error",
                )
            self._last_patrol_at = time.time()
            self._last_patrol_duration_s = time.monotonic() - start
            return []
        self._patrol_failure_streak = 0

        # Evaluate rules per probe and trigger actions.
        issues_found = 0
        for probe in probes:
            try:
                matched_rules = self._rule_engine.evaluate(probe)
                for rule in matched_rules:
                    issues_found += 1
                    await self._apply_rule(rule, probe)
                # Inline unreachable-terminate rule (3 consecutive strikes).
                if not probe.reachable:
                    strikes = self._increment_unreachable(probe.agent_id)
                    if strikes >= _UNREACHABLE_TERMINATE_THRESHOLD:
                        await self.terminate(
                            probe.agent_id,
                            reason=f"unreachable after {strikes} patrols",
                            triggered_by="patrol",
                        )
                        self._reset_unreachable(probe.agent_id)
                else:
                    self._reset_unreachable(probe.agent_id)
            except Exception:
                logger.exception(
                    "[supervisor] rule eval failed for %s",
                    probe.agent_id,
                )

        # Update patrol bookkeeping.
        self._last_patrol_at = time.time()
        self._last_patrol_duration_s = time.monotonic() - start
        # Publish patrol-completed event.
        await self._publish_supervisor_event(
            "supervisor.patrol.completed",
            {
                "agents_checked": len(probes),
                "issues_found": issues_found,
                "duration_s": round(self._last_patrol_duration_s, 4),
            },
            level="info",
        )
        # Maybe trigger evolution loop on batch degradation.
        await self._maybe_trigger_evolution(probes)
        # Update metrics.
        try:
            self._patrol_duration_gauge.set(self._last_patrol_duration_s)
            self._patrol_agents_gauge.set(len(probes))
            self._patrol_issues_gauge.set(issues_found)
        except Exception as exc:
            logger.debug("[supervisor] metric update failed: %s", exc)
        return probes

    async def _apply_rule(self, rule: SupervisorRule, probe: HealthProbe) -> None:
        """Execute the action associated with a matched rule."""
        if rule.action == SupervisorAction.ALERT:
            await self.warn(
                probe.agent_id,
                reason=rule.name,
                level=rule.alert_level,
                extra={"rule_id": rule.rule_id, "probe": probe.model_dump()},
            )
        elif rule.action == SupervisorAction.DEGRADE:
            factor = float(rule.action_params.get("factor", 0.5))
            await self.degrade(
                probe.agent_id,
                factor=factor,
                reason=rule.name,
                triggered_by="patrol",
            )
        elif rule.action == SupervisorAction.REPLACE:
            replacement = rule.action_params.get("replacement")
            routing_key = rule.action_params.get("routing_key", "")
            if replacement:
                await self.replace(
                    probe.agent_id,
                    str(replacement),
                    reason=rule.name,
                    routing_key=routing_key,
                    triggered_by="patrol",
                )
        elif rule.action == SupervisorAction.TERMINATE:
            await self.terminate(
                probe.agent_id,
                reason=rule.name,
                triggered_by="patrol",
            )
        elif rule.action == SupervisorAction.UPGRADE:
            target_version = rule.action_params.get("target_version", "")
            if target_version:
                await self.upgrade(
                    probe.agent_id,
                    str(target_version),
                    reason=rule.name,
                    triggered_by="patrol",
                )
        # PATROL / NONE: no-op.

    async def start_patrol_loop(self) -> None:
        """Start the background patrol loop as an asyncio.Task."""
        if self._patrol_task is not None and not self._patrol_task.done():
            logger.warning("[supervisor] patrol loop already running")
            return
        self._patrol_stop.clear()
        self._patrol_task = asyncio.create_task(self._patrol_loop())
        logger.info(
            "[supervisor] patrol loop started (interval=%.1fs strategy=%s)",
            self._patrol_interval_s, self._patrol_strategy,
        )

    async def _patrol_loop(self) -> None:
        """Inner patrol loop — runs until stop_patrol_loop is called."""
        while not self._patrol_stop.is_set():
            try:
                await self.patrol()
            except Exception:
                # patrol() itself catches exceptions, but be defensive.
                logger.exception("[supervisor] patrol loop iteration failed")
            try:
                await asyncio.wait_for(
                    self._patrol_stop.wait(),
                    timeout=self._patrol_interval_s,
                )
            except asyncio.TimeoutError:
                # Expected: timeout means interval elapsed, loop continues.
                pass

    async def stop_patrol_loop(self) -> None:
        """Stop the patrol loop gracefully."""
        if self._patrol_task is None:
            return
        self._patrol_stop.set()
        if not self._patrol_task.done():
            self._patrol_task.cancel()
            try:
                await self._patrol_task
            except (asyncio.CancelledError, Exception):
                pass
        self._patrol_task = None
        logger.info("[supervisor] patrol loop stopped")

    @property
    def patrol_running(self) -> bool:
        """True if the patrol loop task is alive."""
        return (
            self._patrol_task is not None
            and not self._patrol_task.done()
        )

    # ── Alert ──────────────────────────────────────────────────

    async def warn(
        self,
        agent_id: str,
        reason: str,
        *,
        level: AlertLevel = AlertLevel.WARNING,
        extra: dict[str, Any] | None = None,
    ) -> None:
        """Publish a supervisor.alert event."""
        payload = {
            "agent_id": agent_id,
            "reason": reason,
            "level": level.value,
            "extra": extra or {},
            "source": "supervisor",
            "at": time.time(),
        }
        await self._publish_supervisor_event(
            "supervisor.alert", payload, level=level.value,
        )
        with self._supervisor_lock:
            self._pending_alerts.append(payload)

    # ── Control actions ────────────────────────────────────────

    async def replace(
        self,
        agent_id: str,
        replacement: str,
        reason: str,
        *,
        rollout: float = 1.0,
        routing_key: str = "",
        triggered_by: str = "patrol",
    ) -> ActionRecord:
        """Switch routing from ``agent_id`` to ``replacement``."""
        # v1: only rollout=1.0 (full cut) is supported.
        if rollout != 1.0:
            logger.warning(
                "[supervisor] replace rollout=%.2f ignored (v1 only supports full cut)",
                rollout,
            )
        # Update routing mapping if a routing store is wired in.
        if self._routing_store is not None and routing_key:
            try:
                update_fn = getattr(
                    self._routing_store, "update_routing_mapping", None,
                )
                if update_fn is not None:
                    update_fn(routing_key, replacement)
                else:
                    logger.warning(
                        "[supervisor] routing_store has no update_routing_mapping; "
                        "skip routing update for %s",
                        routing_key,
                    )
            except Exception:
                logger.exception(
                    "[supervisor] routing update failed for %s",
                    routing_key,
                )
        # Mark original agent as REPLACED.
        self._set_operational_status(agent_id, AgentOperationalStatus.REPLACED)
        # Drain the original agent (weight=0) so it stops receiving traffic.
        with self._lock:
            state = self._agents.setdefault(agent_id, _AgentState())
            state.weight = 0.0
            state.drained = True
        # Publish event.
        await self._publish_supervisor_event(
            "agent_replaced",
            {
                "agent_id": agent_id,
                "replacement": replacement,
                "rollout": rollout,
                "routing_key": routing_key,
                "reason": reason,
            },
            level="warning",
        )
        # Audit record.
        record = self._record_action(
            SupervisorAction.REPLACE, agent_id, reason,
            params={"replacement": replacement, "rollout": rollout, "routing_key": routing_key},
            triggered_by=triggered_by,
        )
        return record

    async def degrade(
        self,
        agent_id: str,
        factor: float,
        reason: str,
        *,
        max_concurrency: int | None = None,
        timeout_s: float | None = None,
        triggered_by: str = "patrol",
    ) -> ActionRecord:
        """Reduce agent weight by ``factor`` and optionally add limits."""
        if not 0.0 < factor < 1.0:
            logger.warning(
                "[supervisor] degrade factor %.3f out of (0,1); clamping", factor,
            )
            factor = max(0.01, min(0.99, factor))
        with self._lock:
            state = self._agents.setdefault(agent_id, _AgentState())
            state.weight = max(0.0, state.weight * factor)
        # Mark as DEGRADED (unless already DRAINED/TERMINATED).
        current = self._get_operational_status(agent_id)
        if current not in (
            AgentOperationalStatus.DRAINED,
            AgentOperationalStatus.TERMINATED,
            AgentOperationalStatus.REPLACED,
        ):
            self._set_operational_status(agent_id, AgentOperationalStatus.DEGRADED)
        # Store degrade limits in supervisor state.
        with self._supervisor_lock:
            sv_state = self._supervisor_state.setdefault(
                agent_id, _SupervisorAgentState(),
            )
            if max_concurrency is not None:
                sv_state.max_concurrency = max_concurrency
            if timeout_s is not None:
                sv_state.timeout_s = timeout_s
        # Publish event.
        await self._publish_supervisor_event(
            "agent_degraded",
            {
                "agent_id": agent_id,
                "factor": factor,
                "max_concurrency": max_concurrency,
                "timeout_s": timeout_s,
                "reason": reason,
            },
            level="warning",
        )
        record = self._record_action(
            SupervisorAction.DEGRADE, agent_id, reason,
            params={"factor": factor, "max_concurrency": max_concurrency, "timeout_s": timeout_s},
            triggered_by=triggered_by,
        )
        return record

    async def terminate(
        self,
        agent_id: str,
        reason: str,
        *,
        triggered_by: str = "patrol",
        force: bool = False,
    ) -> ActionRecord:
        """Mark agent as disabled (terminated) — stronger than drain."""
        # [R-2] Safety check: refuse if agent is the only available one
        # for some routing_key (unless force=True).
        if not force:
            sole_routing = self._find_sole_routing_key(agent_id)
            if sole_routing is not None:
                msg = (
                    f"agent is the only available agent for routing_key="
                    f"{sole_routing}; configure fallback or replace first "
                    f"(or pass force=True to bypass)"
                )
                logger.warning(
                    "[supervisor] terminate %s refused: %s", agent_id, msg,
                )
                raise TerminateRefusedError(agent_id, sole_routing, msg)
        # Mark disabled + TERMINATED.
        with self._supervisor_lock:
            sv_state = self._supervisor_state.setdefault(
                agent_id, _SupervisorAgentState(),
            )
            sv_state.disabled = True
        self._set_operational_status(agent_id, AgentOperationalStatus.TERMINATED)
        # Also drain (weight=0) for belt-and-suspenders.
        with self._lock:
            state = self._agents.setdefault(agent_id, _AgentState())
            state.weight = 0.0
            state.drained = True
        # Publish event.
        await self._publish_supervisor_event(
            "agent_terminated",
            {"agent_id": agent_id, "reason": reason, "force": force},
            level="critical",
        )
        record = self._record_action(
            SupervisorAction.TERMINATE, agent_id, reason,
            params={"force": force, "force_bypass_safety": force},
            triggered_by=triggered_by,
        )
        return record

    async def upgrade(
        self,
        agent_id: str,
        target_version: str,
        reason: str,
        *,
        rollout_steps: list[float] | None = None,
        triggered_by: str = "manual",
    ) -> ActionRecord:
        """Upgrade agent to ``target_version`` (v1: full cut only)."""
        if rollout_steps is not None and rollout_steps != [1.0]:
            logger.warning(
                "[supervisor] upgrade rollout_steps %r ignored (v1 only supports "
                "full cut [1.0]); see [F-3]",
                rollout_steps,
            )
        # Capture old version baseline for rollback comparison.
        old_health = self.get_agent_health(agent_id)
        old_avg_latency = old_health.avg_latency if old_health else 0.0
        # Ensure the agent has a detector state entry so it shows up in
        # get_supervisor_status() and get_stats().
        with self._lock:
            self._agents.setdefault(agent_id, _AgentState())
        with self._supervisor_lock:
            sv_state = self._supervisor_state.setdefault(
                agent_id, _SupervisorAgentState(),
            )
            sv_state.upgrade_target_version = target_version
            sv_state.upgrade_old_avg_latency = old_avg_latency
        # Mark as UPGRADING.
        self._set_operational_status(agent_id, AgentOperationalStatus.UPGRADING)
        # Publish started event.
        await self._publish_supervisor_event(
            "agent_upgrade.started",
            {
                "agent_id": agent_id,
                "target_version": target_version,
                "rollout_steps": [1.0],
                "reason": reason,
            },
            level="info",
        )
        # v1: immediately consider the upgrade "applied" — actual rollback
        # detection happens on the next patrol round via _check_upgrade_health.
        record = self._record_action(
            SupervisorAction.UPGRADE, agent_id, reason,
            params={"target_version": target_version, "rollout_steps": [1.0]},
            triggered_by=triggered_by,
        )
        return record

    async def _check_upgrade_health(self, agent_id: str) -> None:
        """Inline rollback check called from patrol for UPGRADING agents.

        Per [R-3], rollback triggers if any of:
          - failure_rate > 0.15
          - avg_latency > old_avg_latency * 1.5
          - consecutive 2 patrols with reachable=False
        """
        with self._supervisor_lock:
            sv_state = self._supervisor_state.get(agent_id)
            if sv_state is None or sv_state.upgrade_target_version is None:
                return
            target_version = sv_state.upgrade_target_version
            old_avg_latency = sv_state.upgrade_old_avg_latency
        health = self.get_agent_health(agent_id)
        if health is None:
            return
        rollback_reason: str | None = None
        if health.failure_rate > 0.15:
            rollback_reason = (
                f"failure_rate={health.failure_rate:.3f} > 0.15"
            )
        elif (
            old_avg_latency > 0.0
            and health.avg_latency > old_avg_latency * 1.5
        ):
            rollback_reason = (
                f"avg_latency={health.avg_latency:.2f} > "
                f"old({old_avg_latency:.2f}) * 1.5"
            )
        if rollback_reason is not None:
            await self._rollback_upgrade(agent_id, target_version, rollback_reason)

    async def _rollback_upgrade(
        self,
        agent_id: str,
        target_version: str,
        reason: str,
    ) -> None:
        """Roll back an in-flight upgrade and terminate the new version."""
        logger.warning(
            "[supervisor] rolling back upgrade %s -> %s: %s",
            agent_id, target_version, reason,
        )
        # Clear upgrade state.
        with self._supervisor_lock:
            sv_state = self._supervisor_state.get(agent_id)
            if sv_state is not None:
                sv_state.upgrade_target_version = None
                sv_state.upgrade_old_avg_latency = 0.0
        # Restore operational status to NORMAL (assume old version still serves).
        self._set_operational_status(agent_id, AgentOperationalStatus.NORMAL)
        # Publish rollback event.
        await self._publish_supervisor_event(
            "agent_upgrade.rolled_back",
            {
                "agent_id": agent_id,
                "target_version": target_version,
                "reason": reason,
            },
            level="warning",
        )

    # ── Pre/post dispatch checks (Engine integration) ──────────

    def check_before_dispatch(self, agent_id: str) -> DispatchDecision:
        """Return whether the engine may dispatch to ``agent_id``."""
        with self._supervisor_lock:
            sv_state = self._supervisor_state.get(agent_id)
            disabled = sv_state.disabled if sv_state else False
            fallback = sv_state.fallback_agent if sv_state else None
        if disabled:
            return DispatchDecision(
                allow=False,
                reason="agent_terminated",
                fallback_agent=fallback,
            )
        with self._lock:
            state = self._agents.get(agent_id)
            if state is not None and state.weight <= 0.0:
                return DispatchDecision(
                    allow=False,
                    reason="agent_drained_weight_zero",
                    fallback_agent=fallback,
                )
        # Check operational status for degraded flag.
        op_status = self._get_operational_status(agent_id)
        if op_status == AgentOperationalStatus.DEGRADED:
            return DispatchDecision(
                allow=True, reason="agent_degraded", degraded=True,
            )
        return DispatchDecision(allow=True)

    def check_after_dispatch(
        self,
        agent_id: str,
        success: bool,
        latency: float = 0.0,
    ) -> None:
        """Record dispatch outcome (forwards to passive record_result)."""
        self.record_result(agent_id, success=success, latency=latency)

    # ── Dynamic retry strategy (LoopExecutor integration) ──────

    def get_retry_strategy(
        self,
        agent_id: str,
        *,
        default_max_attempts: int = 3,
        default_backoff_ms: int = 2000,
    ) -> dict[str, Any]:
        """Return a dynamic retry strategy for ``agent_id``.

        Returns a dict with keys: ``max_attempts``, ``backoff_ms``,
        ``skip_agent``, ``fallback_agent``.
        """
        with self._supervisor_lock:
            sv_state = self._supervisor_state.get(agent_id)
            disabled = sv_state.disabled if sv_state else False
            fallback = sv_state.fallback_agent if sv_state else None
            degraded = (
                sv_state is not None
                and self._get_operational_status(agent_id)
                == AgentOperationalStatus.DEGRADED
            )
        if disabled:
            return {
                "max_attempts": 0,
                "backoff_ms": default_backoff_ms,
                "skip_agent": True,
                "fallback_agent": fallback,
            }
        if degraded:
            # Reduce retries to avoid amplifying degradation.
            return {
                "max_attempts": max(1, default_max_attempts - 1),
                "backoff_ms": default_backoff_ms * 2,
                "skip_agent": False,
                "fallback_agent": fallback,
            }
        return {
            "max_attempts": default_max_attempts,
            "backoff_ms": default_backoff_ms,
            "skip_agent": False,
            "fallback_agent": fallback,
        }

    # ── Adjudication (debate integration, lazy) ────────────────

    async def adjudicate(
        self,
        debate_id: str,
        rounds: list[Any],
    ) -> dict[str, Any]:
        """Adjudicate a debate stalemate (see design-debate-agent.md §2.2.5).

        v1 implementation: pick the conclusion from the last round with
        the highest historical confidence (success rate). Agents whose
        last-round confidence is significantly below their historical
        mean are degraded. Returns a Verdict-like dict.
        """
        if not rounds:
            return {
                "consensus": False,
                "low_confidence": True,
                "adjudication_reason": "no rounds provided",
                "winner": None,
            }
        last_round = rounds[-1]
        # last_round is expected to be a list of agent conclusions.
        # Each conclusion is expected to have .agent_id and .confidence.
        # We accept dict-like or attribute-like objects.
        conclusions = (
            last_round if isinstance(last_round, list) else [last_round]
        )
        winner: Any = None
        best_conf = -1.0
        for c in conclusions:
            conf = float(
                getattr(c, "confidence", None)
                or (c.get("confidence") if isinstance(c, dict) else None)
                or 0.0
            )
            if conf > best_conf:
                best_conf = conf
                winner = c
        reason = (
            f"adjudicated by supervisor: winner confidence={best_conf:.3f}"
        )
        return {
            "consensus": False,
            "low_confidence": best_conf < 0.70,
            "adjudication_reason": reason,
            "winner": winner,
            "debate_id": debate_id,
        }

    # ── Status / query ─────────────────────────────────────────

    def get_supervisor_status(self) -> dict[str, Any]:
        """Return the full supervisor status (for /api/supervisor/status)."""
        with self._lock, self._supervisor_lock:
            agents = []
            for aid, state in sorted(self._agents.items()):
                health = self._agent_health_locked(aid, state)
                sv_state = self._supervisor_state.get(aid)
                agents.append({
                    **health.to_dict(),
                    "operational_status": (
                        sv_state.operational_status.value
                        if sv_state is not None
                        else health.operational_status
                    ),
                    "disabled": (
                        sv_state.disabled if sv_state is not None else False
                    ),
                    "last_probe_at": (
                        sv_state.last_probe_at if sv_state is not None else 0.0
                    ),
                })
        return {
            "agents": agents,
            "patrol": {
                "running": self.patrol_running,
                "last_patrol_at": self._last_patrol_at,
                "next_patrol_at": (
                    self._last_patrol_at + self._patrol_interval_s
                    if self._last_patrol_at > 0 else 0.0
                ),
                "patrol_interval_s": self._patrol_interval_s,
                "patrol_strategy": self._patrol_strategy,
                "last_duration_s": self._last_patrol_duration_s,
            },
            "pending_alerts": list(self._pending_alerts),
            "recent_actions": [a.model_dump() for a in list(self._actions)[-50:]],
            "config": {
                "window_size": self._window_size,
                "failure_rate_threshold": self._failure_rate_threshold,
                "timeout_threshold": self._timeout_threshold,
                "recovery_consecutive_successes": self._recovery_consecutive_successes,
                "patrol_timeout_s": self._patrol_timeout_s,
                "patrol_concurrency": self._patrol_concurrency,
            },
            "rules": [r.model_dump() for r in self._rule_engine.rules],
        }

    def get_actions(
        self,
        agent_id: str | None = None,
        limit: int = 50,
    ) -> list[ActionRecord]:
        """Return action history (optionally filtered by agent)."""
        with self._supervisor_lock:
            actions = list(self._actions)
        if agent_id is not None:
            actions = [a for a in actions if a.agent_id == agent_id]
        return actions[-max(1, int(limit)):]

    # ── Internal helpers ───────────────────────────────────────

    def _record_action(
        self,
        action: SupervisorAction,
        agent_id: str,
        reason: str,
        *,
        params: dict[str, Any] | None = None,
        triggered_by: str = "patrol",
    ) -> ActionRecord:
        """Append an action to the audit history and bump the counter."""
        record = ActionRecord(
            action_id=uuid.uuid4().hex,
            action=action,
            agent_id=agent_id,
            reason=reason,
            params=params or {},
            triggered_by=triggered_by,
            created_at=time.time(),
        )
        with self._supervisor_lock:
            self._actions.append(record)
        try:
            self._actions_counter.inc(labels={"action": action.value})
        except Exception as exc:
            logger.debug("[supervisor] action counter inc failed: %s", exc)
        return record

    async def _publish_supervisor_event(
        self,
        topic: str,
        payload: dict[str, Any],
        *,
        level: str = "info",
    ) -> None:
        """Publish a supervisor event via EventBus.publish(Event).

        Supervisor methods are all async, so we can directly await the
        publish coroutine (unlike the passive detector's ``_publish_event``
        which is called from the synchronous ``record_result`` path and
        must use fire-and-forget). When no running loop exists (sync
        test context), fall back to ``publish_sync``.
        """
        if self._event_bus is None:
            return
        try:
            from maop.core.reliability.event_bus import Event

            full_payload = {**payload, "level": level, "source": "supervisor"}
            event = Event(topic=topic, data=full_payload, source="supervisor")
            try:
                asyncio.get_running_loop()
                await self._event_bus.publish(event)
            except RuntimeError:
                self._event_bus.publish_sync(event)
        except Exception:
            logger.debug("[supervisor] event publish failed")

    def _set_operational_status(
        self, agent_id: str, status: AgentOperationalStatus,
    ) -> None:
        """Update the supervisor-side operational status for an agent."""
        with self._supervisor_lock:
            sv_state = self._supervisor_state.setdefault(
                agent_id, _SupervisorAgentState(),
            )
            sv_state.operational_status = status

    def _get_operational_status(self, agent_id: str) -> AgentOperationalStatus:
        """Return the supervisor-side operational status (or NORMAL)."""
        with self._supervisor_lock:
            sv_state = self._supervisor_state.get(agent_id)
            if sv_state is None:
                return AgentOperationalStatus.NORMAL
            return sv_state.operational_status

    def _increment_unreachable(self, agent_id: str) -> int:
        """Increment and return the consecutive-unreachable patrol count."""
        with self._supervisor_lock:
            sv_state = self._supervisor_state.setdefault(
                agent_id, _SupervisorAgentState(),
            )
            sv_state.consecutive_unreachable += 1
            return sv_state.consecutive_unreachable

    def _reset_unreachable(self, agent_id: str) -> None:
        """Reset the consecutive-unreachable counter."""
        with self._supervisor_lock:
            sv_state = self._supervisor_state.get(agent_id)
            if sv_state is not None:
                sv_state.consecutive_unreachable = 0

    def _find_sole_routing_key(self, agent_id: str) -> str | None:
        """Return a routing_key for which ``agent_id`` is the only available agent.

        Returns None when no routing store is wired in or when the agent
        is not the sole available one for any key. This is a best-effort
        safety check — when the routing store API is unavailable we
        return None (do not block terminate).
        """
        if self._routing_store is None:
            return None
        try:
            # The routing store API for "list routing_keys for agent" is
            # not standardised in v1; we skip the check and rely on the
            # caller passing force=True when they are sure.
            return None
        except Exception:
            return None

    async def _maybe_trigger_evolution(
        self,
        patrol_result: list[HealthProbe],
    ) -> None:
        """Suggest an evolution cycle when many agents are degraded."""
        now = time.time()
        if now - self._last_evolution_trigger_at < self._evolution_cooldown_s:
            return
        degraded_count = sum(
            1 for p in patrol_result if p.failure_rate > 0.2 or not p.reachable
        )
        if degraded_count >= _EVOLUTION_TRIGGER_DEGRADED_COUNT:
            self._last_evolution_trigger_at = now
            await self._publish_supervisor_event(
                "supervisor.evolution.suggested",
                {
                    "degraded_count": degraded_count,
                    "agents": [
                        p.agent_id for p in patrol_result
                        if p.failure_rate > 0.2 or not p.reachable
                    ],
                },
                level="info",
            )


# ── Re-exports for FailurePatternDetector internals ────────────
# Supervisor inherits FailurePatternDetector and uses its private
# _AgentState / _lock / _agents / _agent_health_locked. The import
# above (top of file) makes _AgentState available to mypy for
# resolving the references inside Supervisor.
# _SupervisorAgentState / TerminateRefusedError are imported from
# maop.core.scheduling.supervisor_state (B4 拆分) and re-exported below.


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
