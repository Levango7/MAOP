"""MAOP Worktree Manager — git-worktree style task branching.

Allows complex tasks to fork into multiple parallel branches (approaches),
then selectively merge results, diff branches, and rollback.

Unlike filesystem git-worktrees, this is a logical branching system
backed by SQLite — no git dependency required.

Usage::

    from maop.core.worktree import WorktreeManager

    wt = WorktreeManager(root_dir="/path/to/MAOP")

    # Create root task
    root_id = wt.create_root("Fix authentication bug")

    # Branch into approaches
    branch_a = wt.branch(root_id, name="approach-a", description="Add timeout config")
    branch_b = wt.branch(root_id, name="approach-b", description="Use async retry")

    # Update branch results
    wt.update_result(branch_a, result="Timeout config applied successfully")

    # Merge winning branch
    wt.merge(branch_a, strategy="auto")

    # Diff branches
    diff = wt.diff(branch_a, branch_b)
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from maop.core.db_utils import get_db_path, sqlite_connect

logger = logging.getLogger(__name__)


class BranchStatus(str, Enum):
    ACTIVE = "active"
    MERGED = "merged"
    ABANDONED = "abandoned"


class MergeStrategy(str, Enum):
    AUTO = "auto"
    MANUAL = "manual"
    CONFLICT = "conflict"


class BranchInfo(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:16])
    parent_id: str = ""
    root_id: str = ""
    name: str = ""
    description: str = ""
    status: BranchStatus = BranchStatus.ACTIVE
    result: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


class MergeResult(BaseModel):
    success: bool = True
    source_branch: str = ""
    target_branch: str = ""
    strategy: MergeStrategy = MergeStrategy.AUTO
    conflicts: list[str] = Field(default_factory=list)
    merged_result: str = ""


class DiffItem(BaseModel):
    field: str = ""
    branch_a_value: Any = None
    branch_b_value: Any = None


class DiffReport(BaseModel):
    branch_a: str = ""
    branch_b: str = ""
    differences: list[DiffItem] = Field(default_factory=list)
    identical: bool = False


class CheckpointInfo(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:16])
    node_id: str = ""
    label: str = ""
    snapshot: dict[str, Any] = Field(default_factory=dict)
    created_at: float = Field(default_factory=time.time)


_WORKTREE_DDL = """
CREATE TABLE IF NOT EXISTS worktree_nodes (
    id TEXT PRIMARY KEY,
    parent_id TEXT DEFAULT '',
    root_id TEXT DEFAULT '',
    name TEXT NOT NULL,
    description TEXT DEFAULT '',
    status TEXT DEFAULT 'active',
    result TEXT DEFAULT '',
    metadata TEXT DEFAULT '{}',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_wt_parent ON worktree_nodes(parent_id);
CREATE INDEX IF NOT EXISTS idx_wt_root ON worktree_nodes(root_id);
CREATE INDEX IF NOT EXISTS idx_wt_status ON worktree_nodes(status);

CREATE TABLE IF NOT EXISTS worktree_checkpoints (
    id TEXT PRIMARY KEY,
    node_id TEXT NOT NULL,
    label TEXT NOT NULL,
    snapshot TEXT DEFAULT '{}',
    created_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_cp_node ON worktree_checkpoints(node_id);
"""


class WorktreeManager:
    """git-worktree style multi-branch task management.

    Parameters
    ----------
    root_dir : str | Path
        MAOP project root directory.
    """

    def __init__(self, root_dir: str | Path) -> None:
        self._root = Path(root_dir)
        self._data_dir = self._root / "data"
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._db_path = get_db_path("worktree")
        self._init_db()

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(_WORKTREE_DDL)

    def _connect(self):
        return sqlite_connect(self._db_path, foreign_keys=False)

    def create_root(self, task: str, description: str = "") -> str:
        """Create a root task node. Returns the node ID."""
        now = time.time()
        node_id = uuid.uuid4().hex[:16]
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO worktree_nodes
                   (id, parent_id, root_id, name, description, status, result, metadata, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (node_id, "", node_id, task, description, BranchStatus.ACTIVE.value,
                 "", "{}", now, now),
            )
        logger.debug("Worktree root created: %s", node_id[:8])
        return node_id

    def branch(
        self,
        parent_id: str,
        name: str,
        description: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Fork a new branch from a parent node. Returns the branch ID."""
        now = time.time()

        with self._connect() as conn:
            row = conn.execute(
                "SELECT root_id FROM worktree_nodes WHERE id = ?", (parent_id,),
            ).fetchone()
            if not row:
                raise ValueError(f"Parent node '{parent_id}' not found")
            root_id = row[0] or parent_id

            node_id = uuid.uuid4().hex[:16]
            conn.execute(
                """INSERT INTO worktree_nodes
                   (id, parent_id, root_id, name, description, status, result, metadata, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (node_id, parent_id, root_id, name, description,
                 BranchStatus.ACTIVE.value, "",
                 json.dumps(metadata or {}), now, now),
            )
        logger.debug("Worktree branch '%s' created: %s", name, node_id[:8])
        return node_id

    def update_result(
        self,
        node_id: str,
        result: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Update a branch's result and/or metadata."""
        now = time.time()

        with self._connect() as conn:
            row = conn.execute(
                "SELECT id FROM worktree_nodes WHERE id = ?", (node_id,),
            ).fetchone()
            if not row:
                return False

            sets: list[str] = ["updated_at = ?"]
            params: list[Any] = [now]

            if result:
                sets.append("result = ?")
                params.append(result)
            if metadata is not None:
                sets.append("metadata = ?")
                params.append(json.dumps(metadata))

            params.append(node_id)
            conn.execute(
                f"UPDATE worktree_nodes SET {', '.join(sets)} WHERE id = ?", params,
            )
        return True

    def merge(
        self,
        source_branch: str,
        target_branch: str = "",
        strategy: MergeStrategy = MergeStrategy.AUTO,
    ) -> MergeResult:
        """Merge a source branch into target (or root if target not specified)."""
        with self._connect() as conn:
            src_row = conn.execute(
                "SELECT * FROM worktree_nodes WHERE id = ?", (source_branch,),
            ).fetchone()
            if not src_row:
                return MergeResult(success=False, source_branch=source_branch,
                                   strategy=strategy, conflicts=["Source branch not found"])

            cols = [d[0] for d in conn.execute("SELECT * FROM worktree_nodes LIMIT 0").description]
            src = dict(zip(cols, src_row))

            tgt_id = target_branch or src["root_id"]
            tgt_row = conn.execute(
                "SELECT * FROM worktree_nodes WHERE id = ?", (tgt_id,),
            ).fetchone()
            if not tgt_row:
                return MergeResult(success=False, source_branch=source_branch,
                                   target_branch=tgt_id, strategy=strategy,
                                   conflicts=["Target branch not found"])
            tgt = dict(zip(cols, tgt_row))

            conflicts: list[str] = []
            merged_result = src.get("result", "")
            used_strategy = strategy

            if tgt.get("result") and src.get("result") and strategy == MergeStrategy.AUTO:
                if tgt["result"] != src["result"]:
                    conflicts.append(
                        f"Result conflict: target='{tgt['result'][:50]}', source='{src['result'][:50]}'"
                    )
                    merged_result = src["result"]
                    used_strategy = MergeStrategy.MANUAL

            now = time.time()
            conn.execute(
                "UPDATE worktree_nodes SET result = ?, updated_at = ? WHERE id = ?",
                (merged_result, now, tgt_id),
            )
            conn.execute(
                "UPDATE worktree_nodes SET status = ?, updated_at = ? WHERE id = ?",
                (BranchStatus.MERGED.value, now, source_branch),
            )

        return MergeResult(
            success=True,
            source_branch=source_branch,
            target_branch=tgt_id,
            strategy=used_strategy,
            conflicts=conflicts,
            merged_result=merged_result,
        )

    def diff(self, branch_a: str, branch_b: str) -> DiffReport:
        """Compare two branches' results and metadata."""
        with self._connect() as conn:
            row_a = conn.execute(
                "SELECT * FROM worktree_nodes WHERE id = ?", (branch_a,),
            ).fetchone()
            row_b = conn.execute(
                "SELECT * FROM worktree_nodes WHERE id = ?", (branch_b,),
            ).fetchone()

            if not row_a or not row_b:
                return DiffReport(branch_a=branch_a, branch_b=branch_b)

            cols = [d[0] for d in conn.execute("SELECT * FROM worktree_nodes LIMIT 0").description]
            a = dict(zip(cols, row_a))
            b = dict(zip(cols, row_b))

        differences: list[DiffItem] = []
        for field in ("result", "name", "description", "status", "metadata"):
            va = a.get(field, "")
            vb = b.get(field, "")
            if va != vb:
                differences.append(DiffItem(field=field, branch_a_value=va, branch_b_value=vb))

        return DiffReport(
            branch_a=branch_a,
            branch_b=branch_b,
            differences=differences,
            identical=len(differences) == 0,
        )

    def cherry_pick(
        self,
        source_branch: str,
        target_branch: str,
        items: list[str],
    ) -> bool:
        """Selectively merge specific metadata items from source to target."""
        with self._connect() as conn:
            src_row = conn.execute(
                "SELECT metadata FROM worktree_nodes WHERE id = ?", (source_branch,),
            ).fetchone()
            if not src_row:
                return False

            src_meta = json.loads(src_row[0]) if src_row[0] else {}

            tgt_row = conn.execute(
                "SELECT metadata FROM worktree_nodes WHERE id = ?", (target_branch,),
            ).fetchone()
            if not tgt_row:
                return False

            tgt_meta = json.loads(tgt_row[0]) if tgt_row[0] else {}

            for item in items:
                if item in src_meta:
                    tgt_meta[item] = src_meta[item]

            now = time.time()
            conn.execute(
                "UPDATE worktree_nodes SET metadata = ?, updated_at = ? WHERE id = ?",
                (json.dumps(tgt_meta), now, target_branch),
            )
        return True

    def checkpoint(self, node_id: str, label: str = "") -> str:
        """Create a checkpoint (snapshot) of a branch's current state."""
        now = time.time()

        with self._connect() as conn:
            row = conn.execute(
                "SELECT result, metadata, status FROM worktree_nodes WHERE id = ?",
                (node_id,),
            ).fetchone()
            if not row:
                raise ValueError(f"Node '{node_id}' not found")

            snapshot = {
                "result": row[0],
                "metadata": json.loads(row[1]) if row[1] else {},
                "status": row[2],
            }

            cp_id = uuid.uuid4().hex[:16]
            conn.execute(
                """INSERT INTO worktree_checkpoints
                   (id, node_id, label, snapshot, created_at) VALUES (?, ?, ?, ?, ?)""",
                (cp_id, node_id, label or f"cp-{cp_id[:6]}", json.dumps(snapshot), now),
            )
        return cp_id

    def rollback(self, node_id: str, to_checkpoint: str) -> bool:
        """Rollback a branch to a specific checkpoint."""
        now = time.time()

        with self._connect() as conn:
            cp_row = conn.execute(
                "SELECT snapshot FROM worktree_checkpoints WHERE id = ? AND node_id = ?",
                (to_checkpoint, node_id),
            ).fetchone()
            if not cp_row:
                return False

            snapshot = json.loads(cp_row[0])
            conn.execute(
                "UPDATE worktree_nodes SET result = ?, metadata = ?, status = ?, updated_at = ? WHERE id = ?",
                (snapshot.get("result", ""), json.dumps(snapshot.get("metadata", {})),
                 snapshot.get("status", BranchStatus.ACTIVE.value), now, node_id),
            )
        return True

    def abandon(self, node_id: str) -> bool:
        """Mark a branch as abandoned."""
        now = time.time()
        with self._connect() as conn:
            conn.execute(
                "UPDATE worktree_nodes SET status = ?, updated_at = ? WHERE id = ?",
                (BranchStatus.ABANDONED.value, now, node_id),
            )
        return True

    def list_branches(self, root_id: str = "", active_only: bool = False) -> list[BranchInfo]:
        """List all branches, optionally filtered by root or active status."""
        with self._connect() as conn:
            sql = "SELECT * FROM worktree_nodes WHERE 1=1"
            params: list[Any] = []
            if root_id:
                sql += " AND root_id = ?"
                params.append(root_id)
            if active_only:
                sql += " AND status = ?"
                params.append(BranchStatus.ACTIVE.value)
            sql += " ORDER BY created_at DESC"

            cursor = conn.execute(sql, params)
            cols = [d[0] for d in cursor.description] if cursor.description else []
            rows = cursor.fetchall()

        return [
            BranchInfo(
                id=d["id"], parent_id=d.get("parent_id", ""),
                root_id=d.get("root_id", ""),
                name=d.get("name", ""), description=d.get("description", ""),
                status=BranchStatus(d.get("status", "active")),
                result=d.get("result", ""),
                metadata=json.loads(d.get("metadata", "{}")),
                created_at=d["created_at"], updated_at=float(d.get("updated_at", d["created_at"]) or d["created_at"]),
            )
            for d in (dict(zip(cols, row)) for row in rows)
        ]

    def get_branch(self, node_id: str) -> BranchInfo | None:
        """Get a single branch by ID."""
        with self._connect() as conn:
            cursor = conn.execute(
                "SELECT * FROM worktree_nodes WHERE id = ?", (node_id,),
            )
            cols = [d[0] for d in cursor.description] if cursor.description else []
            row = cursor.fetchone()
        if not row:
            return None
        d = dict(zip(cols, row))
        return BranchInfo(
            id=d["id"], parent_id=d.get("parent_id", ""),
            root_id=d.get("root_id", ""),
            name=d.get("name", ""), description=d.get("description", ""),
            status=BranchStatus(d.get("status", "active")),
            result=d.get("result", ""),
            metadata=json.loads(d.get("metadata", "{}")),
            created_at=d["created_at"], updated_at=float(d.get("updated_at", d["created_at"]) or d["created_at"]),
        )

    def list_checkpoints(self, node_id: str) -> list[CheckpointInfo]:
        """List all checkpoints for a branch."""
        with self._connect() as conn:
            cursor = conn.execute(
                "SELECT * FROM worktree_checkpoints WHERE node_id = ? ORDER BY created_at DESC",
                (node_id,),
            )
            cols = [d[0] for d in cursor.description] if cursor.description else []
            rows = cursor.fetchall()

        return [
            CheckpointInfo(
                id=d["id"], node_id=d["node_id"], label=d.get("label", ""),
                snapshot=json.loads(d.get("snapshot", "{}")),
                created_at=d["created_at"],
            )
            for d in (dict(zip(cols, row)) for row in rows)
        ]
