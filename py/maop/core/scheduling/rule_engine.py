"""MAOP Supervisor — rule engine.

Evaluates :class:`~maop.core.scheduling.models.SupervisorRule` conditions
against :class:`~maop.core.scheduling.models.HealthProbe` snapshots and
returns the matched rules in priority order. Extracted from
``supervisor.py`` to isolate the declarative condition-matching logic
from the asyncio-heavy orchestration code.

References
----------
- docs/design-supervisor-agent.md §2.2.5 (default rule set)
"""

from __future__ import annotations

import threading
import time
from typing import Any

from maop.core.scheduling.models import (
    AlertLevel,
    HealthProbe,
    SupervisorAction,
    SupervisorRule,
)

# ── Rule engine ────────────────────────────────────────────────


class RuleEngine:
    """Evaluates :class:`SupervisorRule` conditions against :class:`HealthProbe`.

    A rule's ``condition`` dict supports the following keys (OR-semantics
    at top level; wrap a list under ``"all"`` for AND-semantics):

    - ``failure_rate_gt``: float — failure_rate > value
    - ``avg_latency_gt``: float — avg_latency > value
    - ``timeout_rate_gt``: float — timeout_rate > value
    - ``breaker_open``: bool — breaker_open == value
    - ``reachable``: bool — reachable == value (False detects unresponsive)
    - ``resource_usage_gt``: dict[str, float] — any named resource > threshold
    - ``all``: list[dict] — all sub-conditions must match (AND)
    """

    def __init__(self, rules: list[SupervisorRule] | None = None) -> None:
        self._rules: list[SupervisorRule] = list(rules or [])
        # Sort by priority descending (high priority first).
        self._rules.sort(key=lambda r: r.priority, reverse=True)
        # Cooldown tracking: {(rule_id, agent_id): last_triggered_at}
        self._cooldowns: dict[tuple[str, str], float] = {}
        self._lock = threading.Lock()

    @property
    def rules(self) -> list[SupervisorRule]:
        """Return the current rule set (sorted by priority desc)."""
        return list(self._rules)

    def set_rules(self, rules: list[SupervisorRule]) -> None:
        """Replace the entire rule set (hot-update from API)."""
        with self._lock:
            self._rules = list(rules)
            self._rules.sort(key=lambda r: r.priority, reverse=True)
            self._cooldowns.clear()

    def evaluate(self, probe: HealthProbe) -> list[SupervisorRule]:
        """Return the list of rules that match ``probe`` and are off cooldown.

        Rules are returned in priority order (highest first). A rule is
        skipped if it is disabled, its condition does not match, or it
        is within its cooldown window for this agent.
        """
        now = time.time()
        matched: list[SupervisorRule] = []
        with self._lock:
            for rule in self._rules:
                if not rule.enabled:
                    continue
                if not self._matches(rule.condition, probe):
                    continue
                key = (rule.rule_id, probe.agent_id)
                last = self._cooldowns.get(key, 0.0)
                if now - last < rule.cooldown_s:
                    continue
                matched.append(rule)
                self._cooldowns[key] = now
        return matched

    def _matches(self, condition: dict[str, Any], probe: HealthProbe) -> bool:
        """Evaluate a condition dict against a probe (OR-semantics at top)."""
        if not condition:
            return False
        for key, value in condition.items():
            if key == "all":
                # AND-semantics: every sub-condition must match.
                return all(self._matches(sub, probe) for sub in value)
            if self._matches_one(key, value, probe):
                return True
        return False

    def _matches_one(self, key: str, value: Any, probe: HealthProbe) -> bool:
        """Evaluate a single condition key/value pair."""
        if key == "failure_rate_gt":
            return probe.failure_rate > float(value)
        if key == "avg_latency_gt":
            return probe.avg_latency > float(value)
        if key == "timeout_rate_gt":
            return probe.timeout_rate > float(value)
        if key == "breaker_open":
            return probe.breaker_open == bool(value)
        if key == "reachable":
            return probe.reachable == bool(value)
        if key == "resource_usage_gt":
            # value is dict[str, float]; any named resource exceeds threshold.
            for res_name, threshold in value.items():
                actual = probe.resource_usage.get(res_name)
                if actual is not None and float(actual) > float(threshold):
                    return True
            return False
        # Unknown key — ignore (forward-compat).
        return False


def default_rules() -> list[SupervisorRule]:
    """Return the built-in default rule set (see design §2.2.5)."""
    return [
        SupervisorRule(
            rule_id="rule.failure_rate.warning",
            name="failure-rate-warning",
            description="Alert when window failure rate exceeds 15%",
            action=SupervisorAction.ALERT,
            alert_level=AlertLevel.WARNING,
            condition={"failure_rate_gt": 0.15},
            cooldown_s=60.0,
            priority=10,
        ),
        SupervisorRule(
            rule_id="rule.latency.warning",
            name="latency-warning",
            description="Alert when avg latency exceeds 15s",
            action=SupervisorAction.ALERT,
            alert_level=AlertLevel.WARNING,
            condition={"avg_latency_gt": 15.0},
            cooldown_s=60.0,
            priority=9,
        ),
        SupervisorRule(
            rule_id="rule.latency.degrade",
            name="latency-degrade",
            description="Degrade weight by 0.5 when avg latency exceeds 25s",
            action=SupervisorAction.DEGRADE,
            alert_level=AlertLevel.ERROR,
            condition={"avg_latency_gt": 25.0},
            action_params={"factor": 0.5},
            cooldown_s=120.0,
            priority=8,
        ),
        SupervisorRule(
            rule_id="rule.breaker.open",
            name="breaker-open",
            description="Alert when breaker is open",
            action=SupervisorAction.ALERT,
            alert_level=AlertLevel.ERROR,
            condition={"breaker_open": True},
            cooldown_s=30.0,
            priority=7,
        ),
        SupervisorRule(
            rule_id="rule.timeout.high",
            name="timeout-high",
            description="Degrade weight by 0.7 when timeout rate exceeds 20%",
            action=SupervisorAction.DEGRADE,
            alert_level=AlertLevel.WARNING,
            condition={"timeout_rate_gt": 0.20},
            action_params={"factor": 0.7},
            cooldown_s=90.0,
            priority=6,
        ),
        SupervisorRule(
            rule_id="rule.resource.high",
            name="resource-high",
            description="Alert when CPU>90% or memory>85%",
            action=SupervisorAction.ALERT,
            alert_level=AlertLevel.WARNING,
            condition={
                "resource_usage_gt": {
                    "cpu_percent": 90.0,
                    "memory_percent": 85.0,
                },
            },
            cooldown_s=60.0,
            priority=5,
        ),
        # NOTE: rule.unreachable.terminate is enforced inline in
        # Supervisor.patrol() because it requires consecutive-patrol
        # counting (3 strikes) which is stateful and not expressible
        # as a stateless condition.
    ]


__all__ = [
    "RuleEngine",
    "default_rules",
]