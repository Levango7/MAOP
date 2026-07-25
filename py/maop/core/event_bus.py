"""MAOP Event Bus — decoupled pub/sub with ACK, retry, and dead-letter support.

Replaces direct function calls between modules with async event dispatch.
Supports sync and async subscribers, wildcard topics, error isolation,
ACK confirmation, configurable retries, and dead-letter tracking.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import re
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Awaitable, Callable, Union

logger = logging.getLogger(__name__)

# M7 fix (Phase R7): 默认上限提取为命名常量，便于统一调整
_DEFAULT_MAX_HISTORY = 200
_DEFAULT_MAX_DEAD_LETTERS = 1000

# ── Event model ───────────────────────────────────────────────


class EventPriority(int, Enum):
    LOW = 0
    NORMAL = 1
    HIGH = 2
    CRITICAL = 3


class AckStatus(str, Enum):
    """Delivery status for an event handler invocation."""
    PENDING = "pending"
    ACKED = "acked"
    FAILED = "failed"
    DEAD = "dead"  # exhausted retries → dead letter


@dataclass
class Event:
    """An event dispatched through the bus."""
    topic: str
    data: dict[str, Any] = field(default_factory=dict)
    source: str = ""
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    priority: EventPriority = EventPriority.NORMAL
    ack_required: bool = False

    # Internal: set by bus after dispatch
    _id: int = field(default=0, init=False, repr=False)


@dataclass
class DeadLetterEntry:
    """Record of a handler invocation that exhausted all retries."""
    event_id: int
    topic: str
    handler_name: str
    error: str
    attempts: int
    timestamp: float = field(default_factory=time.time)


# ── Subscriber type ───────────────────────────────────────────

SyncHandler = Callable[[Event], None]
AsyncHandler = Callable[[Event], Awaitable[None]]
Handler = Union[SyncHandler, AsyncHandler]


@dataclass
class _Subscription:
    handler: Handler
    is_async: bool
    priority: EventPriority
    filter_regex: re.Pattern | None
    max_retries: int  # 0 = no retry (fire-and-forget), >0 = retry count
    retry_delay_s: float  # delay between retries


# ── Event Bus ─────────────────────────────────────────────────


class EventBus:
    """Async-capable pub/sub event bus with ACK and retry support.

    Usage::

        bus = EventBus()

        # Subscribe (with retry on failure)
        def on_result(event: Event):
            print(event.data)

        bus.subscribe("execution.result", on_result, max_retries=3)

        # Publish (ACK-required)
        await bus.publish(Event(
            topic="execution.result",
            data={"agent": "claude"},
            ack_required=True,
        ))

    Wildcard subscriptions:
        bus.subscribe("execution.*", handler)

    ACK + Retry:
        - If ``ack_required=True`` on the event, failed handlers are retried
          up to ``max_retries`` times with ``retry_delay_s`` backoff.
        - After exhausting retries, the failure is recorded in the dead letter log.
        - Use ``get_dead_letters()`` to inspect unrecoverable failures.
    """

    def __init__(self) -> None:
        self._subs: dict[str, list[_Subscription]] = defaultdict(list)
        self._counter = 0
        self._history: list[Event] = []
        self._max_history = _DEFAULT_MAX_HISTORY
        self._dead_letters: list[DeadLetterEntry] = []
        self._max_dead_letters = _DEFAULT_MAX_DEAD_LETTERS
        # Track in-flight retry tasks so we can await them on close
        self._retry_tasks: list[asyncio.Task] = []

    # ── Subscribe ─────────────────────────────────────────────

    def subscribe(
        self,
        topic: str,
        handler: Handler,
        *,
        priority: EventPriority = EventPriority.NORMAL,
        filter_pattern: str | None = None,
        max_retries: int = 0,
        retry_delay_s: float = 1.0,
    ) -> None:
        """Register a handler for a topic.

        Parameters
        ----------
        topic : str
            Topic string. Supports '*' wildcard at end (e.g. "execution.*").
        handler : Handler
            Sync or async callable receiving an Event.
        priority : EventPriority
            Higher priority handlers run first within a topic.
        filter_pattern : str | None
            Optional regex; event.data is matched against this pattern
            for fine-grained filtering.
        max_retries : int
            Number of retries on handler failure (0 = fire-and-forget).
        retry_delay_s : float
            Seconds to wait between retries.
        """
        is_async = inspect.iscoroutinefunction(handler)
        regex = re.compile(filter_pattern) if filter_pattern else None
        sub = _Subscription(
            handler=handler,
            is_async=is_async,
            priority=priority,
            filter_regex=regex,
            max_retries=max_retries,
            retry_delay_s=retry_delay_s,
        )
        self._subs[topic].append(sub)
        # Keep sorted by priority descending
        self._subs[topic].sort(key=lambda s: s.priority.value, reverse=True)

    def unsubscribe(self, topic: str, handler: Handler) -> None:
        """Remove a handler from a topic."""
        if topic in self._subs:
            self._subs[topic] = [
                s for s in self._subs[topic] if s.handler is not handler
            ]

    # ── Publish ───────────────────────────────────────────────

    async def publish(self, event: Event) -> int:
        """Dispatch an event to all matching subscribers.

        Returns the number of handlers that succeeded.
        """
        self._counter += 1
        event._id = self._counter

        # Record in history
        self._history.append(event)
        if len(self._history) > self._max_history:
            self._history = self._history[-self._max_history:]

        matching_subs = self._find_matching_subs(event)
        handlers_succeeded = 0

        for sub in matching_subs:
            # Apply filter regex if set
            if sub.filter_regex is not None:
                import json
                data_str = json.dumps(event.data, default=str)
                if not sub.filter_regex.search(data_str):
                    continue

            # Determine if retry is needed
            should_retry = event.ack_required and sub.max_retries > 0

            if should_retry:
                # Invoke with retry loop
                success = await self._invoke_with_retry(event, sub)
                if success:
                    handlers_succeeded += 1
            else:
                # Fire-and-forget (original behavior)
                success = await self._invoke_once(event, sub)
                if success:
                    handlers_succeeded += 1

                # If ack_required but no retries configured, record failure
                if event.ack_required and not success:
                    self._add_dead_letter(
                        event._id, event.topic,
                        sub.handler.__name__, "no retry configured", 1,
                    )

        return handlers_succeeded

    async def _invoke_once(self, event: Event, sub: _Subscription) -> bool:
        """Invoke handler once. Returns True on success, False on failure."""
        try:
            if sub.is_async:
                await sub.handler(event)  # type: ignore[misc]
            else:
                sub.handler(event)
            return True
        except Exception as exc:
            logger.error(
                "Event handler error on %s [%s]: %s",
                event.topic, sub.handler.__name__, exc,
            )
            return False

    async def _invoke_with_retry(self, event: Event, sub: _Subscription) -> bool:
        """Invoke handler with retry loop. Returns True if eventually succeeded."""
        last_error = "unknown"
        for attempt in range(1 + sub.max_retries):
            if attempt > 0:
                logger.info(
                    "Retrying handler %s on %s (attempt %d/%d)",
                    sub.handler.__name__, event.topic,
                    attempt, sub.max_retries,
                )
                await asyncio.sleep(sub.retry_delay_s)

            success = await self._invoke_once(event, sub)
            if success:
                return True

            # Capture error for dead letter
            last_error = f"attempt {attempt + 1} failed"

        # Exhausted retries → dead letter
        self._add_dead_letter(
            event._id, event.topic,
            sub.handler.__name__, last_error,
            1 + sub.max_retries,
        )
        return False

    def _add_dead_letter(
        self,
        event_id: int,
        topic: str,
        handler_name: str,
        error: str,
        attempts: int,
    ) -> None:
        """Record a dead letter entry."""
        entry = DeadLetterEntry(
            event_id=event_id,
            topic=topic,
            handler_name=handler_name,
            error=error,
            attempts=attempts,
        )
        self._dead_letters.append(entry)
        if len(self._dead_letters) > self._max_dead_letters:
            self._dead_letters = self._dead_letters[-self._max_dead_letters:]
        logger.warning(
            "Dead letter: event %d on %s handler %s after %d attempts: %s",
            event_id, topic, handler_name, attempts, error,
        )

    def _find_matching_subs(self, event: Event) -> list[_Subscription]:
        """Find and deduplicate matching subscriptions for an event."""
        matching_subs: list[_Subscription] = []

        # Exact match
        if event.topic in self._subs:
            matching_subs.extend(self._subs[event.topic])

        # Wildcard match: "execution.*" matches "execution.result"
        for pattern_topic, subs in self._subs.items():
            if pattern_topic.endswith(".*"):
                prefix = pattern_topic[:-1]  # "execution."
                if event.topic.startswith(prefix) and pattern_topic != event.topic:
                    matching_subs.extend(subs)

        # Deduplicate by handler identity
        seen_handlers: set[int] = set()
        unique_subs: list[_Subscription] = []
        for sub in matching_subs:
            hid = id(sub.handler)
            if hid not in seen_handlers:
                seen_handlers.add(hid)
                unique_subs.append(sub)

        # Sort by priority
        unique_subs.sort(key=lambda s: s.priority.value, reverse=True)
        return unique_subs

    def publish_sync(self, event: Event) -> int:
        """Synchronous publish — runs async handlers via asyncio.run if needed."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            asyncio.ensure_future(self.publish(event))
            return 0  # best-effort
        else:
            return asyncio.run(self.publish(event))

    # ── Query ─────────────────────────────────────────────────

    def get_history(
        self,
        topic: str | None = None,
        limit: int = 50,
    ) -> list[Event]:
        """Return recent events, optionally filtered by topic prefix."""
        events = self._history
        if topic:
            events = [e for e in events if e.topic.startswith(topic)]
        return events[-limit:]

    def get_dead_letters(
        self,
        topic: str | None = None,
        limit: int = 50,
    ) -> list[DeadLetterEntry]:
        """Return dead letter entries, optionally filtered by topic."""
        entries = self._dead_letters
        if topic:
            entries = [e for e in entries if e.topic == topic]
        return entries[-limit:]

    def dead_letter_count(self) -> int:
        """Total dead letter entries."""
        return len(self._dead_letters)

    def subscriber_count(self, topic: str | None = None) -> int:
        """Count subscribers, optionally for a specific topic."""
        if topic:
            return len(self._subs.get(topic, []))
        return sum(len(subs) for subs in self._subs.values())

    def clear(self) -> None:
        """Remove all subscriptions, history, and dead letters."""
        self._subs.clear()
        self._history.clear()
        self._dead_letters.clear()


# ── Global singleton ──────────────────────────────────────────

_global_bus: EventBus | None = None


def get_event_bus() -> EventBus:
    """Get the global event bus singleton."""
    global _global_bus
    if _global_bus is None:
        _global_bus = EventBus()
    return _global_bus
