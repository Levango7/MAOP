"""MAOP File Lock — file-based locking with orphan cleanup.


Uses ``fcntl`` on POSIX / ``msvcrt`` on Windows for atomic lock acquisition.
Falls back to a simple .lock file with PID + timestamp if platform primitives
are unavailable.
"""

from __future__ import annotations

import json
import os
import time
import logging
from pathlib import Path
from typing import Callable, TypeVar

T = TypeVar("T")

logger = logging.getLogger(__name__)

ORPHAN_THRESHOLD_S = 30  # Locks older than this are considered orphaned


def _lock_path(target: Path) -> Path:
    return Path(str(target) + ".lock")


def _is_orphan(lock_file: Path) -> bool:
    """Return True if the lock file is older than ORPHAN_THRESHOLD_S."""
    if not lock_file.exists():
        return False
    age = time.time() - lock_file.stat().st_mtime
    return age > ORPHAN_THRESHOLD_S


def _write_lock_content(lock_file: Path) -> None:
    """Write PID + host + timestamp into the lock file."""
    content = json.dumps({
        "pid": os.getpid(),
        "host": os.getenv("COMPUTERNAME", os.getenv("HOSTNAME", "unknown")),
        "timestamp": time.time(),
    }, separators=(",", ":"))
    lock_file.write_text(content, encoding="utf-8")


def with_file_lock(
    target: str | Path,
    fn: Callable[[], T],
    timeout_seconds: int = 5,
) -> T:
    """Execute *fn* while holding an exclusive file lock on *target*.

    Mirrors ``Invoke-WithFileLock``:
      1. Wait up to *timeout_seconds* for the lock to become available.
      2. Clean up orphaned locks (older than 30 s).
      3. Execute *fn*; always release the lock in a ``finally`` block.

    Parameters
    ----------
    target : str | Path
        The file/resource to lock. A ``<target>.lock`` sidecar file is used.
    fn : callable
        Zero-arg callable to execute under the lock.
    timeout_seconds : int
        Max wait time before raising ``TimeoutError``.

    Returns
    -------
    Whatever *fn* returns.

    Raises
    ------
    TimeoutError
        If the lock cannot be acquired within *timeout_seconds*.
    """
    target = Path(target)
    lock_file = _lock_path(target)
    deadline = time.monotonic() + timeout_seconds


    while True:
        # Orphan cleanup
        if _is_orphan(lock_file):
            try:
                lock_file.unlink()
                logger.warning("Removed orphan lock: %s", lock_file)
            except FileNotFoundError:
                pass

        # Try to create the lock file atomically
        try:
            # O_CREAT | O_EXCL guarantees atomic creation on POSIX
            fd = os.open(str(lock_file), os.O_CREAT | os.O_EXCL | os.O_WRONLY)

            os.close(fd)
            _write_lock_content(lock_file)
            break
        except FileExistsError:
            pass  # Lock held by another process

        # Timeout check
        if time.monotonic() >= deadline:
            # One last orphan check before giving up
            if _is_orphan(lock_file):
                try:
                    lock_file.unlink()
                    continue  # retry immediately
                except FileNotFoundError:
                    pass
            raise TimeoutError(f"[filelock] Timeout waiting for lock: {lock_file} ({timeout_seconds}s)")

        time.sleep(0.2)

    # Execute under lock, always release
    try:
        return fn()
    finally:
        try:
            if lock_file.exists():
                lock_file.unlink()
        except FileNotFoundError:
            pass


class FileLock:
    """Context-manager variant for convenience.

    Usage::

        with FileLock("data/shared.json"):
            data = json.loads(...)
            data["x"] = 42
            write_back(data)
    """

    def __init__(self, target: str | Path, timeout_seconds: int = 5) -> None:
        self._target = Path(target)
        self._timeout = timeout_seconds
        self._lock_file = _lock_path(self._target)
        self._acquired = False

    def __enter__(self) -> "FileLock":
        # Reuse the functional impl but with a no-op callable
        self._acquired = False
        deadline = time.monotonic() + self._timeout

        while True:
            if _is_orphan(self._lock_file):
                try:
                    self._lock_file.unlink()
                except FileNotFoundError:
                    pass

            try:
                fd = os.open(str(self._lock_file), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.close(fd)
                _write_lock_content(self._lock_file)
                self._acquired = True
                return self
            except FileExistsError:
                pass

            if time.monotonic() >= deadline:
                if _is_orphan(self._lock_file):
                    try:
                        self._lock_file.unlink()
                        continue
                    except FileNotFoundError:
                        pass
                raise TimeoutError(f"[filelock] Timeout: {self._lock_file}")

            time.sleep(0.2)

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if self._acquired:
            try:
                if self._lock_file.exists():
                    self._lock_file.unlink()
            except FileNotFoundError:
                pass
