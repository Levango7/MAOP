"""MAOP Error Ledger — Structured error tracking with auto-promotion.

Supports:
  - Record errors with context (trigger, root cause, pattern)
  - Find errors by pattern
  - Get hotspots (most frequent error patterns)
  - Auto-promote: patterns appearing >= threshold times become rules

Usage::

    from maop.core.error_ledger import ErrorLedger

    ledger = ErrorLedger(root_dir="/path/to/MAOP")

    # Record an error
    eid = ledger.record(
        error_type="tool_error",
        context="Running git push",
        trigger={"tool": "git", "args": ["push"]},
        output="fatal: not a git repository",
    )

    # Find by pattern
    errors = ledger.find_by_pattern("git repository")

    # Get hotspots
    hotspots = ledger.get_hotspots()

    # Auto-promote recurring errors to rules
    promoted = ledger.auto_promote(threshold=3)
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any, cast

from pydantic import BaseModel, Field

from maop.core.db_utils import get_db_path, sqlite_connect

logger = logging.getLogger(__name__)


class ErrorEvent(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    error_type: str = ""
    context: str = ""
    trigger: dict[str, Any] = Field(default_factory=dict)
    output: str = ""
    expected: str = ""
    root_cause: str = ""
    pattern: str = ""
    rule: str = ""
    action: str = ""
    recurrence: int = 1
    created_at: float = Field(default_factory=time.time)


class Hotspot(BaseModel):
    pattern: str
    count: int
    last_seen: float


class PromotedRule(BaseModel):
    pattern: str
    rule: str
    count: int


_ERROR_LEDGER_DDL = """
CREATE TABLE IF NOT EXISTS error_ledger (
    id TEXT PRIMARY KEY,
    error_type TEXT NOT NULL,
    context TEXT DEFAULT '',
    trigger TEXT DEFAULT '{}',
    output TEXT DEFAULT '',
    expected TEXT DEFAULT '',
    root_cause TEXT DEFAULT '',
    pattern TEXT DEFAULT '',
    rule TEXT DEFAULT '',
    action TEXT DEFAULT '',
    recurrence INTEGER DEFAULT 1,
    created_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_el_type ON error_ledger(error_type);
CREATE INDEX IF NOT EXISTS idx_el_pattern ON error_ledger(pattern);
CREATE INDEX IF NOT EXISTS idx_el_created ON error_ledger(created_at DESC);

CREATE TABLE IF NOT EXISTS promoted_rules (
    id TEXT PRIMARY KEY,
    pattern TEXT NOT NULL,
    rule TEXT NOT NULL,
    count INTEGER DEFAULT 0,
    promoted_at REAL NOT NULL
);
"""


class ErrorLedger:
    def __init__(self, root_dir: str | Path) -> None:
        self._root = Path(root_dir)
        self._data_dir = self._root / "data"
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._db_path = get_db_path("error_ledger")
        self._init_db()

    def _init_db(self) -> None:
        with self._db_connect() as conn:
            conn.executescript(_ERROR_LEDGER_DDL)

    def _db_connect(self):
        return sqlite_connect(self._db_path, foreign_keys=False)

    def record(
        self,
        error_type: str,
        context: str = "",
        trigger: dict[str, Any] | None = None,
        output: str = "",
        expected: str = "",
        root_cause: str = "",
        pattern: str = "",
    ) -> str:
        event = ErrorEvent(
            error_type=error_type, context=context,
            trigger=trigger or {}, output=output,
            expected=expected, root_cause=root_cause,
            pattern=pattern or error_type,
        )
        with self._db_connect() as conn:
            existing = conn.execute(
                "SELECT id, recurrence FROM error_ledger WHERE pattern = ? ORDER BY created_at DESC LIMIT 1",
                (event.pattern,),
            ).fetchone()
            if existing:
                conn.execute(
                    "UPDATE error_ledger SET recurrence = ?, created_at = ? WHERE id = ?",
                    (existing[1] + 1, event.created_at, existing[0]),
                )
                return cast(str, existing[0])
            conn.execute(
                """INSERT INTO error_ledger
                   (id, error_type, context, trigger, output, expected, root_cause, pattern, rule, action, recurrence, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (event.id, event.error_type, event.context,
                 json.dumps(event.trigger), event.output, event.expected,
                 event.root_cause, event.pattern, event.rule, event.action,
                 event.recurrence, event.created_at),
            )
        logger.debug("Error recorded: %s (pattern=%s)", event.id, event.pattern)
        return event.id

    def find_by_pattern(self, pattern: str) -> list[ErrorEvent]:
        with self._db_connect() as conn:
            cursor = conn.execute(
                """SELECT id, error_type, context, trigger, output, expected,
                          root_cause, pattern, rule, action, recurrence, created_at
                   FROM error_ledger WHERE pattern LIKE ?
                   ORDER BY created_at DESC""",
                (f"%{pattern}%",),
            )
            cols = [d[0] for d in cursor.description] if cursor.description else []
            rows = cursor.fetchall()
        results = []
        for row in rows:
            d = dict(zip(cols, row))
            d["trigger"] = json.loads(d.get("trigger", "{}"))
            results.append(ErrorEvent(**d))
        return results

    def get_hotspots(self, top: int = 10) -> list[Hotspot]:
        with self._db_connect() as conn:
            rows = conn.execute(
                """SELECT pattern, SUM(recurrence) as cnt, MAX(created_at) as last_seen
                   FROM error_ledger GROUP BY pattern
                   ORDER BY cnt DESC LIMIT ?""",
                (top,),
            ).fetchall()
        return [Hotspot(pattern=r[0], count=r[1], last_seen=r[2]) for r in rows]

    def auto_promote(self, threshold: int = 3) -> list[PromotedRule]:
        promoted = []
        with self._db_connect() as conn:
            rows = conn.execute(
                """SELECT pattern, SUM(recurrence) as cnt
                   FROM error_ledger GROUP BY pattern
                   HAVING cnt >= ?""",
                (threshold,),
            ).fetchall()
            for pattern, count in rows:
                existing = conn.execute(
                    "SELECT id FROM promoted_rules WHERE pattern = ?", (pattern,)
                ).fetchone()
                if existing:
                    continue
                rule_text = f"When encountering '{pattern}', review and validate before proceeding"
                rule_id = uuid.uuid4().hex[:12]
                conn.execute(
                    """INSERT INTO promoted_rules (id, pattern, rule, count, promoted_at)
                       VALUES (?, ?, ?, ?, ?)""",
                    (rule_id, pattern, rule_text, count, time.time()),
                )
                conn.execute(
                    "UPDATE error_ledger SET rule = ?, action = 'auto_promoted' WHERE pattern = ?",
                    (rule_text, pattern),
                )
                promoted.append(PromotedRule(pattern=pattern, rule=rule_text, count=count))
                logger.info("Auto-promoted pattern '%s' (count=%d) to rule", pattern, count)
        return promoted

    def get_promoted_rules(self) -> list[PromotedRule]:
        with self._db_connect() as conn:
            rows = conn.execute(
                "SELECT pattern, rule, count FROM promoted_rules ORDER BY promoted_at DESC"
            ).fetchall()
        return [PromotedRule(pattern=r[0], rule=r[1], count=r[2]) for r in rows]