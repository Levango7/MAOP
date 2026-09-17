"""Chat & Communication service layer.

Encapsulates business logic extracted from the chat / stream / react /
debate / feedback routers (§2.3 Chat & Communication domain) so the
router layer only does parameter parsing + service call + response
formatting.

The service is intentionally framework-agnostic: it does not import
FastAPI and can be unit-tested or invoked from CLI/CI without an HTTP
context. ``maop_root`` / core instances are passed in explicitly so the
service has no hidden global state.

SSE streaming generators live here (they are pure async iterables);
the construction of :class:`fastapi.responses.StreamingResponse` stays
in the router layer.

NOTE on backward compatibility:
    The routers retain their ``_get_*`` singleton accessors
    (``_get_engine``, ``_get_change_tracker``, ``_get_artifact_store``,
    ``_get_debate_dispatcher``) because existing tests monkeypatch them
    on the router module. The accessors delegate to the factory
    functions exposed here so the construction logic is not duplicated.
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
import os
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════════════
# §1 Chat  (from routers/chat.py)
# ════════════════════════════════════════════════════════════════════


def make_chat_engine(maop_root: Path) -> Any:
    """Construct the :class:`ChatEngine` with MAOP defaults.

    One-number-user fix (2026-08-31): default agent points at the real
    MAOP self-reference (not the ghost ``mavis``), and the default model
    is injected via ``MAOP_LLM_DEFAULT_MODEL``.
    """
    from maop.core.agent.llm_chat.chat_engine import ChatEngine

    return ChatEngine(
        root_dir=str(maop_root),
        default_agent=os.environ.get("MAOP_LLM_DEFAULT_AGENT", "MAOP"),
        default_model=os.environ.get("MAOP_LLM_DEFAULT_MODEL", ""),
    )


def build_chat_request(
    *,
    session_id: str,
    message: str,
    images: list[str],
    agent: str,
    model: str,
    system_prompt: str,
    stream: bool,
    max_tokens: int,
    temperature: float,
) -> Any:
    """Build a :class:`ChatRequest` from parsed HTTP body fields."""
    from maop.core.agent.llm_chat.chat_engine import ChatRequest

    return ChatRequest(
        session_id=session_id,
        message=message,
        images=images,
        agent=agent,
        model=model,
        system_prompt=system_prompt,
        stream=stream,
        max_tokens=max_tokens,
        temperature=temperature,
    )


async def send_chat(engine: Any, chat_req: Any) -> dict[str, Any]:
    """Run a non-streaming chat round and return the response dict."""
    response = await engine.chat(chat_req)
    return {"status": "ok", "data": response.model_dump()}


async def stream_chat_chunks(engine: Any, chat_req: Any) -> Any:
    """Async generator yielding raw SSE chunks from the chat engine.

    The router wraps this in a :class:`StreamingResponse`; we only own
    the async iterable so the streaming behaviour is unit-testable
    without an HTTP context.
    """
    async for chunk in engine.chat_stream(chat_req):
        yield chunk


def list_llm_models(maop_root: Path) -> dict[str, Any]:
    """List enabled LLM models + providers from ``models.yaml``."""
    from maop.core.agent.llm_chat.llm_provider import LLMProviderFactory

    factory = LLMProviderFactory(root_dir=str(maop_root))
    models = factory.list_models(enabled_only=True)
    providers = factory.list_providers(enabled_only=True)
    return {
        "models": [m.model_dump() for m in models],
        "providers": [p.model_dump() for p in providers],
    }


def list_chat_sessions(maop_root: Path) -> dict[str, Any]:
    """List all chat sessions via :class:`SessionManager`."""
    from maop.core.security.session import SessionManager

    mgr = SessionManager(root_dir=str(maop_root))
    sessions = mgr.list()
    return {"sessions": [s.model_dump() for s in sessions]}


def get_session_messages(engine: Any, session_id: str) -> dict[str, Any]:
    """Return the message history for a chat session."""
    history = engine.memory.conversation.get_history(session_id)
    return {
        "session_id": session_id,
        "messages": [m.model_dump() for m in history],
        "message_count": len(history),
    }


def clear_session(engine: Any, session_id: str) -> int:
    """Clear all messages in a chat session; returns cleared count."""
    return engine.memory.conversation.clear_session(session_id)


def search_memory(engine: Any, query: str, top: int) -> Any:
    """Search across all memory layers."""
    return engine.memory.search_all_layers(query=query, top=top)


def consolidate_memory(engine: Any) -> Any:
    """Trigger L2 → L3 memory consolidation."""
    return engine.memory.consolidate()


def get_memory_stats(engine: Any) -> Any:
    """Get memory statistics."""
    return engine.memory.stats()


def save_image(
    maop_root: Path,
    *,
    session_id: str,
    filename: str,
    data: bytes,
    content_type: str,
) -> str:
    """Persist an uploaded image and return its image_id."""
    from maop.core.backends.image_store import ImageStore

    store = ImageStore(root_dir=str(maop_root))
    return store.save(
        session_id=session_id or "default",
        filename=filename,
        data=data,
        content_type=content_type,
    )


def list_session_images(maop_root: Path, session_id: str) -> list[Any]:
    """List all images for a chat session."""
    from maop.core.backends.image_store import ImageStore

    store = ImageStore(root_dir=str(maop_root))
    return [img.model_dump() for img in store.list_session_images(session_id)]


def delete_image(maop_root: Path, image_id: str) -> bool:
    """Delete an uploaded image; returns True if deleted."""
    from maop.core.backends.image_store import ImageStore

    store = ImageStore(root_dir=str(maop_root))
    return store.delete(image_id)


# ════════════════════════════════════════════════════════════════════
# §2 Stream  (from routers/stream.py)
# ════════════════════════════════════════════════════════════════════


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


# ════════════════════════════════════════════════════════════════════
# §3 React  (from routers/react.py)
# ════════════════════════════════════════════════════════════════════


def make_change_tracker(maop_root: Path) -> Any:
    """Construct the :class:`ChangeTracker` singleton."""
    from maop.core.reliability.change_tracker import ChangeTracker

    return ChangeTracker(root_dir=str(maop_root))


def make_artifact_store(maop_root: Path) -> Any:
    """Construct the :class:`ArtifactStore` singleton."""
    from maop.core.backends.artifact_store import ArtifactStore

    return ArtifactStore(root_dir=str(maop_root))


def list_snapshots(tracker: Any, *, workdir: str, limit: int) -> list[Any]:
    """List snapshots via the change tracker."""
    snapshots = tracker.list_snapshots(workdir=workdir, limit=limit)
    return [s.model_dump() for s in snapshots]


def create_snapshot(tracker: Any, *, workdir: str, label: str) -> Any:
    """Create a snapshot and return its model_dump (or None)."""
    snap_id = tracker.snapshot(workdir=workdir, label=label)
    snap = tracker.get_snapshot(snap_id)
    return snap.model_dump() if snap else None


def diff_snapshots(tracker: Any, workdir: str, since_label: str) -> Any:
    """Diff snapshots; returns the diff model_dump."""
    result = tracker.diff(workdir, since_label=since_label)
    return result.model_dump()


def get_change_log(tracker: Any, workdir: str, limit: int) -> Any:
    """Return the change log for a workdir."""
    return tracker.get_change_log(workdir, limit=limit)


def delete_snapshot(tracker: Any, snapshot_id: str) -> bool:
    """Delete a snapshot; returns True if deleted."""
    return tracker.delete_snapshot(snapshot_id)


def list_artifacts(store: Any, limit: int) -> list[Any]:
    """List artifacts via the artifact store."""
    artifacts = store.list_artifacts(limit=limit)
    return [a.model_dump() for a in artifacts]


def save_artifact(
    store: Any,
    *,
    name: str,
    content: str,
    tag: str,
    metadata: dict[str, Any] | None,
) -> int:
    """Save an artifact; returns the new version number."""
    return store.save(name=name, content=content, tag=tag, metadata=metadata)


def load_artifact(store: Any, name: str, version: int | None) -> str | None:
    """Load an artifact's content; returns None if not found."""
    return store.load(name, version=version)


