"""SSE streaming helpers split out of ``chat_service`` (file-size ratchet).

Pure async generators + event classification for the chat/stream domain.
The router still wraps them in ``StreamingResponse``; nothing here imports
FastAPI. ``chat_service`` forwards every public name below, so callers and
tests keep importing from the parent module.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "classify_agent_event",
    "get_streamer",
    "list_active_streams",
    "stream_dag_progress_chunks",
    "stream_from_event_bus",
    "stream_from_streamer",
    "stream_global_state_chunks",
]


def classify_agent_event(topic: str, data: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Classify an agent event bus event into an SSE event type + payload.

    P1-13: agent token events are published on sub-topics
    ``agent.{execution_id}.{token|meta|done|error}``. This helper maps
    the sub-topic suffix to the SSE ``event:`` line name.
    """
    if topic.endswith(".token"):
        return "token", data
    if topic.endswith(".meta"):
        return "meta", data
    if topic.endswith((".done", ".complete")):
        return "done", data
    if topic.endswith(".error"):
        return "error", data
    # Fallback: treat unknown sub-topics as token events.
    return "token", data


async def stream_global_state_chunks() -> Any:
    """Async generator yielding SSE ``state`` events for Monitor.vue.

    P0-2 fix: pushes global system state every 2s. The router wraps
    this in a :class:`StreamingResponse`.
    """
    while True:
        try:
            state: dict[str, Any] = {"ts": time.time()}
            try:
                from maop.dashboard.routers.state import get_bridge

                bridge = get_bridge()
                # F-P0-1 fix: call async snapshot() properly
                snap = await bridge.snapshot() if bridge else {}
                if snap:
                    state.update({
                        "agents": snap.get("agents_count", 0),
                        "healthy_agents": snap.get("healthy_agents", 0),
                        "total_agents": snap.get("total_agents", 0),
                        "memory_usage_pct": snap.get("memory_usage_pct", 0),
                        "cpu_pct": snap.get("cpu_pct", 0),
                        "queue_health_pct": snap.get("queue_health_pct", 0),
                        "active_streams": snap.get("active_streams", 0),
                        "success_rate": snap.get("success_rate", 0),
                        "delegations": snap.get("delegations", []),
                    })
            except Exception as exc:
                logger.warning("[stream] snapshot failed: %s", exc)
            yield f"event: state\ndata: {json.dumps(state)}\n\n"
        except Exception:
            logger.warning(
                '[stream] stream_global_state_chunks：SSE 状态推送单次循环失败已忽略',
                exc_info=True,
            )
        await asyncio.sleep(2)


def list_active_streams() -> dict[str, Any]:
    """List all currently active streaming executions."""
    from maop.core.reliability.streaming import get_stream_registry

    registry = get_stream_registry()
    active = registry.active()
    return {"active": active, "count": len(active)}


async def stream_dag_progress_chunks(execution_id: str, last_event_id: int) -> Any:
    """Async generator yielding DAG node-progress SSE events.

    Events:
      - event: node-status    (node state changes)
      - event: execution-complete (final event, closes the stream)

    Supports Last-Event-ID resumption: events with _id <= last_event_id
    are skipped.
    """
    from maop.core.reliability.event_bus import get_event_bus

    bus = get_event_bus()
    # Fetch history events for this execution
    history = bus.get_history(limit=500)
    sent_complete = False
    for evt in history:
        if evt._id <= last_event_id:
            continue
        # Filter events related to this execution_id
        topic = evt.topic
        if execution_id not in topic:
            continue
        # Determine event type from topic
        if "execution-complete" in topic:
            event_type = "execution-complete"
        else:
            event_type = "node-status"
        data = json.dumps(evt.data)
        yield f"id: {evt._id}\nevent: {event_type}\ndata: {data}\n\n"
        if event_type == "execution-complete":
            sent_complete = True
            break
    if not sent_complete:
        # No complete event in history; subscribe for live events
        queue: asyncio.Queue = asyncio.Queue()
        topic_pattern = f"dag.{execution_id}"

        async def _handler(event):
            await queue.put(event)

        bus.subscribe(topic_pattern, _handler)
        try:
            while True:
                evt = await asyncio.wait_for(queue.get(), timeout=30)
                if evt._id <= last_event_id:
                    continue
                topic = evt.topic
                if "execution-complete" in topic:
                    event_type = "execution-complete"
                else:
                    event_type = "node-status"
                data = json.dumps(evt.data)
                yield f"id: {evt._id}\nevent: {event_type}\ndata: {data}\n\n"
                if event_type == "execution-complete":
                    break
        finally:
            bus.unsubscribe(topic_pattern, _handler)


