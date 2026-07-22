"""Tests for MAOP.core.log_rotate — log file rotation."""

from __future__ import annotations

from pathlib import Path

import pytest

from maop.core.log_rotate import LogRotateConfig, rotate_logs


@pytest.fixture
def log_dir(tmp_path: Path) -> Path:
    """Create a log directory with a large .log file."""
    logs = tmp_path / "logs"
    logs.mkdir()
    # Create a 1KB log file (exceeds default 512KB? no — make it bigger)
    big_content = "x" * (600 * 1024)  # 600 KB
    (logs / "app.log").write_text(big_content, encoding="utf-8")
    (logs / "small.log").write_text("tiny", encoding="utf-8")
    return tmp_path  # return project root


class TestLogRotate:
    def test_rotate_oversized_file(self, log_dir: Path):
        result = rotate_logs(
            max_size_kb=512,
            retain_count=5,
            log_dir=log_dir / "logs",
            data_dir=log_dir / "data",  # doesn't exist, OK
        )
        assert len(result.rotated) == 1
        assert "app.log" in result.rotated
        # Original file should be recreated (empty)
        assert (log_dir / "logs" / "app.log").exists()

    def test_small_file_not_rotated(self, log_dir: Path):
        result = rotate_logs(
            max_size_kb=512,
            log_dir=log_dir / "logs",
            data_dir=log_dir / "data",
        )
        # small.log should NOT be rotated
        assert "small.log" not in result.rotated

    def test_dry_run(self, log_dir: Path):
        original_size = (log_dir / "logs" / "app.log").stat().st_size
        rotate_logs(
            max_size_kb=512,
            dry_run=True,
            log_dir=log_dir / "logs",
            data_dir=log_dir / "data",
        )
        # File should NOT be modified
        assert (log_dir / "logs" / "app.log").stat().st_size == original_size

    def test_json_recreated_as_empty_array(self, tmp_path: Path):
        logs = tmp_path / "logs"
        logs.mkdir()
        big = "x" * (600 * 1024)
        (logs / "delegations.json").write_text(big, encoding="utf-8")

        result = rotate_logs(
            max_size_kb=512,
            log_dir=logs,
            data_dir=tmp_path / "data",
        )
        assert len(result.rotated) >= 1
        # delegations.json should be recreated as "[]"
        content = (logs / "delegations.json").read_text(encoding="utf-8")
        assert content == "[]"

    def test_retention_cleanup(self, tmp_path: Path):
        """Old rotated files beyond retain_count should be deleted."""
        logs = tmp_path / "logs"
        logs.mkdir()
        # Create the main log file
        (logs / "app.log").write_text("x" * (600 * 1024), encoding="utf-8")
        # Create 6 old rotated files
        for i in range(6):
            name = f"app_20260101-00000{i}.log"
            (logs / name).write_text("old", encoding="utf-8")

        result = rotate_logs(
            max_size_kb=512,
            retain_count=3,
            log_dir=logs,
            data_dir=tmp_path / "data",
        )
        # Should delete old rotations beyond retain_count.
        # The current rotation also creates a new rotated file, so total deleted
        # = 6 pre-existing + 1 new rotation - retain_count(3) = at least 3
        assert len(result.deleted) >= 3

    def test_config_object(self, tmp_path: Path):
        logs = tmp_path / "logs"
        logs.mkdir()
        (logs / "app.log").write_text("x" * (600 * 1024), encoding="utf-8")

        config = LogRotateConfig(max_size_kb=512, retain_count=5)
        result = rotate_logs(config=config, log_dir=logs, data_dir=tmp_path / "data")
        assert len(result.rotated) >= 1
