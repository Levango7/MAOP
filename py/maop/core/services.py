"""MAOP Service Container — Lightweight dependency injection for subsystem management.

Provides lazy initialization, shared instances, and graceful degradation
for the 15+ subsystems that MaopLoop depends on.

Usage::

    from maop.core.services import ServiceContainer

    container = ServiceContainer(root_dir="/path/to/MAOP")
    breaker = container.get("circuit_breaker")
    memory = container.get("memory_store")
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class ServiceContainer:
    """Lazy-initialized service container with shared instances."""

    def __init__(self, root_dir: str | Path) -> None:
        self._root = Path(root_dir)
        self._instances: dict[str, Any] = {}
        self._factories: dict[str, Callable[[], Any]] = {}
        self._register_defaults()

    def _register_defaults(self) -> None:
        self.register("circuit_breaker", self._make_circuit_breaker)
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
        if name in self._instances:
            return self._instances[name]
        factory = self._factories.get(name)
        if factory is None:
            return None
        try:
            instance = factory()
            self._instances[name] = instance
            return instance
        except Exception as exc:
            logger.error("Service %s init failed: %s", name, exc)
            if raise_on_failure:
                raise RuntimeError(f"Service '{name}' initialization failed: {exc}") from exc
            return None

    def set(self, name: str, instance: Any) -> None:
        self._instances[name] = instance

    def has(self, name: str) -> bool:
        return name in self._instances or name in self._factories

    def _make_circuit_breaker(self):
        from maop.core.circuit_breaker import CircuitBreaker
        return CircuitBreaker(self._root / "data" / "maop.db")

    def _make_dispatcher(self):
        from maop.delegate.dispatcher import Dispatcher
        breaker = self.get("circuit_breaker")
        config = self.get("config")
        return Dispatcher(MAOP_config=config, breaker=breaker)

    def _make_guardrail(self):
        from maop.core.guardrail import Guardrail
        return Guardrail()

    def _make_verify_engine(self):
        from maop.maop_verify import VerifyEngine
        return VerifyEngine()

    def _make_memory_store(self):
        from maop.memory.store import MemoryStore
        return MemoryStore(root_dir=self._root)

    def _make_worker_pool(self):
        from maop.core.worker_pool import get_worker_pool
        return get_worker_pool()

    def _make_load_balancer(self):
        from maop.core.load_balancer import get_load_balancer
        return get_load_balancer()

    def _make_cache_guard(self):
        from maop.core.cache import CacheGuard, CacheGuardConfig
        return CacheGuard(config=CacheGuardConfig())

    def _make_result_cache(self):
        from maop.core.cache import get_cache
        return get_cache("maop_loop_results", max_size=256, default_ttl_s=300)

    def _make_timeseries(self):
        from maop.core.timeseries import TimeSeriesStore
        return TimeSeriesStore(db_path=self._root / "data" / "timeseries.db")

    def _make_message_queue(self):
        from maop.core.message_queue import MessageQueue
        return MessageQueue(db_path=self._root / "data" / "queue.db")

    def _make_hot_reload(self):
        from maop.config.hot_reload import ConfigHotReload
        return ConfigHotReload(root_dir=self._root)

    def _make_kv_store(self):
        from maop.core.kv_store import KVStore
        return KVStore(db_path=self._root / "data" / "kv_store.db")

    def _make_prompt_manager(self):
        from maop.prompt_manager import PromptManager
        return PromptManager(root_dir=self._root)

    def _make_migration(self):
        from maop.core.migration import MigrationManager
        return MigrationManager(db_path=self._root / "data" / "maop.db")

    def _make_consolidator(self):
        from maop.memory.consolidator import DreamConsolidator
        memory = self.get("memory_store")
        if memory is None:
            return None
        return DreamConsolidator(memory_store=memory)

    def _make_hook_manager(self):
        from maop.core.hook_manager import get_hook_manager
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
        from maop.core.a2a import A2AManager
        worker_pool = self.get("worker_pool", raise_on_failure=False)
        return A2AManager(root_dir=str(self._root), worker_pool=worker_pool)
