"""Response models for the ``/api/evolution`` endpoints the dashboard calls.

Split across two routers (``evolution_experiment`` and ``evolve_insights``);
kept together here because the two share the loop/cycle payload types.

Where the producer already has a Pydantic model it is reused verbatim
(``EvolutionCycleReport``, ``LoopReport``, ``SkillMeta``, ``DeploymentRecord``,
``PromoteResult``, ``SPSRTExperimentResult``) so this module cannot drift from
what the service actually emits. Hand-built dicts are re-declared field for
field. ``error``/optional fields exist because :func:`handle_api_errors`
returns its ``error_value`` as a plain dict — that path IS validated, unlike
the ``HTTPException`` path which becomes a ``JSONResponse`` and bypasses
``response_model``.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from maop.core.evolution.ab_test import SPSRTExperimentResult
from maop.core.evolution.auto_deployer import DeploymentRecord, PromoteResult
from maop.core.evolution.evolution_loop_types import LoopReport
from maop.core.evolution.evolution_perf_loop import EvolutionCycleReport
from maop.core.evolution.skill_version import SkillMeta

# ── 性能演化循环（evolution_experiment） ──────────────────────────


class CycleListEnvelope(BaseModel):
    status: str = "ok"
    cycles: list[EvolutionCycleReport] = []


class PendingListEnvelope(BaseModel):
    status: str = "ok"
    pending: list[EvolutionCycleReport] = []


class ApproveEnvelope(BaseModel):
    status: str = "ok"
    result: PromoteResult | None = None


class SkillListEnvelope(BaseModel):
    # list_skills() has never carried a status key — the envelope says so.
    skills: list[SkillMeta] = []


class SkillCompositeEnvelope(BaseModel):
    status: str = "ok"
    saved: bool = False
    name: str = ""
    version: str = ""


class NarrativeEnvelope(BaseModel):
    """Two disjoint success shapes: ``markdown`` or ``narrative``."""

    status: str = "ok"
    cycle_id: str = ""
    format: str = ""
    markdown: str = ""
    narrative: dict[str, Any] | None = None


# ── AB 实验 / 部署 ────────────────────────────────────────────────


class AbListEnvelope(BaseModel):
    status: str = "ok"
    experiments: list[str] = []


class AbEvaluateEnvelope(BaseModel):
    status: str = "ok"
    result: SPSRTExperimentResult | None = None


class DeployHistoryEnvelope(BaseModel):
    status: str = "ok"
    history: list[DeploymentRecord] = []


# ── 闭环状态机（evolve_insights） ─────────────────────────────────


class LoopStats(BaseModel):
    total_cycles: int = 0
    improved_cycles: int = 0
    improvement_rate: float = 0.0
    avg_duration_s: float = 0.0
    total_suggestions_applied: int = 0
    total_heal_successes: int = 0


class LoopStatusEnvelope(BaseModel):
    status: str = "ok"
    error: str = ""
    state: str = ""
    evolution_loop_enabled: bool = False
    recent_cycles: list[LoopReport] = []
    stats: LoopStats | None = None
    pending_approval_count: int = 0
    pending_approval_ids: list[str] = []


class LoopTriggerEnvelope(BaseModel):
    status: str = "ok"
    error: str = ""
    report: LoopReport | None = None


class LoopRollbackEnvelope(BaseModel):
    status: str = "ok"
    restored_files: int = 0
    cycle_id: str = ""


class VariantSummary(BaseModel):
    success_rate: float = 0.0
    samples: int = 0


class CycleAbResult(BaseModel):
    experiment: str = ""
    p_value: float = 1.0
    significant: bool = False
    control: VariantSummary = Field(default_factory=VariantSummary)
    treatment: VariantSummary = Field(default_factory=VariantSummary)


class AbResultsEnvelope(BaseModel):
    """``ab_result`` is None whenever the cycle has no A/B experiment."""

    status: str = "ok"
    cycle_id: str = ""
    ab_result: CycleAbResult | None = None


class ApprovalItem(BaseModel):
    cycle_id: str = ""
    started_at: float = 0.0
    pending_approval_ids: list[str] = []
    approval_state: str = ""
    errors_observed: int = 0
    suggestions_generated: int = 0
    validation_improved: bool = False


class ApprovalListEnvelope(BaseModel):
    status: str = "ok"
    approvals: list[ApprovalItem] = []
    total: int = 0


class ApprovalDecisionEnvelope(BaseModel):
    status: str = "ok"
    decision: str = ""
    approval_id: str = ""
    cycle_id: str = ""
    approval_state: str = ""