def artifact_history(store: Any, name: str, limit: int) -> list[Any]:
    """List artifact version history."""
    history = store.history(name, limit=limit)
    return [h.model_dump() for h in history]


def restore_artifact(store: Any, name: str, version: int) -> bool:
    """Restore an artifact to a prior version; returns True on success."""
    return store.restore(name, version=version)


def delete_artifact(store: Any, name: str) -> bool:
    """Delete an artifact (all versions); returns True if deleted."""
    return store.delete_artifact(name)


# ════════════════════════════════════════════════════════════════════
# §4 Debate  (from routers/debate.py)
# ════════════════════════════════════════════════════════════════════


def resolve_debate_dispatcher() -> Any:
    """Return the process-wide DebateDispatcher singleton.

    Lazily imports the singleton accessor from the reliability services
    container. Returns ``None`` when no dispatcher has been configured
    (the router translates that into a 404). Raises ``RuntimeError`` if
    the service container itself is unavailable — the router translates
    that into a 503.

    NOTE: the router keeps a thin ``_get_debate_dispatcher`` wrapper
    that maps these outcomes to :class:`HTTPException` because tests
    monkeypatch the router-level accessor.
    """
    from maop.config.env import get_root_dir
    from maop.core.reliability.services import ServiceContainer

    root = str(get_root_dir(default="."))
    container = ServiceContainer(root_dir=root)
    return container.get("debate_dispatcher", raise_on_failure=False)


