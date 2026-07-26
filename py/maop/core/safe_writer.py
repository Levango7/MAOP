"""MAOP Safe Writer — 原子写入工具，防止崩溃导致数据损坏。

提供 ``safe_write_text`` / ``safe_write_bytes`` / ``safe_write_json`` 三个
函数，采用「先写临时文件 → fsync 文件 → fsync 父目录 → os.replace 原子替换」
的标准原子写入模式。这样即使在写入过程中进程崩溃或断电，目标文件要么保持
旧内容完整，要么已经是完整的新内容，不会出现半截写入的损坏文件。

跨平台说明：
  - 在 POSIX 系统上会对父目录执行 fsync 以确保目录条目持久化
  - 在 Windows 上 fsync 父目录可能不被支持（会抛出 OSError），此时静默忽略

Usage::

    from maop.core.safe_writer import safe_write_text, safe_write_json

    safe_write_text("/path/to/file.txt", "hello world")
    safe_write_json("/path/to/config.json", {"key": "value"})
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import tempfile
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["safe_write_bytes", "safe_write_json", "safe_write_text"]


def _fsync_dir(dir_path: Path) -> None:
    """对父目录执行 fsync 以持久化目录条目。

    在 Windows 或某些文件系统上 fsync 目录可能不被支持，此时
    静默忽略 OSError / PermissionError，保证跨平台可用。
    """
    try:
        fd = os.open(str(dir_path), os.O_RDONLY)
    except (OSError, PermissionError):
        # Windows 上无法以 O_RDONLY 打开目录，直接跳过
        return
    try:
        os.fsync(fd)
    except (OSError, PermissionError):
        # 某些文件系统（如网络挂载）不支持 fsync 目录
        pass
    finally:
        os.close(fd)


def safe_write_bytes(path: str | Path, content: bytes) -> None:
    """原子写入 bytes 到指定路径。

    算法：写入临时文件（同目录）→ fsync 文件 → fsync 父目录 → ``os.replace``
    原子替换目标文件。若写入过程中抛出异常，会清理临时文件后重新抛出。

    Parameters
    ----------
    path : str | Path
        目标文件路径。
    content : bytes
        要写入的字节内容。
    """
    target = Path(path)
    parent = target.parent
    parent.mkdir(parents=True, exist_ok=True)

    # 临时文件名格式：<原名>.tmp.<pid>.<random>，确保同目录且唯一
    suffix = f".tmp.{os.getpid()}.{secrets.token_hex(8)}"
    tmp_path = parent / f"{target.name}{suffix}"

    try:
        # 使用 tempfile.NamedTemporaryFile 创建临时文件以保证文件描述符安全
        with tempfile.NamedTemporaryFile(
            delete=False,
            dir=str(parent),
            prefix=f"{target.name}.",
            suffix=suffix,
        ) as tmp:
            tmp.write(content)
            tmp.flush()
            os.fsync(tmp.fileno())
            tmp_name = tmp.name

        # 将临时文件路径转换为 Path 以便跨平台处理
        tmp_file = Path(tmp_name)

        # fsync 父目录以持久化目录条目（Windows 上可能不支持，会静默忽略）
        _fsync_dir(parent)

        # os.replace 是原子操作：在 POSIX 上使用 rename(2)，在 Windows 上使用
        # MoveFileExW with MOVEFILE_REPLACE_EXISTING
        os.replace(tmp_file, target)
    except Exception:
        # 写入失败时清理临时文件，避免残留垃圾文件
        try:
            if "tmp_file" in locals() and tmp_file.exists():
                tmp_file.unlink()
        except Exception as cleanup_exc:
            logger.warning("[safe_writer] 清理临时文件失败: %s", cleanup_exc)
        raise


def safe_write_text(path: str | Path, content: str, encoding: str = "utf-8") -> None:
    """原子写入文本到指定路径。

    内部调用 ``safe_write_bytes``，将文本按指定编码编码为字节后写入。

    Parameters
    ----------
    path : str | Path
        目标文件路径。
    content : str
        要写入的文本内容。
    encoding : str
        文本编码，默认 ``"utf-8"``。
    """
    safe_write_bytes(path, content.encode(encoding))


def safe_write_json(path: str | Path, data: Any, indent: int = 2) -> None:
    """原子写入 JSON 到指定路径。

    内部使用 ``json.dumps`` 序列化后通过 ``safe_write_text`` 写入。
    默认 ``ensure_ascii=False`` 以正确保留中文等非 ASCII 字符。

    Parameters
    ----------
    path : str | Path
        目标文件路径。
    data : Any
        可被 ``json.dumps`` 序列化的对象。
    indent : int
        JSON 缩进空格数，默认 2。
    """
    text = json.dumps(data, indent=indent, ensure_ascii=False)
    safe_write_text(path, text)
