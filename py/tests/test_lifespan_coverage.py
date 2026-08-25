"""Coverage tests for maop.dashboard.lifespan — 应用生命周期管理.

该模块在基线测试中覆盖率为 0%。本文件补充 lifespan 和信号处理的测试。
使用 mock 避免启动真实的服务。
"""

from __future__ import annotations

import signal
from unittest.mock import patch

from maop.dashboard import lifespan


class TestSignalHandler:
    """测试 _signal_handler 函数。"""

    def test_signal_handler_sets_shutting_down(self):
        """第一次调用设置 _shutting_down 标志。"""
        lifespan._shutting_down = False
        with patch.object(lifespan, "_prev_handlers", {}):
            lifespan._signal_handler(signal.SIGINT, None)
        assert lifespan._shutting_down is True
        # 清理
        lifespan._shutting_down = False

    def test_signal_handler_idempotent(self):
        """重复调用不会重置标志。"""
        lifespan._shutting_down = True
        with patch.object(lifespan, "_prev_handlers", {}):
            lifespan._signal_handler(signal.SIGINT, None)
        assert lifespan._shutting_down is True
        # 清理
        lifespan._shutting_down = False

    def test_signal_handler_chains_to_prev(self):
        """链式调用前一个处理器。"""
        lifespan._shutting_down = False
        prev_called = False

        def prev_handler(signum, frame):
            nonlocal prev_called
            prev_called = True

        with patch.object(lifespan, "_prev_handlers", {signal.SIGINT: prev_handler}):
            lifespan._signal_handler(signal.SIGINT, None)

        assert prev_called is True
        # 清理
        lifespan._shutting_down = False

    def test_signal_handler_with_sig_dfl(self):
        """前一个处理器是 SIG_DFL 时的处理。"""
        lifespan._shutting_down = False
        with patch.object(lifespan, "_prev_handlers", {signal.SIGINT: signal.SIG_DFL}), \
             patch("signal.signal") as mock_signal, \
             patch("signal.raise_signal") as mock_raise:
            lifespan._signal_handler(signal.SIGINT, None)
            mock_signal.assert_called_once_with(signal.SIGINT, signal.SIG_DFL)
            mock_raise.assert_called_once_with(signal.SIGINT)
        # 清理
        lifespan._shutting_down = False

    def test_signal_handler_with_sig_ign(self):
        """前一个处理器是 SIG_IGN 时不做额外操作。"""
        lifespan._shutting_down = False
        with patch.object(lifespan, "_prev_handlers", {signal.SIGINT: signal.SIG_IGN}):
            # 不应抛异常
            lifespan._signal_handler(signal.SIGINT, None)
        # 清理
        lifespan._shutting_down = False


class TestInstallSignalHandlers:
    """测试 install_signal_handlers 函数。"""

    def test_install_on_non_windows(self):
        """在非 Windows 平台安装 SIGTERM 和 SIGINT 处理器。"""
        with patch("sys.platform", "linux"), patch("signal.signal") as mock_signal:
            lifespan.install_signal_handlers()
            # 应该安装了 SIGTERM 和 SIGINT
            calls = [c.args[0] for c in mock_signal.call_args_list]
            assert signal.SIGTERM in calls
            assert signal.SIGINT in calls

    def test_install_on_windows(self):
        """在 Windows 平台只安装 SIGINT 处理器。"""
        with patch("sys.platform", "win32"), patch("signal.signal") as mock_signal:
            lifespan.install_signal_handlers()
            # Windows 上只安装 SIGINT
            calls = [c.args[0] for c in mock_signal.call_args_list]
            assert signal.SIGINT in calls
            assert signal.SIGTERM not in calls


class TestLifespanModule:
    """测试 lifespan 模块属性。"""

    def test_shutting_down_initial(self):
        """_shutting_down 初始为 False。"""
        # 重置后检查
        lifespan._shutting_down = False
        assert lifespan._shutting_down is False

    def test_prev_handlers_is_dict(self):
        """_prev_handlers 是字典。"""
        assert isinstance(lifespan._prev_handlers, dict)

    def test_logger_exists(self):
        """模块有 logger。"""
        assert lifespan.logger is not None
        assert lifespan.logger.name == "maop.dashboard.lifespan"