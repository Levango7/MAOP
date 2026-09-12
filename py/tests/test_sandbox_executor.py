"""SandboxExecutor 白盒测试.

覆盖：SandboxConfig/SandboxResult 构造、配置验证、权限检查、
命令执行（mock subprocess）、网络/目录拒绝、超时杀死、线程安全。
每个测试使用 tmp_path 隔离，不触碰真实环境。
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from maop.core.agent.ops.sandbox_executor import (
    SandboxConfig,
    SandboxExecutor,
    SandboxResult,
)


# ── Fixtures ─────────────────────────────────────────────────────


@pytest.fixture
def executor() -> SandboxExecutor:
    """沙箱执行器实例。"""
    return SandboxExecutor()


@pytest.fixture
def basic_config(tmp_path: Path) -> SandboxConfig:
    """基础沙箱配置（允许 tmp_path，禁止网络）。"""
    return SandboxConfig(
        allowed_dirs=[str(tmp_path)],
        blocked_dirs=[],
        allow_network=False,
        max_execution_time_s=10.0,
        working_dir=str(tmp_path),
    )


# ── 1. TestSandboxConfig ────────────────────────────────────────


class TestSandboxConfig:
    """SandboxConfig 构造与默认值。"""

    def test_default_values(self) -> None:
        """默认构造：所有可变字段使用工厂函数。"""
        config = SandboxConfig()
        assert config.allowed_dirs == []
        assert config.blocked_dirs == []
        assert config.allow_network is False
        assert config.allow_env_vars == []
        assert config.max_cpu_percent == 100.0
        assert config.max_memory_mb == 0
        assert config.max_execution_time_s == 300.0
        assert config.working_dir == ""

    def test_custom_construction(self, tmp_path: Path) -> None:
        """自定义构造。"""
        config = SandboxConfig(
            allowed_dirs=[str(tmp_path)],
            blocked_dirs=["/etc"],
            allow_network=True,
            allow_env_vars=["PATH", "HOME"],
            max_cpu_percent=50.0,
            max_memory_mb=512,
            max_execution_time_s=60.0,
            working_dir=str(tmp_path),
        )
        assert config.allowed_dirs == [str(tmp_path)]
        assert config.blocked_dirs == ["/etc"]
        assert config.allow_network is True
        assert config.allow_env_vars == ["PATH", "HOME"]
        assert config.max_cpu_percent == 50.0
        assert config.max_memory_mb == 512
        assert config.max_execution_time_s == 60.0

    def test_mutable_defaults_not_shared(self) -> None:
        """可变默认值不应在实例间共享。"""
        c1 = SandboxConfig()
        c2 = SandboxConfig()
        c1.allowed_dirs.append("/tmp")
        c1.allow_env_vars.append("PATH")
        assert c2.allowed_dirs == []
        assert c2.allow_env_vars == []


# ── 2. TestSandboxResult ────────────────────────────────────────


class TestSandboxResult:
    """SandboxResult 构造。"""

    def test_success_result(self) -> None:
        """成功结果。"""
        result = SandboxResult(
            success=True,
            stdout="hello",
            stderr="",
            return_code=0,
            duration_s=0.5,
        )
        assert result.success
        assert result.stdout == "hello"
        assert result.killed is False
        assert result.kill_reason == ""

    def test_killed_result(self) -> None:
        """被杀结果。"""
        result = SandboxResult(
            success=False,
            stderr="timeout",
            return_code=-1,
            duration_s=10.0,
            killed=True,
            kill_reason="timeout after 10s",
        )
        assert not result.success
        assert result.killed
        assert result.kill_reason == "timeout after 10s"


# ── 3. TestValidateConfig ───────────────────────────────────────


class TestValidateConfig:
    """配置验证。"""

    def test_valid_config(self, executor: SandboxExecutor, tmp_path: Path) -> None:
        """合法配置返回空违规列表。"""
        config = SandboxConfig(
            allowed_dirs=[str(tmp_path)],
            blocked_dirs=["/etc"],
            working_dir=str(tmp_path),
        )
        violations = executor.validate_config(config)
        assert violations == []

    def test_overlap_dirs_violation(self, executor: SandboxExecutor, tmp_path: Path) -> None:
        """允许/禁止目录重叠应报违规。"""
        path = str(tmp_path)
        config = SandboxConfig(
            allowed_dirs=[path],
            blocked_dirs=[path],
        )
        violations = executor.validate_config(config)
        assert len(violations) == 1
        assert "overlap" in violations[0]

    def test_nonexistent_working_dir(self, executor: SandboxExecutor) -> None:
        """不存在的 working_dir 应报违规。"""
        config = SandboxConfig(working_dir="/nonexistent/path/xyz")
        violations = executor.validate_config(config)
        assert any("working_dir" in v for v in violations)


# ── 4. TestCheckPermissions ─────────────────────────────────────


class TestCheckPermissions:
    """权限检查。"""

    def test_network_command_detected(
        self, executor: SandboxExecutor, basic_config: SandboxConfig,
    ) -> None:
        """curl 命令应检测到 network 权限。"""
        perms = executor.check_permissions("curl http://example.com", basic_config)
        assert "network" in perms

    def test_dangerous_command_detected(
        self, executor: SandboxExecutor, basic_config: SandboxConfig,
    ) -> None:
        """rm 命令应检测到 dangerous_command 权限。"""
        perms = executor.check_permissions("rm -rf /tmp/x", basic_config)
        assert "dangerous_command" in perms

    def test_env_var_detected(
        self, executor: SandboxExecutor, basic_config: SandboxConfig,
    ) -> None:
        """$VAR 应检测到 env_var 权限。"""
        perms = executor.check_permissions("echo $HOME", basic_config)
        assert any(p.startswith("env_var:HOME") for p in perms)

    def test_empty_command(self, executor: SandboxExecutor, basic_config: SandboxConfig) -> None:
        """空命令返回空权限列表。"""
        assert executor.check_permissions("", basic_config) == []
        assert executor.check_permissions("   ", basic_config) == []

    def test_python_network_import(
        self, executor: SandboxExecutor, basic_config: SandboxConfig,
    ) -> None:
        """Python requests import 应检测到 network 权限。"""
        perms = executor.check_permissions(
            "python -c 'import requests; requests.get(\"http://x\")'",
            basic_config,
        )
        assert "network" in perms


# ── 5. TestExecute ──────────────────────────────────────────────


class TestExecute:
    """命令执行。"""

    def test_execute_success(
        self, executor: SandboxExecutor, basic_config: SandboxConfig,
    ) -> None:
        """成功执行 echo 命令。"""
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = b"hello world\n"
        mock_proc.stderr = b""
        with patch("subprocess.run", return_value=mock_proc):
            result = executor.execute("echo hello world", basic_config)
        assert result.success
        assert "hello world" in result.stdout
        assert result.return_code == 0
        assert result.killed is False

    def test_execute_network_denied(
        self, executor: SandboxExecutor, basic_config: SandboxConfig,
    ) -> None:
        """未授权网络命令应被拒绝。"""
        result = executor.execute("curl http://example.com", basic_config)
        assert not result.success
        assert "network" in result.stderr
        assert result.return_code == -1
        # subprocess.run 不应被调用
        stats = executor.get_stats()
        assert stats["total_rejected"] >= 1

    def test_execute_network_allowed(
        self, executor: SandboxExecutor, basic_config: SandboxConfig,
    ) -> None:
        """授权网络命令应执行。"""
        config = basic_config.model_copy(update={"allow_network": True})
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = b"response"
        mock_proc.stderr = b""
        with patch("subprocess.run", return_value=mock_proc):
            result = executor.execute("curl http://example.com", config)
        assert result.success

    def test_execute_blocked_dir_denied(
        self, executor: SandboxExecutor, basic_config: SandboxConfig,
    ) -> None:
        """访问禁止目录应被拒绝。"""
        config = basic_config.model_copy(update={"blocked_dirs": ["/secret"]})
        result = executor.execute("cat /secret/data", config)
        assert not result.success
        assert "blocked" in result.stderr

    def test_execute_timeout(
        self, executor: SandboxExecutor, basic_config: SandboxConfig,
    ) -> None:
        """超时应被杀死。"""
        # Python 3.14 的 TimeoutExpired 不再接受 stdout/stderr 关键字参数，
        # 使用 output 参数（向后兼容别名）或仅传 cmd+timeout。
        try:
            exc = subprocess.TimeoutExpired(
                cmd="sleep 100", timeout=10.0,
                output=b"", stderr=b"",
            )
        except TypeError:
            # 更老的签名：仅 cmd + timeout
            exc = subprocess.TimeoutExpired(cmd="sleep 100", timeout=10.0)
        with patch("subprocess.run", side_effect=exc):
            result = executor.execute("sleep 100", basic_config)
        assert not result.success
        assert result.killed
        assert "timeout" in result.kill_reason
        stats = executor.get_stats()
        assert stats["total_killed"] >= 1

    def test_execute_config_violation_rejected(
        self, executor: SandboxExecutor, tmp_path: Path,
    ) -> None:
        """配置违反应拒绝执行。"""
        path = str(tmp_path)
        config = SandboxConfig(
            allowed_dirs=[path],
            blocked_dirs=[path],  # 重叠 → 违规
        )
        result = executor.execute("echo hi", config)
        assert not result.success
        assert "config violations" in result.stderr

    def test_execute_nonzero_return_code(
        self, executor: SandboxExecutor, basic_config: SandboxConfig,
    ) -> None:
        """非零返回码应 success=False。"""
        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.stdout = b""
        mock_proc.stderr = b"error occurred"
        with patch("subprocess.run", return_value=mock_proc):
            result = executor.execute("false", basic_config)
        assert not result.success
        assert result.return_code == 1

    def test_stats_tracking(
        self, executor: SandboxExecutor, basic_config: SandboxConfig,
    ) -> None:
        """统计计数器正确跟踪。"""
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = b"ok"
        mock_proc.stderr = b""
        with patch("subprocess.run", return_value=mock_proc):
            executor.execute("echo ok", basic_config)
        # 拒绝一次
        executor.execute("curl http://x", basic_config)
        stats = executor.get_stats()
        assert stats["total_executions"] == 2
        assert stats["total_rejected"] == 1


# ── 6. TestThreadSafety ─────────────────────────────────────────


class TestThreadSafety:
    """线程安全测试。"""

    def test_concurrent_execute(
        self, executor: SandboxExecutor, basic_config: SandboxConfig,
    ) -> None:
        """并发执行不抛异常，统计正确。"""
        import threading

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = b"ok"
        mock_proc.stderr = b""

        errors: list[Exception] = []

        def _run() -> None:
            try:
                with patch("subprocess.run", return_value=mock_proc):
                    executor.execute("echo ok", basic_config)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=_run) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        stats = executor.get_stats()
        assert stats["total_executions"] == 10