"""CLI 原生 usage 出口（``--usage-output-file``）解析与回读的回归测试。

背景：GAP-2 方案 A 解析的是 agent 主动打印的 ``[USAGE]`` 协议行。那是 MAOP
自定义的约定，真实第三方 CLI 从不输出，因此生产环境该路径恒为 ``None``，
成本侧只能退回 CJK 启发式（``tokens_source=estimated``）。本组测试守住第二条
取值链：CLI 自己落盘的 usage JSON。

**最危险的口径守卫**：copilot 的 prompt 侧消耗 = input + cache_read +
cache_write。只取 ``input`` 会把 19853 报成 525（低估 37 倍），而且因为"有数"
不会被启发式兜底纠正——那是最坏的假实测。
"""
from __future__ import annotations

import json
import sys
from typing import Any

import pytest

from maop.core.agent.usage_protocol import parse_usage_file
from maop.delegate.drivers import _run_cli
from maop.delegate.models import AgentConfig

_COPILOT_USAGE: dict[str, Any] = {
    "tokenDetails": {
        "input": {"tokenCount": 525},
        "cache_read": {"tokenCount": 19328},
        "cache_write": {"tokenCount": 0},
        "output": {"tokenCount": 5},
    }
}


def _write(tmp_path: Any, payload: Any) -> str:
    path = tmp_path / "usage.json"
    path.write_text(
        payload if isinstance(payload, str) else json.dumps(payload),
        encoding="utf-8",
    )
    return str(path)


class TestParseUsageFile:
    def test_copilot_prompt_includes_cache(self, tmp_path):
        """prompt = input + cache_read + cache_write（与 CLI 的 ↑ 口径一致）。"""
        usage = parse_usage_file(_write(tmp_path, _COPILOT_USAGE), "copilot")
        assert usage == {
            "prompt_tokens": 19853,
            "completion_tokens": 5,
            "total_tokens": 19858,
            "cache_read_tokens": 19328,
            "cache_write_tokens": 0,
        }

    def test_prompt_not_understated(self, tmp_path):
        """回归守卫：只取 input 会把 19853 报成 525。"""
        usage = parse_usage_file(_write(tmp_path, _COPILOT_USAGE), "copilot")
        assert usage is not None
        assert usage["prompt_tokens"] > 525 * 10

    def test_cache_write_counted(self, tmp_path):
        payload = {
            "tokenDetails": {
                "input": {"tokenCount": 10},
                "cache_read": {"tokenCount": 20},
                "cache_write": {"tokenCount": 30},
                "output": {"tokenCount": 4},
            }
        }
        usage = parse_usage_file(_write(tmp_path, payload), "copilot")
        assert usage is not None
        assert usage["prompt_tokens"] == 60
        assert usage["cache_write_tokens"] == 30

    def test_missing_file(self, tmp_path):
        assert parse_usage_file(str(tmp_path / "nope.json"), "copilot") is None

    def test_invalid_json(self, tmp_path):
        assert parse_usage_file(_write(tmp_path, "not-json{"), "copilot") is None

    def test_non_object_json(self, tmp_path):
        assert parse_usage_file(_write(tmp_path, [1, 2, 3]), "copilot") is None

    def test_no_token_details(self, tmp_path):
        assert parse_usage_file(_write(tmp_path, {"foo": 1}), "copilot") is None

    def test_all_zero_returns_none(self, tmp_path):
        """全零不冒充实测——宁可退回启发式。"""
        payload = {
            "tokenDetails": {
                "input": {"tokenCount": 0},
                "cache_read": {"tokenCount": 0},
                "cache_write": {"tokenCount": 0},
                "output": {"tokenCount": 0},
            }
        }
        assert parse_usage_file(_write(tmp_path, payload), "copilot") is None

    def test_unknown_format(self, tmp_path):
        assert parse_usage_file(_write(tmp_path, _COPILOT_USAGE), "nope") is None

    def test_empty_format(self, tmp_path):
        assert parse_usage_file(_write(tmp_path, _COPILOT_USAGE), "") is None

    def test_empty_path(self):
        assert parse_usage_file("", "copilot") is None

    def test_bool_and_string_counts_ignored(self, tmp_path):
        """bool 是 int 子类，但 True 不是 token 数；字符串同样不算。"""
        payload = {
            "tokenDetails": {
                "input": {"tokenCount": True},
                "output": {"tokenCount": "7"},
            }
        }
        assert parse_usage_file(_write(tmp_path, payload), "copilot") is None


