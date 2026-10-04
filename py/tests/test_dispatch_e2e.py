"""派发主链的端到端用例：**不 mock `_DRIVERS`**。

为什么单独开一个文件：`tests/test_dispatcher.py` 有 60+ 条用例，但大量直接替换
`maop.delegate.dispatcher._DRIVERS`（例如 `:96` 起的 `mock_cli`）—— 也就是说
**Dispatcher → driver 注册表 → 子进程** 这一段接线被剪断时，那个文件可以全绿。
那些用例测的是"在 driver 已被替换的前提下 Dispatcher 的决策逻辑"，本身没问题；
缺的是**没人守那根线**。

所以这里刻意一个替身都不用：真实 `AgentConfig`（不是 MagicMock —— 替身越宽容，
越测不出接线错误）、真实 driver 注册表、真实子进程。子进程会往磁盘写一个哨兵文件，
所以"文件在"是"driver 真的跑起来了"的证据，而不只是"某个函数返回了 0"。

变异验证：把 `dispatch_impl_inner` 里 `self._drivers[...]` 的取值改成构造好的假
driver（或直接返回一个成功结果），本文件必红。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from maop.delegate.dispatcher import Dispatcher
from maop.delegate.models import AgentConfig

_MARKER = "dispatch-e2e-marker"


def _write_probe(tmp_path: Path) -> str:
    """写一个"打印标记 + 落一个哨兵文件"的子进程脚本，返回可直接当 cli_args 用的路径。

    用正斜杠：Windows 上 `cli_args` 会被 `shlex.split(..., posix=True)` 切分，
    反斜杠会被当转义吃掉（既存缺陷，已在别处单独上报），正斜杠两平台都接受。
    """
    sentinel = tmp_path / "ran.sentinel"
    script = tmp_path / "probe.py"
    script.write_text(
        "import pathlib, sys\n"
        f"pathlib.Path({str(sentinel)!r}).write_text('ran')\n"
        f"sys.stdout.write({_MARKER!r})\n",
        encoding="utf-8",
    )
    return str(script).replace("\\", "/")


class _RealShapedConfig:
    """最小但**真实形状**的配置对象。

    刻意不用 MagicMock：`getattr(cfg, "agents", None)` 在 MagicMock 上永远"有值"，
    会把"配置里根本没有这个 agent"这类问题掩盖掉。
    """

    def __init__(self, agents: list[AgentConfig]) -> None:
        self.agents = agents
        self.workflows: list = []


@pytest.fixture
def dispatched(tmp_path: Path):
    """跑一次真实派发，返回 (DispatchResult, 哨兵文件路径)。"""
    sentinel = tmp_path / "ran.sentinel"
    config = _RealShapedConfig([
        AgentConfig(
            name="e2e-probe",
            cli=sys.executable,
            driver="cli",
            cli_args=_write_probe(tmp_path),
            timeout_s=60,
        ),
    ])
    dispatcher = Dispatcher(MAOP_config=config)
    result = asyncio.run(dispatcher.dispatch(agent="e2e-probe", task="ignored"))
    return result, sentinel


def test_real_driver_actually_spawns_a_process(dispatched) -> None:
    """核心：真实 driver 真的起了子进程并把结果带回来。"""
    result, sentinel = dispatched
    assert result.result.exit_code == 0, result.result.error
    assert _MARKER in (result.result.stdout or ""), result.result.stdout
    assert sentinel.exists(), (
        "子进程没有落哨兵文件 —— driver 注册表到子进程这一段没真的跑起来"
    )
    assert sentinel.read_text(encoding="utf-8") == "ran"


def test_driver_used_is_reported_honestly(dispatched) -> None:
    """`driver_used` 必须报真实 driver 名，不许空着或写别的。"""
    result, _ = dispatched
    assert result.driver_used == "cli", result.driver_used


def test_unknown_agent_is_rejected_without_touching_the_registry(tmp_path: Path) -> None:
    """对照：没配过的 agent 要在解析阶段就被挡住，而不是落到 driver 再报奇怪的错。"""
    dispatcher = Dispatcher(MAOP_config=_RealShapedConfig([]))
    result = asyncio.run(dispatcher.dispatch(agent="never-configured", task="t"))
    assert result.result.exit_code != 0
    assert "not found" in (result.result.error or "").lower()
    assert result.driver_used == ""


def test_agent_config_is_not_silently_reinterpreted(dispatched) -> None:
    """配置里写什么就派发什么：确认 `cli_args` 真被用上（而不是走了默认 `-p {task}`）。

    脚本路径是我们给的、`{task}` 不在其中 —— 若实现忽略了 `cli_args` 改走默认模板，
    子进程会因为参数不对而失败/不落哨兵，本用例随之变红。
    """
    result, sentinel = dispatched
    assert result.result.exit_code == 0
    assert sentinel.exists()