async def start_debate(
    dispatcher: Any,
    *,
    question: str,
    participants: list[str],
    context: dict[str, Any],
    routing_key: str,
    trace_id: str,
    max_rounds: int,
    consensus_threshold: float,
) -> Any:
    """Run a debate and return the ``DebateVerdict`` (not dict).

    The router is responsible for ``verdict.model_dump()`` so the
    service stays framework-agnostic.
    """
    from maop.delegate.dispatch_debate import DebateConfig

    config = DebateConfig(
        max_rounds=max_rounds,
        consensus_threshold=consensus_threshold,
    )
    return await dispatcher.run_debate(
        question,
        participants,
        context=context,
        routing_key=routing_key,
        trace_id=trace_id,
        config_override=config,
    )


def get_debate_history(dispatcher: Any, limit: int) -> list[Any]:
    """Return recent debate verdicts (list of model objects)."""
    return dispatcher.get_history(limit=limit)


def get_debate_verdict(dispatcher: Any, debate_id: str) -> Any:
    """Return a debate verdict by id (or None)."""
    return dispatcher.get_verdict(debate_id)


def configure_debate(
    dispatcher: Any,
    *,
    max_rounds: int,
    min_rounds: int,
    consensus_threshold: float,
    agent_timeout_s: float,
    round_timeout_s: float,
    max_debate_tokens: int,
    early_exit_on_unanimous: bool,
    retention_days: int,
) -> Any:
    """Update the dispatcher's runtime config in place.

    Returns the new :class:`DebateConfig` (caller does ``model_dump()``).
    Prefers the public ``update_config`` method, falls back to setattr.
    """
    from maop.delegate.dispatch_debate import DebateConfig

    new_config = DebateConfig(
        max_rounds=max_rounds,
        min_rounds=min_rounds,
        consensus_threshold=consensus_threshold,
        agent_timeout_s=agent_timeout_s,
        round_timeout_s=round_timeout_s,
        max_debate_tokens=max_debate_tokens,
        early_exit_on_unanimous=early_exit_on_unanimous,
        retention_days=retention_days,
    )
    if hasattr(dispatcher, "update_config") and callable(dispatcher.update_config):
        dispatcher.update_config(new_config)
    else:
        setattr(dispatcher, "_config", new_config)
    return new_config


# ════════════════════════════════════════════════════════════════════
# §5 Feedback  (from routers/feedback.py)
# ════════════════════════════════════════════════════════════════════

# 允许的目标类型白名单 — 防止任意字符串注入.
VALID_TARGET_TYPES: tuple[str, ...] = ("agent", "task", "result")

# 修复：SELECT * 改为明确列名 —— 避免表新增列后 dict 键漂移破坏 API 契约，
# 同时让查询列显式可审计。与 CREATE TABLE 列定义保持一致。
FEEDBACK_COLUMNS: str = (
    "feedback_id, target_type, target_id, user_id, tenant_id, rating, "
    "comment, tags, created_at, updated_at"
)

# 模块级 schema 初始化状态（线程安全单例）
_schema_lock = threading.Lock()
_schema_initialized = False


