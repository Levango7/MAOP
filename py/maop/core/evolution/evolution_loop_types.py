"""Evolution Loop type definitions.

Extracted from evolution_loop.py for single-responsibility separation.
All Pydantic models and enums for the evolution loop live here.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class LoopPhase(str, Enum):
    OBSERVE = "observe"
    HEAL = "heal"
    SUGGEST = "suggest"
    # F-5: DEBATE 阶段在 SUGGEST 与 EVALUATE 之间，对建议逐条辩论。
    # 默认禁用（向后兼容），由 EvolutionLoop.debate_enabled 开关控制。
    DEBATE = "debate"
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
    """统一进化建议模型 — 兼容三套历史实现。

    字段映射:
      - category: 进化维度 (performance/reliability/capability/routing/error/cost/cache/bottleneck/preference)
      - mutation_type: 具体动作 (adjust_timeout/change_routing/disable_agent/add_capability/adjust_retries/record_lesson/record_preference/adjust_cache/switch_model/error_pattern_rule)
      - severity: 统一大写 HIGH/MEDIUM/LOW
      - type: 向后兼容别名，等于 mutation_type
      - suggestion_type: 向后兼容别名，等于 category
    """
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:8])
    source: str = ""
    category: str = ""
    mutation_type: str = ""
    severity: str = "MEDIUM"
    description: str = ""
    auto_applicable: bool = False
    applied: bool = False
    target_type: str = ""
    target_name: str = ""
    mutation_params: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @property
    def type(self) -> str:
        """向后兼容: ConfigMutator 通过 type 查找 handler。"""
        return self.mutation_type

    @property
    def suggestion_type(self) -> str:
        """向后兼容: EvolutionLoop 历史字段。"""
        return self.category

    def model_dump(self, **kwargs: Any) -> dict[str, Any]:
        d = super().model_dump(**kwargs)
        d["type"] = self.mutation_type
        d["suggestion_type"] = self.category
        return d  # type: ignore


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

    # AC-04 / spec §14: 人工 gate 字段
    # 建议在 EVALUATE 阶段被策略判定为 need_approval（should_apply=False 且 non-trivial）
    # 的 suggestion_id 列表，暂存待审批。不进入 APPLY 阶段。
    pending_approval: list[str] = Field(default_factory=list)
    # 审批状态：n/a（无需审批）| pending | approved | rejected | partial
    approval_state: str = "n/a"
    approved_by: str = ""
    approved_at: float = 0.0

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
