"""Audit event system — structured audit trail for all control plane actions."""
from __future__ import annotations

import json
import time
import uuid
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class AuditLevel(str, Enum):
    INFO = "info"
    WARN = "warn"
    ERROR = "error"
    CRITICAL = "critical"


class AuditEvent(BaseModel):
    """A single audit event record."""
    event_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    timestamp: float = Field(default_factory=time.time)
    level: AuditLevel = AuditLevel.INFO
    actor: str = ""          # who triggered (user/agent/system)
    action: str = ""         # what was done (model.switch, control.run, etc.)
    target: str = ""         # what was affected (agent name, model name, etc.)
    detail: dict[str, Any] = Field(default_factory=dict)
    trace_id: str = ""


class AuditLog:
    """Append-only audit log backed by JSONL file."""

    def __init__(self, log_path: str | Path | None = None) -> None:
        if log_path is None:
            log_path = Path.home() / ".maop" / "audit.jsonl"
        self._path = Path(log_path)
        self._readonly = False  # if True, record() is no-op (dir creation failed)
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
        except (PermissionError, OSError):
            # Fallback: try user home .MAOP directory
            try:
                fallback = Path.home() / ".maop" / "audit.jsonl"
                fallback.parent.mkdir(parents=True, exist_ok=True)
                self._path = fallback
            except Exception:
                # Last resort: in-memory only (no persistence)
                self._path = Path(fallback) if 'fallback' in dir() else Path(log_path)
                self._readonly = True

    def record(self, event: AuditEvent) -> None:
        """Append an audit event to the log file."""
        if self._readonly:
            return  # dir creation failed; silently skip persistence
        try:
            with open(self._path, "a", encoding="utf-8") as f:
                f.write(event.model_dump_json() + "\n")
        except (PermissionError, OSError):
            self._readonly = True

    def log(
        self,
        action: str,
        actor: str = "system",
        target: str = "",
        level: AuditLevel = AuditLevel.INFO,
        detail: dict[str, Any] | None = None,
        trace_id: str = "",
    ) -> AuditEvent:
        """Create, record, and return an audit event."""
        event = AuditEvent(
            action=action, actor=actor, target=target,
            level=level, detail=detail or {}, trace_id=trace_id,
        )
        self.record(event)
        return event

    def read_recent(self, limit: int = 100) -> list[AuditEvent]:
        """Read the most recent N audit events."""
        if not self._path.exists():
            return []
        events: list[AuditEvent] = []
        lines = self._path.read_text(encoding="utf-8", errors="replace").strip().split("\n")
        for line in lines[-limit:]:
            if not line.strip():
                continue
            try:
                events.append(AuditEvent(**json.loads(line)))
            except Exception as exc:
                import logging
                logging.getLogger(__name__).debug("[audit] Skipping malformed line: %s", exc)
        return events

    def filter(
        self,
        action: str = "",
        actor: str = "",
        target: str = "",
        limit: int = 100,
    ) -> list[AuditEvent]:
        """Filter audit events by criteria."""
        events = self.read_recent(limit=limit * 5)  # over-read then filter
        result = []
        for e in events:
            if action and e.action != action:
                continue
            if actor and e.actor != actor:
                continue
            if target and e.target != target:
                continue
            result.append(e)
            if len(result) >= limit:
                break
        return result
