"""MAOP Engine — Type definitions (enums and Pydantic models).

Extracted from ``engine.py`` (Phase 3-1 module split) to isolate the
pure data model layer from the engine logic and helper functions.

Contents:
    - StepType / StepStatus  — step kind and run-state enums.
    - WorkflowStep / StepResult / EngineResult — Pydantic models consumed
      and produced by the workflow engine.

This module has no dependency on ``engine_utils`` or ``engine`` to keep
the dependency graph single-directional (engine → engine_types).
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

# ── Step types ────────────────────────────────────────────────

class StepType(str, Enum):
    PLAN = "plan"
    AGENT = "agent"
    DAG = "dag"
    VERIFY = "verify"
    CONDITION = "condition"
    TERMINAL = "terminal"


class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


# ── Models ────────────────────────────────────────────────────

class WorkflowStep(BaseModel):
    """A single step in a workflow DAG."""
    id: str
    type: StepType = StepType.AGENT
    agent: str = ""
    task: str = ""
    depends_on: list[str] = Field(default_factory=list)
    retry: int = 0
    timeout: int = 120
    description: str = ""
    on_failure: str = ""       # "fallback" | "skip" | "abort"
    fallback_to: str = ""
    params: dict[str, Any] = Field(default_factory=dict)


class StepResult(BaseModel):
    """Result of a single step execution."""
    id: str
    status: StepStatus = StepStatus.PENDING
    output: str = ""
    error: str = ""
    exit_code: int = -1
    duration_ms: int = 0
    agent: str = ""


class EngineResult(BaseModel):
    """Result of the entire engine run."""
    trace_id: str = ""
    steps: list[StepResult] = Field(default_factory=list)
    success: bool = False
    total_duration_ms: int = 0
    context: dict[str, Any] = Field(default_factory=dict)