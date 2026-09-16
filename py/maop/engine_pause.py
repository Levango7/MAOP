"""MAOP Engine — pause checking helpers.

Extracted from ``engine.py`` (Phase 3-1 split) to keep the main engine
module focused on workflow execution. These helpers check for the
``.maop_pause`` marker file created by ``control.py`` and allow the
engine to block dispatch while the system is paused.

Public symbols:
    - ``PAUSE_CHECK_INTERVAL_S`` — polling interval (seconds).
    - ``PAUSE_FILE_NAME`` — marker file name (``.maop_pause``).
    - ``MAX_PAUSE_SECONDS`` — maximum wait before giving up.
    - ``_get_pause_file_path`` — resolve the marker file path.
    - ``is_paused`` — synchronous pause check.
    - ``check_pause_async`` — async pause check that waits until unpaused.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


# ── M4 修复：pause 检查 ────────────────────────────────────────
# control.py 的 pause() 创建 .maop_pause 标记文件，但原 engine.py 未检查该文件，
# 导致 pause 后系统继续执行。现新增 check_pause() 在任务派发前检查标记文件。
PAUSE_CHECK_INTERVAL_S: float = 1.0  # pause 检查轮询间隔（秒）
PAUSE_FILE_NAME: str = ".maop_pause"  # pause 标记文件名（与 control.py 一致）
# P2-fix: pause 最大超时限制——防止 pause 标记文件未被清理导致引擎无限阻塞。
# 超过此限制后记录 warning 并继续执行，避免整个引擎卡死。
MAX_PAUSE_SECONDS: int = 3600  # pause 最大等待时长（秒），默认 1 小时


def _get_pause_file_path() -> Path:
    """获取 pause 标记文件路径。

    文件位于 <root>/logs/.maop_pause，与 control.py 中的路径保持一致。
    使用 get_root_dir() 统一解析根目录（M3 修复）。
    """
    try:
        from maop.config.env import get_root_dir
        root = get_root_dir(default=".")
    except Exception:
        # 回退：使用当前工作目录，避免导入失败导致引擎不可用
        root = Path.cwd()
    return root / "logs" / PAUSE_FILE_NAME


def is_paused() -> bool:
    """检查系统是否处于暂停状态。

    Returns
    -------
    bool
        True 表示系统已暂停（.maop_pause 文件存在）。
    """
    return _get_pause_file_path().exists()


async def check_pause_async() -> None:
    """异步检查 pause 状态，若已暂停则等待直到恢复。

    在任务派发前调用此函数，确保暂停期间不执行新任务。
    使用异步 sleep 避免阻塞事件循环。

    P2-fix: 添加 MAX_PAUSE_SECONDS 超时限制——如果 pause 持续时间超过此限制，
    记录 warning 并继续执行，防止 pause 标记文件未被清理导致引擎无限阻塞。
    """
    waited = 0.0
    while is_paused():
        if waited >= MAX_PAUSE_SECONDS:
            logger.warning(
                "系统暂停已超过 %d 秒（MAX_PAUSE_SECONDS），可能 pause 标记文件未被清理；"
                "放弃等待，继续执行以避免引擎无限阻塞。",
                MAX_PAUSE_SECONDS,
            )
            return
        logger.info("系统已暂停（.maop_pause 存在），等待恢复...")
        await asyncio.sleep(PAUSE_CHECK_INTERVAL_S)
        waited += PAUSE_CHECK_INTERVAL_S