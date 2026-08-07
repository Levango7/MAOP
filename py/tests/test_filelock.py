"""Tests for MAOP.core.filelock."""

from pathlib import Path

import pytest

from maop.core.reliability.filelock import FileLock, with_file_lock


@pytest.fixture
def target_file(tmp_path: Path) -> Path:
    p = tmp_path / "shared.dat"
    p.write_text("{}", encoding="utf-8")
    return p


class TestWithFileLock:
    def test_basic_lock_and_execute(self, target_file: Path):
        result = with_file_lock(target_file, lambda: 42)
        assert result == 42
        # Lock file should be cleaned up
        lock_path = Path(str(target_file) + ".lock")
        assert not lock_path.exists()

    def test_lock_file_created_and_removed(self, target_file: Path):
        lock_path = Path(str(target_file) + ".lock")
        with_file_lock(target_file, lambda: None)
        assert not lock_path.exists()

    def test_timeout_on_contended_lock(self, target_file: Path):
        lock_path = Path(str(target_file) + ".lock")
        # Pre-create a lock file to simulate contention
        lock_path.write_text('{"pid": 99999}', encoding="utf-8")
        # Should timeout since lock is held (and not orphaned yet)
        with pytest.raises(TimeoutError):
            with_file_lock(target_file, lambda: None, timeout_seconds=1)


class TestFileLockContextManager:
    def test_context_manager(self, target_file: Path):
        with FileLock(target_file):
            pass  # should acquire and release without error

    def test_lock_cleanup_on_exception(self, target_file: Path):
        lock_path = Path(str(target_file) + ".lock")
        try:
            with FileLock(target_file):
                raise ValueError("boom")
        except ValueError:
            pass
        assert not lock_path.exists()
