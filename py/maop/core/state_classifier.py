"""Task State Classifier — classify verification results into actionable states.

Inspired by Claude Code's background-task state taxonomy. Instead of a binary
passed/not-passed, every verify result gets one of four states:

  - done:    Task completed successfully, no further action needed.
  - working: Task made progress but not finished; re-try is worthwhile.
  - blocked: Task cannot proceed without external input (user decision,
             missing dependency, permission required).
  - failed:  Task hit a structural / unrecoverable error; re-trying wastes cycles.

This lets the feedback loop in maop_loop.py make intelligent decisions:
  - done    -> break, success
  - working -> re-plan and re-execute (existing behaviour)
  - blocked -> break, surface to user, do NOT waste cycles
  - failed  -> break, log structural failure, do NOT re-try
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Any

from pydantic import BaseModel


class TaskState(str, Enum):
    """Four-state taxonomy for task outcomes."""
    DONE = "done"
    WORKING = "working"
    BLOCKED = "blocked"
    FAILED = "failed"


class ClassificationResult(BaseModel):
    """Output of the state classifier."""
    state: TaskState = TaskState.WORKING
    confidence: float = 0.0
    reason: str = ""
    block_reason: str = ""  # Populated only when state == BLOCKED
    matched_pattern: str = ""


# ── Pattern banks ──────────────────────────────────────────────

# Patterns that indicate the task is BLOCKED (needs external input)
_BLOCKED_PATTERNS: list[tuple[str, float]] = [
    (r"(?:please\s+)?(?:confirm|approve|allow|permit|grant)\b", 0.85),
    (r"\bpermission\s+denied\b", 0.90),
    (r"\baccess\s+denied\b", 0.88),
    (r"\bwaiting\s+for\s+(?:user|input|confirmation|approval)\b", 0.92),
    (r"\brequires?\s+(?:user\s+)?(?:input|confirmation|decision|approval)\b", 0.90),
    (r"\bcannot\s+(?:proceed|continue)\s+without\b", 0.88),
    (r"\bneeds?\s+(?:human|intervention|manual)\b", 0.85),
    (r"\bunauthorized\b", 0.82),
    (r"\b2FA\b|\bMFA\b|\bauthentication\s+required\b", 0.80),
    (r"\bcredential\s+(?:missing|not\s+found|required)\b", 0.85),
    (r"\binteractive\s+(?:mode|prompt)\b", 0.75),
    (r"\bY/N\b|\byes/no\b|\bpress\s+any\s+key\b", 0.80),
]

# Patterns that indicate a structural FAILURE (re-trying won't help)
_FAILED_PATTERNS: list[tuple[str, float]] = [
    (r"\b(?:module|package|library)\s+(?:not\s+found|missing)\b", 0.85),
    (r"\bImportError\b.*\bcannot\s+import\b", 0.90),
    (r"\bModuleNotFoundError\b", 0.90),
    (r"\bFileNotFoundError\b.*\bNo\s+such\s+file\b", 0.88),
    (r"\bSyntaxError\b", 0.85),
    (r"\bIndentationError\b", 0.85),
    (r"\bnot\s+supported\b", 0.75),
    (r"\bincompatible\b", 0.75),
    (r"\bunsupported\s+(?:operation|format|version)\b", 0.80),
    (r"\bsegmentation\s+fault\b", 0.95),
    (r"\bcore\s+dumped\b", 0.95),
    (r"\bout\s+of\s+memory\b|\bOOM\b", 0.90),
    (r"\bdisk\s+full\b|\bNo\s+space\s+left\b", 0.92),
    (r"\bcircular\s+dependency\b", 0.88),
    (r"\bcannot\s+resolve\b.*\bdependency\b", 0.85),
    (r"\barchitecture\s+mismatch\b", 0.85),
    (r"\bABI\s+incompatible\b", 0.82),
]

# Patterns that indicate WORKING state (progress made, not done)
_WORKING_PATTERNS: list[tuple[str, float]] = [
    (r"\bpartial(?:ly)?\b.*\bcomplete\b", 0.70),
    (r"\bin\s+progress\b", 0.75),
    (r"\bnot\s+yet\s+(?:done|complete|finished)\b", 0.72),
    (r"\bstill\s+(?:running|processing|working)\b", 0.70),
    (r"\btimeout\b.*\bretry\b", 0.65),
    (r"\bretry(?:ing)?\b", 0.60),
    (r"\brate\s+limit\b", 0.68),
    (r"\b429\b", 0.65),
    (r"\b503\b", 0.60),
]


class TaskStateClassifier:
    """Classify a verification result into one of four actionable states.

    Usage::

        classifier = TaskStateClassifier()
        result = classifier.classify(
            passed=False,
            summary="Failed: exit_code, output",
            feedback="exit_code=1; Empty output",
            stdout="...",
            stderr="...",
        )
        if result.state == TaskState.BLOCKED:
            # Surface to user, don't re-try
        elif result.state == TaskState.FAILED:
            # Log structural failure, don't re-try
        elif result.state == TaskState.WORKING:
            # Re-plan and re-execute
    """

    def __init__(
        self,
        blocked_patterns: list[tuple[str, float]] | None = None,
        failed_patterns: list[tuple[str, float]] | None = None,
        working_patterns: list[tuple[str, float]] | None = None,
    ) -> None:
        self._blocked = blocked_patterns or _BLOCKED_PATTERNS
        self._failed = failed_patterns or _FAILED_PATTERNS
        self._working = working_patterns or _WORKING_PATTERNS

    def classify(
        self,
        passed: bool,
        summary: str = "",
        feedback: str = "",
        stdout: str = "",
        stderr: str = "",
        gates: list[dict[str, Any]] | None = None,
    ) -> ClassificationResult:
        """Classify a verification outcome.

        Parameters
        ----------
        passed : bool
            Whether all gates passed.
        summary, feedback : str
            VerifyResult fields.
        stdout, stderr : str
            Execution output for pattern matching.
        gates : list[dict] | None
            Individual gate results (name, passed, reason).

        Returns
        -------
        ClassificationResult
        """
        # Fast path: all gates passed -> done
        if passed:
            return ClassificationResult(
                state=TaskState.DONE,
                confidence=1.0,
                reason="All gates passed",
            )

        # Combine all text for pattern matching
        combined = " ".join(filter(None, [summary, feedback, stdout, stderr]))
        combined_lower = combined.lower()

        # Also check gate reasons
        if gates:
            for g in gates:
                if not g.get("passed", True):
                    combined_lower += " " + str(g.get("reason", "")).lower()

        # Priority 1: BLOCKED — needs external input
        best_blocked = self._best_match(combined_lower, self._blocked)
        if best_blocked:
            pattern, score = best_blocked
            return ClassificationResult(
                state=TaskState.BLOCKED,
                confidence=score,
                reason=f"Blocked: matched '{pattern}'",
                block_reason=self._extract_block_reason(combined),
                matched_pattern=pattern,
            )

        # Priority 2: FAILED — structural / unrecoverable
        best_failed = self._best_match(combined_lower, self._failed)
        if best_failed:
            pattern, score = best_failed
            return ClassificationResult(
                state=TaskState.FAILED,
                confidence=score,
                reason=f"Structural failure: matched '{pattern}'",
                matched_pattern=pattern,
            )

        # Priority 3: WORKING — progress made, retry worthwhile
        best_working = self._best_match(combined_lower, self._working)
        if best_working:
            pattern, score = best_working
            return ClassificationResult(
                state=TaskState.WORKING,
                confidence=score,
                reason=f"In progress: matched '{pattern}'",
                matched_pattern=pattern,
            )

        # Default: if not passed and no pattern matched, treat as working
        # (give the feedback loop a chance to re-plan)
        return ClassificationResult(
            state=TaskState.WORKING,
            confidence=0.40,
            reason="No specific pattern matched; defaulting to working for retry",
        )

    @staticmethod
    def _best_match(
        text: str,
        patterns: list[tuple[str, float]],
    ) -> tuple[str, float] | None:
        """Find the highest-confidence pattern that matches text."""
        best: tuple[str, float] | None = None
        for pattern, score in patterns:
            if re.search(pattern, text, re.IGNORECASE):
                if best is None or score > best[1]:
                    best = (pattern, score)
        return best

    @staticmethod
    def _extract_block_reason(text: str) -> str:
        """Extract a human-readable block reason from the output."""
        # Try to find a sentence containing the block keyword
        sentences = re.split(r"[.!?]\s+", text)
        for s in sentences:
            s_lower = s.lower()
            for pattern, _ in _BLOCKED_PATTERNS:
                if re.search(pattern, s_lower, re.IGNORECASE):
                    return s.strip()[:200]
        return "External input required"
