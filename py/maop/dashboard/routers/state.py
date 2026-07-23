"""Shared state for MAOP Dashboard routers.

All router modules import from here to access shared resources:
  - MAOP_ROOT, DASH_DIR, SRC_DIR: path constants
  - get_bridge(): lazy DataBridge singleton
  - cache, cache_lock: in-memory cache
  - active_jobs: running job registry
  - start_time: server start timestamp
  - config flags: tls_enabled, auth_enabled, rl_enabled
"""

from __future__ import annotations

import asyncio
import importlib
import os
import time
from pathlib import Path
from typing import Any

# ── Paths ──────────────────────────────────────────────────────────
MAOP_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
SRC_DIR = MAOP_ROOT / "src"
DASH_DIR = MAOP_ROOT / "dashboard"

# ── Data Bridge ────────────────────────────────────────────────────
from maop.dashboard.data_bridge import DataBridge
_bridge: DataBridge | None = None

def get_bridge() -> DataBridge:
    """Lazy-init DataBridge singleton."""
    global _bridge
    if _bridge is None:
        _bridge = DataBridge(root_dir=MAOP_ROOT)
    return _bridge

# ── Shared cache ───────────────────────────────────────────────────
cache: dict[str, tuple[float, Any]] = {}
cache_lock = asyncio.Lock()

# ── Active jobs ────────────────────────────────────────────────────
active_jobs: dict[str, Any] = {}

# ── Subsystem Registry ─────────────────────────────────────────────
_SUBSYSTEMS: dict[str, Any] = {}

def init_subsystems() -> None:
    """Initialize all subsystems for dashboard integration."""
    if _SUBSYSTEMS:
        return
    _lazy_import("analyzer", "maop.core.analyzer", "analyze")
    _lazy_import("cache_guard", "maop.core.cache_guard", "CacheGuard")
    _lazy_import("cache_lru", "maop.core.cache", "get_cache")
    _lazy_import("vector", "maop.core.vector", "VectorStore")
    _lazy_import("worker_pool", "maop.core.worker_pool", "WorkerPool")
    _lazy_import("load_balancer", "maop.core.load_balancer", "LoadBalancer")
    _lazy_import("evolve", "maop.evolve", "EvolveEngine")
    _lazy_import("monitoring", "maop.core.monitoring", "MetricsCollector")
    _lazy_import("timeseries", "maop.core.timeseries", "TimeSeriesStore")
    _lazy_import("message_queue", "maop.core.message_queue", "MessageQueue")
    _lazy_import("bloom_filter", "maop.core.bloom_filter", "BloomFilter")
    _lazy_import("kv_store", "maop.core.kv_store", "KVStore")
    _lazy_import("migration", "maop.core.migration", "MigrationManager")
    _lazy_import("hot_reload", "maop.config.hot_reload", "ConfigHotReload")
    _lazy_import("context_compressor", "maop.core.context_compressor", "ContextCompressor")
    _lazy_import("prompt_manager", "maop.prompt_manager", "PromptManager")
    _lazy_import("sandbox", "maop.core.sandbox", "SandboxManager")
    _lazy_import("runtime", "maop.core.runtime", "create_runtime")
    _lazy_import("circuit_breaker", "maop.core.circuit_breaker", "CircuitBreaker")
    _lazy_import("guardrail", "maop.core.guardrail", "Guardrail")
    _lazy_import("rate_limiter", "maop.core.rate_limiter", "RateLimiter")
    _lazy_import("auth", "maop.core.auth", "AuthManager")
    _lazy_import("subagent", "maop.core.subagent_lifecycle", "SubAgentManager")
    _lazy_import("worktree", "maop.core.worktree", "WorktreeManager")
    _lazy_import("protocol", "maop.core.protocol", "ProtocolRegistry")
    _lazy_import("api_key_vault", "maop.core.api_key_vault", "ApiKeyVault")
    _lazy_import("provider_health", "maop.core.provider_health", "ProviderHealthChecker")
    _lazy_import("hook_manager", "maop.core.hook_manager", "HookManager")
    _lazy_import("budget_guard", "maop.core.budget_guard", "BudgetGuard")
    _lazy_import("tool_audit", "maop.core.tool_audit", "ToolAuditLog")
    _lazy_import("agent_bridge", "maop.core.agent_bridge", "AgentBridge")
    _lazy_import("mcp_hub", "maop.core.mcp_hub", "MCPHub")
    _lazy_import("skill_version", "maop.core.skill_version", "SkillVersionManager")

def _lazy_import(name: str, module_path: str, class_name: str) -> None:
    try:
        mod = importlib.import_module(module_path)
        cls = getattr(mod, class_name)
        _SUBSYSTEMS[name] = {"class": cls, "available": True, "module": module_path}
    except Exception as exc:
        _SUBSYSTEMS[name] = {"class": None, "available": False, "error": str(exc), "module": module_path}

def get_subsystems() -> dict[str, Any]:
    return _SUBSYSTEMS

# ── Start time ─────────────────────────────────────────────────────
start_time = time.time()

# ── Config flags ───────────────────────────────────────────────────
tls_enabled = os.environ.get("MAOP_TLS", "0") == "1"
_env_is_prod = os.environ.get("MAOP_ENV", "").strip().lower() == "production"
auth_enabled = os.environ.get("MAOP_AUTH", "1" if _env_is_prod else "0") == "1"
rl_enabled = os.environ.get("MAOP_RATE_LIMIT", "1") == "1"
