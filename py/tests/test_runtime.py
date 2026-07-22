"""Unit tests for MAOP.core.runtime module."""

from __future__ import annotations

import sys

import pytest

from maop.core.runtime import (
    LocalRuntime, IsolatedRuntime, RuntimeConfig, RuntimeType,
    _resolve_cmd,
)


class TestResolveCmd:
    def test_simple_command(self):
        result = _resolve_cmd("echo hello")
        assert result[0] in ("echo", "cmd.exe")

    def test_with_args(self):
        result = _resolve_cmd("python", args=["-c", "print(1)"])
        assert "python" in result[0]
        assert "-c" in result

    def test_empty_command(self):
        result = _resolve_cmd("")
        assert result == []

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows only")
    def test_windows_builtin(self):
        result = _resolve_cmd("dir")
        assert result[0] == "cmd.exe"
        assert "/c" in result


class TestLocalRuntime:
    @pytest.fixture
    def runtime(self):
        return LocalRuntime(RuntimeConfig())

    def test_info(self, runtime):
        info = runtime.info()
        assert info.type == RuntimeType.LOCAL
        assert info.available is True
        assert info.version  # non-empty

    def test_execute_simple(self, runtime):
        result = runtime.execute("echo hello")
        assert result.exit_code == 0
        assert "hello" in result.stdout

    def test_execute_with_args(self, runtime):
        result = runtime.execute("python", args=["-V"])
        assert result.exit_code in (0, -1)  # -1 if python not on PATH

    def test_execute_failure(self, runtime):
        result = runtime.execute(f"{sys.executable} -c 'import nonexistent_module_xyz'")
        assert result.exit_code != 0

    def test_is_available(self, runtime):
        assert runtime.is_available() is True


class TestIsolatedRuntime:
    @pytest.fixture
    def runtime(self, tmp_path):
        cfg = RuntimeConfig(
            type=RuntimeType.ISOLATED,
            sandbox_dir=str(tmp_path / "sandbox"),
        )
        return IsolatedRuntime(cfg)

    def test_info(self, runtime):
        info = runtime.info()
        assert info.type == RuntimeType.ISOLATED

    def test_execute(self, runtime):
        result = runtime.execute("echo isolated")
        assert result.exit_code == 0
        assert "isolated" in result.stdout


class TestRuntimeConfig:
    def test_default_config(self):
        cfg = RuntimeConfig()
        assert cfg.type == RuntimeType.LOCAL
        assert cfg.timeout_s == 300.0

    def test_custom_config(self):
        cfg = RuntimeConfig(type=RuntimeType.CONTAINER, image="python:3.13", timeout_s=60)
        assert cfg.type == RuntimeType.CONTAINER
        assert cfg.image == "python:3.13"