"""MAOP Database Backup — Scheduled SQLite backup with retention policy.

Provides litestream-style continuous backup for MAOP's SQLite databases:
  - data/maop.db (delegations, metrics, checkpoints, circuit_breaker)
  - data/memory.db (memory entries, traces, trajectory)
  - data/queue.db (message queue, dead letters)

Features:
  - Incremental backup via SQLite VACUUM INTO (fast, no lock contention)
  - Timestamped backup files: maop.db.2026-07-13_030000.bak
  - Retention policy: keep N most recent backups per database
  - Scheduled execution: run on interval or on-demand
  - Backup manifest: JSON file tracking all backups

Usage::

    backup = DbBackup(root_dir="/path/to/MAOP")
    backup.run()  # backup all databases
    backup.cleanup()  # apply retention policy

    # Or schedule periodically
    backup.start_scheduler(interval_s=3600)  # every hour
    backup.stop_scheduler()
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field

from maop.core.backends.db_utils import sqlite_connect

logger = logging.getLogger(__name__)


# ── Models ──────────────────────────────────────────────────────

class BackupEntry(BaseModel):
    """A single backup record."""
    db_name: str
    backup_path: str
    size_bytes: int = 0
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    duration_ms: int = 0


class BackupStats(BaseModel):
    """Backup statistics."""
    total_backups: int = 0
    total_size_bytes: int = 0
    databases: list[str] = Field(default_factory=list)
    last_backup_at: str = ""
    retention_count: int = 0


# ── Constants ───────────────────────────────────────────────────

# Default databases to backup (relative to data/)
DEFAULT_DATABASES = ["maop.db", "memory.db", "queue.db", "human_queue.db"]

# Default retention: keep 10 most recent backups per database
DEFAULT_RETENTION = 10


# ── DbBackup ───────────────────────────────────────────────────

class DbBackup:
    """Scheduled SQLite backup with retention policy.

    Parameters
    ----------
    root_dir : str | Path
        MAOP project root directory.
    retention : int
        Number of recent backups to keep per database.
    databases : list[str] | None
        Database filenames to backup (relative to data/).
        Defaults to DEFAULT_DATABASES.
    """

    def __init__(
        self,
        root_dir: str | Path,
        retention: int = DEFAULT_RETENTION,
        databases: list[str] | None = None,
    ) -> None:
        self._root = Path(root_dir)
        self._data_dir = self._root / "data"
        self._backup_dir = self._data_dir / "backups"
        self._retention = max(1, retention)
        self._databases = databases or DEFAULT_DATABASES
        self._manifest_path = self._backup_dir / "backup_manifest.json"
        self._manifest: list[BackupEntry] = []
        self._scheduler_thread: threading.Thread | None = None
        self._scheduler_running = False

        # Load existing manifest
        self._load_manifest()

    # ── Backup execution ─────────────────────────────────────

    def run(
        self,
        db_names: list[str] | None = None,
        wal_checkpoint: bool = True,
    ) -> list[BackupEntry]:
        """Run backup for specified databases (or all).

        Uses SQLite VACUUM INTO for fast, consistent backup
        without holding a write lock on the source database.

        Parameters
        ----------
        wal_checkpoint : bool
            If True (default), run ``PRAGMA wal_checkpoint(TRUNCATE)`` before
            VACUUM INTO so WAL-mode databases flush pending frames into the
            main database file, yielding a more consistent snapshot. The
            checkpoint waits for active readers and may briefly block. On
            failure a warning is logged and the backup proceeds.

        Returns list of BackupEntry for each successful backup.
        """
        targets = db_names or self._databases
        results: list[BackupEntry] = []

        self._backup_dir.mkdir(parents=True, exist_ok=True)

        for db_name in targets:
            db_path = self._data_dir / db_name
            if not db_path.exists():
                logger.debug("[backup] Skip %s: not found", db_name)
                continue

            entry = self._backup_one(db_name, db_path, wal_checkpoint=wal_checkpoint)
            if entry is not None:
                results.append(entry)
                self._manifest.append(entry)
                self._save_manifest()

        if results:
            logger.info("[backup] Backed up %d databases", len(results))

        return results

    def _backup_one(
        self,
        db_name: str,
        db_path: Path,
        wal_checkpoint: bool = True,
    ) -> BackupEntry | None:
        """Backup a single database using VACUUM INTO.

        When ``wal_checkpoint`` is True, ``PRAGMA wal_checkpoint(TRUNCATE)``
        is executed first so WAL frames are flushed into the main database
        file and the WAL is truncated. This gives VACUUM INTO a more
        consistent snapshot. The checkpoint may briefly block until active
        readers finish; failures are logged as warnings and do not abort
        the backup.
        """
        if not re.match(r'^[a-zA-Z0-9_\-]+\.db$', db_name):
            logger.warning("[backup] Invalid db_name rejected: %s", db_name)
            return None
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S%f")
        backup_name = f"{db_name}.{timestamp}.bak"
        backup_path = self._backup_dir / backup_name
        # Guard against timestamp collisions when multiple backups are taken
        # within the same microsecond (e.g. rapid successive runs).
        suffix = 0
        while backup_path.exists():
            suffix += 1
            backup_name = f"{db_name}.{timestamp}_{suffix}.bak"
            backup_path = self._backup_dir / backup_name
        if "'" in str(backup_path):
            logger.warning("[backup] Rejected backup_path with quote: %s", backup_path)
            return None

        start = time.monotonic()
        try:
            try:
                with sqlite_connect(db_path, timeout=10, wal=True, foreign_keys=False) as conn:
                    # Flush WAL frames into the main database so VACUUM INTO
                    # captures a consistent snapshot. TRUNCATE also resets
                    # the WAL file to zero size after checkpointing.
                    if wal_checkpoint:
                        try:
                            cur = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                            row = cur.fetchone() if cur is not None else None
                            logger.debug(
                                "[backup] %s wal_checkpoint(TRUNCATE) -> %s",
                                db_name, row,
                            )
                        except Exception as cp_exc:
                            logger.warning(
                                "[backup] %s wal_checkpoint failed (continuing): %s",
                                db_name, cp_exc,
                            )
                    conn.execute(f"VACUUM INTO '{backup_path}'")
            except Exception:
                shutil.copy2(str(db_path), str(backup_path))

            duration_ms = int((time.monotonic() - start) * 1000)
            size = backup_path.stat().st_size

            entry = BackupEntry(
                db_name=db_name,
                backup_path=str(backup_path),
                size_bytes=size,
                duration_ms=duration_ms,
            )
            logger.info(
                "[backup] %s -> %s (%d bytes, %d ms)",
                db_name, backup_name, size, duration_ms,
            )
            return entry

        except Exception as exc:
            logger.warning("[backup] Failed to backup %s: %s", db_name, exc)
            return None

    # ── Retention cleanup ────────────────────────────────────

    def cleanup(self, retention: int | None = None) -> int:
        """Apply retention policy: remove old backups.

        P2 fix: previously only cleaned manifest entries, leaving orphan
        .bak files on disk when manifest was out of sync. Now scans disk
        files directly to ensure full cleanup.

        Returns number of backups removed.
        """
        retain = retention or self._retention
        removed = 0

        # P2 fix: scan actual .bak files on disk grouped by db_name
        # This catches orphans not tracked in manifest
        import re as _re
        disk_by_db: dict[str, list[Path]] = {}
        if self._backup_dir.exists():
            for f in self._backup_dir.glob("*.bak"):
                # Parse db_name from filename: maop.db.2026-07-23_125324.bak
                m = _re.match(r'^(.+\.db)\.\d{4}-\d{2}-\d{2}_\d+(_\d+)?\.bak$', f.name)
                if m:
                    disk_by_db.setdefault(m.group(1), []).append(f)

        # Also group manifest entries by db_name
        by_db: dict[str, list[BackupEntry]] = {}
        for entry in self._manifest:
            by_db.setdefault(entry.db_name, []).append(entry)

        # Process all databases found on disk (superset of manifest)
        all_db_names = set(disk_by_db.keys()) | set(by_db.keys())

        for db_name in all_db_names:
            # Sort disk files by modification time (newest first)
            disk_files = disk_by_db.get(db_name, [])
            disk_files.sort(key=lambda p: p.stat().st_mtime, reverse=True)

            # Remove excess files beyond retention limit
            if len(disk_files) > retain:
                for f in disk_files[retain:]:
                    try:
                        f.unlink(missing_ok=True)
                        removed += 1
                        logger.debug("[backup] Removed orphan/old backup: %s", f.name)
                    except Exception as exc:
                        logger.warning("[backup] Failed to remove %s: %s", f, exc)

            # Also clean manifest entries beyond retention
            entries = by_db.get(db_name, [])
            entries.sort(key=lambda e: e.created_at, reverse=True)
            for entry in entries[retain:]:
                if entry in self._manifest:
                    self._manifest.remove(entry)

        # Reconcile manifest: remove entries whose files no longer exist
        self._manifest = [
            e for e in self._manifest
            if Path(e.backup_path).exists()
        ]

        if removed > 0:
            self._save_manifest()

        logger.info("[backup] Cleanup: removed %d old backups (retention=%d)", removed, retain)
        return removed

    # ── Manifest ─────────────────────────────────────────────

    def _load_manifest(self) -> None:
        """Load backup manifest from disk."""
        if self._manifest_path.exists():
            try:
                data = json.loads(self._manifest_path.read_text(encoding="utf-8"))
                self._manifest = [BackupEntry(**e) for e in data]
            except Exception as exc:
                logger.warning("[backup] Failed to load manifest: %s", exc)
                self._manifest = []

    def _save_manifest(self) -> None:
        """Save backup manifest to disk."""
        try:
            self._backup_dir.mkdir(parents=True, exist_ok=True)
            data = [e.model_dump() for e in self._manifest]
            self._manifest_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.warning("[backup] Failed to save manifest: %s", exc)

    # ── Scheduler ────────────────────────────────────────────

    def start_scheduler(self, interval_s: float = 3600) -> None:
        """Start periodic backup in a background thread.

        Parameters
        ----------
        interval_s : float
            Seconds between backup runs.
        """
        if self._scheduler_running:
            return

        self._scheduler_running = True

        def _loop():
            while self._scheduler_running:
                try:
                    self.run()
                    self.cleanup()
                except Exception as exc:
                    logger.warning("[backup] Scheduler error: %s", exc)
                time.sleep(interval_s)

        self._scheduler_thread = threading.Thread(target=_loop, daemon=True)
        self._scheduler_thread.start()
        logger.info("[backup] Scheduler started (interval=%ds)", interval_s)

    def stop_scheduler(self) -> None:
        """Stop the periodic backup scheduler."""
        self._scheduler_running = False
        if self._scheduler_thread is not None:
            self._scheduler_thread.join(timeout=5)
            self._scheduler_thread = None
        logger.info("[backup] Scheduler stopped")

    # ── Query ─────────────────────────────────────────────────

    def stats(self) -> BackupStats:
        """Get backup statistics."""
        total_size = sum(e.size_bytes for e in self._manifest)
        db_names = sorted({e.db_name for e in self._manifest})
        last_at = max((e.created_at for e in self._manifest), default="")
        return BackupStats(
            total_backups=len(self._manifest),
            total_size_bytes=total_size,
            databases=db_names,
            last_backup_at=last_at,
            retention_count=self._retention,
        )

    def list_backups(self, db_name: str | None = None) -> list[BackupEntry]:
        """List backups, optionally filtered by database name."""
        if db_name is None:
            return list(self._manifest)
        return [e for e in self._manifest if e.db_name == db_name]

    def restore(self, db_name: str, backup_path: str | None = None) -> bool:
        """Restore a database from its most recent backup.

        Parameters
        ----------
        db_name : str
            Database to restore (e.g. "maop.db").
        backup_path : str | None
            Specific backup file. If None, use most recent.

        Returns True on success.
        """
        # Find backup entry
        if backup_path is None:
            entries = [e for e in self._manifest if e.db_name == db_name]
            if not entries:
                logger.warning("[backup] No backups found for %s", db_name)
                return False
            entries.sort(key=lambda e: e.created_at, reverse=True)
            backup_path = entries[0].backup_path

        # #6 fix: path traversal prevention
        source = Path(backup_path).resolve()
        try:
            source.relative_to(self._backup_dir.resolve())
        except ValueError:
            logger.warning("[backup] Path traversal blocked: %s", backup_path)
            return False
        target = self._data_dir / db_name

        if not source.exists():
            logger.warning("[backup] Backup file not found: %s", backup_path)
            return False

        try:
            # Backup current database before overwriting
            if target.exists():
                interim = target.with_suffix(".db.pre-restore")
                shutil.copy2(str(target), str(interim))
                logger.info("[backup] Current DB saved to %s", interim.name)

            # Copy backup to target
            shutil.copy2(str(source), str(target))
            logger.info("[backup] Restored %s from %s", db_name, source.name)
            return True
        except Exception as exc:
            logger.warning("[backup] Restore failed: %s", exc)
            return False

    def __repr__(self) -> str:
        return f"DbBackup(dbs={len(self._databases)}, retention={self._retention})"
