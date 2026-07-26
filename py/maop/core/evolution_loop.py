"""MAOP Evolution Loop — Closed-loop self-evolution orchestrator.

Ties together three existing subsystems into an automated cycle:

  1. ErrorLedger   → detect error hotspots, generate evolution suggestions
  2. SelfHealEngine → attempt auto-repair before escalating
  3. StrategyEngine → evaluate & apply evolution decisions
  4. ThreeLayerMemory → consolidate knowledge, validate improvements

Loop phases:
  OBSERVE  — gather errors from ErrorLedger + episodic stats
  HEAL     — run SelfHealEngine on detected issues
  SUGGEST  — convert unhealed issues into evolution suggestions
  EVALUATE — StrategyEngine decides which suggestions to apply
  APPLY    — ConfigMutator applies approved mutations
  VALIDATE — compare before/after metrics to confirm improvement
  CONSOLIDATE — extract lessons into Semantic Memory

Usage::

    from maop.core.evolution_loop import EvolutionLoop

    loop = EvolutionLoop(root_dir="/path/to/MAOP")
    report = loop.run_cycle()
    print(report.summary())
"""

from __future__ import annotations

import contextlib
import json
import logging
import time
import uuid
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from maop.core.db_utils import get_db_path, sqlite_connect

logger = logging.getLogger(__name__)


class LoopPhase(str, Enum):
    OBSERVE = "observe"
    HEAL = "heal"
    SUGGEST = "suggest"
    EVALUATE = "evaluate"
    APPLY = "apply"
    VALIDATE = "validate"
    CONSOLIDATE = "consolidate"


class PhaseResult(BaseModel):
    phase: LoopPhase
    success: bool = True
    duration_s: float = 0.0
    details: dict[str, Any] = Field(default_factory=dict)
    error: str = ""


class EvolutionSuggestion(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:8])
    source: str = ""
    suggestion_type: str = ""
    severity: str = "MEDIUM"
    description: str = ""
    auto_applicable: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class LoopReport(BaseModel):
    cycle_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    started_at: float = 0.0
    finished_at: float = 0.0
    total_duration_s: float = 0.0
    phases: list[PhaseResult] = Field(default_factory=list)
    errors_observed: int = 0
    heal_attempts: int = 0
    heal_successes: int = 0
    # t10: dry-run mode + rollback support.
    dry_run: bool = False
    snapshot_id: str = ""  # ChangeTracker snapshot taken before APPLY
    rolled_back: bool = False  # True if auto-rollback fired after failed VALIDATE
    suggestions_generated: int = 0
    suggestions_applied: int = 0
    validation_improved: bool = False
    consolidated: int = 0

    def summary(self) -> str:
        return (
            f"EvolutionLoop({self.cycle_id}): "
            f"{self.errors_observed} errors → "
            f"{self.heal_successes}/{self.heal_attempts} healed → "
            f"{self.suggestions_generated} suggestions → "
            f"{self.suggestions_applied} applied → "
            f"improved={self.validation_improved} → "
            f"{self.consolidated} consolidated "
            f"in {self.total_duration_s:.1f}s"
        )


_EVOLUTION_LOOP_DDL = """
CREATE TABLE IF NOT EXISTS evolution_cycles (
    id TEXT PRIMARY KEY,
    started_at REAL NOT NULL,
    finished_at REAL DEFAULT 0,
    total_duration_s REAL DEFAULT 0,
    errors_observed INTEGER DEFAULT 0,
    heal_attempts INTEGER DEFAULT 0,
    heal_successes INTEGER DEFAULT 0,
    suggestions_generated INTEGER DEFAULT 0,
    suggestions_applied INTEGER DEFAULT 0,
    validation_improved INTEGER DEFAULT 0,
    consolidated INTEGER DEFAULT 0,
    report_json TEXT DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_evo_cycles_started ON evolution_cycles(started_at DESC);
"""


# ── Parallel Implementation Note ──────────────────────────────
# NOTE: EvolutionLoop is one of two parallel self-evolution implementations.
# The other is EvolveEngine in maop/evolve.py.
# Both have production callers:
#   - EvolutionLoop (this class): used by core/three_layer_memory.py (consolidation)
#   - EvolveEngine: used by maop_loop.py (main loop), dashboard/routers/evolve.py
# Future work: consider merging into a single canonical implementation.

