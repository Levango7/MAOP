"""MAOP Conversation Manager — Multi-turn conversation history with context window.

Provides:
  - Message history per session (user/assistant/system/tool roles)
  - Context window management (token counting + sliding window)
  - Automatic context compression when window overflows
  - Message search and retrieval
  - SQLite persistence

Usage::

    from maop.core.conversation import ConversationManager

    mgr = ConversationManager(root_dir="/path/to/MAOP")
    mgr.add_message("sess-abc", role="user", content="Fix the bug")
    mgr.add_message("sess-abc", role="assistant", content="Bug fixed in main.py")
    history = mgr.get_history("sess-abc")
    window = mgr.get_context_window("sess-abc", max_tokens=4000)
"""

from __future__ import annotations

import contextlib
import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

from pydantic import BaseModel, Field

from maop.core.db_utils import get_db_path, sqlite_connect

logger = logging.getLogger(__name__)


class MessageRole(str):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class Message(BaseModel):
    id: str = ""
    session_id: str = ""
    role: str = "user"
    content: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    token_count: int = 0
    created_at: str = ""


class ContextWindow(BaseModel):
    messages: list[Message] = Field(default_factory=list)
    total_tokens: int = 0
    max_tokens: int = 4000
    compressed: bool = False
    compression_summary: str = ""


