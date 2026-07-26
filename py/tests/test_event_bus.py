"""Tests for MAOP.core.event_bus — pub/sub with ACK, retry, and dead-letter."""

from __future__ import annotations

import asyncio

from maop.core.event_bus import (
    Event,
    EventBus,
    EventPriority,
)


class TestEventBus:
    def test_subscribe_and_publish_sync(self):
        bus = EventBus()
        received: list[Event] = []

        def handler(event: Event):
            received.append(event)

        bus.subscribe("test.topic", handler)
        event = Event(topic="test.topic", data={"key": "value"})

        result = asyncio.run(bus.publish(event))
        assert result == 1
        assert len(received) == 1
        assert received[0].data["key"] == "value"

    def test_async_handler(self):
        bus = EventBus()
        received: list[Event] = []

        async def handler(event: Event):
            received.append(event)

        bus.subscribe("async.topic", handler)
        event = Event(topic="async.topic", data={"x": 1})

        result = asyncio.run(bus.publish(event))
        assert result == 1
        assert len(received) == 1

    def test_wildcard_subscription(self):
        bus = EventBus()
        received: list[Event] = []

        def handler(event: Event):
            received.append(event)

        bus.subscribe("execution.*", handler)

        asyncio.run(bus.publish(Event(topic="execution.result", data={"ok": True})))
        asyncio.run(bus.publish(Event(topic="execution.error", data={"err": "fail"})))

        assert len(received) == 2

    def test_unsubscribe(self):
        bus = EventBus()
        received: list[Event] = []

        def handler(event: Event):
            received.append(event)

        bus.subscribe("test.topic", handler)
        bus.unsubscribe("test.topic", handler)

        result = asyncio.run(bus.publish(Event(topic="test.topic")))
        assert result == 0
        assert len(received) == 0

    def test_priority_ordering(self):
        bus = EventBus()
        order: list[str] = []

        def high_handler(event: Event):
            order.append("high")

        def low_handler(event: Event):
            order.append("low")

        bus.subscribe("prio", low_handler, priority=EventPriority.LOW)
        bus.subscribe("prio", high_handler, priority=EventPriority.HIGH)

        asyncio.run(bus.publish(Event(topic="prio")))
        assert order == ["high", "low"]

    def test_error_isolation(self):
        """A failing handler should not block other handlers."""
        bus = EventBus()
        received: list[Event] = []

        def bad_handler(event: Event):
            raise RuntimeError("boom")

        def good_handler(event: Event):
            received.append(event)

        bus.subscribe("err", bad_handler)
        bus.subscribe("err", good_handler)

        result = asyncio.run(bus.publish(Event(topic="err")))
        assert result == 1  # only good_handler succeeded
        assert len(received) == 1

    def test_history(self):
        bus = EventBus()
        asyncio.run(bus.publish(Event(topic="a", data={"n": 1})))
        asyncio.run(bus.publish(Event(topic="b", data={"n": 2})))

        history = bus.get_history()
        assert len(history) == 2

        filtered = bus.get_history(topic="a")
        assert len(filtered) == 1

    def test_subscriber_count(self):
        bus = EventBus()
        bus.subscribe("x", lambda e: None)
        bus.subscribe("x", lambda e: None)
        bus.subscribe("y", lambda e: None)

        assert bus.subscriber_count("x") == 2
        assert bus.subscriber_count() == 3

    def test_clear(self):
        bus = EventBus()
        bus.subscribe("x", lambda e: None)
        asyncio.run(bus.publish(Event(topic="x")))
        bus.clear()
        assert bus.subscriber_count() == 0
        assert len(bus.get_history()) == 0


