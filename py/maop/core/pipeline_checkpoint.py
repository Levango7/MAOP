"""MAOP Pipeline Checkpoint — Interrupt-resilient workflow execution.

Enables long-running pipelines to resume from the last completed step
after an interruption (crash, restart, timeout). Each step's completion
is recorded as a checkpoint; on resume, already-completed steps are
skipped automatically.

Usage::

    from maop.core.pipeline_checkpoint import PipelineCheckpoint

    ckpt = PipelineCheckpoint(root_dir="/path/to/MAOP")

    # Start a pipeline run
    run_id = ckpt.start_run("my_workflow", steps=["collect", "analyze", "report"])

    # Mark steps as completed
    ckpt.complete_step(run_id, "collect", output="data collected")
    ckpt.complete_step(run_id, "analyze", output="analysis done")

    # After crash/restart, check what's left
    state = ckpt.get_run(run_id)
    pending = ckpt.pending_steps(run_id)  # ["report"]

    # Resume from checkpoint
    ckpt.resume_run(run_id)
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from maop.core.db_utils import get_db_path, sqlite_connect

logger = logging.getLogger(__name__)


class StepCheckpoint(BaseModel):
    """Checkpoint for a single pipeline step."""
    step_name: str = ""
    status: str = "pending"
    output: str = ""
    started_at: float = 0.0
    completed_at: float = 0.0
    metadata: dict[str, Any] = Field(default_factory=dict)


class RunState(BaseModel):
    """Full state of a pipeline run."""
    run_id: str = ""
    workflow_name: str = ""
    status: str = "running"
    steps: list[StepCheckpoint] = Field(default_factory=list)
    variables: dict[str, Any] = Field(default_factory=dict)
    created_at: float = 0.0
    updated_at: float = 0.0


_CHECKPOINT_DDL = """
CREATE TABLE IF NOT EXISTS pipeline_runs (
    run_id TEXT PRIMARY KEY,
    workflow_name TEXT NOT NULL,
    status TEXT DEFAULT 'running',
    variables TEXT DEFAULT '{}',
    created_at REAL NOT NULL,
    updated_at REAL DEFAULT 0.0
);

CREATE TABLE IF NOT EXISTS pipeline_step_checkpoints (
    run_id TEXT NOT NULL,
    step_name TEXT NOT NULL,
    status TEXT DEFAULT 'pending',
    output TEXT DEFAULT '',
    started_at REAL DEFAULT 0.0,
    completed_at REAL DEFAULT 0.0,
    metadata TEXT DEFAULT '{}',
    PRIMARY KEY (run_id, step_name)
);

