"""Response models for the agents router.

Every route the dashboard calls declares one of these as ``response_model`` so
the OpenAPI schema describes the actual payload instead of ``dict[str, Any]``.
The models mirror the producers field for field — ``RegisteredAgent`` /
``ScannedAgent`` / ``HealthCheckResult`` are reused straight from the core
layer, and the dataclass results from ``agent_repair`` / ``agent_evolution``
are re-declared here because those classes expose ``model_dump()`` rather than
being Pydantic models.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from maop.core.agent.lifecycle.agent_registry import HealthCheckResult, RegisteredAgent
from maop.core.agent.lifecycle.agent_scanner import ScannedAgent

# ── Presets ───────────────────────────────────────────────────────


class AgentPreset(BaseModel):
    name: str
    description: str = ""
    capabilities: list[str] = []
    model: str = ""
    cli: str = ""
    cli_args: str = ""
    driver: str = "cli"
    timeout_s: int = 120


class PresetsEnvelope(BaseModel):
    status: str = "ok"
    presets: list[AgentPreset]


class AgentListEnvelope(BaseModel):
    status: str = "ok"
    agents: list[RegisteredAgent]


class ScanEnvelope(BaseModel):
    status: str = "ok"
    scanned: int
    synced: int
    agents: list[ScannedAgent]


class RegisterEnvelope(BaseModel):
    status: str = "ok"
    agent: RegisteredAgent
    synced_to_yaml: bool


class AgentDeleteEnvelope(BaseModel):
    status: str = "ok"
    deleted: bool
    errors: list[str] = []


# ── Health / diagnose / repair ────────────────────────────────────


class HealthCheckEnvelope(BaseModel):
    status: str = "ok"
    result: HealthCheckResult


class DiagnosisOut(BaseModel):
    """Mirror of ``agent_repair.DiagnosisResult`` (a plain dataclass)."""

    agent_name: str = ""
    cli_name: str = ""
    cli_path: str = ""
    cli_exists: bool = False
    cli_executable: bool = False
    version: str = ""
    install_method: str = ""
    missing_dependencies: list[str] = []
    config_issues: list[str] = []
    overall_status: str = "healthy"


class DiagnosisEnvelope(BaseModel):
    status: str = "ok"
    diagnosis: DiagnosisOut


class RepairOut(BaseModel):
    """Mirror of ``agent_repair.RepairResult`` (a plain dataclass)."""

    agent_name: str = ""
    success: bool = False
    actions_taken: list[str] = []
    errors: list[str] = []
    diagnosis_before: dict[str, Any] = {}
    diagnosis_after: dict[str, Any] = {}


class RepairEnvelope(BaseModel):
    status: str = "ok"
    result: RepairOut


# ── Routing table ─────────────────────────────────────────────────


class AgentRoute(BaseModel):
    capability: str = ""
    name: str = ""
    provider: str = ""
    enabled: bool = True
    primary: str = ""
    primary_model: str = ""
    fallback: str = ""
    fallback_model: str = ""
    tertiary: str = ""
    tertiary_model: str = ""
    keywords: list[str] = []
    match: str = ""


class RoutesEnvelope(BaseModel):
    routes: list[AgentRoute]
    count: int


# ── Memory ────────────────────────────────────────────────────────


class MemoryRecord(BaseModel):
    id: int
    agent_name: str = ""
    memory_type: str = ""
    content: dict[str, Any] = {}
    metadata: dict[str, Any] = {}
    importance: float = 0.0
    created_at: str = ""


class MemoryListEnvelope(BaseModel):
    memories: list[MemoryRecord]
    count: int


class MemoryStoreEnvelope(BaseModel):
    id: int
    status: str = "stored"


class MemoryClearEnvelope(BaseModel):
    deleted: int


class ErrorPattern(BaseModel):
    pattern: dict[str, Any] = {}
    frequency: int = 0


class MemorySummary(BaseModel):
    agent_name: str = ""
    total_memories: int = 0
    by_type: dict[str, int] = {}
    last_activity: str = ""
    avg_importance: float = 0.0
    top_error_patterns: list[ErrorPattern] = []
    evolution_count: int = 0


class MemorySummaryEnvelope(BaseModel):
    summary: MemorySummary


# ── Upgrade / evolution ───────────────────────────────────────────


class UpgradeStatusEntry(BaseModel):
    name: str = ""
    cli: str = ""
    current: str = ""
    latest: str = ""
    install_method: str = ""
    status: str = ""


class UpgradeStatusEnvelope(BaseModel):
    agents: list[UpgradeStatusEntry]


class UpgradeCheckEnvelope(BaseModel):
    """``upgrade_service.check_agent_upgrade`` — the error branch only sets
    ``status``/``error``, so every other field is defaulted."""

    status: str
    agent: str = ""
    cli: str = ""
    current_version: str = ""
    latest_version: str = ""
    install_method: str = ""
    update_available: bool = False
    release_notes: str = ""
    error: str = ""


class UpgradeInfo(BaseModel):
    """``upgrade_service.upgrade_agent_cli`` — keys appear per install method."""

    agent: str = ""
    cli: str = ""
    upgrade_status: str = ""
    install_method: str = ""
    npm_package: str = ""
    exit_code: int | None = None
    output: str = ""
    error: str = ""


class UpgradeRunEnvelope(BaseModel):
    status: str
    info: UpgradeInfo | None = None
    error: str = ""


class EvolutionSuggestionOut(BaseModel):
    category: str = ""
    priority: str = "low"
    description: str = ""
    action: str = ""
    changes: dict[str, Any] = {}
    confidence: float = 0.0


class EvolutionResultOut(BaseModel):
    """Mirror of ``agent_evolution.EvolutionResult`` (a plain dataclass)."""

    agent_name: str = ""
    suggestions: list[EvolutionSuggestionOut] = []
    auto_applied: list[dict[str, Any]] = []
    errors: list[str] = []
    summary: str = ""


class EvolveEnvelope(BaseModel):
    result: EvolutionResultOut
