"""MAOP Core Protocols — Structural typing interfaces for Mixin dependencies.

P4-§1.6: replaces ``Any`` annotations on ``ExecuteMixin`` attributes with
``typing.Protocol`` interfaces so mypy can verify the host class (``MaopLoop``)
provides compatible implementations, and IDEs can jump-to-definition on
``self._dispatcher.dispatch(...)`` etc.

All protocols are ``runtime_checkable`` so callers may use ``isinstance``
checks when needed (none are required by current call sites, but the
decorator is cheap and keeps the option open).

Why structural typing instead of ABCs?
  - The host class ``MaopLoop`` already exists and inherits ``ExecuteMixin``;
    switching to ABCs would force multiple inheritance changes and risk MRO
    surprises. Protocols are purely structural — no inheritance required.
  - Several real implementations (``Dispatcher``, ``WorkerPool``, ``EventBus``)
    are Pydantic models / plain classes that we cannot retroactively make
    subclass an ABC without touching their definitions.

References:
  - loop_executor.py: ExecuteMixin._dispatcher / _worker_pool / _loop_config /
    _bus / _log
  - maop_loop.py: MaopLoop (host class)
  - delegate/dispatcher.py: Dispatcher.dispatch -> DispatchResult
  - core/reliability/worker_pool.py: WorkerPool._sem
  - core/reliability/event_bus.py: EventBus.publish
  - loop_models.py: LoopConfig (max_workers, iterative_max_attempts, ...)
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Protocol, runtime_checkable

# Type-only imports — avoid runtime cycle (delegate imports core).
from typing import TYPE_CHECKING

if TYPE_CHECKING:

    from maop.core.reliability.event_bus import Event
    from maop.delegate.models import DispatchResult


# ── Dispatcher ────────────────────────────────────────────────


@runtime_checkable
class DispatcherProtocol(Protocol):
    """Structural interface for ``ExecuteMixin._dispatcher``.

    Mirrors ``maop.delegate.dispatcher.Dispatcher.dispatch``. Only the
    subset of methods actually called from ``loop_executor.py`` is
    declared; extra dispatcher methods (``dispatch_priority`` etc.) are
    intentionally omitted — Protocol membership is structural and does
    not require exhaustive coverage.
    """

    async def dispatch(
        self,
        agent: str,
        task: str,
        *,
        routing_key: str = ...,
        workdir: str = ...,
        timeout_seconds: int | None = ...,
        trace_id: str = ...,
        streamer: Any | None = ...,
        priority: int = ...,
        deadline_ms: int | None = ...,
    ) -> "DispatchResult":
        """Dispatch a task to the specified agent; returns envelope."""
        ...


# ── WorkerPool ────────────────────────────────────────────────


@runtime_checkable
class WorkerPoolProtocol(Protocol):
    """Structural interface for ``ExecuteMixin._worker_pool``.

    ``loop_executor._resolve_semaphore`` reads ``self._worker_pool._sem``
    via ``getattr``; we surface it as a typed attribute here so mypy can
    verify the host's WorkerPool exposes a Semaphore. The attribute is
    intentionally not ``@property`` — the real WorkerPool sets it in
    ``__init__``.
    """

    _sem: asyncio.Semaphore


# ── LoopConfig ────────────────────────────────────────────────


@runtime_checkable
class LoopConfigProtocol(Protocol):
    """Structural interface for ``ExecuteMixin._loop_config``.

    Covers every config attribute read by ``loop_executor.py`` AND
    ``maop_loop.py`` (the host class). LoopConfig is a Pydantic BaseModel
    with ~30 fields; we declare the subset actually consumed by the
    mixin + host so mypy can verify attribute access.

    loop_executor.py reads:
      - ``max_workers`` (``_resolve_semaphore`` fallback)
      - ``iterative_max_attempts`` (``_try_agent_with_iterations``)
      - ``iterative_backoff_ms`` (``_try_agent_with_iterations`` sleep)
      - ``retry_backoff_ms`` (``_execute_with_retry`` inter-agent sleep)

    maop_loop.py reads (host class):
      - ``enable_parallel``, ``enable_load_balancer``, ``enable_cache_guard``,
        ``enable_result_cache``, ``enable_metrics``, ``enable_timeseries``,
        ``enable_evolve``, ``enable_dream``, ``enable_log_rotation``,
        ``enable_memory_inject``, ``enable_semantic_analyze``,
        ``enable_llm_analyze``
      - ``skip_verify``, ``skip_analyze``
      - ``default_timeout_s``, ``feedback_max_cycles``, ``max_subtasks``
      - ``log_rotation_max_kb``, ``log_rotation_retain``
      - ``dream_interval_cycles``, ``llm_analyze_model``
    """

    # WorkerPool
    max_workers: int
    enable_parallel: bool
    # Retry / iterative
    iterative_max_attempts: int
    iterative_backoff_ms: int
    retry_backoff_ms: int
    # Timeouts / cycles
    default_timeout_s: int
    feedback_max_cycles: int
    max_subtasks: int
    # Skip flags
    skip_verify: bool
    skip_analyze: bool
    # Feature toggles (bool)
    enable_load_balancer: bool
    enable_cache_guard: bool
    enable_result_cache: bool
    enable_metrics: bool
    enable_timeseries: bool
    enable_evolve: bool
    enable_dream: bool
    enable_log_rotation: bool
    enable_memory_inject: bool
    enable_semantic_analyze: bool
    enable_llm_analyze: bool
    # Log rotation
    log_rotation_max_kb: int
    log_rotation_retain: int
    # Dream
    dream_interval_cycles: int
    # LLM analyze
    llm_analyze_model: str


# ── EventBus ──────────────────────────────────────────────────


@runtime_checkable
class EventBusProtocol(Protocol):
    """Structural interface for ``ExecuteMixin._bus``.

    ``loop_executor._make_dag_emitter`` only needs to pass the bus to
    ``DagProgressEmitter(bus, ...)``; the emitter itself calls
    ``bus.publish(...)``. We declare ``publish`` here so the bus
    attribute is typed and downstream emitter code can be tightened
    incrementally.
    """

    async def publish(self, event: "Event") -> int:
        """Publish an event; returns number of successful handlers."""
        ...


# ── Logger callable ───────────────────────────────────────────

# ``MaopLoop._log`` has signature ``(phase, level, message, **data) -> None``.
# ``Callable`` cannot express ``**kwargs: Any`` precisely, so we use the
# broad ``Callable[..., None]`` form. This still beats ``Any``: mypy will
# reject ``self._log(123)`` (wrong arity-ish) at call sites that pass
# positional args, and IDEs get a signature hint.
LogCallable = Callable[..., None]


__all__ = [
    "DispatcherProtocol",
    "WorkerPoolProtocol",
    "LoopConfigProtocol",
    "EventBusProtocol",
    "LogCallable",
]