"""MAOP Change Tracker — File change detection and review gates.

Provides:
  - Snapshot: capture file state (hash + metadata) at a point in time
  - Diff: compare two snapshots to detect changes
  - Review gate: block execution if unauthorized changes are detected
  - Change log: persistent record of all file modifications
  - Rollback support: restore files from a previous snapshot

Usage::

    from maop.core.reliability.change_tracker import ChangeTracker

    tracker = ChangeTracker(root_dir="/path/to/MAOP")
    tracker.snapshot("/project", label="before-fix")
    # ... agent makes changes ...
    changes = tracker.diff("/project", since_label="before-fix")
    if changes.has_unauthorized:
        tracker.rollback("/project", to_label="before-fix")
"""

from __future__ import annotations

import hashlib
import logging
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, ClassVar, cast

from pydantic import BaseModel, Field

from maop.core.backends.db_utils import get_db_path, sqlite_connect

logger = logging.getLogger(__name__)


class FileChange(BaseModel):
    path: str
    change_type: str = ""  # added | modified | deleted
    old_hash: str = ""
    new_hash: str = ""
    old_size: int = 0
    new_size: int = 0


class SnapshotInfo(BaseModel):
    id: str = ""
    workdir: str = ""
    label: str = ""
    file_count: int = 0
    total_size: int = 0
    created_at: str = ""


class DiffResult(BaseModel):
    from_snapshot: str = ""
    to_snapshot: str = ""
    changes: list[FileChange] = Field(default_factory=list)
    added: int = 0
    modified: int = 0
    deleted: int = 0
    has_unauthorized: bool = False
    unauthorized_paths: list[str] = Field(default_factory=list)


