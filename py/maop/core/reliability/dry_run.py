"""Dry-run sentinel contract — shared by producers and the verify gate.

T2.1 (2026-10-11): the dry-run gate used to accept *fuzzy* stdout
substrings ("dry-run", "dry_run", "no changes applied"). That contract
was unverifiable in both directions — any agent that merely *mentions*
the words passed the gate, and agents with a real dry-run mode had no
standard way to prove it. This module is the single source of the
line-anchored contract:

    MAOP_DRY_RUN_MARKER: {"artifacts": ["docs/out.md"], "agent": "doc-pipeline"}

**Producers** (executors that performed a dry run) emit one such line
via :func:`emit_marker` — it must be a stdout line of its own (leading
anchor, no prefix text).

**Consumers** (the verify gate, dashboards, tests) use
:func:`extract_markers`, which only ever accepts lines that *start*
with the marker keyword — prose that merely mentions "dry-run" can no
longer fake a signal.

The legacy substring check stays in the gate for one minor release
(deprecated; see maop_verify._gate_dry_run).
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

DRY_RUN_MARKER = "MAOP_DRY_RUN_MARKER"

# Instruction block dispatch prepends to a task when the target agent
# declares the ``dry_run_supported`` capability. It is intentionally
# explicit about (a) zero side effects and (b) the exact marker line,
# so an agentic CLI can comply without guessing the format.
DRY_RUN_PREAMBLE = (
    "[MAOP DRY-RUN MODE] Do NOT modify, create, or delete any file, "
    "and do NOT execute any command with side effects. Only produce "
    "the plan of what you would do. When done, print exactly one line "
    f"by itself, starting with \"{DRY_RUN_MARKER}:\" followed by a JSON "
    'object, e.g. "'
    + DRY_RUN_MARKER
    + ': {\\"artifacts\\": [\\"path/you/would/change\\"]}". '
    "This line is machine-verified; without it the run fails."
)


def emit_marker(artifacts: list[str] | None = None, **extra: Any) -> str:
    """Return the sentinel line for a completed dry run.

    Parameters
    ----------
    artifacts:
        Paths (or logical names) the dry run *would* have created or
        modified. Verified against the plan's
        ``expected_dry_run_artifacts`` by the gate.
    **extra:
        Additional JSON fields (e.g. ``agent``, ``steps``). Must be
        JSON-serialisable.
    """
    payload: dict[str, Any] = {"artifacts": list(artifacts or [])}
    payload.update(extra)
    return f"{DRY_RUN_MARKER}: {json.dumps(payload, ensure_ascii=False)}"


def extract_markers(text: str) -> list[dict[str, Any]]:
    """Extract all marker payloads from stdout text.

    Only lines whose (stripped) content *starts* with the marker
    keyword are considered; the rest of the line must be a JSON
    object. Malformed marker lines are skipped (and logged at
    debug) rather than failing the caller — a broken marker is the
    producer's bug and surfaces as "no marker" at the gate.
    """
    markers: list[dict[str, Any]] = []
    for line in (text or "").splitlines():
        stripped = line.strip()
        if not stripped.startswith(DRY_RUN_MARKER + ":"):
            continue
        payload = stripped[len(DRY_RUN_MARKER) + 1:].strip()
        try:
            parsed = json.loads(payload)
        except (json.JSONDecodeError, TypeError):
            logger.debug("[dry-run] malformed marker line skipped: %r", line)
            continue
        if isinstance(parsed, dict):
            markers.append(parsed)
    return markers


def has_marker(text: str) -> bool:
    """True when ``text`` contains at least one well-formed marker line."""
    return bool(extract_markers(text))
