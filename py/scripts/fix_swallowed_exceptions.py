"""批量修复静默吞异常：`except Exception: pass/continue` → 加 logger.debug 记录。

用法:
  python fix_swallowed_exceptions.py --dry-run   # 预览
  python fix_swallowed_exceptions.py             # 执行
"""
from __future__ import annotations

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # py/
SRC = os.path.join(ROOT, "maop")

# 匹配单行/多行 `except Exception: pass|continue`（含 `as e`），`\s*` 跨行
_PAT = re.compile(
    r"except\s+Exception(?P<as>[^:\n]*):(?P<ws>\s*)(?P<body>pass|continue)\b",
)

# 缺 logger 定义时在文件头插入的内容
_LOGGER_BOOTSTRAP = (
    "import logging\n"
    "logger = logging.getLogger(__name__)\n"
)


def _has_logger(txt: str) -> bool:
    return bool(re.search(r"logger\s*=\s*logging\.getLogger", txt))


def transform(txt: str) -> tuple[str, int, bool]:
    """返回 (新文本, 改动数, 是否已含 logger)。"""
    had_logger = _has_logger(txt)
    count = 0

    def repl(m: re.Match) -> str:
        nonlocal count
        count += 1
        as_clause = m.group("as")
        ws = m.group("ws")          # 换行+缩进 或 空格
        body = m.group("body")      # pass / continue
        # body 的缩进 = ws 中最后一个换行之后的空白（单行时用空格）
        indent = ws.rsplit("\n", 1)[-1] if "\n" in ws else " "
        return (
            f"except Exception{as_clause}:{ws}"
            f"logger.debug('swallowed exception', exc_info=True)\n"
            f"{indent}{body}"
        )

    new_txt = _PAT.sub(repl, txt)
    return new_txt, count, had_logger


def main() -> int:
    dry_run = "--dry-run" in sys.argv
    changed_files = 0
    total = 0
    for root, _dirs, files in os.walk(SRC):
        if "__pycache__" in root or ".maop-snapshots" in root:
            continue
        for fn in files:
            if not fn.endswith(".py"):
                continue
            path = os.path.join(root, fn)
            with open(path, encoding="utf-8") as fh:
                txt = fh.read()
            new_txt, n, had_logger = transform(txt)
            if n == 0:
                continue
            total += n
            changed_files += 1
            if not had_logger:
                # 在首个 import 之后插入 logger 定义
                new_txt = _inject_logger(new_txt)
            if dry_run:
                print(f"[dry-run] {os.path.relpath(path, ROOT)}: {n} 处")
            else:
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(new_txt)
    print(f"{'[dry-run] ' if dry_run else ''}共 {changed_files} 文件 / {total} 处吞异常")
    return 0


def _inject_logger(txt: str) -> str:
    """在文件首个 import 行后插入 logger 定义（若缺失）。

    跳过所有 ``from __future__`` 导入（必须位于文件最顶部），
    在第一个普通 import 之后插入。
    """
    if _has_logger(txt):
        return txt
    # 定位第一个非 __future__ 的 import 行
    m = re.search(
        r"^(?:from\s+__future__[^\n]*\n)*(?:from\s+[^\n]*import[^\n]*\n|import\s+[^\n]*\n)",
        txt,
        re.MULTILINE,
    )
    if m:
        return txt[: m.end()] + "\n" + _LOGGER_BOOTSTRAP + txt[m.end():]
    # 无任何 import 行，加在文件头
    return _LOGGER_BOOTSTRAP + txt


if __name__ == "__main__":
    raise SystemExit(main())