class ChangeTracker:
    """Track file changes with snapshot/diff/rollback support.

    Usage::

        tracker = ChangeTracker(root_dir="/path/to/MAOP")
        tracker.snapshot("/project", label="before")
        changes = tracker.diff("/project", since_label="before")
    """

    SKIP_DIRS: ClassVar[set[str]] = {
        ".git", "__pycache__", "node_modules", ".venv", "venv",
        ".mypy_cache", ".pytest_cache", ".ruff_cache",
        "dist", "dist-enterprise", "build", ".tox",
        ".eggs", "*.egg-info",
        ".maop-worktrees", ".maop-snapshots", "data",
    }

    SKIP_EXTENSIONS: ClassVar[set[str]] = {
        ".pyc", ".pyo", ".so", ".dll", ".dylib", ".exe", ".obj", ".o",
        ".log", ".tmp", ".cache", ".db", ".sqlite",
    }

    _MAX_FILES = 10000  # Safety limit to prevent runaway scans
    # Files larger than this are hashed by a cheap size signature instead of
    # reading the full content into memory. Prevents _hash_file from hanging
    # on large binaries (models, archives, db snapshots) under Windows CI.
    _MAX_HASH_SIZE = 16 * 1024 * 1024  # 16 MiB
    _HASH_CHUNK = 65536  # 64 KiB streaming read chunk

    def __init__(self, root_dir: str | Path) -> None:
        self._root = Path(root_dir)
        self._db_path = get_db_path("change_tracker")
        # Backup directory for real file restoration (rollback)
        self._backup_root = self._root / ".maop-snapshots"
        self._backup_root.mkdir(parents=True, exist_ok=True)
        self._ensure_db()

    def _ensure_db(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS snapshots (
                    id TEXT PRIMARY KEY,
                    workdir TEXT NOT NULL,
                    label TEXT NOT NULL DEFAULT '',
                    file_count INTEGER NOT NULL DEFAULT 0,
                    total_size INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS file_states (
                    snapshot_id TEXT NOT NULL,
                    path TEXT NOT NULL,
                    hash TEXT NOT NULL DEFAULT '',
                    size INTEGER NOT NULL DEFAULT 0,
                    modified TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY (snapshot_id, path)
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_fs_snapshot
                ON file_states(snapshot_id)
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS change_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    workdir TEXT NOT NULL,
                    path TEXT NOT NULL,
                    change_type TEXT NOT NULL,
                    old_hash TEXT DEFAULT '',
                    new_hash TEXT DEFAULT '',
                    snapshot_from TEXT DEFAULT '',
                    snapshot_to TEXT DEFAULT '',
                    created_at TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_cl_workdir
                ON change_log(workdir, created_at)
            """)

    def _connect(self):
        return sqlite_connect(self._db_path, foreign_keys=False)

    def _should_skip(self, path: Path) -> bool:
        # Skip if any parent directory is in SKIP_DIRS
        for part in path.parts:
            if part in self.SKIP_DIRS:
                return True
        # Skip specific suffixes
        if path.suffix in self.SKIP_EXTENSIONS:
            return True
        # Skip symlinks (avoid cycles)
        if path.is_symlink():
            return True
        # Skip if not a regular file (directories are handled by rglob caller)
        return bool(path.exists() and not path.is_file())

    def _hash_file(self, path: Path) -> str:
        """Compute a short file signature.

        - Files <= ``_MAX_HASH_SIZE`` are streamed in chunks through sha256
          (avoids loading large files fully into memory, which was the root
          cause of Windows CI timeouts when ``rglob`` hit big binaries).
        - Larger files get a cheap ``large:<size>`` signature so they are
          still tracked for size changes without a full read.
        """
        try:
            size = path.stat().st_size
            if size > self._MAX_HASH_SIZE:
                return f"large:{size:x}"
            h = hashlib.sha256()
            with path.open("rb") as fh:
                for chunk in iter(lambda: fh.read(self._HASH_CHUNK), b""):
                    h.update(chunk)
            return h.hexdigest()[:16]
        except Exception:
            logger.debug("Silent exception in core/change_tracker.py:167", exc_info=True)
            return ""

    def snapshot(self, workdir: str, label: str = "") -> str:
        workdir_path = Path(workdir)
        snap_id = f"snap-{uuid.uuid4().hex[:10]}"
        now = datetime.now(timezone.utc).isoformat()
        file_count = 0
        total_size = 0

        with self._connect() as conn:
            conn.execute(
                "INSERT INTO snapshots (id, workdir, label, file_count, total_size, created_at) VALUES (?,?,?,?,?,?)",
                (snap_id, str(workdir_path), label, 0, 0, now),
            )
            if workdir and workdir_path.is_dir():
                for fpath in workdir_path.rglob("*"):
                    if file_count >= self._MAX_FILES:
                        logger.warning(
                            "[change_tracker] Snapshot truncated at %d files (workdir=%s)",
                            file_count,
                            workdir,
                        )
                        break
                    if not fpath.is_file():
                        continue
                    if self._should_skip(fpath):
                        continue
                    try:
                        rel = str(fpath.relative_to(workdir_path))
                        fhash = self._hash_file(fpath)
                        fsize = fpath.stat().st_size
                        mtime = datetime.fromtimestamp(fpath.stat().st_mtime, tz=timezone.utc).isoformat()
                        conn.execute(
                            "INSERT INTO file_states (snapshot_id, path, hash, size, modified) VALUES (?,?,?,?,?)",
                            (snap_id, rel, fhash, fsize, mtime),
                        )
                        # Copy file to backup directory for real rollback
                        backup_path = self._backup_root / snap_id / rel
                        backup_path.parent.mkdir(parents=True, exist_ok=True)
                        if not backup_path.exists():
                            shutil.copy2(fpath, backup_path)
                        file_count += 1
                        total_size += fsize
                    except Exception as exc:
                        logger.debug("[change_tracker] Skip file during snapshot: %s", exc)
                        continue
            conn.execute(
                "UPDATE snapshots SET file_count=?, total_size=? WHERE id=?",
                (file_count, total_size, snap_id),
            )

        logger.info("[change_tracker] Snapshot %s: %d files, label=%s", snap_id, file_count, label)
        return snap_id

    def get_snapshot(self, snapshot_id: str) -> SnapshotInfo | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM snapshots WHERE id=?", (snapshot_id,)).fetchone()
        if row is None:
            return None
        return SnapshotInfo(**dict(row))

    def list_snapshots(self, workdir: str = "", limit: int = 20) -> list[SnapshotInfo]:
        with self._connect() as conn:
            if workdir:
                rows = conn.execute(
                    "SELECT * FROM snapshots WHERE workdir=? ORDER BY created_at DESC LIMIT ?",
                    (str(Path(workdir)), limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM snapshots ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        return [SnapshotInfo(**dict(r)) for r in rows]

    def diff(
        self,
        workdir: str,
        *,
        since_label: str = "",
        since_id: str = "",
        authorized_paths: list[str] | None = None,
    ) -> DiffResult:
        workdir_path = Path(workdir)
        from_id = since_id
        if not from_id and since_label:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT id FROM snapshots WHERE workdir=? AND label=? ORDER BY created_at DESC LIMIT 1",
                    (str(workdir_path), since_label),
                ).fetchone()
            if row:
                from_id = row["id"]
        if not from_id:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT id FROM snapshots WHERE workdir=? ORDER BY created_at DESC LIMIT 1 OFFSET 1",
                    (str(workdir_path),),
                ).fetchone()
            if row:
                from_id = row["id"]

        to_id = self.snapshot(workdir, label="diff-auto")

        from_files: dict[str, dict] = {}
        to_files: dict[str, dict] = {}
        with self._connect() as conn:
            if from_id:
                for row in conn.execute("SELECT path, hash, size FROM file_states WHERE snapshot_id=?", (from_id,)).fetchall():
                    from_files[row["path"]] = {"hash": row["hash"], "size": row["size"]}
            for row in conn.execute("SELECT path, hash, size FROM file_states WHERE snapshot_id=?", (to_id,)).fetchall():
                to_files[row["path"]] = {"hash": row["hash"], "size": row["size"]}

        changes: list[FileChange] = []
        all_paths = set(from_files.keys()) | set(to_files.keys())
        for path in sorted(all_paths):
            f = from_files.get(path)
            t = to_files.get(path)
            if f is None and t is not None:
                changes.append(FileChange(path=path, change_type="added", new_hash=t["hash"], new_size=t["size"]))
            elif f is not None and t is None:
                changes.append(FileChange(path=path, change_type="deleted", old_hash=f["hash"], old_size=f["size"]))
            elif f is not None and t is not None and f["hash"] != t["hash"]:
                changes.append(FileChange(
                    path=path, change_type="modified",
                    old_hash=f["hash"], new_hash=t["hash"],
                    old_size=f["size"], new_size=t["size"],
                ))

        added = sum(1 for c in changes if c.change_type == "added")
        modified = sum(1 for c in changes if c.change_type == "modified")
        deleted = sum(1 for c in changes if c.change_type == "deleted")

        unauthorized: list[str] = []
        if authorized_paths is not None:
            auth_set = set(authorized_paths)
            for c in changes:
                if c.path not in auth_set:
                    unauthorized.append(c.path)

        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            for c in changes:
                conn.execute(
                    "INSERT INTO change_log (workdir, path, change_type, old_hash, new_hash, snapshot_from, snapshot_to, created_at) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (str(workdir_path), c.path, c.change_type, c.old_hash, c.new_hash, from_id or "", to_id, now),
                )

        return DiffResult(
            from_snapshot=from_id or "",
            to_snapshot=to_id,
            changes=changes,
            added=added,
            modified=modified,
            deleted=deleted,
            has_unauthorized=len(unauthorized) > 0,
            unauthorized_paths=unauthorized,
        )

    def rollback(self, workdir: str, to_label: str = "", to_id: str = "") -> int:
        """Restore files to the state captured in a snapshot.

        Performs real file restoration:
        - Modified/deleted files: overwritten with backup content
        - Added files (not in snapshot): deleted from workdir

        Returns the number of files affected (restored + deleted).
        """
        workdir_path = Path(workdir)
        snap_id = to_id
        if not snap_id and to_label:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT id FROM snapshots WHERE workdir=? AND label=? ORDER BY created_at DESC LIMIT 1",
                    (str(workdir_path), to_label),
                ).fetchone()
            if row:
                snap_id = row["id"]
        if not snap_id:
            logger.warning("[change_tracker] No snapshot found for rollback")
            return 0

        with self._connect() as conn:
            rows = conn.execute(
                "SELECT path, hash FROM file_states WHERE snapshot_id=?", (snap_id,)
            ).fetchall()

        snap_files = {r["path"]: r["hash"] for r in rows}
        backup_dir = self._backup_root / snap_id
        restored = 0

        # 1. Restore modified/deleted files from backup
        for rel_path, expected_hash in snap_files.items():
            fpath = workdir_path / rel_path
            need_restore = True
            if fpath.exists():
                current_hash = self._hash_file(fpath)
                if current_hash == expected_hash:
                    need_restore = False
            if not need_restore:
                continue
            backup_path = backup_dir / rel_path
            if backup_path.exists():
                try:
                    fpath.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(backup_path, fpath)
                    restored += 1
                    logger.info("[change_tracker] Restored: %s", rel_path)
                except Exception as exc:
                    logger.warning("[change_tracker] Failed to restore %s: %s", rel_path, exc)
            else:
                logger.warning("[change_tracker] Backup missing for: %s", rel_path)

        # 2. Delete files added after snapshot (not in snap_files)
        if workdir and workdir_path.is_dir():
            for fpath in workdir_path.rglob("*"):
                if not fpath.is_file():
                    continue
                if self._should_skip(fpath):
                    continue
                try:
                    rel = str(fpath.relative_to(workdir_path))
                except ValueError:
                    continue
                if rel not in snap_files:
                    try:
                        fpath.unlink()
                        restored += 1
                        logger.info(
                            "[change_tracker] Deleted (added after snapshot): %s", rel
                        )
                    except Exception as exc:
                        logger.warning(
                            "[change_tracker] Failed to delete %s: %s", rel, exc
                        )

        logger.info(
            "[change_tracker] Rollback to %s complete: %d files affected",
            snap_id,
            restored,
        )
        return restored

    def get_change_log(self, workdir: str, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM change_log WHERE workdir=? ORDER BY created_at DESC LIMIT ?",
                (str(Path(workdir)), limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def delete_snapshot(self, snapshot_id: str) -> bool:
        with self._connect() as conn:
            conn.execute("DELETE FROM file_states WHERE snapshot_id=?", (snapshot_id,))
            cursor = conn.execute("DELETE FROM snapshots WHERE id=?", (snapshot_id,))
            deleted = cast(bool, cursor.rowcount > 0)
        if deleted:
            backup_dir = self._backup_root / snapshot_id
            if backup_dir.exists():
                shutil.rmtree(backup_dir, ignore_errors=True)
                logger.info("[change_tracker] Cleaned up backup for %s", snapshot_id)
        return deleted
