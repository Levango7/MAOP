"""MAOP Error Schema — standardized result object for all modules.


Uses Pydantic v2 for validation and serialization.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


class MaopResult(BaseModel):
    """Canonical result shape returned by every MAOP module / agent call.

    Fields mirror the PowerShell hashtable from error-schema.psm1:
        ok, exit_code, stdout, stderr, error, duration_ms,
        agent, task, trace_id, routing_key, driver, start_time, model
    """

    ok: bool = True
    exit_code: int = 0
    stdout: str = ""
    stderr: str = ""
    error: str | None = None
    duration_ms: int = 0
    agent: str
    task: str
    trace_id: str = ""
    routing_key: str = ""
    driver: str | None = None
    start_time: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    model: str | None = None
    structured_output: dict[str, Any] | None = None

    # ── helpers ──────────────────────────────────────────────

    def is_success(self) -> bool:
        """Return True iff exit_code == 0 and no error message."""
        return self.exit_code == 0 and not self.error

    def format_error(self, *, include_details: bool = False) -> str:
        """Human-readable one-line (or multi-line with details) error string."""
        ec = self.exit_code if self.exit_code is not None else "?"
        msg = f"[MAOP-{ec}] Agent='{self.agent}' Task='{self.task}'"
        if self.error:
            msg += f" — {self.error}"
        if self.duration_ms >= 0:
            msg += f" ({self.duration_ms}ms)"
        if include_details:
            if self.stderr:
                msg += f"\nstderr: {self.stderr}"
            if self.stdout:
                msg += f"\nstdout: {self.stdout}"
        return msg


# ── Factory function (mirrors New-ResultObject) ────────────────

def new_result(
    agent: str,
    task: str,
    exit_code: int = 0,
    stdout: str = "",
    stderr: str = "",
    error: str | None = None,
    duration_ms: int = 0,
    trace_id: str = "",
    routing_key: str = "",
    driver: str | None = None,
    model: str | None = None,
    structured_output: dict[str, Any] | None = None,
) -> MaopResult:
    """Create a MaopResult with ``ok`` derived from exit_code + error."""
    ok = exit_code == 0 and not error
    return MaopResult(
        ok=ok,
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        error=error,
        duration_ms=duration_ms,
        agent=agent,
        task=task,
        trace_id=trace_id,
        routing_key=routing_key,
        driver=driver,
        model=model,
        structured_output=structured_output,
    )