def ensure_schema() -> None:
    """幂等创建 feedback 表. 使用双重检查锁定避免重复 DDL."""
    global _schema_initialized
    if _schema_initialized:
        return
    with _schema_lock:
        if _schema_initialized:
            return
        from maop.core.backends.db_utils import get_db_path, sqlite_connect

        db_path = get_db_path("feedback")
        with sqlite_connect(str(db_path)) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS feedback (
                    feedback_id  TEXT    PRIMARY KEY,
                    target_type  TEXT    NOT NULL,
                    target_id    TEXT    NOT NULL,
                    user_id      TEXT    NOT NULL,
                    tenant_id    TEXT    NOT NULL DEFAULT '',
                    rating       INTEGER NOT NULL CHECK(rating BETWEEN 1 AND 5),
                    comment      TEXT    NOT NULL DEFAULT '',
                    tags         TEXT    NOT NULL DEFAULT '[]',
                    created_at   REAL    NOT NULL,
                    updated_at   REAL    NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_feedback_target "
                "ON feedback(target_type, target_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_feedback_user "
                "ON feedback(user_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_feedback_created "
                "ON feedback(created_at)"
            )
        _schema_initialized = True


def reset_schema_for_tests() -> None:
    """Reset schema init flag — used by unit tests via the router shim."""
    global _schema_initialized
    _schema_initialized = False


def new_feedback_id() -> str:
    """生成反馈 ID."""
    return f"fb_{uuid.uuid4().hex[:16]}"


def row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    """将 sqlite3.Row 转为可 JSON 序列化的 dict."""
    d = dict(row)
    try:
        d["tags"] = json.loads(d.get("tags", "[]"))
    except (json.JSONDecodeError, TypeError):
        d["tags"] = []
    return d


