"""Chat & Communication service layer.

Encapsulates business logic extracted from the chat / stream / react /
debate / feedback routers (§2.3 Chat & Communication domain) so the
router layer only does parameter parsing + service call + response
formatting.

The service is intentionally framework-agnostic: it does not import
FastAPI and can be unit-tested or invoked from CLI/CI without an HTTP
context. ``maop_root`` / core instances are passed in explicitly so the
service has no hidden global state.

SSE streaming generators live in ``services/chat/streaming.py``; user
feedback CRUD lives in ``services/chat/feedback.py`` (file-size
ratchet). Both submodules are re-exported here by name so the module's
public surface is unchanged.

NOTE on backward compatibility:
    The routers retain their ``_get_*`` singleton accessors
    (``_get_engine``, ``_get_change_tracker``, ``_get_artifact_store``,
    ``_get_debate_dispatcher``) because existing tests monkeypatch them
    on the router module. The accessors delegate to the factory
    functions exposed here so the construction logic is not duplicated.
"""

from __future__ import annotations

import logging
import os
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
    image_id = store.save(
        session_id=session_id or "default",
        filename=filename,
        data=data,
        content_type=content_type,
    )
    # M2.2 B2 收尾: 上传即多模态索进口（异步入队、fail-soft）。默认只在
    # provider 是语义型 CLIP 时开启——没装 [multimodal] extra 的部署行为不变。
    from maop.memory.multimodal import maybe_index_uploaded_image

    maybe_index_uploaded_image(
        maop_root, image_id, session_id=session_id or "default", filename=filename
    )
    return image_id


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
# §2 Stream  →  services/chat/streaming.py  (forwarded)
# ════════════════════════════════════════════════════════════════════

from maop.dashboard.services.chat.streaming import (
    classify_agent_event,
    get_streamer,
    list_active_streams,
    stream_dag_progress_chunks,
    stream_from_event_bus,
    stream_from_streamer,
    stream_global_state_chunks,
)

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
        dispatcher._config = new_config
    return new_config


# ════════════════════════════════════════════════════════════════════
# §5 Feedback  →  services/chat/feedback.py  (forwarded)
# ════════════════════════════════════════════════════════════════════

from maop.dashboard.services.chat.feedback import (
    FEEDBACK_COLUMNS,
    VALID_TARGET_TYPES,
    check_feedback_ownership,
    create_feedback,
    delete_feedback,
    ensure_schema,
    export_feedback_rows,
    format_feedback_csv,
    get_feedback_summary,
    list_feedback,
    new_feedback_id,
    reset_schema_for_tests,
    row_to_dict,
    update_feedback,
)

__all__ = [
    "FEEDBACK_COLUMNS",
    # Feedback
    "VALID_TARGET_TYPES",
    "artifact_history",
    "build_chat_request",
    "check_feedback_ownership",
    # Stream
    "classify_agent_event",
    "clear_session",
    "configure_debate",
    "consolidate_memory",
    "create_feedback",
    "create_snapshot",
    "delete_artifact",
    "delete_feedback",
    "delete_image",
    "delete_snapshot",
    "diff_snapshots",
    "ensure_schema",
    "export_feedback_rows",
    "format_feedback_csv",
    "get_change_log",
    "get_debate_history",
    "get_debate_verdict",
    "get_feedback_summary",
    "get_memory_stats",
    "get_session_messages",
    "get_streamer",
    "list_active_streams",
    "list_artifacts",
    "list_chat_sessions",
    "list_feedback",
    "list_llm_models",
    "list_session_images",
    "list_snapshots",
    "load_artifact",
    "make_artifact_store",
    # React
    "make_change_tracker",
    # Chat
    "make_chat_engine",
    "new_feedback_id",
    "reset_schema_for_tests",
    # Debate
    "resolve_debate_dispatcher",
    "restore_artifact",
    "row_to_dict",
    "save_artifact",
    "save_image",
    "search_memory",
    "send_chat",
    "start_debate",
    "stream_chat_chunks",
    "stream_dag_progress_chunks",
    "stream_from_event_bus",
    "stream_from_streamer",
    "stream_global_state_chunks",
    "update_feedback",
]
