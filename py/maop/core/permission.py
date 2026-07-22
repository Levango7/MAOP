"""MAOP Permission Manager — always_allow / ask / deny rule engine.

Provides fine-grained permission control for agent actions.
Rules are stored in SQLite and matched by agent + action patterns.
When a rule matches 'ask', the action is deferred to HumanProxy
for interactive approval.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import cast

from pydantic import BaseModel

from maop.core.db_utils import get_db_path, sqlite_connect

logger = logging.getLogger(__name__)


class PermissionRule(BaseModel):
    id: str = ""
    agent: str = "*"
    action: str = "*"
    decision: str = "ask"  # allow | ask | deny
    reason: str = ""
    created: str = ""
    priority: int = 0


class PermissionCheck(BaseModel):
    allowed: bool
    decision: str  # allow | ask | deny
    matched_rule: str = ""
    reason: str = ""


class PermissionManager:
    """Permission rule engine with SQLite persistence.

    Usage::

        pm = PermissionManager(root_dir="/path/to/MAOP")
        pm.add_rule(agent="claude", action="file_write", decision="ask")
        check = pm.check(agent="claude", action="file_write")
        if check.decision == "ask":
            # defer to HumanProxy
            ...
    """

    def __init__(self, root_dir: str | Path) -> None:
        self._root = Path(root_dir)
        self._db_path = get_db_path("permission")
        self._ensure_db()

    def _ensure_db(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS permission_rules (
                    id TEXT PRIMARY KEY,
                    agent TEXT NOT NULL DEFAULT '*',
                    action TEXT NOT NULL DEFAULT '*',
                    decision TEXT NOT NULL DEFAULT 'ask',
                    reason TEXT DEFAULT '',
                    created TEXT NOT NULL,
                    priority INTEGER DEFAULT 0
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_perm_agent_action
                ON permission_rules(agent, action, priority DESC)
            """)

    def _connect(self):
        return sqlite_connect(self._db_path, foreign_keys=False)

    def _new_id(self) -> str:
        import uuid
        return f"perm-{uuid.uuid4().hex[:8]}"

    def add_rule(
        self,
        agent: str = "*",
        action: str = "*",
        decision: str = "ask",
        reason: str = "",
        priority: int = 0,
        rule_id: str = "",
    ) -> str:
        rid = rule_id or self._new_id()
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO permission_rules (id, agent, action, decision, reason, created, priority) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (rid, agent, action, decision, reason, now, priority),
            )
        logger.info("Permission rule added: %s agent=%s action=%s decision=%s", rid, agent, action, decision)
        return rid

    def remove_rule(self, rule_id: str) -> bool:
        with self._connect() as conn:
            cursor = conn.execute("DELETE FROM permission_rules WHERE id=?", (rule_id,))
            return cast(bool, cursor.rowcount > 0)

    def check(self, agent: str, action: str = "*") -> PermissionCheck:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM permission_rules ORDER BY priority DESC"
            ).fetchall()

        for row in rows:
            r_agent = row["agent"]
            r_action = row["action"]
            if self._match(agent, r_agent) and self._match(action, r_action):
                decision = row["decision"]
                return PermissionCheck(
                    allowed=(decision == "allow"),
                    decision=decision,
                    matched_rule=row["id"],
                    reason=row["reason"],
                )

        return PermissionCheck(allowed=False, decision="ask", reason="No matching rule; default=ask")

    def _match(self, value: str, pattern: str) -> bool:
        if pattern == "*":
            return True
        if pattern == value:
            return True
        import fnmatch
        return fnmatch.fnmatch(value, pattern)

    def list_rules(self, limit: int = 100) -> list[PermissionRule]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM permission_rules ORDER BY priority DESC, created DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [PermissionRule(
            id=r["id"], agent=r["agent"], action=r["action"],
            decision=r["decision"], reason=r["reason"],
            created=r["created"], priority=r["priority"],
        ) for r in rows]

    def get_rule(self, rule_id: str) -> PermissionRule | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM permission_rules WHERE id=?", (rule_id,)).fetchone()
        if row is None:
            return None
        return PermissionRule(
            id=row["id"], agent=row["agent"], action=row["action"],
            decision=row["decision"], reason=row["reason"],
            created=row["created"], priority=row["priority"],
        )