CREATE INDEX IF NOT EXISTS idx_ckpt_run ON pipeline_step_checkpoints(run_id);
CREATE INDEX IF NOT EXISTS idx_ckpt_status ON pipeline_step_checkpoints(status);
"""


class PipelineCheckpoint:
    """Interrupt-resilient pipeline execution with step-level checkpoints.

    Parameters
    ----------
    root_dir : str | Path
        MAOP project root directory.
    """

    def __init__(self, root_dir: str | Path) -> None:
        self._root = Path(root_dir)
        self._data_dir = self._root / "data"
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._db_path = get_db_path("pipeline_checkpoint")
        self._init_db()

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(_CHECKPOINT_DDL)

    def _connect(self):
        return sqlite_connect(self._db_path, foreign_keys=False)

    def start_run(
        self,
        workflow_name: str,
        steps: list[str],
        variables: dict[str, Any] | None = None,
    ) -> str:
        """Start a new pipeline run with named steps.

        Returns the run_id.
        """
        run_id = uuid.uuid4().hex[:16]
        now = time.time()

        with self._connect() as conn:
            conn.execute(
                """INSERT INTO pipeline_runs (run_id, workflow_name, status, variables, created_at, updated_at)
                   VALUES (?, ?, 'running', ?, ?, ?)""",
                (run_id, workflow_name, json.dumps(variables or {}), now, now),
            )
            for step_name in steps:
                conn.execute(
                    """INSERT INTO pipeline_step_checkpoints
                       (run_id, step_name, status, output, started_at, completed_at, metadata)
                       VALUES (?, ?, 'pending', '', 0.0, 0.0, '{}')""",
                    (run_id, step_name),
                )

        logger.info("[checkpoint] Started run %s for workflow '%s' (%d steps)", run_id[:8], workflow_name, len(steps))
        return run_id

    def complete_step(
        self,
        run_id: str,
        step_name: str,
        output: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Mark a step as completed.

        Returns True if the step was found and updated.
        """
        now = time.time()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT step_name FROM pipeline_step_checkpoints WHERE run_id = ? AND step_name = ?",
                (run_id, step_name),
            ).fetchone()
            if not row:
                return False

            conn.execute(
                """UPDATE pipeline_step_checkpoints
                   SET status = 'completed', output = ?, completed_at = ?, metadata = ?
                   WHERE run_id = ? AND step_name = ?""",
                (output, now, json.dumps(metadata or {}), run_id, step_name),
            )
            conn.execute(
                "UPDATE pipeline_runs SET updated_at = ? WHERE run_id = ?",
                (now, run_id),
            )

        logger.debug("[checkpoint] Step '%s' completed in run %s", step_name, run_id[:8])
        return True

    def fail_step(
        self,
        run_id: str,
        step_name: str,
        error: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Mark a step as failed."""
        now = time.time()
        with self._connect() as conn:
            conn.execute(
                """UPDATE pipeline_step_checkpoints
                   SET status = 'failed', output = ?, completed_at = ?, metadata = ?
                   WHERE run_id = ? AND step_name = ?""",
                (error, now, json.dumps(metadata or {}), run_id, step_name),
            )
            conn.execute(
                "UPDATE pipeline_runs SET status = 'failed', updated_at = ? WHERE run_id = ?",
                (now, run_id),
            )
        return True

    def start_step(self, run_id: str, step_name: str) -> bool:
        """Mark a step as started (in-progress)."""
        now = time.time()
        with self._connect() as conn:
            conn.execute(
                """UPDATE pipeline_step_checkpoints
                   SET status = 'running', started_at = ?
                   WHERE run_id = ? AND step_name = ?""",
                (now, run_id, step_name),
            )
        return True

    def pending_steps(self, run_id: str) -> list[str]:
        """Get step names that are still pending or failed (for retry)."""
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT step_name FROM pipeline_step_checkpoints
                   WHERE run_id = ? AND status IN ('pending', 'failed')
                   ORDER BY rowid""",
                (run_id,),
            ).fetchall()
        return [row[0] for row in rows]

    def completed_steps(self, run_id: str) -> list[str]:
        """Get step names that have been completed."""
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT step_name FROM pipeline_step_checkpoints
                   WHERE run_id = ? AND status = 'completed'
                   ORDER BY completed_at""",
                (run_id,),
            ).fetchall()
        return [row[0] for row in rows]

    def get_run(self, run_id: str) -> RunState | None:
        """Get the full state of a pipeline run."""
        with self._connect() as conn:
            run_row = conn.execute(
                "SELECT * FROM pipeline_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if not run_row:
                return None

            cols = [d[0] for d in conn.execute("SELECT * FROM pipeline_runs LIMIT 0").description]
            run_data = dict(zip(cols, run_row))

            step_cursor = conn.execute(
                "SELECT * FROM pipeline_step_checkpoints WHERE run_id = ? ORDER BY rowid",
                (run_id,),
            )
            step_cols = [d[0] for d in step_cursor.description] if step_cursor.description else []
            step_rows = step_cursor.fetchall()

        steps = []
        for row in step_rows:
            d = dict(zip(step_cols, row))
            steps.append(StepCheckpoint(
                step_name=d.get("step_name", ""),
                status=d.get("status", "pending"),
                output=d.get("output", ""),
                started_at=d.get("started_at", 0.0),
                completed_at=d.get("completed_at", 0.0),
                metadata=json.loads(d.get("metadata", "{}")),
            ))

        return RunState(
            run_id=run_id,
            workflow_name=run_data.get("workflow_name", ""),
            status=run_data.get("status", "running"),
            steps=steps,
            variables=json.loads(run_data.get("variables", "{}")),
            created_at=run_data.get("created_at", 0.0),
            updated_at=run_data.get("updated_at", 0.0),
        )

    def resume_run(self, run_id: str) -> RunState | None:
        """Mark a run as running again (for resumption after interruption)."""
        now = time.time()
        with self._connect() as conn:
            conn.execute(
                "UPDATE pipeline_runs SET status = 'running', updated_at = ? WHERE run_id = ?",
                (now, run_id),
            )
        return self.get_run(run_id)

    def finish_run(self, run_id: str) -> bool:
        """Mark a run as completed."""
        now = time.time()
        with self._connect() as conn:
            conn.execute(
                "UPDATE pipeline_runs SET status = 'completed', updated_at = ? WHERE run_id = ?",
                (now, run_id),
            )
        return True

    def list_runs(
        self,
        workflow_name: str = "",
        status: str = "",
        limit: int = 50,
    ) -> list[RunState]:
        """List pipeline runs with optional filters."""
        with self._connect() as conn:
            sql = "SELECT run_id FROM pipeline_runs WHERE 1=1"
            params: list[Any] = []
            if workflow_name:
                sql += " AND workflow_name = ?"
                params.append(workflow_name)
            if status:
                sql += " AND status = ?"
                params.append(status)
            sql += " ORDER BY created_at DESC LIMIT ?"
            params.append(limit)

            rows = conn.execute(sql, params).fetchall()

        return [r for row in rows if (r := self.get_run(row[0])) is not None]

    def cleanup(self, max_age_days: int = 30) -> int:
        """Remove old completed/failed runs. Returns count removed."""
        cutoff = time.time() - (max_age_days * 86400)
        with self._connect() as conn:
            run_ids = conn.execute(
                "SELECT run_id FROM pipeline_runs WHERE status IN ('completed', 'failed') AND created_at < ?",
                (cutoff,),
            ).fetchall()
            for (run_id,) in run_ids:
                conn.execute("DELETE FROM pipeline_step_checkpoints WHERE run_id = ?", (run_id,))
                conn.execute("DELETE FROM pipeline_runs WHERE run_id = ?", (run_id,))
        return len(run_ids)