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
    retry: int = 0            # Number of retries on failure (implemented in engine._execute_step)
    timeout: int = 120
    description: str = ""
    on_failure: str = ""       # "fallback" | "skip" | "abort"
    fallback_to: str = ""      # Fallback agent name if this step fails (implemented in engine._execute_step)
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


class SpawnDirective(BaseModel):
    """Runtime fan-out directive (dynamic orchestration).

    A step executor signals it by returning an object carrying a
    ``spawn`` attribute holding this directive. After the step
    succeeds, the engine merges ``steps`` into the *run* graph —
    they execute in the same run, after the spawning step
    (``depends_on`` defaults to the spawner's id; set it to
    reference any step that exists in the run).

    Validation at merge time: duplicate step ids and unknown
    ``depends_on`` references are dropped with an error log
    (the run continues). Cycles cannot form: spawned steps may
    only reference steps that already exist when they are
    merged, and existing steps' edges never change.

    Boundary: single-process runs only. In distributed
    execution the step executor runs on the worker, so
    directives cannot cross the process boundary — they are
    not delivered (the worker's result posting ignores the
    attribute).
    """

    steps: list[WorkflowStep] = Field(default_factory=list)
    depends_on: list[str] | None = None


class EngineResult(BaseModel):
    """Result of the entire engine run."""
    trace_id: str = ""
    steps: list[StepResult] = Field(default_factory=list)
    success: bool = False
    total_duration_ms: int = 0
    context: dict[str, Any] = Field(default_factory=dict)