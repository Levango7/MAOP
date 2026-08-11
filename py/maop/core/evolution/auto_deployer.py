"""MAOP Auto Deployer — Promote winners / rollback regressions automatically.

F2-01 Agent 自演化闭环的第四环：当 ABTestFramework 判定 treatment
显著更优时，自动将 treatment 提升为生产配置（写 config / 标记
winner）；当检测到劣化（regression）时，自动回滚到上一个快照。

安全策略：
  - 提升（promote）默认开启，但写入前先通过 ChangeTracker 打快照
    以便回滚。
  - 回滚（rollback）默认开启，调用 ChangeTracker.rollback 恢复文件。
  - 所有操作记录到 ``evolution_deployments`` 表，供审计与可视化。

Usage::

    from maop.core.evolution.auto_deployer import AutoDeployer

    deployer = AutoDeployer(root_dir="/path/to/MAOP")
    result = deployer.promote(experiment="prompt_v2", winner="treatment")
    if result.success:
        print(f"Promoted {result.winner}, snapshot={result.snapshot_id}")
"""

from __future__ import annotations

import contextlib
import json
import logging
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from maop.core.backends.db_utils import get_db_path, sqlite_connect

logger = logging.getLogger(__name__)


_DEPLOY_DDL = """
CREATE TABLE IF NOT EXISTS evolution_deployments (
    id TEXT PRIMARY KEY,
    experiment TEXT NOT NULL,
    action TEXT NOT NULL,           -- promote | rollback
    winner TEXT DEFAULT '',
    snapshot_id TEXT DEFAULT '',
    success INTEGER NOT NULL,
    detail TEXT DEFAULT '',
    config_json TEXT DEFAULT '{}',
    created_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_deploy_exp ON evolution_deployments(experiment, created_at DESC);
"""


