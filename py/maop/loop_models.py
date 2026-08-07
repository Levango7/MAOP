"""MAOP Loop Models — Pydantic models for the MAOP Loop orchestrator.

Extracted from maop_loop.py for single-responsibility separation.
All models are re-exported from maop_loop.py for backward compatibility.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from maop.core.reliability.error_schema import MaopResult
from maop.maop_verify import VerifyResult


class LoopResult(BaseModel):
    """Final result of a MAOP Loop cycle."""
    task: str
    trace_id: str = ""
    selected_agent: str = ""
    routing_key: str = ""
    plan: dict[str, Any] = Field(default_factory=dict)
    execution: MaopResult | None = None
    verify: VerifyResult | None = None
    feedback_cycles: int = 0
    total_duration_ms: int = 0
    success: bool = False
    analysis: dict[str, Any] = Field(default_factory=dict)
    parallel_executed: bool = False
    block_reason: str = ""  # Populated when verify state == "blocked"


class RequirementAnalysis(BaseModel):
    """Result of Phase 0: Requirements Analysis.

    Extracts objectives, boundaries, acceptance criteria, and assumptions
    from a task description before entering Plan phase.

    t18 (2026-07-21) — extended with semantic analysis fields:
      - action_verbs:  verbs describing the work ("implement", "fix",
                       "refactor", "test", "deploy", "document", ...)
      - tech_stack:     detected technology keywords ("api", "database",
                       "ui", "cli", "http", "config", ...)
      - complexity:     rough estimate — "simple" | "moderate" | "complex"
                       based on task length, verb count and tech stack size.
    All new fields are optional with safe defaults so existing callers
    and tests are unaffected.
    """
    task: str
    objectives: list[str] = Field(default_factory=list)
    boundaries: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    clarified_task: str = ""
    # ── t18 semantic fields ─────────────────────────────────────
    action_verbs: list[str] = Field(default_factory=list)
    tech_stack: list[str] = Field(default_factory=list)
    complexity: str = "unknown"


class LoopConfig(BaseModel):
    """Configuration for the MAOP Loop."""
    max_retries: int = 1
    retry_backoff_ms: int = 2000
    default_timeout_s: int = 120
    iterative_max_attempts: int = 3
    iterative_backoff_ms: int = 2000
    feedback_max_cycles: int = 2
    skip_verify: bool = False
    skip_analyze: bool = False
    enable_memory_inject: bool = True
    enable_log_rotation: bool = True
    log_rotation_max_kb: int = 512
    log_rotation_retain: int = 5
    # Analyzer
    enable_semantic_analyze: bool = True
    max_subtasks: int = 20
    # G0a (2026-07-22, Phase G): LLM-based semantic extraction toggle.
    # When True, simple_analyze / core/analyzer._semantic_analyze will call
    # llm_provider.chat() for real semantic understanding (action_verbs /
    # tech_stack / complexity / structured sections). Rule-based fallback
    # is retained per ADR-013 dual-path policy. Defaults to False so the
    # existing rule-based behavior stays unchanged unless explicitly opted
    # in (LLM provider must be configured with a valid API key).
    enable_llm_analyze: bool = False
    llm_analyze_model: str = ""  # model_name key in models.yaml; empty = use default
    # WorkerPool
    enable_parallel: bool = True
    max_workers: int = 4
    # LoadBalancer
    enable_load_balancer: bool = True
    lb_algorithm: str = "adaptive"
    # CacheGuard
    enable_cache_guard: bool = True
    cache_ttl_s: float = 300.0
    # LRU Cache
    enable_result_cache: bool = True
    result_cache_size: int = 128
    result_cache_ttl_s: float = 60.0
    # Monitoring
    enable_metrics: bool = True
    # TimeSeries
    enable_timeseries: bool = True
    # Evolve
    enable_evolve: bool = True
    # Dream Consolidation
    enable_dream: bool = True
    dream_interval_cycles: int = 10  # Run dream consolidation every N loops
    dream_min_group_size: int = 3