async def stream_from_streamer(streamer: Any) -> Any:
    """Stream tokens from an active StreamRegistry entry.

    Yields SSE-formatted events: ``token`` (each chunk), ``done``
    (completion), ``error`` (error event).
    """
    full_content: list[str] = []
    async for chunk in streamer.sse.stream():
        # Parse existing SSE chunk to extract content
        for line in chunk.split("\n"):
            if not line.startswith("data: "):
                continue
            data = line[6:].strip()
            if data == "[DONE]":
                yield (
                    f"event: done\ndata: {json.dumps({'content_length': len(''.join(full_content)), 'tokens': len(''.join(full_content)) // 4})}\n\n"
                )
                return
            try:
                parsed = json.loads(data)
                content = parsed.get("content", "")
                if content:
                    full_content.append(content)
                    yield f"event: token\ndata: {json.dumps({'content': content})}\n\n"
                if parsed.get("error"):
                    yield f"event: error\ndata: {json.dumps({'error': parsed['error']})}\n\n"
                    return
            except Exception:
                logger.warning(
                    '[stream] stream_from_streamer：解析上游 SSE 数据行失败已忽略',
                    exc_info=True,
                )
    yield (
        f"event: done\ndata: {json.dumps({'content_length': len(''.join(full_content)), 'tokens': len(''.join(full_content)) // 4})}\n\n"
    )


async def stream_from_event_bus(execution_id: str, last_event_id: int) -> Any:
    """Subscribe to agent token events from the event bus.

    P1-13: subscribes via the ``agent.{execution_id}.*`` wildcard so
    events published on ``agent.{execution_id}.token``, ``.meta``,
    ``.done``, ``.error`` are all received. Replays history first
    (for late-joining clients) then subscribes for live events.
    """
    from maop.core.reliability.event_bus import get_event_bus

    bus = get_event_bus()
    queue: asyncio.Queue = asyncio.Queue()
    # P1-13: wildcard subscription — matches agent.{execution_id}.token,
    # .meta, .done, .error sub-topics emitted by maop_execute / chat_engine.
    topic_pattern = f"agent.{execution_id}.*"
    # History prefix for replaying events to late-joining clients.
    history_prefix = f"agent.{execution_id}."

    async def _handler(event):
        await queue.put(event)

    # P1-13: replay history events for late-joining clients so tokens
    # emitted before the SSE connection opened are not lost.
    sent_complete = False
    for evt in bus.get_history(limit=500):
        if evt._id <= last_event_id:
            continue
        if not evt.topic.startswith(history_prefix):
            continue
        event_type, data = classify_agent_event(evt.topic, evt.data)
        yield f"id: {evt._id}\nevent: {event_type}\ndata: {json.dumps(data)}\n\n"
        if event_type in ("done", "error"):
            sent_complete = True
            break

    if not sent_complete:
        bus.subscribe(topic_pattern, _handler)
        try:
            while True:
                evt = await asyncio.wait_for(queue.get(), timeout=60)
                if evt._id <= last_event_id:
                    continue
                event_type, data = classify_agent_event(evt.topic, evt.data)
                yield f"id: {evt._id}\nevent: {event_type}\ndata: {json.dumps(data)}\n\n"
                if event_type in ("done", "error"):
                    break
        except asyncio.TimeoutError:
            yield (
                f"event: done\ndata: {json.dumps({'content_length': 0, 'tokens': 0, 'reason': 'timeout'})}\n\n"
            )
        finally:
            bus.unsubscribe(topic_pattern, _handler)


def get_streamer(execution_id: str) -> Any:
    """Return the active streamer for an execution, or None."""
    from maop.core.reliability.streaming import get_stream_registry

    registry = get_stream_registry()
    return registry.get(execution_id)