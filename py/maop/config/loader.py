"""MAOP Config Loader — YAML configuration loading with Pydantic validation.

Mirrors the structure of config/agents.yaml and config/rules.yaml.
"""

from __future__ import annotations

import itertools
import logging
from pathlib import Path
from typing import Any, cast

import yaml
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr

logger = logging.getLogger(__name__)

# ── Pydantic models ───────────────────────────────────────────


class SubagentDef(BaseModel):
    """A subagent entry within a parent agent."""
    model_config = ConfigDict(protected_namespaces=())
    cli_args: str = ""
    capabilities: list[str] = Field(default_factory=list)
    description: str = ""
    model_display: str = ""
    # F2b (2026-07-22, Phase F): LLM provider name (ADR-013 dual-path).
    # Empty by default — subagent inherits parent's CLI path. When set,
    # ReactLoop may use LLM direct call for this subagent.
    provider: str = ""


class AgentDef(BaseModel):
    """One agent entry from agents.yaml."""
    cli: str = ""
    cli_args: str = ""
    driver: str = "cli"  # cli | powershell | wrapper
    capabilities: list[str] = Field(default_factory=list)
    model: str = ""
    # P2-8 fix: enabled field was silently ignored (extra='ignore' default),
    # causing disabled agents to be registered as enabled=True.
    enabled: bool = True
    # F2b (2026-07-22, Phase F): LLM provider name for direct API path
    # (ADR-013 dual-path). Maps to ProviderConfig.name in models.yaml.
    # Empty by default — preserves prior CLI-only behavior.
    provider: str = ""
    timeout_s: int = 120
    description: str = ""
    wrapper: str = ""  # for driver=wrapper
    subagents: dict[str, SubagentDef] = Field(default_factory=dict)


class WorkflowStepDef(BaseModel):
    """A single step within a workflow."""
    agent: str = ""
    task: str = ""
    condition: str = ""
    parallel: bool = False
    always_run: bool = False
    timeout_s: int = 120
    depends_on: list[str] = Field(default_factory=list)


class WorkflowDef(BaseModel):
    """A workflow entry from agents.yaml."""
    cli: str = ""
    driver: str = "wrapper"
    capabilities: list[str] = Field(default_factory=list)
    model: str = ""
    # F2b (2026-07-22, Phase F): LLM provider name (ADR-013 dual-path).
    provider: str = ""
    timeout_s: int = 300
    description: str = ""
    wrapper: str = ""
    steps: list[WorkflowStepDef] = Field(default_factory=list)
    variables: dict[str, str] = Field(default_factory=dict)


class RouteEntry(BaseModel):
    """Routing table entry: primary -> fallback -> tertiary.

    ``match``: optional regex pattern (re.search) to match against task text.
    ``keywords``: optional list of keyword strings; any match triggers this route.
    Both are backward-compatible — empty defaults mean 'no matching rule'.
    """
    primary: str = ""
    fallback: str = ""
    tertiary: str = ""
    match: str = ""
    keywords: list[str] = Field(default_factory=list)


class IterativeLoop(BaseModel):
    max_attempts: int = 3
    backoff_ms: int = 2000
    stop_on_success: bool = True


class RoutingLoop(BaseModel):
    enabled: bool = True


class LoopsConfig(BaseModel):
    iterative: IterativeLoop = Field(default_factory=IterativeLoop)
    routing: RoutingLoop = Field(default_factory=RoutingLoop)


class RetryConfig(BaseModel):
    max_attempts: int = 3
    backoff_ms: int = 2000


class TimeoutConfig(BaseModel):
    default_s: int = 120


class GuardsConfig(BaseModel):
    retry: RetryConfig = Field(default_factory=RetryConfig)
    timeout: TimeoutConfig = Field(default_factory=TimeoutConfig)


class MaopConfig(BaseModel):
    """Top-level MAOP configuration (agents.yaml + rules.yaml merged)."""
    model_config = ConfigDict(protected_namespaces=())
    agents: dict[str, AgentDef] = Field(default_factory=dict)
    workflows: dict[str, WorkflowDef] = Field(default_factory=dict)
    routing: dict[str, RouteEntry] = Field(default_factory=dict)
    loops: LoopsConfig = Field(default_factory=LoopsConfig)
    guards: GuardsConfig = Field(default_factory=GuardsConfig)
    _raw_models: dict[str, Any] = PrivateAttr(default_factory=dict)
    _version: int = PrivateAttr(default=0)


# ── YAML loading ──────────────────────────────────────────────


def _load_yaml(path: Path) -> dict[str, Any] | None:
    """Load a YAML file, returning None on failure."""
    if not path.exists():
        return None
    try:
        raw = path.read_text(encoding="utf-8")
        data = yaml.safe_load(raw)
        if data is None:
            return {}
        return cast(dict[str, Any] | None, data)
    except Exception as exc:
        logger.warning("Failed to load %s: %s", path, exc)
        return None


