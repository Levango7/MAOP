"""MAOP Log Rotation — prevent unbounded log growth.

Log file rotation and compression.: rotates .log/.jsonl/.json files when they
exceed a size threshold, with retention and optional gzip compression.

Includes a background scheduler for automatic periodic rotation.
"""

from __future__ import annotations

import gzip
import logging
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel

logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────

ROTATE_EXTENSIONS = {".log", ".jsonl", ".json"}

# Pattern to identify rotated files: basename_YYYYMMDD-HHMMSS.ext[.gz]
_ROTATED_RE = re.compile(r"^(.+)_\d{8}-\d{6}(\.(log|jsonl|json)(\.gz)?)$")


class RotateResult(BaseModel):
    """Result of a log rotation run."""
    rotated: list[str] = []
    deleted: list[str] = []
    errors: list[str] = []


class LogRotateConfig(BaseModel):
    """Configuration for log rotation."""
    max_size_kb: int = 512
    retain_count: int = 5
    compress: bool = False
    log_dir: str = ""
    data_dir: str = ""


# ── Core functions ────────────────────────────────────────────


from maop.core.backends.db_utils import find_project_root


def _compress_file(source: Path) -> bool:
    """Gzip-compress a file, replacing it with .gz."""
    dest = Path(str(source) + ".gz")
    try:
        with open(source, "rb") as f_in, gzip.open(dest, "wb") as f_out:
            f_out.writelines(f_in)
        source.unlink()
        return True
    except Exception as exc:
        logger.warning("Compress failed for %s: %s", source, exc)
        return False


def _rotate_file(
    file_path: Path,
    max_size_bytes: int,
    compress: bool,
    dry_run: bool = False,
) -> bool:
    """Rotate a single file if it exceeds the size threshold.

    Returns True if the file was rotated.
    """
    if not file_path.exists():
        return False

    size = file_path.stat().st_size
    if size < max_size_bytes:
        return False

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    rotated_name = f"{file_path.stem}_{timestamp}{file_path.suffix}"
    rotated_path = file_path.parent / rotated_name

    if dry_run:
        logger.info("DRY: Would rotate %s (%.1f KB) -> %s",
                     file_path.name, size / 1024, rotated_name)
        return True

    try:
        # Rename current file to timestamped backup
        file_path.rename(rotated_path)
        logger.info("Rotated %s -> %s", file_path.name, rotated_name)

        # Optionally compress
        if compress and _compress_file(rotated_path):
            logger.info("Compressed -> %s.gz", rotated_name)

        # Recreate empty file so append operations don't fail
        if file_path.suffix in (".jsonl", ".log"):
            file_path.touch()
        elif file_path.suffix == ".json":
            file_path.write_text("[]", encoding="utf-8")

        return True
    except Exception as exc:
        logger.warning("Failed to rotate %s: %s", file_path.name, exc)
        return False


def _cleanup_old_rotations(
    directory: Path,
    retain_count: int,
    dry_run: bool = False,
) -> list[str]:
    """Delete rotated files beyond retention count.

    Returns list of deleted file names.
    """
    if not directory.exists():
        return []

    # Group rotated files by their base name
    groups: dict[str, list[Path]] = {}
    for f in directory.iterdir():
        if not f.is_file():
            continue
        m = _ROTATED_RE.match(f.name)
        if m:
            key = m.group(1) + m.group(2)
            groups.setdefault(key, []).append(f)

    deleted: list[str] = []
    for key, files in groups.items():
        # Sort by modification time descending (newest first)
        files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        if len(files) <= retain_count:
            continue
        for f in files[retain_count:]:
            if dry_run:
                logger.info("DRY: Would delete old rotation %s", f.name)
            else:
                try:
                    f.unlink()
                    logger.info("Deleted old rotation: %s", f.name)
                except Exception as exc:
                    logger.warning("Failed to delete %s: %s", f.name, exc)
                    continue
            deleted.append(f.name)

    return deleted


# ── Main entry point ─────────────────────────────────────────


