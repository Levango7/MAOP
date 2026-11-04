"""Response models for the workflows router（M-X2 E2-b 深集成切片）.

形状与 ``orchestration.wiring.run_workflow`` 的返回（``WorkflowResult``）
及 ``config.loader.WorkflowDef`` 对齐；run 端点额外携带 ``trace_id``，
供面板打开既有 ``/api/runs/{trace_id}`` 时间线看回写事件。
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class WorkflowSummary(BaseModel):
    name: str
    steps: int = 0
    agents: list[str] = Field(default_factory=list)
    description: str = ""


class WorkflowsEnvelope(BaseModel):
    status: str = "ok"
    workflows: list[WorkflowSummary] = Field(default_factory=list)


class WorkflowRunRequest(BaseModel):
    initial_vars: dict[str, str] = Field(default_factory=dict)
    backend: str = ""


class WorkflowStepResultOut(BaseModel):
    step_index: int = 0
    agent: str = ""
    status: str = ""
    output: str = ""


class WorkflowResultOut(BaseModel):
    steps_total: int = 0
    steps_planned: int = 0
    steps_completed: int = 0
    step_results: list[WorkflowStepResultOut] = Field(default_factory=list)


class WorkflowRunEnvelope(BaseModel):
    status: str = "ok"
    trace_id: str = ""
    result: WorkflowResultOut | None = None
    error: str = ""
