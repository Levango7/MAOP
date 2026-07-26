"""Tests for MAOP.core.change_tracker — Snapshot, diff, and real rollback."""

from __future__ import annotations

from pathlib import Path

import pytest

from maop.core.change_tracker import ChangeTracker


@pytest.fixture
def tracker(tmp_path: Path) -> ChangeTracker:
    return ChangeTracker(root_dir=tmp_path)


@pytest.fixture
def workdir(tmp_path: Path) -> Path:
    """A working directory with sample files for snapshot/rollback tests."""
    wd = tmp_path / "project"
    wd.mkdir()
    (wd / "main.py").write_text("print('hello')\n", encoding="utf-8")
    (wd / "config.yaml").write_text("name: test\n", encoding="utf-8")
    (wd / "subdir").mkdir()
    (wd / "subdir" / "utils.py").write_text("def helper(): pass\n", encoding="utf-8")
    return wd


# ── Snapshot ────────────────────────────────────────────────────

class TestSnapshot:
    def test_snapshot_returns_id(self, tracker: ChangeTracker, workdir: Path):
        sid = tracker.snapshot(str(workdir), label="v1")
        assert sid.startswith("snap-")

    def test_snapshot_records_files(self, tracker: ChangeTracker, workdir: Path):
        sid = tracker.snapshot(str(workdir), label="v1")
        info = tracker.get_snapshot(sid)
        assert info is not None
        assert info.file_count == 3  # main.py, config.yaml, subdir/utils.py
        assert info.label == "v1"

    def test_snapshot_creates_backup(self, tracker: ChangeTracker, workdir: Path, tmp_path: Path):
        sid = tracker.snapshot(str(workdir), label="v1")
        backup_dir = tmp_path / ".maop-snapshots" / sid
        assert backup_dir.exists()
        assert (backup_dir / "main.py").exists()
        assert (backup_dir / "config.yaml").exists()
        assert (backup_dir / "subdir" / "utils.py").exists()


# ── Rollback ────────────────────────────────────────────────────

class TestRollback:
    def test_rollback_restores_modified_file(self, tracker: ChangeTracker, workdir: Path):
        tracker.snapshot(str(workdir), label="v1")
        # Modify a file
        (workdir / "main.py").write_text("print('modified')\n", encoding="utf-8")
        assert "modified" in (workdir / "main.py").read_text(encoding="utf-8")
        # Rollback
        restored = tracker.rollback(str(workdir), to_label="v1")
        assert restored >= 1
        # Verify content is restored
        assert (workdir / "main.py").read_text(encoding="utf-8") == "print('hello')\n"

    def test_rollback_restores_deleted_file(self, tracker: ChangeTracker, workdir: Path):
        tracker.snapshot(str(workdir), label="v1")
        # Delete a file
        (workdir / "config.yaml").unlink()
        assert not (workdir / "config.yaml").exists()
        # Rollback
        restored = tracker.rollback(str(workdir), to_label="v1")
        assert restored >= 1
        # Verify file is restored
        assert (workdir / "config.yaml").exists()
        assert (workdir / "config.yaml").read_text(encoding="utf-8") == "name: test\n"

    def test_rollback_deletes_added_file(self, tracker: ChangeTracker, workdir: Path):
        tracker.snapshot(str(workdir), label="v1")
        # Add a new file
        (workdir / "new_file.py").write_text("# new\n", encoding="utf-8")
        assert (workdir / "new_file.py").exists()
        # Rollback
        restored = tracker.rollback(str(workdir), to_label="v1")
        assert restored >= 1
        # Verify added file is deleted
        assert not (workdir / "new_file.py").exists()

    def test_rollback_noop_when_unchanged(self, tracker: ChangeTracker, workdir: Path):
        tracker.snapshot(str(workdir), label="v1")
        # No changes
        restored = tracker.rollback(str(workdir), to_label="v1")
        assert restored == 0

    def test_rollback_by_id(self, tracker: ChangeTracker, workdir: Path):
        sid = tracker.snapshot(str(workdir), label="v1")
        (workdir / "main.py").write_text("modified\n", encoding="utf-8")
        restored = tracker.rollback(str(workdir), to_id=sid)
        assert restored >= 1
        assert (workdir / "main.py").read_text(encoding="utf-8") == "print('hello')\n"

    def test_rollback_no_snapshot_returns_zero(self, tracker: ChangeTracker, workdir: Path):
        restored = tracker.rollback(str(workdir), to_label="nonexistent")
        assert restored == 0


# ── Diff ────────────────────────────────────────────────────────

class TestDiff:
    def test_diff_detects_modification(self, tracker: ChangeTracker, workdir: Path):
        tracker.snapshot(str(workdir), label="v1")
        (workdir / "main.py").write_text("modified\n", encoding="utf-8")
        diff = tracker.diff(str(workdir), since_label="v1")
        assert diff.modified >= 1
        assert any(c.path == "main.py" and c.change_type == "modified" for c in diff.changes)

    def test_diff_detects_addition(self, tracker: ChangeTracker, workdir: Path):
        tracker.snapshot(str(workdir), label="v1")
        (workdir / "new.py").write_text("# new\n", encoding="utf-8")
        diff = tracker.diff(str(workdir), since_label="v1")
        assert diff.added >= 1

    def test_diff_detects_deletion(self, tracker: ChangeTracker, workdir: Path):
        tracker.snapshot(str(workdir), label="v1")
        (workdir / "config.yaml").unlink()
        diff = tracker.diff(str(workdir), since_label="v1")
        assert diff.deleted >= 1
