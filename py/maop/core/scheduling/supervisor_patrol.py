"""Patrol-loop mixin for the Supervisor (巡检).

Provides the background patrol loop and the per-probe rule evaluation
(``_apply_rule``). Methods in this mixin call sibling-mixin methods via
``self.*`` (resolved at runtime on the assembled :class:`Supervisor`
class), so no sibling imports are needed here.
"""

from __future__ import annotations

import asyncio
import logging
import time

from maop.core.scheduling.supervisor_models import (
    _UNREACHABLE_TERMINATE_THRESHOLD,
    HealthProbe,
    SupervisorAction,
    SupervisorRule,
)

logger = logging.getLogger(__name__)


class SupervisorPatrolMixin:
    """Patrol (巡检) capability for the assembled Supervisor class."""

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
                # 任务取消/清理时的预期异常，静默忽略
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