class ConversationManager:
    """Manage multi-turn conversation history with context window control.

    Features:
      - Per-session message history (SQLite-backed)
      - Token counting and context window enforcement
      - Automatic compression via ContextCompressor when window overflows
      - Message search within a session
    """

    def __init__(self, root_dir: str | Path, max_context_tokens: int = 4000) -> None:
        self._root = Path(root_dir)
        self._db_path = get_db_path("conversation")
        self._max_context_tokens = max_context_tokens
        self._ensure_db()

    def _ensure_db(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    metadata TEXT NOT NULL DEFAULT '{}',
                    token_count INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_messages_session
                ON messages(session_id, created_at)
            """)

    def _connect(self):
        return sqlite_connect(self._db_path, foreign_keys=False)

    def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        metadata: dict[str, Any] | None = None,
        token_count: int = 0,
    ) -> str:
        now = datetime.now(timezone.utc).isoformat()
        msg_id = f"msg-{uuid.uuid4().hex[:10]}"
        if token_count <= 0:
            token_count = self._estimate_tokens(content)
        msg = Message(
            id=msg_id,
            session_id=session_id,
            role=role,
            content=content,
            metadata=metadata or {},
            token_count=token_count,
            created_at=now,
        )
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO messages (id, session_id, role, content, metadata, token_count, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (msg.id, msg.session_id, msg.role, msg.content,
                 json.dumps(msg.metadata), msg.token_count, msg.created_at),
            )
        return msg_id

    def get_history(
        self,
        session_id: str,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Message]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM messages WHERE session_id=? ORDER BY created_at ASC LIMIT ? OFFSET ?",
                (session_id, limit, offset),
            ).fetchall()
        return [self._row_to_message(r) for r in rows]

    def get_recent(self, session_id: str, count: int = 10) -> list[Message]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM messages WHERE session_id=? ORDER BY created_at DESC LIMIT ?",
                (session_id, count),
            ).fetchall()
        return [self._row_to_message(r) for r in reversed(rows)]

    def get_context_window(
        self,
        session_id: str,
        max_tokens: int | None = None,
    ) -> ContextWindow:
        budget = max_tokens or self._max_context_tokens
        messages = self.get_history(session_id, limit=1000)
        if not messages:
            return ContextWindow(max_tokens=budget)

        total = sum(m.token_count for m in messages)
        if total <= budget:
            return ContextWindow(
                messages=messages,
                total_tokens=total,
                max_tokens=budget,
            )

        selected: list[Message] = []
        token_sum = 0
        for msg in reversed(messages):
            if token_sum + msg.token_count > budget:
                break
            selected.insert(0, msg)
            token_sum += msg.token_count

        if not selected and messages:
            selected = [messages[-1]]
            token_sum = messages[-1].token_count

        compressed = len(selected) < len(messages)
        compression_summary = ""
        if compressed:
            dropped = len(messages) - len(selected)
            compression_summary = f"Dropped {dropped} earlier messages to fit {budget} token budget"

        return ContextWindow(
            messages=selected,
            total_tokens=token_sum,
            max_tokens=budget,
            compressed=compressed,
            compression_summary=compression_summary,
        )

    def get_compressed_context(
        self,
        session_id: str,
        max_tokens: int | None = None,
    ) -> ContextWindow:
        budget = max_tokens or self._max_context_tokens
        window = self.get_context_window(session_id, max_tokens=budget)
        if not window.compressed:
            return window

        try:
            from maop.core.context_compressor import ContextCompressor
            all_messages = self.get_history(session_id, limit=1000)
            compressor = ContextCompressor()
            msg_dicts = [{"role": m.role, "content": m.content} for m in all_messages]
            result = compressor.compress(msg_dicts, max_tokens=budget // 2)
            summary = compressor.to_prompt(result)
            system_msg = Message(
                id="compressed-summary",
                session_id=session_id,
                role=MessageRole.SYSTEM,
                content=summary,
                token_count=self._estimate_tokens(summary),
            )
            recent = window.messages[-6:] if len(window.messages) > 6 else window.messages
            combined = [system_msg] + recent
            total = sum(m.token_count for m in combined)
            return ContextWindow(
                messages=combined,
                total_tokens=total,
                max_tokens=budget,
                compressed=True,
                compression_summary=f"Compressed {len(all_messages)} messages into summary + {len(recent)} recent",
            )
        except Exception as exc:
            logger.warning("[conversation] Compression failed, using sliding window: %s", exc)
            return window

    def search(
        self,
        session_id: str,
        query: str,
        *,
        limit: int = 20,
    ) -> list[Message]:
        pattern = f"%{query}%"
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM messages WHERE session_id=? AND content LIKE ? ORDER BY created_at DESC LIMIT ?",
                (session_id, pattern, limit),
            ).fetchall()
        return [self._row_to_message(r) for r in rows]

    def delete_message(self, message_id: str) -> bool:
        with self._connect() as conn:
            cursor = conn.execute("DELETE FROM messages WHERE id=?", (message_id,))
            return cast(bool, cursor.rowcount > 0)

    def clear_session(self, session_id: str) -> int:
        with self._connect() as conn:
            cursor = conn.execute("DELETE FROM messages WHERE session_id=?", (session_id,))
            return cast(int, cursor.rowcount)

    def message_count(self, session_id: str) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) as c FROM messages WHERE session_id=?", (session_id,),
            ).fetchone()
            return row["c"] if row else 0

    def token_total(self, session_id: str) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT SUM(token_count) as c FROM messages WHERE session_id=?", (session_id,),
            ).fetchone()
            return row["c"] or 0

    def to_messages_list(self, session_id: str, max_tokens: int | None = None) -> list[dict[str, Any]]:
        window = self.get_context_window(session_id, max_tokens=max_tokens)
        result = []
        for m in window.messages:
            entry: dict[str, Any] = {"role": m.role, "content": m.content}
            if m.role == MessageRole.TOOL and m.metadata:
                entry["tool_call_id"] = m.metadata.get("tool_call_id", "")
            result.append(entry)
        return result

    @staticmethod
    def _estimate_tokens(text: str | None) -> int:
        if not text:
            return 1
        cjk = sum(1 for c in text if '\u4e00' <= c <= '\u9fff' or '\u3400' <= c <= '\u4dbf')
        non_cjk = len(text) - cjk
        return max(1, int(cjk / 1.5 + non_cjk / 4))

    def _row_to_message(self, row: Any) -> Message:
        metadata = {}
        with contextlib.suppress(json.JSONDecodeError, TypeError):
            metadata = json.loads(row["metadata"])
        return Message(
            id=row["id"],
            session_id=row["session_id"],
            role=row["role"],
            content=row["content"],
            metadata=metadata,
            token_count=row["token_count"],
            created_at=row["created_at"],
        )