def create_feedback(
    *,
    user_id: str,
    tenant_id: str,
    target_type: str,
    target_id: str,
    rating: int,
    comment: str,
    tags: list[str],
) -> str:
    """Insert a new feedback row and return its feedback_id.

    Assumes ``target_type`` has already been validated against
    ``VALID_TARGET_TYPES`` by the caller (router).
    """
    from maop.core.backends.db_utils import get_db_path, sqlite_connect

    ensure_schema()
    db_path = get_db_path("feedback")
    feedback_id = new_feedback_id()
    now = time.time()
    tags_json = json.dumps(tags, ensure_ascii=False)

    with sqlite_connect(str(db_path)) as conn:
        conn.execute(
            """
            INSERT INTO feedback
                (feedback_id, target_type, target_id, user_id, tenant_id,
                 rating, comment, tags, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                feedback_id,
                target_type,
                target_id,
                user_id,
                tenant_id,
                rating,
                comment,
                tags_json,
                now,
                now,
            ),
        )

    return feedback_id


def list_feedback(
    *,
    user_id: str,
    is_admin: bool,
    target_type: str,
    target_id: str,
    rating: int | None,
    date_from: float | None,
    date_to: float | None,
    page: int,
    page_size: int,
) -> dict[str, Any]:
    """Query feedback rows with IDOR防护 (non-admin sees only own rows).

    Returns ``{"data": [...], "total": int, "page": int, "page_size": int}``.
    """
    from maop.core.backends.db_utils import get_db_path, sqlite_connect

    ensure_schema()
    db_path = get_db_path("feedback")

    where_clauses: list[str] = []
    params: list[Any] = []

    if not is_admin:
        where_clauses.append("user_id = ?")
        params.append(user_id)

    if target_type:
        where_clauses.append("target_type = ?")
        params.append(target_type)
    if target_id:
        where_clauses.append("target_id = ?")
        params.append(target_id)
    if rating is not None:
        where_clauses.append("rating = ?")
        params.append(rating)
    if date_from is not None:
        where_clauses.append("created_at >= ?")
        params.append(date_from)
    if date_to is not None:
        where_clauses.append("created_at <= ?")
        params.append(date_to)

    where_sql = (" WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
    offset = (page - 1) * page_size

    with sqlite_connect(str(db_path)) as conn:
        total = conn.execute(
            f"SELECT COUNT(*) AS c FROM feedback{where_sql}", params
        ).fetchone()["c"]

        rows = conn.execute(
            f"""
            SELECT {FEEDBACK_COLUMNS} FROM feedback{where_sql}
            ORDER BY created_at DESC
            LIMIT ? OFFSET ?
            """,
            [*params, page_size, offset],
        ).fetchall()

    return {
        "data": [row_to_dict(r) for r in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


def get_feedback_summary(
    *,
    user_id: str,
    is_admin: bool,
    target_type: str,
    target_id: str,
    date_from: float | None,
    date_to: float | None,
) -> dict[str, Any]:
    """Aggregate feedback summary: avg rating, distribution, tag frequency.

    Returns the ``summary`` dict (without the outer ``{"status": "ok"}``
    wrapper — the router adds that).
    """
    from maop.core.backends.db_utils import get_db_path, sqlite_connect

    ensure_schema()
    db_path = get_db_path("feedback")

    where_clauses: list[str] = []
    params: list[Any] = []

    if not is_admin:
        where_clauses.append("user_id = ?")
        params.append(user_id)

    if target_type:
        where_clauses.append("target_type = ?")
        params.append(target_type)
    if target_id:
        where_clauses.append("target_id = ?")
        params.append(target_id)
    if date_from is not None:
        where_clauses.append("created_at >= ?")
        params.append(date_from)
    if date_to is not None:
        where_clauses.append("created_at <= ?")
        params.append(date_to)

    where_sql = (" WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

    with sqlite_connect(str(db_path)) as conn:
        agg = conn.execute(
            f"""
            SELECT
                COUNT(*)                                AS total,
                AVG(rating)                             AS avg_rating,
                SUM(CASE WHEN comment != '' THEN 1 ELSE 0 END) AS comment_count
            FROM feedback{where_sql}
            """,
            params,
        ).fetchone()

        dist_rows = conn.execute(
            f"""
            SELECT rating, COUNT(*) AS c
            FROM feedback{where_sql}
            GROUP BY rating
            """,
            params,
        ).fetchall()
        rating_distribution = {int(r["rating"]): int(r["c"]) for r in dist_rows}

        tag_rows = conn.execute(
            f"SELECT tags FROM feedback{where_sql}",
            params,
        ).fetchall()
    tag_freq: dict[str, int] = {}
    for tr in tag_rows:
        try:
            for tag in json.loads(tr["tags"]):
                if tag:
                    tag_freq[tag] = tag_freq.get(tag, 0) + 1
        except (json.JSONDecodeError, TypeError):
            continue

    total = int(agg["total"])
    avg_rating = float(agg["avg_rating"]) if agg["avg_rating"] is not None else 0.0

    return {
        "total": total,
        "average_rating": round(avg_rating, 2),
        "rating_distribution": {str(k): v for k, v in sorted(rating_distribution.items())},
        "comment_count": int(agg["comment_count"] or 0),
        "tag_frequency": dict(sorted(tag_freq.items(), key=lambda x: (-x[1], x[0]))),
    }


def export_feedback_rows(
    *,
    target_type: str,
    date_from: float | None,
    date_to: float | None,
) -> list[dict[str, Any]]:
    """Fetch feedback rows for export (admin only).

    Returns a list of dict rows (tags already parsed). The router
    formats them as CSV or JSON.
    """
    from maop.core.backends.db_utils import get_db_path, sqlite_connect

    ensure_schema()
    db_path = get_db_path("feedback")

    where_clauses: list[str] = []
    params: list[Any] = []
    if target_type:
        where_clauses.append("target_type = ?")
        params.append(target_type)
    if date_from is not None:
        where_clauses.append("created_at >= ?")
        params.append(date_from)
    if date_to is not None:
        where_clauses.append("created_at <= ?")
        params.append(date_to)
    where_sql = (" WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

    with sqlite_connect(str(db_path)) as conn:
        rows = conn.execute(
            f"SELECT {FEEDBACK_COLUMNS} FROM feedback{where_sql} ORDER BY created_at DESC",
            params,
        ).fetchall()
    return [row_to_dict(r) for r in rows]


def format_feedback_csv(items: list[dict[str, Any]]) -> str:
    """Render feedback rows as a CSV string (with header row)."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        [
            "feedback_id",
            "target_type",
            "target_id",
            "user_id",
            "tenant_id",
            "rating",
            "comment",
            "tags",
            "created_at",
            "updated_at",
        ]
    )
    for it in items:
        writer.writerow(
            [
                it["feedback_id"],
                it["target_type"],
                it["target_id"],
                it["user_id"],
                it.get("tenant_id", ""),
                it["rating"],
                it["comment"],
                json.dumps(it["tags"], ensure_ascii=False),
                it["created_at"],
                it["updated_at"],
            ]
        )
    return buf.getvalue()