_FAKE_CLI = (
    "import json,sys;"
    "a=sys.argv[1:];"
    "t=a[a.index('--usage-output-file')+1];"
    "open(t,'w',encoding='utf-8').write(json.dumps("
    "{'tokenDetails':{'input':{'tokenCount':525},"
    "'cache_read':{'tokenCount':19328},"
    "'cache_write':{'tokenCount':0},"
    "'output':{'tokenCount':5}}}));"
    "print('done')"
)


def _config(**overrides: Any) -> AgentConfig:
    defaults: dict[str, Any] = {
        "name": "test-agent",
        "cli": sys.executable,
        "driver": "cli",
        "model": "test-model",
    }
    defaults.update(overrides)
    return AgentConfig(**defaults)


class TestRunCliUsageFile:
    @pytest.mark.asyncio
    async def test_readback_replaces_estimate(self, tmp_path):
        """声明 usage_format 后，usage 来自 CLI 落盘文件而非协议行/启发式。"""
        config = _config(
            cli_args=f'-c "{_FAKE_CLI}" --usage-output-file "{{usage_file}}"',
            usage_format="copilot",
        )
        result = await _run_cli(config, "hi", 30, str(tmp_path), "t-usage")
        assert result.exit_code == 0
        assert result.usage is not None
        assert result.usage["prompt_tokens"] == 19853
        assert result.usage["total_tokens"] == 19858

    @pytest.mark.asyncio
    async def test_tempdir_cleaned_up(self, tmp_path, monkeypatch):
        """临时目录必须在返回前删除（否则每次 run 泄漏一个目录）。"""
        import maop.delegate.drivers as drv

        created: list[str] = []
        real_mkdtemp = drv.tempfile.mkdtemp

        def _spy(*args: Any, **kwargs: Any) -> str:
            path = real_mkdtemp(*args, **kwargs)
            created.append(path)
            return path

        monkeypatch.setattr(drv.tempfile, "mkdtemp", _spy)
        config = _config(
            cli_args=f'-c "{_FAKE_CLI}" --usage-output-file "{{usage_file}}"',
            usage_format="copilot",
        )
        await _run_cli(config, "hi", 30, str(tmp_path), "t-clean")
        assert created, "mkdtemp 未被调用——占位符路径未生效"
        from pathlib import Path

        assert not Path(created[0]).exists()

    @pytest.mark.asyncio
    async def test_no_usage_format_leaves_behavior_unchanged(self, tmp_path, monkeypatch):
        """未声明 usage_format：不建临时目录，usage 仍走协议行路径。"""
        import maop.delegate.drivers as drv

        called = False
        real_mkdtemp = drv.tempfile.mkdtemp

        def _spy(*args: Any, **kwargs: Any) -> str:
            nonlocal called
            called = True
            return real_mkdtemp(*args, **kwargs)

        monkeypatch.setattr(drv.tempfile, "mkdtemp", _spy)
        config = _config(cli_args='-c "print(\'[USAGE] turns=1 tokens=42 source=measured\')"')
        result = await _run_cli(config, "hi", 30, str(tmp_path), "t-plain")
        assert result.exit_code == 0
        assert called is False
        assert result.usage is not None
        assert result.usage["total_tokens"] == 42

    @pytest.mark.asyncio
    async def test_usage_file_wins_over_protocol_line(self, tmp_path):
        """两条链同时存在时，原生文件（实测）优先于协议行。"""
        cli = _FAKE_CLI.replace("print('done')", "print('[USAGE] turns=1 tokens=999 source=measured')")
        config = _config(
            cli_args=f'-c "{cli}" --usage-output-file "{{usage_file}}"',
            usage_format="copilot",
        )
        result = await _run_cli(config, "hi", 30, str(tmp_path), "t-priority")
        assert result.usage is not None
        assert result.usage["total_tokens"] == 19858