class TestAckAndRetry:
    """Test ACK confirmation, retry, and dead-letter behavior."""

    def test_ack_success_first_try(self):
        """ACK-required event succeeds on first attempt."""
        bus = EventBus()
        received: list[Event] = []

        def handler(event: Event):
            received.append(event)

        bus.subscribe("ack.topic", handler, max_retries=3)
        event = Event(topic="ack.topic", ack_required=True)

        result = asyncio.run(bus.publish(event))
        assert result == 1
        assert len(received) == 1
        assert bus.dead_letter_count() == 0

    def test_retry_then_success(self):
        """Handler fails once, then succeeds on retry."""
        bus = EventBus()
        received: list[Event] = []
        call_count = 0

        def handler(event: Event):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("transient failure")
            received.append(event)

        bus.subscribe("retry.topic", handler, max_retries=3, retry_delay_s=0.01)
        event = Event(topic="retry.topic", ack_required=True)

        result = asyncio.run(bus.publish(event))
        assert result == 1
        assert len(received) == 1
        assert call_count == 2  # 1 fail + 1 success
        assert bus.dead_letter_count() == 0

    def test_retry_exhausted_dead_letter(self):
        """Handler fails all retries → dead letter."""
        bus = EventBus()

        def handler(event: Event):
            raise RuntimeError("permanent failure")

        bus.subscribe("dead.topic", handler, max_retries=2, retry_delay_s=0.01)
        event = Event(topic="dead.topic", ack_required=True)

        result = asyncio.run(bus.publish(event))
        assert result == 0  # 0 succeeded (all failed)
        assert bus.dead_letter_count() == 1

        dead = bus.get_dead_letters()
        assert dead[0].topic == "dead.topic"
        assert dead[0].attempts == 3  # 1 initial + 2 retries
        assert "permanent failure" in dead[0].error or "failed" in dead[0].error

    def test_ack_required_no_retries_dead_letter(self):
        """ACK-required event with no retries → immediate dead letter on failure."""
        bus = EventBus()

        def handler(event: Event):
            raise ValueError("no retry")

        bus.subscribe("noretry.topic", handler, max_retries=0)
        event = Event(topic="noretry.topic", ack_required=True)

        result = asyncio.run(bus.publish(event))
        assert result == 0  # 0 succeeded
        assert bus.dead_letter_count() == 1

    def test_no_ack_fire_and_forget(self):
        """Non-ACK event: handler failure is logged but no dead letter."""
        bus = EventBus()

        def handler(event: Event):
            raise RuntimeError("fire-and-forget failure")

        bus.subscribe("fan.topic", handler, max_retries=3)
        event = Event(topic="fan.topic", ack_required=False)

        result = asyncio.run(bus.publish(event))
        assert result == 0  # 0 succeeded (handler raised)
        # No dead letter because ack_required=False
        assert bus.dead_letter_count() == 0

    def test_async_handler_retry(self):
        """Async handler with retry."""
        bus = EventBus()
        call_count = 0

        async def handler(event: Event):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise RuntimeError("async fail")

        bus.subscribe("async.retry", handler, max_retries=3, retry_delay_s=0.01)
        event = Event(topic="async.retry", ack_required=True)

        result = asyncio.run(bus.publish(event))
        assert result == 1
        assert call_count == 3  # 2 fails + 1 success
        assert bus.dead_letter_count() == 0

    def test_dead_letter_filter_by_topic(self):
        """Dead letters can be filtered by topic."""
        bus = EventBus()

        def handler_a(event: Event):
            raise RuntimeError("fail a")

        def handler_b(event: Event):
            raise RuntimeError("fail b")

        bus.subscribe("topic.a", handler_a, max_retries=0)
        bus.subscribe("topic.b", handler_b, max_retries=0)

        asyncio.run(bus.publish(Event(topic="topic.a", ack_required=True)))
        asyncio.run(bus.publish(Event(topic="topic.b", ack_required=True)))

        assert bus.dead_letter_count() == 2
        assert len(bus.get_dead_letters(topic="topic.a")) == 1
        assert len(bus.get_dead_letters(topic="topic.b")) == 1

    def test_clear_removes_dead_letters(self):
        """clear() also removes dead letters."""
        bus = EventBus()

        def handler(event: Event):
            raise RuntimeError("fail")

        bus.subscribe("clear.topic", handler, max_retries=0)
        asyncio.run(bus.publish(Event(topic="clear.topic", ack_required=True)))
        assert bus.dead_letter_count() == 1

        bus.clear()
        assert bus.dead_letter_count() == 0

    def test_dead_letter_max_cap(self):
        """Dead letter log is capped at max_dead_letters."""
        bus = EventBus()
        bus._max_dead_letters = 5

        def handler(event: Event):
            raise RuntimeError("fail")

        bus.subscribe("cap.topic", handler, max_retries=0)

        for _i in range(10):
            asyncio.run(bus.publish(Event(topic="cap.topic", ack_required=True)))

        assert bus.dead_letter_count() == 5
