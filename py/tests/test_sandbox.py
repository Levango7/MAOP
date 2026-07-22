"""Unit tests for MAOP.core.sandbox module."""

from __future__ import annotations

import pytest

from maop.core.sandbox import SandboxManager


@pytest.fixture
def mgr(tmp_path):
    return SandboxManager(root_dir=str(tmp_path))


class TestSandboxCreate:
    def test_create_default_id(self, mgr):
        sb = mgr.create()
        assert sb.status == "active"
        assert sb.id.startswith("sb-")

    def test_create_custom_id(self, mgr):
        sb = mgr.create(sandbox_id="my-sandbox")
        assert sb.id == "my-sandbox"

    def test_create_invalid_id(self, mgr):
        with pytest.raises(ValueError, match="Invalid"):
            mgr.create(sandbox_id="../../etc")

    def test_create_dirs_exist(self, mgr):
        sb = mgr.create(sandbox_id="test-sb")
        from pathlib import Path
        sb_path = Path(sb.path)
        assert (sb_path / "input").is_dir()
        assert (sb_path / "output").is_dir()


class TestSandboxGet:
    def test_get_existing(self, mgr):
        mgr.create(sandbox_id="get-test")
        result = mgr.get("get-test")
        assert result is not None
        assert result.id == "get-test"

    def test_get_nonexistent(self, mgr):
        result = mgr.get("no-such-sandbox")
        assert result is None


class TestSandboxList:
    def test_list_empty(self, mgr):
        result = mgr.list_all()
        assert result == []

    def test_list_with_sandboxes(self, mgr):
        mgr.create(sandbox_id="sb-a")
        mgr.create(sandbox_id="sb-b")
        result = mgr.list_all()
        ids = [s.id for s in result]
        assert "sb-a" in ids
        assert "sb-b" in ids


class TestSandboxCleanup:
    def test_cleanup_existing(self, mgr):
        mgr.create(sandbox_id="cleanup-test")
        result = mgr.cleanup("cleanup-test")
        assert result is True

    def test_cleanup_nonexistent(self, mgr):
        result = mgr.cleanup("no-such")
        assert result is False


class TestSandboxRun:
    def test_run_simple(self, mgr):
        mgr.create(sandbox_id="run-test")
        result = mgr.run("run-test", "echo hello")
        assert result.ok is True


    def test_run_nonexistent(self, mgr):
        result = mgr.run("no-such", "echo hi")
        assert result.ok is False

    def test_run_failing_command(self, mgr):
        mgr.create(sandbox_id="fail-test")
        result = mgr.run("fail-test", "exit 1")
        assert result.ok is False