"""MAOP Prompt Version Management — Version-chain with rollback support.

Supports:
  - Prompt version chain (parent_version + rollback_version)
  - Create, update, rollback
  - Tag-based lookup
  - Auto-rollback on failure signals

Usage::

    from maop.core.evolution.prompt_version import PromptVersionManager

    mgr = PromptVersionManager(root_dir="/path/to/MAOP")

    v1 = mgr.create("system_prompt", "You are a helpful assistant.")
    v2 = mgr.create("system_prompt", "You are a precise coding assistant.", parent_version=v1)

    # Rollback
    mgr.rollback("system_prompt", v2)

    # Get current
    current = mgr.get_current("system_prompt")
"""

from __future__ import annotations

import contextlib
import json
import logging
import sqlite3
import time
import uuid
from pathlib import Path

from pydantic import BaseModel, Field

from maop.core.backends.db_utils import get_db_path, sqlite_connect

logger = logging.getLogger(__name__)


class PromptVersion(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    prompt_name: str = ""
    content: str = ""
    parent_version: str = ""
    rollback_version: str = ""
    tags: list[str] = Field(default_factory=list)
    is_active: bool = False
    created_at: float = Field(default_factory=time.time)


_PROMPT_VER_DDL = """
CREATE TABLE IF NOT EXISTS prompt_versions (
    id TEXT PRIMARY KEY,
    prompt_name TEXT NOT NULL,
    content TEXT NOT NULL,
    parent_version TEXT DEFAULT '',
    rollback_version TEXT DEFAULT '',
    tags TEXT DEFAULT '[]',
    is_active INTEGER DEFAULT 0,
    created_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_pv_name ON prompt_versions(prompt_name);
CREATE INDEX IF NOT EXISTS idx_pv_active ON prompt_versions(prompt_name, is_active);
"""


class PromptVersionManager:
    def __init__(self, root_dir: str | Path) -> None:
        self._root = Path(root_dir)
        self._data_dir = self._root / "data"
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._db_path = get_db_path("prompt_version")
        self._init_db()

    def _init_db(self) -> None:
        with self._db_connect() as conn:
            conn.executescript(_PROMPT_VER_DDL)

    def _db_connect(self) -> contextlib.AbstractContextManager[sqlite3.Connection]:
        return sqlite_connect(self._db_path, foreign_keys=False)

    def create(
        self,
        prompt_name: str,
        content: str,
        parent_version: str = "",
        tags: list[str] | None = None,
    ) -> PromptVersion:
        pv = PromptVersion(
            prompt_name=prompt_name, content=content,
            parent_version=parent_version, tags=tags or [],
        )
        with self._db_connect() as conn:
            conn.execute(
                "UPDATE prompt_versions SET is_active = 0 WHERE prompt_name = ? AND is_active = 1",
                (prompt_name,),
            )
            conn.execute(
                """INSERT INTO prompt_versions (id, prompt_name, content, parent_version, rollback_version, tags, is_active, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (pv.id, pv.prompt_name, pv.content, pv.parent_version, pv.rollback_version,
                 json.dumps(pv.tags), 1, pv.created_at),
            )
        logger.info("Prompt version created: %s@%s", prompt_name, pv.id)
        return pv

    def get_current(self, prompt_name: str) -> PromptVersion | None:
        with self._db_connect() as conn:
            cursor = conn.execute(
                """SELECT id, prompt_name, content, parent_version, rollback_version, tags, is_active, created_at
                   FROM prompt_versions WHERE prompt_name = ? AND is_active = 1""",
                (prompt_name,),
            )
            cols = [d[0] for d in cursor.description] if cursor.description else []
            row = cursor.fetchone()
        if not row:
            return None
        d = dict(zip(cols, row))
        d["tags"] = json.loads(d.get("tags", "[]"))
        d["is_active"] = bool(d["is_active"])
        return PromptVersion(**d)

    def get_version(self, version_id: str) -> PromptVersion | None:
        with self._db_connect() as conn:
            cursor = conn.execute(
                """SELECT id, prompt_name, content, parent_version, rollback_version, tags, is_active, created_at
                   FROM prompt_versions WHERE id = ?""",
                (version_id,),
            )
            cols = [d[0] for d in cursor.description] if cursor.description else []
            row = cursor.fetchone()
        if not row:
            return None
        d = dict(zip(cols, row))
        d["tags"] = json.loads(d.get("tags", "[]"))
        d["is_active"] = bool(d["is_active"])
        return PromptVersion(**d)

    def list_versions(self, prompt_name: str) -> list[PromptVersion]:
        with self._db_connect() as conn:
            cursor = conn.execute(
                """SELECT id, prompt_name, content, parent_version, rollback_version, tags, is_active, created_at
                   FROM prompt_versions WHERE prompt_name = ? ORDER BY created_at DESC""",
                (prompt_name,),
            )
            cols = [d[0] for d in cursor.description] if cursor.description else []
            rows = cursor.fetchall()
        results = []
        for row in rows:
            d = dict(zip(cols, row))
            d["tags"] = json.loads(d.get("tags", "[]"))
            d["is_active"] = bool(d["is_active"])
            results.append(PromptVersion(**d))
        return results

    def rollback(self, prompt_name: str, from_version: str) -> PromptVersion | None:
        pv = self.get_version(from_version)
        if not pv:
            return None
        parent = pv.parent_version
        if not parent:
            logger.warning("No parent version to rollback to for %s", from_version)
            return None
        with self._db_connect() as conn:
            conn.execute(
                "UPDATE prompt_versions SET is_active = 0 WHERE prompt_name = ? AND is_active = 1",
                (prompt_name,),
            )
            conn.execute(
                "UPDATE prompt_versions SET is_active = 1, rollback_version = ? WHERE id = ?",
                (from_version, parent),
            )
        logger.info("Rolled back %s from %s to %s", prompt_name, from_version, parent)
        return self.get_version(parent)

    def find_by_tag(self, tag: str) -> list[PromptVersion]:
        with self._db_connect() as conn:
            cursor = conn.execute(
                """SELECT id, prompt_name, content, parent_version, rollback_version, tags, is_active, created_at
                   FROM prompt_versions WHERE tags LIKE ? ORDER BY created_at DESC""",
                (f'%"{tag}"%',),
            )
            cols = [d[0] for d in cursor.description] if cursor.description else []
            rows = cursor.fetchall()
        results = []
        for row in rows:
            d = dict(zip(cols, row))
            d["tags"] = json.loads(d.get("tags", "[]"))
            d["is_active"] = bool(d["is_active"])
            results.append(PromptVersion(**d))
        return results