from maop.core.backends.db_utils import find_project_root

# === Config loading cache ============================================
#
# High-frequency call point: every dispatch re-reads agent config. Cache the
# parsed MaopConfig keyed by config directory + max mtime of the YAML files.
# A hit returns the previously parsed config without touching disk; a miss
# re-reads and re-parses. Invalidated automatically when any YAML mtime
# changes, and cleared explicitly on reload().
_agent_config_cache: dict[str, tuple[float, MaopConfig]] = {}

# Monotonic version counter for MaopConfig._version (used by hot-reload
# detection in RouteScorer). next() on itertools.count is atomic under CPython.
_config_version_seq = itertools.count(1)


def _next_config_version() -> int:
    """Return a monotonically increasing config version."""
    return next(_config_version_seq)


def _config_signature(config_dir: Path) -> float:
    """Return the max mtime across the config YAML files (0.0 if none exist).

    If any of agents.yaml / rules.yaml / models.yaml changes, its mtime
    changes and the cache entry is invalidated.
    """
    mtimes: list[float] = []
    for name in ("agents.yaml", "rules.yaml", "models.yaml"):
        p = config_dir / name
        try:
            if p.exists():
                mtimes.append(p.stat().st_mtime)
        except OSError as exc:
            logger.debug("config.loader: stat failed for %s: %s", p, exc)
    return max(mtimes) if mtimes else 0.0


# ── Config loader ─────────────────────────────────────────────


class ConfigLoader:
    """Load and merge MAOP configuration from YAML files.

    Usage::

        cfg = ConfigLoader(project_root="/path/to/MAOP").load()
        agent = cfg.agents["claude"]
        route = cfg.routing["codegen"]
    """

    def __init__(self, project_root: Path | str | None = None) -> None:
        if project_root is None:
            project_root = find_project_root()
        self._root = Path(project_root)
        self._config_dir = self._root / "config"

    def load(self) -> MaopConfig:
        """Load agents.yaml + rules.yaml + models.yaml and return a merged MaopConfig.

        Results are cached keyed by the config directory and the max mtime of
        the YAML files; repeated calls without file changes return the cached
        MaopConfig without re-reading disk.
        """
        cache_key = str(self._config_dir)
        try:
            sig = _config_signature(self._config_dir)
        except OSError:
            sig = 0.0
        cached = _agent_config_cache.get(cache_key)
        if cached is not None and cached[0] == sig:
            return cached[1]

        agents_data = _load_yaml(self._config_dir / "agents.yaml") or {}
        rules_data = _load_yaml(self._config_dir / "rules.yaml") or {}
        models_data = _load_yaml(self._config_dir / "models.yaml") or {}

        # Parse agents
        agents: dict[str, AgentDef] = {}
        for name, entry in agents_data.get("agents", {}).items():
            raw_subagents = entry.pop("subagents", None) if isinstance(entry, dict) else None
            subagents: dict[str, SubagentDef] = {}
            if raw_subagents and isinstance(raw_subagents, dict):
                for sa_name, sa_entry in raw_subagents.items():
                    subagents[sa_name] = SubagentDef(**sa_entry)
            agents[name] = AgentDef(**entry, subagents=subagents)

        # Parse workflows
        workflows: dict[str, WorkflowDef] = {}
        for name, entry in agents_data.get("workflows", {}).items():
            workflows[name] = WorkflowDef(**entry)

        # Parse routing
        routing: dict[str, RouteEntry] = {}
        for key, entry in agents_data.get("routing", {}).items():
            routing[key] = RouteEntry(**entry)

        # Parse loops
        loops_data = agents_data.get("loops", {})
        iterative_data = loops_data.get("iterative", {})
        routing_loop_data = loops_data.get("routing", {})
        loops = LoopsConfig(
            iterative=IterativeLoop(**iterative_data),
            routing=RoutingLoop(**routing_loop_data),
        )

        # Parse guards (from rules.yaml)
        guards_data = rules_data.get("guards", {})
        retry_data = guards_data.get("retry", {})
        timeout_data = guards_data.get("timeout", {})
        guards = GuardsConfig(
            retry=RetryConfig(**retry_data),
            timeout=TimeoutConfig(**timeout_data),
        )

        cfg = MaopConfig(
            agents=agents,
            workflows=workflows,
            routing=routing,
            loops=loops,
            guards=guards,
        )
        cfg._raw_models = models_data or {}
        cfg._version = _next_config_version()
        _agent_config_cache[cache_key] = (sig, cfg)
        return cfg

    def reload(self) -> MaopConfig:
        """Re-read config files (for hot-reload support).

        Clears the config cache first so the next load() is guaranteed to
        re-read from disk regardless of mtime.
        """
        _agent_config_cache.clear()
        return self.load()


def load_config(project_root: Path | str | None = None) -> MaopConfig:
    """Convenience function: load MAOP config from the given root."""
    return ConfigLoader(project_root=project_root).load()
