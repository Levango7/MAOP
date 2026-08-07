"""MAOP Service Container — Lightweight dependency injection for subsystem management.

Provides lazy initialization, shared instances, and graceful degradation
for the 15+ subsystems that MaopLoop depends on.

Usage::

    from maop.core.reliability.services import ServiceContainer

    container = ServiceContainer(root_dir="/path/to/MAOP")
    breaker = container.get("circuit_breaker")
    memory = container.get("memory_store")
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from maop.core.backends.db_utils import get_db_path

logger = logging.getLogger(__name__)


class ServiceContainer:
    """Lazy-initialized service container with shared instances."""

    def __init__(self, root_dir: str | Path) -> None:
        self._root = Path(root_dir)
        self._instances: dict[str, Any] = {}
        self._factories: dict[str, Callable[[], Any]] = {}
        # C6 fix: RLock (factories call self.get() recursively, e.g.
        # dispatcher → circuit_breaker) guards against double-init races;
        # _constructing detects circular factory dependencies.
        self._lock = threading.RLock()
        self._constructing: set[str] = set()
        self._register_defaults()

    def _register_defaults(self) -> None:
        self.register("circuit_breaker", self._make_circuit_breaker)
        # High fix: "config" was referenced by _make_dispatcher but never
        # registered — get("config") always returned None, silently degrading
        # the Dispatcher to registry-only agent resolution.
        self.register("config", self._make_config)
        self.register("dispatcher", self._make_dispatcher)
        self.register("guardrail", self._make_guardrail)
        self.register("verify_engine", self._make_verify_engine)
        self.register("memory_store", self._make_memory_store)
        self.register("worker_pool", self._make_worker_pool)
        self.register("load_balancer", self._make_load_balancer)
        self.register("cache_guard", self._make_cache_guard)
        self.register("result_cache", self._make_result_cache)
        self.register("timeseries", self._make_timeseries)
        self.register("message_queue", self._make_message_queue)
        self.register("hot_reload", self._make_hot_reload)
        self.register("kv_store", self._make_kv_store)
        self.register("prompt_manager", self._make_prompt_manager)
        self.register("migration", self._make_migration)
        self.register("consolidator", self._make_consolidator)
        self.register("hook_manager", self._make_hook_manager)
        # F6b (2026-07-22, Phase F): A2A manager — bridges A2A protocol
        # to MAOP's WorkerPool so external agents can dispatch tasks via
        # the JSON-RPC /a2a endpoint. WorkerPool injection is lazy: the
        # factory pulls it from the container at first access, which
        # guarantees the pool is constructed before the manager tries to
        # dispatch. See ADR-013.
        self.register("a2a_manager", self._make_a2a_manager)

    def register(self, name: str, factory: Callable[[], Any]) -> None:
        self._factories[name] = factory

    def get(self, name: str, *, raise_on_failure: bool = True) -> Any | None:
        # Fast path without lock for already-built singletons.
        if name in self._instances:
            return self._instances[name]
        with self._lock:
            # Re-check under lock (another thread may have built it).
            if name in self._instances:
                return self._instances[name]
            factory = self._factories.get(name)
            if factory is None:
                return None
            # C6 fix: circular dependency guard — a factory that (transitively)
            # calls get() for a service already under construction would
            # previously recurse until RecursionError with no useful message.
            if name in self._constructing:
                chain = " → ".join([*self._constructing, name])
                msg = f"Circular service dependency detected: {chain}"
                logger.error(msg)
                if raise_on_failure:
                    raise RuntimeError(msg)
                return None
            self._constructing.add(name)
            try:
                instance = factory()
                self._instances[name] = instance
                return instance
            except Exception as exc:
                logger.error("Service %s init failed: %s", name, exc)
                if raise_on_failure:
                    raise RuntimeError(f"Service '{name}' initialization failed: {exc}") from exc
                return None
            finally:
                self._constructing.discard(name)

    def set(self, name: str, instance: Any) -> None:
        with self._lock:
            self._instances[name] = instance

    def has(self, name: str) -> bool:
        return name in self._instances or name in self._factories

    def _make_circuit_breaker(self):
        from maop.core.reliability.circuit_breaker import CircuitBreaker
        return CircuitBreaker(get_db_path())

    def _make_config(self):
        from maop.config.loader import load_config
        return load_config(project_root=self._root)

    def _make_dispatcher(self):
        # Intentional lazy import (audit item 4.5): avoids a top-level
        # core->delegate dependency that would introduce a circular import,
        # preserving the strict downward layering.
        from maop.delegate.dispatcher import Dispatcher
        breaker = self.get("circuit_breaker")
        # High fix: don't hard-fail dispatcher construction if config load
        # fails — Dispatcher supports config=None (registry fallback).
        config = self.get("config", raise_on_failure=False)
        return Dispatcher(MAOP_config=config, breaker=breaker)

    def _make_guardrail(self):
        from maop.core.security.guardrail import Guardrail
        return Guardrail()

    def _make_verify_engine(self):
        from maop.maop_verify import VerifyEngine
        return VerifyEngine()

    def _make_memory_store(self):
        from maop.memory.store import MemoryStore
        return MemoryStore(root_dir=self._root)

    def _make_worker_pool(self):
        from maop.core.reliability.worker_pool import get_worker_pool
        return get_worker_pool()

    def _make_load_balancer(self):
        from maop.core.routing.load_balancer import get_load_balancer
        return get_load_balancer()

    def _make_cache_guard(self):
        from maop.core.reliability.cache import CacheGuard, CacheGuardConfig
        return CacheGuard(config=CacheGuardConfig())

    def _make_result_cache(self):
        from maop.core.reliability.cache import get_cache
        return get_cache("maop_loop_results", max_size=256, default_ttl_s=300)

    def _make_timeseries(self):
        from maop.core.monitoring.timeseries import TimeSeriesStore
        return TimeSeriesStore(db_path=get_db_path("timeseries"))

    def _make_message_queue(self):
        from maop.core.reliability.message_queue import MessageQueue
        return MessageQueue(db_path=get_db_path("queue"))

    def _make_hot_reload(self):
        from maop.config.hot_reload import ConfigHotReload
        return ConfigHotReload(root_dir=self._root)

    def _make_kv_store(self):
        from maop.core.backends.kv_store import KVStore
        return KVStore(db_path=get_db_path("kv_store"))

    def _make_prompt_manager(self):
        from maop.prompt_manager import PromptManager
        return PromptManager(root_dir=self._root)

    def _make_migration(self):
        from maop.core.backends.migration import MigrationManager
        return MigrationManager(db_path=get_db_path())

    def _make_consolidator(self):
        from maop.memory.consolidator import DreamConsolidator
        memory = self.get("memory_store")
        if memory is None:
            return None
        return DreamConsolidator(memory_store=memory)

    def _make_hook_manager(self):
        from maop.core.agent.plugins_hooks.hook_manager import get_hook_manager
        mgr = get_hook_manager(root_dir=str(self._root))
        try:
            mgr.load_from_yaml(self._root / "config" / "agents.yaml")
        except Exception as exc:
            logger.warning("Failed to load hooks from YAML: %s", exc)
        return mgr

    def _make_a2a_manager(self):
        """F6b (2026-07-22, Phase F): construct A2AManager with the
        container's WorkerPool injected.

        The WorkerPool is fetched via ``get("worker_pool", raise_on_failure=False)``
        so a missing/broken pool degrades gracefully — the A2AManager
        will still serve ``agent/card`` and ``tasks/get`` requests, only
        ``tasks/send`` becomes a no-op (records the task but doesn't
        execute it). When the pool is available, every dispatched task
        is forwarded with ``agent_name`` (see F6a) so the MaopLoop runs
        with the explicitly-requested agent. See ADR-013.
        """
        from maop.core.agent.delegation.a2a import A2AManager
        worker_pool = self.get("worker_pool", raise_on_failure=False)
        return A2AManager(root_dir=str(self._root), worker_pool=worker_pool)
