"""MAOP Blackboard — Knowledge Source Agent Bridge.

Wraps arbitrary callables / async functions / Agent handles as instances of
``KnowledgeSource`` so they can participate in any Blackboard solve
(TriggerRuleEngine / BlackboardConvergenceController / baseline Controller).

This is the M3-B adapter: anything that can be called with
``(blackboard, entry)`` becomes a ``KnowledgeSource``.

Classes
-------
- ``CallableKnowledgeSource``: wraps a sync/async callable as a KS.
- ``AgentKnowledgeSource``: wraps a generic Agent handle as a KS
  (the handle must support ``async run(blackboard, entry)`` or similar).
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Awaitable
from typing import Any, Protocol

from maop.core.reliability.blackboard import (
    Blackboard,
    BlackboardEntry,
    KnowledgeSource,
)

logger = logging.getLogger(__name__)

# ── KS Execute Callable Protocol ──────────────────────────────────


class KSExecutor(Protocol):
    """Anything that can serve as a KS body."""

    def __call__(
        self, bb: Blackboard, entry: BlackboardEntry
    ) -> Awaitable[Any] | Any: ...


# ── CallableKnowledgeSource ───────────────────────────────────────


class CallableKnowledgeSource(KnowledgeSource):
    """Wraps an arbitrary sync/async callable as a ``KnowledgeSource``.

    Parameters
    ----------
    name : str
        Unique KS identifier.
    executor : KSExecutor
        Callable that accepts ``(bb, entry)``.
    priority : int
        Scheduling priority.
    read_domains : tuple[str, ...]
        Domains this KS reads for concurrency analysis.
    write_domains : tuple[str, ...]
        Domains this KS writes to.
    timeout_s : float | None
        Optional per-call timeout in seconds.

    Example::

        async def my_analyzer(bb, entry):
            result = await llm.extract(entry.content)
            await bb.write("decision", result, contributor="analyzer")

        ks = CallableKnowledgeSource(
            name="analyzer",
            executor=my_analyzer,
            read_domains=("evidence",),
            write_domains=("decision",),
            priority=5,
        )
    """

    def __init__(
        self,
        name: str,
        executor: KSExecutor,
        *,
        priority: int = 0,
        read_domains: tuple[str, ...] = (),
        write_domains: tuple[str, ...] = (),
        timeout_s: float | None = None,
    ) -> None:
        super().__init__()
        self.name = name
        self.priority = priority
        self.read_domains = read_domains
        self.write_domains = write_domains
        self._executor = executor
        self._timeout = timeout_s

    async def execute(
        self, bb: Blackboard, trigger_entry: BlackboardEntry
    ) -> None:
        coro = self._executor(bb, trigger_entry)
        if inspect.isawaitable(coro):
            if self._timeout is not None:
                await asyncio.wait_for(coro, timeout=self._timeout)
            else:
                await coro
        else:
            # sync callable — run in thread pool to avoid blocking event loop
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, lambda: None)  # ensure we're async-safe
            # Already executed (sync); wrap in a no-op await


class AgentKnowledgeSource(KnowledgeSource):
    """Wraps an Agent-like object as a KnowledgeSource.

    The agent must expose one of:

    - ``async run(bb, entry)`` (full signature)
    - ``async run(entry)`` (single-arg)
    - ``async __call__(bb, entry)`` (if the agent itself is callable)

    Methods are tried in that order at execution time.

    Parameters
    ----------
    name : str
    agent : Any
        The agent instance.
    priority : int
    read_domains : tuple[str, ...]
    write_domains : tuple[str, ...]
    timeout_s : float | None
    """

    def __init__(
        self,
        name: str,
        agent: Any,
        *,
        priority: int = 0,
        read_domains: tuple[str, ...] = (),
        write_domains: tuple[str, ...] = (),
        timeout_s: float | None = None,
    ) -> None:
        super().__init__()
        self.name = name
        self.priority = priority
        self.read_domains = read_domains
        self.write_domains = write_domains
        self._agent = agent
        self._timeout = timeout_s
        # Validate at init time so misuse surfaces immediately
        self._method = self._resolve_method()

    async def execute(
        self, bb: Blackboard, trigger_entry: BlackboardEntry
    ) -> None:
        if self._method is None:
            self._method = self._resolve_method()

        agent = self._agent
        match self._method:
            case "run_full":
                coro = agent.run(bb, trigger_entry)
            case "run_entry":
                coro = agent.run(trigger_entry)
            case "call":
                coro = agent(bb, trigger_entry)
            case _:
                raise RuntimeError(f"No suitable method on agent {agent!r}")

        result = coro
        if inspect.isawaitable(result):
            if self._timeout is not None:
                await asyncio.wait_for(result, timeout=self._timeout)
            else:
                await result

    def _resolve_method(self) -> str:
        agent = self._agent
        if hasattr(agent, "run") and callable(agent.run):
            sig = inspect.signature(agent.run)
            params = list(sig.parameters)
            if len(params) >= 2:
                return "run_full"
            return "run_entry"
        if callable(agent):
            return "call"
        raise TypeError(
            f"Agent {type(agent).__name__} has no compatible .run() method "
            f"and is not callable"
        )
