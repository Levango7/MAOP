"""MAOP Blackboard — Trigger Rule Engine & Convergence Controller.

Extends the baseline ``Controller`` with:

* **Predicate-based** trigger rules (not just domain matching).
* **Priority scheduling** with preemption / yield semantics.
* **Re-entrancy guard** (same KS + same entry is not re-dispatched within a cycle).
* **Convergence detection** — terminate when goal state reached, delta below
  threshold, or stakeholder quiescence.

Design reference: docs/design-blackboard.md §2.1 (state-driven closed loop).

Classes
-------
- ``TriggerRule``: declarative trigger — ``predicate(bb, entry) -> bool``,
  priority, max_dispatches, reentrant.
- ``TriggerRuleEngine``: matches rules against blackboard entries.
- ``ConvergenceCriterion``: pluggable termination condition.
- ``BlackboardConvergenceController``: extends ``Controller`` with rule engine
  and convergence checking.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

from maop.core.reliability.blackboard import (
    Blackboard,
    BlackboardEntry,
    KnowledgeSource,
)

logger = logging.getLogger(__name__)

# ── Type Aliases ──────────────────────────────────────────────────

# A predicate receives the full blackboard + entry and decides whether to fire.
PredicateFn = Callable[
    [Blackboard, BlackboardEntry], Awaitable[bool] | bool
]


# ── TriggerRule ───────────────────────────────────────────────────


@dataclass
class TriggerRule:
    """Declarative trigger rule.

    Parameters
    ----------
    name : str
        Unique rule name (used in trace/logs).
    ks : KnowledgeSource
        The knowledge source to dispatch when the rule fires.
    predicate : PredicateFn
        Receives ``(blackboard, entry)`` and returns ``True`` to fire.
        May be sync or async.
    priority : int
        Higher = dispatched first. Default 0.
    max_dispatches : int
        Maximum times this rule may fire per solve lifecycle.
        ``-1`` means unlimited. Default ``-1``.
    reentrant : bool
        If ``False`` (default), the same rule will not fire twice on the
        *same entry* within one cycle.
    enabled : bool
        Temporarily disable without unregistering.
    """

    name: str
    ks: KnowledgeSource
    predicate: PredicateFn
    priority: int = 0
    max_dispatches: int = -1
    reentrant: bool = False
    enabled: bool = True
    _dispatch_count: int = field(default=0, repr=False)
    _seen_entries: set[str] = field(default_factory=set, repr=False)

    def mark_dispatched(self, entry_id: str) -> None:
        """Record a dispatch for quota tracking."""
        self._dispatch_count += 1
        self._seen_entries.add(entry_id)

    @property
    def exhausted(self) -> bool:
        if self.max_dispatches < 0:
            return False
        return self._dispatch_count >= self.max_dispatches

    def would_fire_on(self, entry: BlackboardEntry) -> bool:
        """Quick re-entrancy check without evaluating the predicate."""
        if not self.reentrant and entry.entry_id in self._seen_entries:
            return False
        return not self.exhausted

    def reset(self) -> None:
        """Reset dispatch counters for a new solve."""
        self._dispatch_count = 0
        self._seen_entries.clear()


# ── ConvergenceCriterion ──────────────────────────────────────────


class ConvergenceCriterion(Protocol):
    """Protocol for termination conditions."""

    async def is_satisfied(self, bb: Blackboard, trace: list[dict]) -> bool: ...
    def describe(self) -> str: ...


@dataclass
class GoalStateCriterion:
    """Terminate when an entry with the given domain/state appears."""

    domain: str
    state_value: Any = "done"
    metadata_key: str = "state"
    _satisfied: bool = field(default=False, repr=False)

    async def is_satisfied(self, bb: Blackboard, trace: list[dict]) -> bool:
        if self._satisfied:
            return True
        for entry in bb.read(self.domain):
            state = entry.metadata.get(self.metadata_key)
            if state == self.state_value:
                self._satisfied = True
                return True
        return False

    def describe(self) -> str:
        return (
            f"GoalState(domain={self.domain!r}, "
            f"{self.metadata_key}={self.state_value!r})"
        )

    def reset(self) -> None:
        self._satisfied = False


@dataclass
class MaxIterationsCriterion:
    """Terminate after N controller iterations."""

    max_iter: int
    _counter: int = field(default=0, repr=False)

    async def is_satisfied(self, bb: Blackboard, trace: list[dict]) -> bool:
        return self._counter >= self.max_iter

    def describe(self) -> str:
        return f"MaxIterations({self.max_iter})"

    def bump(self) -> None:
        self._counter += 1

    def reset(self) -> None:
        self._counter = 0


@dataclass
class ConfidenceThresholdCriterion:
    """Terminate when the best confidence in a domain exceeds threshold."""

    domain: str
    threshold: float = 0.95
    _satisfied: bool = field(default=False, repr=False)

    async def is_satisfied(self, bb: Blackboard, trace: list[dict]) -> bool:
        if self._satisfied:
            return True
        best = max(
            (e.confidence for e in bb.read(self.domain)),
            default=0.0,
        )
        if best >= self.threshold:
            self._satisfied = True
            return True
        return False

    def describe(self) -> str:
        return f"ConfidenceThreshold(domain={self.domain!r}, threshold={self.threshold})"

    def reset(self) -> None:
        self._satisfied = False


@dataclass
class QuiescenceCriterion:
    """Terminate when no rule fires for ``n`` consecutive steps."""

    quiet_steps: int = 3
    _consecutive_quiet: int = field(default=0, repr=False)

    async def is_satisfied(self, bb: Blackboard, trace: list[dict]) -> bool:
        return self._consecutive_quiet >= self.quiet_steps

    def describe(self) -> str:
        return f"Quiescence({self.quiet_steps} quiet steps)"

    def record_step(self, fired: int) -> None:
        if fired == 0:
            self._consecutive_quiet += 1
        else:
            self._consecutive_quiet = 0

    def reset(self) -> None:
        self._consecutive_quiet = 0


# ── TriggerRuleEngine ─────────────────────────────────────────────


class TriggerRuleEngine:
    """Evaluates trigger rules against blackboard entries.

    Maintains a sorted list of rules (by priority desc) and dispatches
    matching KnowledgeSources with concurrency control.
    """

    def __init__(self, max_concurrent: int = 4) -> None:
        self._rules: list[TriggerRule] = []
        self._max_concurrent = max_concurrent
        self._sem = asyncio.Semaphore(max_concurrent)
        self._lock = threading.RLock()

    @property
    def rules(self) -> list[TriggerRule]:
        with self._lock:
            return list(self._rules)

    def register(self, rule: TriggerRule) -> None:
        """Register a trigger rule. Kept sorted by priority desc."""
        with self._lock:
            self._rules.append(rule)
            self._rules.sort(key=lambda r: r.priority, reverse=True)
        logger.debug(
            "[bb_trigger] Registered rule %s (priority=%d)",
            rule.name,
            rule.priority,
        )

    def unregister(self, name: str) -> bool:
        """Remove a rule by name. Returns ``True`` if found."""
        with self._lock:
            n = len(self._rules)
            self._rules = [r for r in self._rules if r.name != name]
            return len(self._rules) < n

    def reset_all(self) -> None:
        with self._lock:
            for r in self._rules:
                r.reset()

    async def evaluate(
        self,
        bb: Blackboard,
        entry: BlackboardEntry,
    ) -> list[tuple[TriggerRule, KnowledgeSource]]:
        """Evaluate all rules against *entry*; return matched (rule, ks) pairs.

        Calls the ``predicate`` and applies re-entrancy / dispatch-quota
        checks. Results are already sorted by rule priority.
        """
        matched: list[tuple[TriggerRule, KnowledgeSource]] = []
        rules = self.rules  # snapshot
        for rule in rules:
            if not rule.enabled:
                continue
            if not rule.would_fire_on(entry):
                continue
            try:
                result = rule.predicate(bb, entry)
                if asyncio.iscoroutine(result):
                    result = await result
            except Exception as exc:
                logger.error(
                    "[bb_trigger] rule %s predicate error: %s",
                    rule.name, exc,
                )
                continue
            if result:
                matched.append((rule, rule.ks))
        return matched

    @property
    def max_concurrent(self) -> int:
        return self._max_concurrent

    @property
    def semaphore(self) -> asyncio.Semaphore:
        return self._sem


# ── BlackboardConvergenceController ───────────────────────────────


class BlackboardConvergenceController:
    """Advanced controller that wires ``TriggerRuleEngine`` + convergence.

    Drop-in replacement for the baseline ``Controller`` when you need:

    * predicate-based trigger rules (not just domain matching)
    * pluggable convergence criteria
    * re-entrancy guards and per-rule dispatch quotas

    Usage::

        bb = Blackboard()
        ctrl = BlackboardConvergenceController(bb, max_iterations=50)
        ctrl.add_convergence(GoalStateCriterion("decision", "finalized"))
        ctrl.register_rule(TriggerRule(
            name="on_evidence",
            ks=analyzer_ks,
            predicate=lambda bb, e: e.domain == "evidence",
            priority=10,
        ))
        await ctrl.run_until_convergence()
    """

    def __init__(
        self,
        bb: Blackboard,
        *,
        max_iterations: int = 100,
        max_concurrent: int = 4,
        cycle_delay: float = 0.0,
    ) -> None:
        self._bb = bb
        self._engine = TriggerRuleEngine(max_concurrent=max_concurrent)
        self._convergence: list[ConvergenceCriterion] = []
        self._max_iterations = max_iterations
        self._cycle_delay = cycle_delay
        self._iteration = 0
        self._running = False
        self._trace: list[dict[str, Any]] = []
        self._lock = threading.RLock()
        self._solve_id: str = ""

    # ── Rule management (delegates to engine) ──────────────────

    def register_rule(self, rule: TriggerRule) -> None:
        self._engine.register(rule)

    def unregister_rule(self, name: str) -> bool:
        return self._engine.unregister(name)

    @property
    def rules(self) -> list[TriggerRule]:
        return self._engine.rules

    # ── Convergence management ─────────────────────────────────

    def add_convergence(self, criterion: ConvergenceCriterion) -> None:
        self._convergence.append(criterion)

    @property
    def convergence_criteria(self) -> list[ConvergenceCriterion]:
        return list(self._convergence)

    # ── Snapshot / trace ───────────────────────────────────────

    @property
    def solve_id(self) -> str:
        return self._solve_id

    @property
    def iteration(self) -> int:
        return self._iteration

    @property
    def is_running(self) -> bool:
        return self._running

    def get_trace(self, limit: int = 200) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._trace[-limit:])

    # ── Solve lifecycle ─────────────────────────────────────────

    async def run_until_convergence(self) -> dict[str, Any]:
        """Run until any convergence criterion is satisfied or max_iter hit.

        Returns a solve report dict with ``converged``, ``iterations``,
        ``reason``, ``trace_len``.
        """
        self._solve_id = uuid.uuid4().hex[:12]
        self._running = True
        self._iteration = 0
        self._trace.clear()
        self._engine.reset_all()
        for c in self._convergence:
            if hasattr(c, "reset"):
                c.reset()

        reason = "max_iterations"
        converged = False

        try:
            while self._iteration < self._max_iterations and self._running:
                # Yield control so external stop() can take effect
                await asyncio.sleep(0)
                # Check convergence before dispatching
                if await self._check_convergence():
                    reason = "convergence"
                    converged = True
                    break

                fired = await self._cycle()
                # Notify quiescence criteria
                for c in self._convergence:
                    if isinstance(c, QuiescenceCriterion):
                        c.record_step(fired)
                # Notify iteration criteria
                for c in self._convergence:
                    if isinstance(c, MaxIterationsCriterion):
                        c.bump()

                if self._cycle_delay > 0:
                    await asyncio.sleep(self._cycle_delay)

            # After loop — one final convergence check
            if not converged and await self._check_convergence():
                reason = "convergence"
                converged = True
        finally:
            self._running = False

        return {
            "solve_id": self._solve_id,
            "converged": converged,
            "iterations": self._iteration,
            "reason": reason,
            "trace_len": len(self._trace),
        }

    async def _check_convergence(self) -> bool:
        for c in self._convergence:
            try:
                if await c.is_satisfied(self._bb, self._trace):
                    logger.info(
                        "[bb_converge] %s satisfied (%s)",
                        type(c).__name__,
                        c.describe(),
                    )
                    return True
            except Exception as exc:
                logger.error(
                    "[bb_converge] criterion %s error: %s",
                    type(c).__name__, exc,
                )
        return False

    async def _cycle(self) -> int:
        """Evaluate every pending blackboard entry against rules and dispatch."""
        # Allow stop() to interrupt before starting a new cycle
        if not self._running:
            return 0
        # Snapshot all entries across all domains
        domains = self._bb.get_domains()
        entries: list[BlackboardEntry] = []
        for d in domains:
            entries.extend(self._bb.read(d))

        # Collect all (rule, entry) pairs
        tasks: list[tuple[TriggerRule, BlackboardEntry, KnowledgeSource]] = []
        seen: set[str] = set()
        for entry in entries:
            pairs = await self._engine.evaluate(self._bb, entry)
            for rule, ks in pairs:
                key = f"{rule.name}:{entry.entry_id}"
                if key in seen:
                    continue
                seen.add(key)
                tasks.append((rule, entry, ks))

        fired = 0
        sem = self._engine.semaphore

        async def _dispatch(rule: TriggerRule, entry: BlackboardEntry, ks: KnowledgeSource) -> None:
            nonlocal fired
            async with sem:
                try:
                    await ks.execute(self._bb, entry)
                    rule.mark_dispatched(entry.entry_id)
                    fired += 1
                    with self._lock:
                        self._trace.append({
                            "solve_id": self._solve_id,
                            "ks": ks.name,
                            "rule": rule.name,
                            "entry_id": entry.entry_id,
                            "domain": entry.domain,
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                            "status": "ok",
                        })
                except Exception as exc:
                    logger.error(
                        "[bb_converge] KS %s via rule %s failed: %s",
                        ks.name, rule.name, exc,
                    )
                    with self._lock:
                        self._trace.append({
                            "solve_id": self._solve_id,
                            "ks": ks.name,
                            "rule": rule.name,
                            "entry_id": entry.entry_id,
                            "domain": entry.domain,
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                            "status": "error",
                            "error": str(exc),
                        })

        if tasks:
            # Yield before gather so a pending stop() can interrupt
            await asyncio.sleep(0)
            if not self._running:
                return fired
            await asyncio.gather(
                *[_dispatch(r, e, k) for r, e, k in tasks],
                return_exceptions=True,
            )

        self._iteration += 1
        return fired

    def stop(self) -> None:
        """Signal the controller to stop at the end of current cycle."""
        self._running = False