def check_feedback_ownership(
    row: sqlite3.Row | None,
    *,
    user_id: str,
    is_admin: bool,
) -> dict[str, Any]:
    """Load a feedback row and enforce IDOR防护.

    Returns the row dict if the caller may access it. Raises
    ``KeyError("not_found")`` otherwise — the router translates that
    into a 404 (we use KeyError rather than HTTPException to keep the
    service framework-agnostic).
    """
    if row is None:
        raise KeyError("not_found")
    item = row_to_dict(row)
    if is_admin:
        return item
    if user_id and item.get("user_id", "") != user_id:
        raise KeyError("not_found")
    return item


def update_feedback(
    feedback_id: str,
    *,
    user_id: str,
    is_admin: bool,
    rating: int | None,
    comment: str | None,
    tags: list[str] | None,
) -> dict[str, Any]:
    """Update a feedback row (IDOR-checked) and return the new row dict.

    If no fields are provided, returns the existing row unchanged.
    """
    from maop.core.backends.db_utils import get_db_path, sqlite_connect

    ensure_schema()
    db_path = get_db_path("feedback")

    with sqlite_connect(str(db_path)) as conn:
        row = conn.execute(
            f"SELECT {FEEDBACK_COLUMNS} FROM feedback WHERE feedback_id = ?",
            (feedback_id,),
        ).fetchone()
        item = check_feedback_ownership(row, user_id=user_id, is_admin=is_admin)

        updates: list[str] = []
        params: list[Any] = []
        if rating is not None:
            updates.append("rating = ?")
            params.append(rating)
        if comment is not None:
            updates.append("comment = ?")
            params.append(comment)
        if tags is not None:
            updates.append("tags = ?")
            params.append(json.dumps(tags, ensure_ascii=False))

        if not updates:
            return item

        updates.append("updated_at = ?")
        params.append(time.time())
        params.append(feedback_id)
        conn.execute(
            f"UPDATE feedback SET {', '.join(updates)} WHERE feedback_id = ?",
            params,
        )
        row = conn.execute(
            f"SELECT {FEEDBACK_COLUMNS} FROM feedback WHERE feedback_id = ?",
            (feedback_id,),
        ).fetchone()
        item = row_to_dict(row)

    return item


def delete_feedback(
    feedback_id: str,
    *,
    user_id: str,
    is_admin: bool,
) -> None:
    """Delete a feedback row (IDOR-checked).

    Raises ``KeyError("not_found")`` if the row does not exist or the
    caller lacks ownership.
    """
    from maop.core.backends.db_utils import get_db_path, sqlite_connect

    ensure_schema()
    db_path = get_db_path("feedback")

    with sqlite_connect(str(db_path)) as conn:
        row = conn.execute(
            f"SELECT {FEEDBACK_COLUMNS} FROM feedback WHERE feedback_id = ?",
            (feedback_id,),
        ).fetchone()
        check_feedback_ownership(row, user_id=user_id, is_admin=is_admin)
        conn.execute(
            "DELETE FROM feedback WHERE feedback_id = ?",
            (feedback_id,),
        )


__all__ = [
    # Chat
    "make_chat_engine",
    "build_chat_request",
    "send_chat",
    "stream_chat_chunks",
    "list_llm_models",
    "list_chat_sessions",
    "get_session_messages",
    "clear_session",
    "search_memory",
    "consolidate_memory",
    "get_memory_stats",
    "save_image",
    "list_session_images",
    "delete_image",
    # Stream
    "classify_agent_event",
    "stream_global_state_chunks",
    "list_active_streams",
    "stream_dag_progress_chunks",
    "stream_from_streamer",
    "stream_from_event_bus",
    "get_streamer",
    # React
    "make_change_tracker",
    "make_artifact_store",
    "list_snapshots",
    "create_snapshot",
    "diff_snapshots",
    "get_change_log",
    "delete_snapshot",
    "list_artifacts",
    "save_artifact",
    "load_artifact",
    "artifact_history",
    "restore_artifact",
    "delete_artifact",
    # Debate
    "resolve_debate_dispatcher",
    "start_debate",
    "get_debate_history",
    "get_debate_verdict",
    "configure_debate",
    # Feedback
    "VALID_TARGET_TYPES",
    "FEEDBACK_COLUMNS",
    "ensure_schema",
    "reset_schema_for_tests",
    "new_feedback_id",
    "row_to_dict",
    "create_feedback",
    "list_feedback",
    "get_feedback_summary",
    "export_feedback_rows",
    "format_feedback_csv",
    "check_feedback_ownership",
    "update_feedback",
    "delete_feedback",
]