class EvolutionLoop:
    """Closed-loop self-evolution orchestrator.

    Parameters
    ----------
    root_dir : str | Path
        MAOP project root directory.
    strategy_name : str
        Strategy for evaluation (conservative/aggressive/balanced/cost_aware).
    auto_consolidate : bool
        Whether to run memory consolidation after each cycle.
    heal_threshold : int
        Min recurrence for an error pattern to trigger heal attempt.
    suggest_threshold : int
        Min recurrence for unhealed errors to become evolution suggestions.
    """

    def __init__(
        self,
        root_dir: str | Path,
        strategy_name: str = "balanced",
        auto_consolidate: bool = True,
        heal_threshold: int = 2,
        suggest_threshold: int = 3,
    ) -> None:
        self._root = Path(root_dir)
        self._data_dir = self._root / "data"
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._db_path = get_db_path("evolution_loop")
        self._strategy_name = strategy_name
        self._auto_consolidate = auto_consolidate
        self._heal_threshold = heal_threshold
        self._suggest_threshold = suggest_threshold
        self._init_db()

    def _init_db(self) -> None:
        with self._db_connect() as conn:
            conn.executescript(_EVOLUTION_LOOP_DDL)

    def _db_connect(self):
        return sqlite_connect(self._db_path, foreign_keys=False)

    def run_cycle(self, dry_run: bool = False, auto_rollback: bool = True) -> LoopReport:
        """Execute one complete evolution cycle: OBSERVE→HEAL→SUGGEST→EVALUATE→APPLY→VALIDATE→CONSOLIDATE.

        Parameters
        ----------
        dry_run : bool
            When True, runs all phases but APPLY only logs proposed mutations
            without executing them. Useful for previewing impact.
        auto_rollback : bool
            When True (and not dry_run), if VALIDATE shows no improvement,
            automatically rolls back to the pre-APPLY snapshot.
        """
        report = LoopReport(started_at=time.time(), dry_run=dry_run)
        logger.info("[evo-loop] Starting cycle %s (dry_run=%s)", report.cycle_id, dry_run)

        observe = self._phase_observe()
        report.phases.append(observe)
        report.errors_observed = observe.details.get("hotspot_count", 0)

        if report.errors_observed == 0:
            logger.info("[evo-loop] No errors observed, skipping cycle")
            report.finished_at = time.time()
            report.total_duration_s = round(report.finished_at - report.started_at, 2)
            self._save_report(report)
            return report

        heal = self._phase_heal(observe.details.get("hotspot_patterns", []))
        report.phases.append(heal)
        report.heal_attempts = heal.details.get("attempts", 0)
        report.heal_successes = heal.details.get("successes", 0)

        suggest = self._phase_suggest(observe.details.get("hotspot_patterns", []))
        report.phases.append(suggest)
        report.suggestions_generated = suggest.details.get("count", 0)

        evaluate = self._phase_evaluate(suggest.details.get("suggestions", []))
        report.phases.append(evaluate)

        # Pre-APPLY snapshot: capture file state so we can rollback if needed.
        if not dry_run:
            try:
                from maop.core.change_tracker import ChangeTracker
                ct = ChangeTracker(root_dir=str(self._root))
                snap_id = ct.snapshot(str(self._root), label=f"pre-apply-{report.cycle_id}")
                report.snapshot_id = snap_id
                logger.info("[evo-loop] Pre-APPLY snapshot: %s", snap_id)
            except Exception as exc:
                logger.warning("[evo-loop] Pre-APPLY snapshot failed (rollback unavailable): %s", exc)

        apply_result = self._phase_apply(evaluate.details.get("approved", []), dry_run=dry_run)
        report.phases.append(apply_result)
        report.suggestions_applied = apply_result.details.get("applied", 0)

        validate = self._phase_validate(report.errors_observed)
        report.phases.append(validate)
        report.validation_improved = validate.details.get("improved", False)

        # Auto-rollback: if not improved and we have a snapshot, undo APPLY.
        if (
            not dry_run
            and auto_rollback
            and not report.validation_improved
            and report.snapshot_id
            and apply_result.details.get("applied", 0) > 0
        ):
            try:
                restored = self.rollback_cycle(report.cycle_id, snapshot_id=report.snapshot_id)
                report.rolled_back = restored > 0
                logger.info(
                    "[evo-loop] Auto-rollback: restored %d files (cycle %s)",
                    restored, report.cycle_id,
                )
            except Exception as exc:
                logger.warning("[evo-loop] Auto-rollback failed: %s", exc)

        if self._auto_consolidate:
            consolidate = self._phase_consolidate()
            report.phases.append(consolidate)
            report.consolidated = consolidate.details.get("consolidated", 0)

        report.finished_at = time.time()
        report.total_duration_s = round(report.finished_at - report.started_at, 2)
        self._save_report(report)
        logger.info("[evo-loop] Cycle %s complete: %s", report.cycle_id, report.summary())
        return report

    def _phase_observe(self) -> PhaseResult:
        start = time.time()
        try:
            from maop.core.error_ledger import ErrorLedger
            ledger = ErrorLedger(root_dir=str(self._root))
            hotspots = ledger.get_hotspots(top=20)
            unhealed = [h for h in hotspots if h.count >= self._heal_threshold]
            patterns = [h.pattern for h in unhealed]
            return PhaseResult(
                phase=LoopPhase.OBSERVE,
                success=True,
                duration_s=round(time.time() - start, 3),
                details={
                    "hotspot_count": len(unhealed),
                    "hotspot_patterns": patterns,
                    "top_patterns": [{"pattern": h.pattern, "count": h.count} for h in unhealed[:5]],
                },
            )
        except Exception as exc:
            logger.warning("[evo-loop] Observe phase failed: %s", exc)
            return PhaseResult(phase=LoopPhase.OBSERVE, success=False, error=str(exc), duration_s=round(time.time() - start, 3))

    def _phase_heal(self, patterns: list[str]) -> PhaseResult:
        start = time.time()
        attempts = 0
        successes = 0
        try:
            from maop.core.self_heal import SelfHealEngine
            engine = SelfHealEngine(root_dir=str(self._root))
            for pattern in patterns:
                attempts += 1
                report = engine.run_all(trigger_condition=pattern)
                if report.repaired > 0:
                    successes += 1
            return PhaseResult(
                phase=LoopPhase.HEAL,
                success=True,
                duration_s=round(time.time() - start, 3),
                details={"attempts": attempts, "successes": successes},
            )
        except Exception as exc:
            logger.warning("[evo-loop] Heal phase failed: %s", exc)
            return PhaseResult(phase=LoopPhase.HEAL, success=False, error=str(exc), duration_s=round(time.time() - start, 3), details={"attempts": attempts, "successes": successes})

    def _phase_suggest(self, patterns: list[str]) -> PhaseResult:
        start = time.time()
        suggestions: list[dict[str, Any]] = []
        try:
            from maop.core.error_ledger import ErrorLedger
            ledger = ErrorLedger(root_dir=str(self._root))
            promoted = ledger.auto_promote(threshold=self._suggest_threshold)

            for rule in promoted:
                suggestions.append(EvolutionSuggestion(
                    source="error_ledger",
                    suggestion_type="error_pattern_rule",
                    severity="HIGH" if rule.count >= 5 else "MEDIUM",
                    description=f"Recurring pattern '{rule.pattern}' (count={rule.count}) → auto-promoted rule",
                    auto_applicable=True,
                    metadata={"pattern": rule.pattern, "count": rule.count, "rule": rule.rule},
                ).model_dump())

            for pattern in patterns:
                if not any(s.get("metadata", {}).get("pattern") == pattern for s in suggestions):
                    errors = ledger.find_by_pattern(pattern)
                    if errors:
                        latest = errors[0]
                        suggestions.append(EvolutionSuggestion(
                            source="error_ledger",
                            suggestion_type="routing_mismatch" if "routing" in pattern else "agent_low_success",
                            severity="MEDIUM",
                            description=f"Unhealed error pattern '{pattern}' needs config adjustment",
                            auto_applicable=False,
                            metadata={"pattern": pattern, "error_type": latest.error_type, "context": latest.context},
                        ).model_dump())

            self._write_suggestions(suggestions)

            return PhaseResult(
                phase=LoopPhase.SUGGEST,
                success=True,
                duration_s=round(time.time() - start, 3),
                details={"count": len(suggestions), "suggestions": suggestions},
            )
        except Exception as exc:
            logger.warning("[evo-loop] Suggest phase failed: %s", exc)
            return PhaseResult(phase=LoopPhase.SUGGEST, success=False, error=str(exc), duration_s=round(time.time() - start, 3), details={"count": len(suggestions), "suggestions": suggestions})

    def _phase_evaluate(self, suggestions: list[dict[str, Any]]) -> PhaseResult:
        start = time.time()
        approved: list[dict[str, Any]] = []
        try:
            from maop.core.evolution_strategies import StrategyEngine
            engine = StrategyEngine(root_dir=str(self._root), strategy_name=self._strategy_name)
            decisions = engine.evaluate(suggestions)
            for decision in decisions:
                if decision.should_apply:
                    approved.append({
                        "suggestion_id": decision.suggestion_id,
                        "type": decision.suggestion_type,
                        "severity": decision.severity,
                        "reason": decision.reason,
                    })
            return PhaseResult(
                phase=LoopPhase.EVALUATE,
                success=True,
                duration_s=round(time.time() - start, 3),
                details={"approved": approved, "total": len(suggestions), "approved_count": len(approved)},
            )
        except Exception as exc:
            logger.warning("[evo-loop] Evaluate phase failed: %s", exc)
            return PhaseResult(phase=LoopPhase.EVALUATE, success=False, error=str(exc), duration_s=round(time.time() - start, 3), details={"approved": approved})

    def _phase_apply(self, approved: list[dict[str, Any]], dry_run: bool = False) -> PhaseResult:
        """Apply approved mutations. In dry_run mode, log proposed changes only."""
        start = time.time()
        applied = 0
        proposed: list[dict[str, Any]] = []
        try:
            from maop.core.evolution_strategies import StrategyEngine
            engine = StrategyEngine(root_dir=str(self._root), strategy_name=self._strategy_name)
            for item in approved:
                sid = item.get("suggestion_id", "")
                if not sid:
                    continue
                if dry_run:
                    proposed.append({
                        "suggestion_id": sid,
                        "type": item.get("type", ""),
                        "severity": item.get("severity", ""),
                        "reason": item.get("reason", ""),
                    })
                    applied += 1
                else:
                    result = engine.apply(sid)
                    if result.get("applied", False):
                        applied += 1
            details: dict[str, Any] = {"applied": applied, "total": len(approved)}
            if dry_run:
                details["proposed"] = proposed
                details["dry_run"] = True
            return PhaseResult(
                phase=LoopPhase.APPLY,
                success=True,
                duration_s=round(time.time() - start, 3),
                details=details,
            )
        except Exception as exc:
            logger.warning("[evo-loop] Apply phase failed: %s", exc)
            return PhaseResult(phase=LoopPhase.APPLY, success=False, error=str(exc), duration_s=round(time.time() - start, 3), details={"applied": applied})

    def _phase_validate(self, baseline_errors: int) -> PhaseResult:
        start = time.time()
        try:
            from maop.core.error_ledger import ErrorLedger
            ledger = ErrorLedger(root_dir=str(self._root))
            current_hotspots = ledger.get_hotspots(top=20)
            current_unhealed = sum(h.count for h in current_hotspots if h.count >= self._heal_threshold)
            improved = current_unhealed < baseline_errors

            if improved:
                logger.info("[evo-loop] Validation: errors decreased %d → %d", baseline_errors, current_unhealed)
            else:
                logger.info("[evo-loop] Validation: no improvement (%d → %d)", baseline_errors, current_unhealed)

            return PhaseResult(
                phase=LoopPhase.VALIDATE,
                success=True,
                duration_s=round(time.time() - start, 3),
                details={"improved": improved, "baseline": baseline_errors, "current": current_unhealed},
            )
        except Exception as exc:
            logger.warning("[evo-loop] Validate phase failed: %s", exc)
            return PhaseResult(phase=LoopPhase.VALIDATE, success=False, error=str(exc), duration_s=round(time.time() - start, 3))

    def _phase_consolidate(self) -> PhaseResult:
        start = time.time()
        try:
            from maop.core.three_layer_memory import ThreeLayerMemory
            mem = ThreeLayerMemory(root_dir=str(self._root))
            report = mem.consolidate(min_score=0.6, limit=50)
            return PhaseResult(
                phase=LoopPhase.CONSOLIDATE,
                success=True,
                duration_s=round(time.time() - start, 3),
                details={"candidates": report.candidates, "consolidated": report.consolidated, "errors": report.errors},
            )
        except Exception as exc:
            logger.warning("[evo-loop] Consolidate phase failed: %s", exc)
            return PhaseResult(phase=LoopPhase.CONSOLIDATE, success=False, error=str(exc), duration_s=round(time.time() - start, 3))

    def rollback_cycle(self, cycle_id: str, snapshot_id: str = "") -> int:
        """Roll back file changes made during a cycle's APPLY phase.

        Restores files to the state captured by the pre-APPLY snapshot.
        If ``snapshot_id`` is not provided, looks up the cycle's snapshot_id
        from the persisted cycle report.

        Returns the number of files restored.
        """
        snap = snapshot_id
        if not snap:
            # Look up from persisted cycle report.
            with self._db_connect() as conn:
                row = conn.execute(
                    "SELECT report_json FROM evolution_cycles WHERE id=?",
                    (cycle_id,),
                ).fetchone()
            if row is None:
                logger.warning("[evo-loop] rollback_cycle: cycle '%s' not found", cycle_id)
                return 0
            try:
                report = LoopReport.model_validate_json(row[0])
                snap = report.snapshot_id
            except Exception as exc:
                logger.warning("[evo-loop] rollback_cycle: cannot parse cycle report: %s", exc)
                return 0
        if not snap:
            logger.info("[evo-loop] rollback_cycle: no snapshot_id recorded for cycle %s", cycle_id)
            return 0
        try:
            from maop.core.change_tracker import ChangeTracker
            ct = ChangeTracker(root_dir=str(self._root))
            restored = ct.rollback(str(self._root), to_id=snap)
            logger.info(
                "[evo-loop] rollback_cycle: cycle %s → snapshot %s, %d files restored",
                cycle_id, snap, restored,
            )
            return restored
        except Exception as exc:
            logger.error("[evo-loop] rollback_cycle failed: %s", exc)
            return 0

    def _write_suggestions(self, suggestions: list[dict[str, Any]]) -> None:
        path = self._data_dir / "evolve-suggestions.json"
        existing: list[dict[str, Any]] = []
        if path.exists():
            try:
                with open(path, encoding="utf-8") as f:
                    existing = json.load(f)
            except (json.JSONDecodeError, OSError):
                existing = []
        existing_ids = {s.get("id") for s in existing}
        for s in suggestions:
            if s.get("id") not in existing_ids:
                existing.append(s)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(existing[-200:], f, indent=2, ensure_ascii=False)

    def _save_report(self, report: LoopReport) -> None:
        with self._db_connect() as conn:
            conn.execute(
                """INSERT INTO evolution_cycles
                   (id, started_at, finished_at, total_duration_s,
                    errors_observed, heal_attempts, heal_successes,
                    suggestions_generated, suggestions_applied,
                    validation_improved, consolidated, report_json)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (report.cycle_id, report.started_at, report.finished_at,
                 report.total_duration_s, report.errors_observed,
                 report.heal_attempts, report.heal_successes,
                 report.suggestions_generated, report.suggestions_applied,
                 int(report.validation_improved), report.consolidated,
                 report.model_dump_json()),
            )

    def get_cycle_history(self, limit: int = 20) -> list[LoopReport]:
        with self._db_connect() as conn:
            rows = conn.execute(
                "SELECT report_json FROM evolution_cycles ORDER BY started_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        reports = []
        for row in rows:
            with contextlib.suppress(Exception):
                reports.append(LoopReport.model_validate_json(row[0]))
        return reports

    def get_stats(self) -> dict[str, Any]:
        with self._db_connect() as conn:
            total = conn.execute("SELECT COUNT(*) FROM evolution_cycles").fetchone()[0]
            improved = conn.execute("SELECT COUNT(*) FROM evolution_cycles WHERE validation_improved = 1").fetchone()[0]
            avg_duration = conn.execute("SELECT AVG(total_duration_s) FROM evolution_cycles").fetchone()[0] or 0.0
            total_suggestions = conn.execute("SELECT SUM(suggestions_applied) FROM evolution_cycles").fetchone()[0] or 0
            total_heals = conn.execute("SELECT SUM(heal_successes) FROM evolution_cycles").fetchone()[0] or 0
        return {
            "total_cycles": total,
            "improved_cycles": improved,
            "improvement_rate": round(improved / total, 3) if total > 0 else 0.0,
            "avg_duration_s": round(avg_duration, 2),
            "total_suggestions_applied": total_suggestions,
            "total_heal_successes": total_heals,
        }
