"""Shared state for MAOP Dashboard routers.

All router modules import from here to access shared resources:
  - MAOP_ROOT, DASH_DIR, SRC_DIR: path constants
  - get_bridge(): lazy DataProxy singleton
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
from maop.dashboard.data_proxy import DataProxy

_bridge: DataProxy | None = None

def get_bridge() -> DataProxy:
    """Lazy-init DataProxy singleton."""
    global _bridge
    if _bridge is None:
        _bridge = DataProxy(root_dir=MAOP_ROOT)
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
    _lazy_import("analyzer", "maop.core.agent.analyzer", "analyze")
    _lazy_import("cache_guard", "maop.core.reliability.cache", "CacheGuard")
    _lazy_import("cache_lru", "maop.core.reliability.cache", "get_cache")
    _lazy_import("vector", "maop.core.memory.vector", "VectorStore")
    _lazy_import("worker_pool", "maop.core.reliability.worker_pool", "WorkerPool")
    _lazy_import("load_balancer", "maop.core.routing.load_balancer", "LoadBalancer")
    _lazy_import("evolve", "maop.evolve", "EvolveEngine")
    _lazy_import("monitoring", "maop.core.monitoring.monitoring", "MetricsCollector")
    _lazy_import("timeseries", "maop.core.monitoring.timeseries", "TimeSeriesStore")
    _lazy_import("message_queue", "maop.core.reliability.message_queue", "MessageQueue")
    _lazy_import("bloom_filter", "maop.core.memory.bloom_filter", "BloomFilter")
    _lazy_import("kv_store", "maop.core.backends.kv_store", "KVStore")
    _lazy_import("migration", "maop.core.backends.migration", "MigrationManager")
    _lazy_import("hot_reload", "maop.config.hot_reload", "ConfigHotReload")
    _lazy_import("context_compressor", "maop.core.agent.llm_chat.context_compressor", "ContextCompressor")
    _lazy_import("prompt_manager", "maop.prompt_manager", "PromptManager")
    _lazy_import("sandbox", "maop.core.security.sandbox", "SandboxManager")
    _lazy_import("runtime", "maop.core.agent.lifecycle.runtime", "create_runtime")
    _lazy_import("circuit_breaker", "maop.core.reliability.circuit_breaker", "CircuitBreaker")
    _lazy_import("guardrail", "maop.core.security.guardrail", "Guardrail")
    _lazy_import("rate_limiter", "maop.core.reliability.rate_limiter", "RateLimiter")
    _lazy_import("auth", "maop.core.security.auth", "AuthManager")
    _lazy_import("subagent", "maop.core.subagent_lifecycle", "SubAgentManager")
    _lazy_import("worktree", "maop.core.agent.memory_ctx.worktree", "WorktreeManager")
    _lazy_import("protocol", "maop.core.agent.plugins_hooks.protocol", "ProtocolRegistry")
    _lazy_import("api_key_vault", "maop.core.security.api_key_vault", "ApiKeyVault")
    _lazy_import("provider_health", "maop.core.routing.provider_health", "ProviderHealthChecker")
    _lazy_import("hook_manager", "maop.core.agent.plugins_hooks.hook_manager", "HookManager")
    _lazy_import("budget_guard", "maop.core.monitoring.budget_guard", "BudgetGuard")
    _lazy_import("tool_audit", "maop.core.agent.tools.tool_audit", "ToolAuditLog")
    _lazy_import("agent_proxy", "maop.core.agent.delegation.agent_proxy", "AgentProxy")
    _lazy_import("mcp_hub", "maop.core.mcp.mcp_hub", "MCPHub")
    _lazy_import("skill_version", "maop.core.evolution.skill_version", "SkillVersionManager")

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
# High 安全修复 (2.3): secure-by-default，与 routers/auth.py 和
# settings._default_auth_enabled 保持一致。
_env_is_dev = os.environ.get("MAOP_ENV", "").strip().lower() in (
    "dev", "development", "local", "test",
)
auth_enabled = os.environ.get("MAOP_AUTH", "0" if _env_is_dev else "1") == "1"
rl_enabled = os.environ.get("MAOP_RATE_LIMIT", os.environ.get("MAOP_RATE_LIMIT_ENABLED", "1")) == "1"
