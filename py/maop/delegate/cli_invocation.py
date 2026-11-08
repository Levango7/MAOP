"""CLI 参数模板展开 —— 从 ``delegate.drivers`` 拆出的纯函数。

``drivers._run_cli`` 原本内联了占位符检测、哨兵替换、``shlex.split`` 与
``{usage_file}`` 临时文件生命周期。2026-11-08 加入原生 usage 采集后该文件越过
800 行规模门禁（``py/scripts/check_file_size.py``；``ALLOWED_OVERSIZE`` 已归零，
既定方向是不再加白名单），因此把这段无副作用的模板处理迁出，drivers 只保留调用。

两个占位符：

- ``{task}``（及 ``'{task}'`` / ``{{safePrompt}}``）：替换为哨兵后再 split，
  避免模板里未配对引号导致拆分失败。
- ``{usage_file}``：CLI 原生 usage 出口的落盘路径（copilot 的
  ``--usage-output-file``）。``cli_args`` 走 posix 模式 split，Windows 路径的
  反斜杠会被当转义符吃掉，因此必须在 split **之后**才还原成真实路径。
"""

from __future__ import annotations

import logging
import os
import shlex
import shutil
import tempfile

from maop.core.agent.usage_protocol import parse_usage_file, parse_usage_line

__all__ = ["USAGE_FILE_TOKEN", "discard_usage_dir", "prepare_argv", "resolve_usage"]

logger = logging.getLogger(__name__)

_TASK_SENTINELS = ("'{task}'", "{task}", "{{safePrompt}}")
_TASK_PLACEHOLDER = "\x00MAOP_TASK_PLACEHOLDER\x00"
_USAGE_PLACEHOLDER = "\x00MAOP_USAGE_PLACEHOLDER\x00"
USAGE_FILE_TOKEN = "{usage_file}"


def prepare_argv(
    args_template: str,
    prompt: str,
    *,
    agent_name: str = "",
    usage_format: str = "",
) -> tuple[list[str], str, str]:
    """展开 ``cli_args`` 模板，返回 ``(argv, usage_dir, usage_file)``。

    ``usage_dir`` 为空串表示未启用原生 usage 出口。调用方必须在子进程结束后
    调用 :func:`discard_usage_dir` 清理；本函数只在**自身**失败时清理。

    模板引号不配对时抛 ``ValueError``（调用方转成失败结果）。
    """
    task_placeholder = next(
        (ph for ph in _TASK_SENTINELS if ph in args_template), None
    )

    usage_dir = ""
    usage_file = ""
    if usage_format and USAGE_FILE_TOKEN in args_template:
        # mkdtemp 而非 mkstemp：给 CLI 一个**尚不存在**的目标文件，避免各家
        # 对「文件已存在」的行为差异（覆盖 / 追加 / 拒绝）。
        try:
            usage_dir = tempfile.mkdtemp(prefix="maop-usage-")
            usage_file = os.path.join(usage_dir, "usage.json")
        except OSError:
            usage_dir = usage_file = ""
    elif USAGE_FILE_TOKEN in args_template:
        # 配置自相矛盾：声明了占位符却没声明格式。不静默替换成空串（那会让
        # CLI 收到一个不存在的路径），也不硬失败（旧配置可能只是多余），
        # 保留字面量并留下可诊断的告警。
        logger.warning(
            "agent=%s uses {usage_file} in cli_args but declares no usage_format; "
            "the placeholder will be passed through literally",
            agent_name or "<unknown>",
        )

    template = args_template
    if task_placeholder:
        template = template.replace(task_placeholder, _TASK_PLACEHOLDER)
    if usage_file:
        template = template.replace(USAGE_FILE_TOKEN, _USAGE_PLACEHOLDER)

    try:
        parts = shlex.split(template)
    except ValueError:
        discard_usage_dir(usage_dir)
        raise

    argv = [
        prompt if part == _TASK_PLACEHOLDER
        else usage_file if part == _USAGE_PLACEHOLDER
        else part
        for part in parts
    ]
    return argv, usage_dir, usage_file


def discard_usage_dir(usage_dir: str) -> None:
    """删除临时目录（幂等；空串是 no-op）。"""
    if usage_dir:
        shutil.rmtree(usage_dir, ignore_errors=True)


def resolve_usage(
    usage_file: str,
    usage_format: str,
    raw_stdout: str | None,
) -> dict[str, int] | None:
    """取值链：原生 usage 文件（实测）→ 协议行（agent 自报）→ None（启发式）。"""
    if usage_file:
        parsed = parse_usage_file(usage_file, usage_format)
        if parsed:
            return parsed
    return parse_usage_line(raw_stdout)