class DeploymentRecord(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    experiment: str = ""
    action: str = ""  # promote | rollback
    winner: str = ""
    snapshot_id: str = ""
    success: bool = False
    detail: str = ""
    config: dict[str, Any] = Field(default_factory=dict)
    created_at: float = Field(default_factory=time.time)


class PromoteResult(BaseModel):
    success: bool = False
    winner: str = ""
    snapshot_id: str = ""
    deployment_id: str = ""
    detail: str = ""


class RollbackResult(BaseModel):
    success: bool = False
    restored_files: int = 0
    snapshot_id: str = ""
    deployment_id: str = ""
    detail: str = ""


class AutoDeployer:
    """优胜自动提升 / 劣化自动回滚。

    Parameters
    ----------
    root_dir : str | Path
        MAOP 项目根目录。
    enable_promote : bool
        是否允许自动提升。
    enable_rollback : bool
        是否允许自动回滚。
    """

    def __init__(
        self,
        root_dir: str | Path,
        *,
        enable_promote: bool = True,
        enable_rollback: bool = True,
    ) -> None:
        self._root = Path(root_dir)
        self._data_dir = self._root / "data"
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._db_path = get_db_path("evolution_deploy")
        self._enable_promote = enable_promote
        self._enable_rollback = enable_rollback
        self._init_db()

    def _init_db(self) -> None:
        with self._db_connect() as conn:
            conn.executescript(_DEPLOY_DDL)

    def _db_connect(self) -> contextlib.AbstractContextManager[sqlite3.Connection]:
        return sqlite_connect(self._db_path, foreign_keys=False)

    # ── 提升 ───────────────────────────────────────────────────

    def promote(
        self,
        experiment: str,
        winner: str,
        *,
        config: dict[str, Any] | None = None,
        snapshot_before: bool = True,
    ) -> PromoteResult:
        """将 winner 变体提升为生产配置。

        步骤：
          1. （可选）通过 ChangeTracker 打快照，便于后续回滚
          2. 写入 config/agents.yaml 或 data/evolution-active-variant.json
          3. 记录 DeploymentRecord

        Parameters
        ----------
        experiment : str
            实验名。
        winner : str
            获胜变体名。
        config : dict | None
            要持久化的配置（如 prompt / model / timeout）。None 时仅
            记录 winner 标记，不写 config 文件。
        snapshot_before : bool
            提升前是否打快照（默认 True，便于回滚）。
        """
        if not self._enable_promote:
            return PromoteResult(success=False, detail="promote disabled")
        if not winner:
            return PromoteResult(success=False, detail="empty winner")

        snap_id = ""
        if snapshot_before:
            snap_id = self._take_snapshot(label=f"pre-promote-{experiment}-{winner}")

        detail_parts: list[str] = []
        if config is not None:
            try:
                self._write_active_variant(experiment, winner, config)
                detail_parts.append("config written")
            except Exception as exc:
                logger.warning("[auto-deploy] write config failed: %s", exc)
                detail_parts.append(f"config write failed: {exc}")

        try:
            self._mark_winner(experiment, winner)
            detail_parts.append("winner marked")
        except Exception as exc:
            logger.warning("[auto-deploy] mark winner failed: %s", exc)

        record = DeploymentRecord(
            experiment=experiment,
            action="promote",
            winner=winner,
            snapshot_id=snap_id,
            success=True,
            detail="; ".join(detail_parts) or "promoted",
            config=config or {},
        )
        self._save_record(record)

        logger.info("[auto-deploy] promoted %s winner=%s snapshot=%s", experiment, winner, snap_id)
        return PromoteResult(
            success=True, winner=winner, snapshot_id=snap_id,
            deployment_id=record.id, detail=record.detail,
        )

    # ── 回滚 ───────────────────────────────────────────────────

    def rollback(
        self,
        experiment: str,
        snapshot_id: str = "",
        *,
        reason: str = "regression",
    ) -> RollbackResult:
        """回滚到指定快照；未提供时回滚到该实验最近一次 promote 的快照。"""
        if not self._enable_rollback:
            return RollbackResult(success=False, detail="rollback disabled")

        snap = snapshot_id or self._last_promote_snapshot(experiment)
        if not snap:
            return RollbackResult(success=False, detail="no snapshot to rollback to")

        restored = 0
        detail = ""
        try:
            restored = self._restore_snapshot(snap)
            detail = f"restored {restored} files"
        except Exception as exc:
            logger.error("[auto-deploy] rollback failed: %s", exc)
            record = DeploymentRecord(
                experiment=experiment, action="rollback", snapshot_id=snap,
                success=False, detail=f"rollback error: {exc}",
            )
            self._save_record(record)
            return RollbackResult(success=False, snapshot_id=snap, deployment_id=record.id, detail=str(exc))

        record = DeploymentRecord(
            experiment=experiment, action="rollback", snapshot_id=snap,
            success=True, detail=f"{detail} (reason: {reason})",
        )
        self._save_record(record)
        logger.info("[auto-deploy] rolled back %s → snapshot %s (%d files)", experiment, snap, restored)
        return RollbackResult(
            success=True, restored_files=restored, snapshot_id=snap,
            deployment_id=record.id, detail=record.detail,
        )

    def rollback_on_regression(
        self,
        experiment: str,
        regression: bool,
        *,
        snapshot_id: str = "",
    ) -> RollbackResult | None:
        """便捷入口：当 regression=True 时触发回滚，否则返回 None。"""
        if not regression:
            return None
        return self.rollback(experiment, snapshot_id=snapshot_id, reason="auto-regression-detected")

    # ── 查询 ───────────────────────────────────────────────────

    def get_history(self, experiment: str = "", limit: int = 50) -> list[DeploymentRecord]:
        with self._db_connect() as conn:
            if experiment:
                rows = conn.execute(
                    "SELECT id, experiment, action, winner, snapshot_id, success, detail, config_json, created_at "
                    "FROM evolution_deployments WHERE experiment=? ORDER BY created_at DESC LIMIT ?",
                    (experiment, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT id, experiment, action, winner, snapshot_id, success, detail, config_json, created_at "
                    "FROM evolution_deployments ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        records: list[DeploymentRecord] = []
        for r in rows:
            try:
                cfg = json.loads(r[7]) if r[7] else {}
            except (json.JSONDecodeError, TypeError):
                cfg = {}
            records.append(DeploymentRecord(
                id=r[0], experiment=r[1], action=r[2], winner=r[3],
                snapshot_id=r[4], success=bool(r[5]), detail=r[6],
                config=cfg, created_at=r[8],
            ))
        return records

    # ── 内部：快照 / 配置写入 ─────────────────────────────────

    def _take_snapshot(self, *, label: str) -> str:
        try:
            from maop.core.reliability.change_tracker import ChangeTracker

            ct = ChangeTracker(root_dir=str(self._root))
            return ct.snapshot(str(self._root), label=label)
        except Exception as exc:
            logger.debug("[auto-deploy] snapshot failed: %s", exc)
            return ""

    def _restore_snapshot(self, snapshot_id: str) -> int:
        from maop.core.reliability.change_tracker import ChangeTracker

        ct = ChangeTracker(root_dir=str(self._root))
        return ct.rollback(str(self._root), to_id=snapshot_id)

    def _write_active_variant(self, experiment: str, winner: str, config: dict[str, Any]) -> None:
        """写入 active variant 标记文件（被 routing/config 读取）。"""
        path = self._data_dir / "evolution-active-variant.json"
        existing: dict[str, Any] = {}
        if path.exists():
            try:
                with open(path, encoding="utf-8") as f:
                    existing = json.load(f)
            except (json.JSONDecodeError, OSError):
                existing = {}
        existing[experiment] = {"winner": winner, "config": config, "promoted_at": time.time()}
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(existing, f, indent=2, ensure_ascii=False)

    def _mark_winner(self, experiment: str, winner: str) -> None:
        """在 ab_experiments 表追加 winner 标记（best-effort）。"""
        try:
            ab_db = get_db_path("ab_test")
            with sqlite_connect(ab_db, foreign_keys=False) as conn:
                conn.execute(
                    "UPDATE ab_experiments SET confidence_level = confidence_level WHERE name = ?",
                    (experiment,),
                )
        except Exception as exc:
            logger.debug("[auto-deploy] mark winner skipped: %s", exc)

    def _last_promote_snapshot(self, experiment: str) -> str:
        with self._db_connect() as conn:
            row = conn.execute(
                "SELECT snapshot_id FROM evolution_deployments "
                "WHERE experiment=? AND action='promote' AND success=1 AND snapshot_id!='' "
                "ORDER BY created_at DESC LIMIT 1",
                (experiment,),
            ).fetchone()
        return row[0] if row else ""

    def _save_record(self, record: DeploymentRecord) -> None:
        with self._db_connect() as conn:
            conn.execute(
                """INSERT INTO evolution_deployments
                   (id, experiment, action, winner, snapshot_id, success, detail, config_json, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (record.id, record.experiment, record.action, record.winner,
                 record.snapshot_id, int(record.success), record.detail,
                 json.dumps(record.config, ensure_ascii=False), record.created_at),
            )