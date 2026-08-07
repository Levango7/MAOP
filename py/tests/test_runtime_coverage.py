"""Coverage tests for maop.core.runtime — execution environment abstraction.

Exercises _resolve_cmd (Windows built-in handling), LocalRuntime (success /
timeout / exception), IsolatedRuntime (sandbox), ContainerRuntime (no-Docker
fallback), and the create_runtime factory.
"""
from __future__ import annotations

import sys

import pytest

from maop.core.agent.lifecycle.runtime import (
    ContainerRuntime,
    IsolatedRuntime,
    LocalRuntime,
    RuntimeConfig,
    RuntimeType,
    _resolve_cmd,
    create_runtime,
)


class TestResolveCmd:
    def test_normal_command(self):
        cmd = _resolve_cmd("python --version")
        assert cmd[0].endswith("python") or cmd[0] == "python"

    def test_with_extra_args(self):
        cmd = _resolve_cmd("python", ["-c", "print(1)"])
        assert "-c" in cmd and "print(1)" in cmd

    def test_empty_command(self):
        assert _resolve_cmd("") == []

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows built-in handling")
    def test_windows_builtin_prefixed(self):
        cmd = _resolve_cmd("echo hello")
        assert cmd[0].endswith("cmd.exe") and "/c" in cmd


class TestLocalRuntime:
    def test_info(self):
        rt = LocalRuntime(RuntimeConfig())
        info = rt.info()
        assert info.type == RuntimeType.LOCAL
        assert info.available is True

    def test_execute_success(self):
        rt = LocalRuntime(RuntimeConfig(timeout_s=10))
        result = rt.execute("python", args=["--version"])
        assert result.exit_code == 0
        assert result.runtime_type == RuntimeType.LOCAL

    def test_execute_timeout(self):
        rt = LocalRuntime(RuntimeConfig(timeout_s=0.01))
        result = rt.execute("python", args=["-c", "import time; time.sleep(2)"])
        assert result.exit_code == -1
        assert result.timed_out is True

    def test_execute_exception(self):
        rt = LocalRuntime(RuntimeConfig(timeout_s=10))
        result = rt.execute("nonexistent-binary-xyz")
        assert result.exit_code == -1
        assert result.stderr != ""

    def test_is_available(self):
        assert LocalRuntime(RuntimeConfig()).is_available() is True


class TestIsolatedRuntime:
    def test_info(self, tmp_path):
        rt = IsolatedRuntime(RuntimeConfig(sandbox_dir=str(tmp_path / "sb")))
        info = rt.info()
        assert info.type == RuntimeType.ISOLATED
        assert info.available is True

    def test_execute_creates_sandbox(self, tmp_path):
        sb = tmp_path / "sandbox"
        rt = IsolatedRuntime(RuntimeConfig(sandbox_dir=str(sb), timeout_s=10))
        result = rt.execute("python", args=["--version"])
        assert result.exit_code == 0
        assert result.runtime_type == RuntimeType.ISOLATED
        assert sb.is_dir()
        for d in ("input", "output", "temp"):
            assert (sb / d).is_dir()

    def test_execute_exception(self, tmp_path):
        rt = IsolatedRuntime(RuntimeConfig(sandbox_dir=str(tmp_path / "sb"), timeout_s=10))
        result = rt.execute("nonexistent-binary-xyz")
        assert result.exit_code == -1

    def test_default_sandbox_dir(self):
        rt = IsolatedRuntime(RuntimeConfig())
        assert rt.config.sandbox_dir != ""


class TestContainerRuntime:
    def test_is_available_returns_bool(self):
        rt = ContainerRuntime(RuntimeConfig())
        assert isinstance(rt.is_available(), bool)

    def test_info(self):
        rt = ContainerRuntime(RuntimeConfig(image="python:3.11"))
        info = rt.info()
        assert info.type == RuntimeType.CONTAINER
        assert info.details["image"] == "python:3.11"

    def test_execute_without_docker(self, monkeypatch):
        # Force Docker unavailable → execute returns -1 immediately.
        monkeypatch.setattr(ContainerRuntime, "is_available", lambda self: False)
        rt = ContainerRuntime(RuntimeConfig())
        result = rt.execute("echo hi")
        assert result.exit_code == -1
        assert "Docker" in result.stderr


class TestCreateRuntime:
    def test_local(self):
        rt = create_runtime(RuntimeConfig(type=RuntimeType.LOCAL))
        assert isinstance(rt, LocalRuntime)

    def test_isolated(self):
        rt = create_runtime(RuntimeConfig(type=RuntimeType.ISOLATED))
        assert isinstance(rt, IsolatedRuntime)

    def test_default_config(self):
        rt = create_runtime(None)
        assert isinstance(rt, LocalRuntime)

    def test_container_fallback_when_unavailable(self, monkeypatch):
        monkeypatch.setattr(ContainerRuntime, "is_available", lambda self: False)
        rt = create_runtime(RuntimeConfig(type=RuntimeType.CONTAINER))
        # Unavailable container → falls back to LocalRuntime.
        assert isinstance(rt, LocalRuntime)