def rotate_logs(
    config: LogRotateConfig | None = None,
    *,
    max_size_kb: int = 512,
    retain_count: int = 5,
    compress: bool = False,
    dry_run: bool = False,
    log_dir: str | Path | None = None,
    data_dir: str | Path | None = None,
) -> RotateResult:
    """Scan log/data directories and rotate oversized files.

    Parameters
    ----------
    config : LogRotateConfig | None
        Optional config object (overrides individual params).
    max_size_kb : int
        Size threshold in KB to trigger rotation.
    retain_count : int
        Number of rotated backups to keep per file.
    compress : bool
        Gzip-compress rotated files.
    dry_run : bool
        Report what would happen without modifying files.
    log_dir : str | Path | None
        Log directory (default: <project>/logs).
    data_dir : str | Path | None
        Data directory (default: <project>/data).

    Returns
    -------
    RotateResult
    """
    if config is not None:
        max_size_kb = config.max_size_kb
        retain_count = config.retain_count
        compress = config.compress
        if config.log_dir:
            log_dir = config.log_dir
        if config.data_dir:
            data_dir = config.data_dir

    project_root = find_project_root()
    if log_dir is None:
        log_dir = project_root / "logs"
    if data_dir is None:
        data_dir = project_root / "data"

    max_size_bytes = max_size_kb * 1024
    result = RotateResult()

    # Scan directories
    dirs_to_scan: list[Path] = []
    log_dir_path = Path(log_dir)
    data_dir_path = Path(data_dir)
    if log_dir_path.exists():
        dirs_to_scan.append(log_dir_path)
    if data_dir_path.exists():
        dirs_to_scan.append(data_dir_path)

    for d in dirs_to_scan:
        # Rotate oversized files
        for f in d.iterdir():
            if not f.is_file():
                continue
            if f.suffix.lower() not in ROTATE_EXTENSIONS:
                continue
            if _rotate_file(f, max_size_bytes, compress, dry_run):
                result.rotated.append(f.name)

        # Cleanup old rotations
        deleted = _cleanup_old_rotations(d, retain_count, dry_run)
        result.deleted.extend(deleted)

    return result


# ── Scheduler ────────────────────────────────────────────────────


class LogRotateScheduler:
    """Background scheduler for periodic log rotation.

    Runs ``rotate_logs`` on a fixed interval in a daemon thread,
    so it does not block the main event loop or prevent process exit.

    Usage::

        sched = LogRotateScheduler(interval_s=600)
        sched.start()   # begin periodic rotation
        sched.stop()    # stop on shutdown
    """

    def __init__(
        self,
        interval_s: float = 600,
        *,
        max_size_kb: int = 512,
        retain_count: int = 5,
        compress: bool = False,
        log_dir: str | Path | None = None,
        data_dir: str | Path | None = None,
    ) -> None:
        self._interval = max(60, interval_s)  # floor at 1 min
        self._kwargs = {
            "max_size_kb": max_size_kb,
            "retain_count": retain_count,
            "compress": compress,
            "log_dir": log_dir,
            "data_dir": data_dir,
        }
        self._thread: threading.Thread | None = None
        self._running = False

    def start(self) -> None:
        """Start the background rotation thread."""
        if self._running:
            return
        self._running = True

        def _loop():
            while self._running:
                try:
                    rotate_logs(**self._kwargs)  # type: ignore[arg-type]
                except Exception as exc:
                    logger.warning("[log-rotate] Scheduler error: %s", exc)
                # P0-§4.2: 保留 time.sleep — _loop 运行在独立 daemon 线程中，
                # 无事件循环，asyncio.sleep 在此无效。
                time.sleep(self._interval)

        self._thread = threading.Thread(target=_loop, daemon=True)
        self._thread.start()
        logger.info("[log-rotate] Scheduler started (interval=%ds)", self._interval)

    def stop(self) -> None:
        """Stop the background rotation thread."""
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
        logger.info("[log-rotate] Scheduler stopped")
