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
import threading
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
# H3 fix: active_jobs 全局字典在多请求并发下会被同时读写（control_run 写入、
# control_status 迭代、control_cancel 修改），无锁保护会触发
# RuntimeError: dictionary changed size during iteration 或丢失更新。
# 提供 active_jobs_lock 供所有赋值/pop/迭代处用 `with active_jobs_lock:` 包裹。
active_jobs: dict[str, Any] = {}
active_jobs_lock = threading.Lock()

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
    _lazy_import("subagent", "maop.core.agent.delegation.subagent_lifecycle", "SubAgentManager")
    _lazy_import("worktree", "maop.core.agent.memory_ctx.worktree", "WorktreeManager")
    _lazy_import("protocol", "maop.core.agent.plugins_hooks.protocol", "ProtocolRegistry")
    _lazy_import("api_key_vault", "maop.core.security.api_key_vault", "ApiKeyVault")
    _lazy_import("provider_health", "maop.core.routing.provider_health", "ProviderHealthChecker")
    _lazy_import("hook_manager", "maop.core.agent.plugins_hooks.hook_manager", "HookManager")
    _lazy_import("budget_guard", "maop.core.budget_guard", "BudgetGuard")
    _lazy_import("tool_audit", "maop.core.agent.tools.tool_audit", "ToolAuditLog")
    _lazy_import("agent_proxy", "maop.core.agent.delegation.agent_proxy", "AgentProxy")
    _lazy_import("mcp_hub", "maop.core.mcp.mcp_hub", "MCPHub")
    _lazy_import("skill_version", "maop.core.evolution.skill_version", "SkillVersionManager")

def _lazy_import(name: str, module_path: str, class_name: str) -> None:
    try:
        mod = importlib.import_module(module_path)
        cls = getattr(mod, class_name)
        _SUBSYSTEMS[name] = {"class": cls, "available": True, "module": module_path}
    except Exception:
        # P1 fix: 不向客户端透传异常字符串（信息泄露），仅记录布尔标志。
        _SUBSYSTEMS[name] = {"class": None, "available": False, "error": True, "module": module_path}

def get_subsystems() -> dict[str, Any]:
    return _SUBSYSTEMS

# ── Start time ─────────────────────────────────────────────────────
start_time = time.time()

# ── Config flags ───────────────────────────────────────────────────
# M2 修复：统一读取 MAOP_TLS_ENABLED（兼容旧名 MAOP_TLS，触发 DeprecationWarning）
from maop.config.env import get_tls_enabled as _get_tls_enabled

tls_enabled = _get_tls_enabled()
# H-4 fix: 消除 auth_enabled 双真相源 —— 统一从 settings.py 的 MAOPSettings.auth_enabled
# 获取，与 server.py 保持单一来源。旧代码使用 os.environ.get("MAOP_AUTH", ...)
# 直接读取环境变量，绕过了 settings.py 的 AliasChoices（MAOP_AUTH_ENABLED 优先）
# 和 _default_auth_enabled 的 secure-by-default 策略。
from maop.config.settings import get_settings as _get_settings

auth_enabled = _get_settings().auth_enabled
rl_enabled = os.environ.get("MAOP_RATE_LIMIT", os.environ.get("MAOP_RATE_LIMIT_ENABLED", "1")) == "1"
