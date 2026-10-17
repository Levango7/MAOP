"""MAOP Blackboard — Conflict Detection & Consensus Resolution Tracker.

Complements the DebateEngine by tracking *long-running* agent conflicts
across multiple blackboard cycles. When agents continuously disagree on the
same domain, the tracker escalates (e.g., raises a conflict entry or triggers
a human-in-the-loop debate).

Classes
-------
- ``ConflictRecord``: a detected conflict between two or more agents.
- ``ConsensusTracker``: monitors blackboard writes and detects / resolves
  conflicts.
"""

from __future__ import annotations

import logging
import uuid
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from maop.core.reliability.blackboard import Blackboard, BlackboardEntry

logger = logging.getLogger(__name__)


# ── Conflict severity ─────────────────────────────────────────────


class ConflictSeverity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    DEADLOCK = "deadlock"


# ── Conflict record ───────────────────────────────────────────────


@dataclass
class ConflictRecord:
    """Detected disagreement between agents on the same domain + key."""

    conflict_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    domain: str = ""
    entry_ids: list[str] = field(default_factory=list)
    contributors: list[str] = field(default_factory=list)
    severity: ConflictSeverity = ConflictSeverity.LOW
    cycle_count: int = 1
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    resolved: bool = False
    resolution: str = ""
    resolved_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "conflict_id": self.conflict_id,
            "domain": self.domain,
            "entry_ids": self.entry_ids,
            "contributors": self.contributors,
            "severity": self.severity.value,
            "cycle_count": self.cycle_count,
            "created_at": self.created_at,
            "resolved": self.resolved,
            "resolution": self.resolution,
            "resolved_at": self.resolved_at,
        }


# ── Resolution strategy type ──────────────────────────────────────

ResolutionFn = Callable[["ConflictRecord", Blackboard], None]


# ── ConsensusTracker ──────────────────────────────────────────────


class ConsensusTracker:
    """Monitors blackboard writes to detect inter-agent conflicts.

    A **conflict** is defined as >=2 agents contributing entries of opposite
    polarity to the same domain within a configurable cycle window.
    Polarity is derived from ``entry.metadata[polarity_key]``.

    Usage::

        tracker = ConsensusTracker(bb, polarity_key="verdict")
        # After each controller cycle:
        conflicts = await tracker.scan()
    """

    def __init__(
        self,
        bb: Blackboard,
        *,
        polarity_key: str = "polarity",
        polarity_values: tuple[str, str] = ("positive", "negative"),
        high_confidence: float = 0.7,
        deadlock_cycles: int = 3,
    ) -> None:
        self._bb = bb
        self._polarity_key = polarity_key
        self._positive, self._negative = polarity_values
        self._high_confidence = high_confidence
        self._deadlock_cycles = deadlock_cycles
        self._conflicts: dict[str, ConflictRecord] = {}
        self._active_by_domain: dict[str, ConflictRecord] = {}
        self._conflict_callback: ResolutionFn | None = None

    # ── Configuration ────────────────────────────────────────────

    def set_conflict_callback(self, fn: ResolutionFn) -> None:
        """Set a callback invoked when a DEADLOCK conflict is detected."""
        self._conflict_callback = fn

    # ── Scan ─────────────────────────────────────────────────────

    async def scan(self) -> list[ConflictRecord]:
        """Scan all blackboard domains for cross-agent conflicts.

        Returns the list of currently active (unresolved) conflicts.
        """
        domains = self._bb.get_domains()
        for domain in domains:
            entries = self._bb.read(domain)
            self._scan_domain(domain, entries)
        return list(self._active_by_domain.values())

    def _scan_domain(
        self, domain: str, entries: list[BlackboardEntry]
    ) -> None:
        """Detect polarity-based conflicts within one domain."""
        by_polarity: dict[str, list[BlackboardEntry]] = defaultdict(list)
        for e in entries:
            pol = e.metadata.get(self._polarity_key)
            if pol is None:
                continue
            by_polarity[pol].append(e)

        pos = by_polarity.get(self._positive, [])
        neg = by_polarity.get(self._negative, [])

        if not pos or not neg:
            if domain in self._active_by_domain:
                self._resolve(domain, "natural_resolution")
            return

        existing = self._active_by_domain.get(domain)
        contributors = list({e.contributor for e in pos + neg if e.contributor})
        entry_ids = [e.entry_id for e in pos + neg]

        max_pos_conf = max((e.confidence for e in pos), default=0.0)
        max_neg_conf = max((e.confidence for e in neg), default=0.0)
        both_high = (
            max_pos_conf >= self._high_confidence
            and max_neg_conf >= self._high_confidence
        )

        if existing:
            existing.cycle_count += 1
            existing.entry_ids = entry_ids
            existing.contributors = contributors
            if both_high and existing.severity != ConflictSeverity.DEADLOCK:
                existing.severity = ConflictSeverity.HIGH
            if existing.cycle_count >= self._deadlock_cycles:
                existing.severity = ConflictSeverity.DEADLOCK
                if self._conflict_callback:
                    try:
                        self._conflict_callback(existing, self._bb)
                    except Exception as exc:
                        logger.error("conflict callback error: %s", exc)
        else:
            severity = (
                ConflictSeverity.HIGH if both_high
                else ConflictSeverity.MEDIUM
            )
            rec = ConflictRecord(
                domain=domain,
                entry_ids=entry_ids,
                contributors=contributors,
                severity=severity,
                cycle_count=1,
            )
            self._conflicts[rec.conflict_id] = rec
            self._active_by_domain[domain] = rec
            logger.warning(
                "[consensus_tracker] new conflict in %s: %s (%s)",
                domain, contributors, severity.value,
            )

    def _resolve(self, domain: str, resolution: str) -> None:
        """Mark an active conflict as resolved."""
        rec = self._active_by_domain.pop(domain, None)
        if rec:
            rec.resolved = True
            rec.resolution = resolution
            rec.resolved_at = datetime.now(timezone.utc).isoformat()
            logger.info(
                "[consensus_tracker] conflict %s resolved: %s",
                rec.conflict_id, resolution,
            )

    # ── Manual resolution ────────────────────────────────────────

    def resolve_conflict(self, conflict_id: str, resolution: str) -> bool:
        """Manually resolve a conflict by ID."""
        rec = self._conflicts.get(conflict_id)
        if rec and not rec.resolved:
            self._resolve(rec.domain, resolution)
            return True
        return False

    # ── Query ────────────────────────────────────────────────────

    @property
    def active_conflicts(self) -> list[ConflictRecord]:
        return list(self._active_by_domain.values())

    @property
    def all_conflicts(self) -> list[ConflictRecord]:
        return list(self._conflicts.values())

    def get_conflict(self, conflict_id: str) -> ConflictRecord | None:
        return self._conflicts.get(conflict_id)

    def summary(self) -> dict[str, Any]:
        active = self.active_conflicts
        return {
            "active_count": len(active),
            "total_count": len(self._conflicts),
            "by_severity": {
                "low": sum(
                    1 for c in active
                    if c.severity == ConflictSeverity.LOW
                ),
                "medium": sum(
                    1 for c in active
                    if c.severity == ConflictSeverity.MEDIUM
                ),
                "high": sum(
                    1 for c in active
                    if c.severity == ConflictSeverity.HIGH
                ),
                "deadlock": sum(
                    1 for c in active
                    if c.severity == ConflictSeverity.DEADLOCK
                ),
            },
            "deadlock_domains": [
                c.domain for c in active
                if c.severity == ConflictSeverity.DEADLOCK
            ],
        }
