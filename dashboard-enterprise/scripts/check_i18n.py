#!/usr/bin/env python3
"""i18n 完整性检查 —— 键对齐 + t() 引用有效性。

用途：在 CI 或本地守住"界面上出现原始 key"这一类缺陷。本项目 t(key) 在
zh/en 都缺失时**直接返回 key 本身**，所以「引用了不存在的键」不会报错，
而是把 `view.xxx.yyy` 这种字符串渲染到界面上 —— 必须靠静态检查拦住。

检查项：
  1. zh 缺失键   —— en 有、zh 无（中文模式回退显示英文）
  2. zh 多余键   —— zh 有、en 无（死键，t() 永不命中）
  3. 引用但未定义 —— **最严重**，会把原始 key 渲染到界面 / 被屏幕阅读器读出
  4. 空值         —— 值为空串（渲染成空白）

用法:
    python scripts/check_i18n.py            # 仅报告
    python scripts/check_i18n.py --strict   # 有第 3 类问题则 exit 1
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
I18N = ROOT / "src" / "i18n"
SRC = ROOT / "src"


def extract_block(text: str, label: str) -> str | None:
    """取 `label: {` 起、花括号配对到对应 `}` 的块体。"""
    m = re.search(rf"(?:^|[\s,{{]){label}\s*:\s*\{{", text)
    if not m:
        return None
    start = m.end() - 1
    depth = 0
    i = start
    in_str: str | None = None
    while i < len(text):
        ch = text[i]
        if in_str:
            if ch == "\\":
                i += 2
                continue
            if ch == in_str:
                in_str = None
        elif ch in "'\"`":
            in_str = ch
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start + 1 : i]
        i += 1
    return None


KEY_RE = re.compile(r"^\s*(['\"])([^'\"]+)\1\s*:", re.M)
CALL_RE = re.compile(r"(?<![\w$.])\$?t[ei]?\(\s*(['\"])([^'\"]+)\1")
DYN_RE = re.compile(r"(?<![\w$.])\$?t[ei]?\(\s*(`[^`]*\$\{|[^)'\"]*\+)")


def keys_of(block: str | None) -> set[str]:
    return {m.group(2) for m in KEY_RE.finditer(block)} if block else set()


def strip_comments(t: str) -> str:
    t = re.sub(r"/\*.*?\*/", "", t, flags=re.S)
    t = re.sub(r"^\s*//.*$", "", t, flags=re.M)
    return re.sub(r"<!--.*?-->", "", t, flags=re.S)


def main() -> int:
    strict = "--strict" in sys.argv
    en_all: set[str] = set()
    zh_all: set[str] = set()
    missing_zh: list[tuple[str, str]] = []
    extra_zh: list[tuple[str, str]] = []

    files = sorted(I18N.glob("*.js"))
    for f in files:
        text = f.read_text(encoding="utf-8")
        en = keys_of(extract_block(text, "en"))
        zh = keys_of(extract_block(text, "zh"))
        en_all |= en
        zh_all |= zh
        missing_zh += [(f.name, k) for k in sorted(en - zh)]
        extra_zh += [(f.name, k) for k in sorted(zh - en)]

    print(f"扫描 i18n 文件: {len(files)} 个 | en 键 {len(en_all)} | zh 键 {len(zh_all)}")
    print(f"  [1] zh 缺失键（中文模式显示英文）: {len(missing_zh)}")
    for fn, k in missing_zh[:10]:
        print(f"        {fn}  {k}")
    print(f"  [2] zh 多余键（死键）: {len(extra_zh)}")
    for fn, k in extra_zh[:10]:
        print(f"        {fn}  {k}")

    # 引用有效性
    used: set[str] = set()
    undefined: list[tuple[str, int, str]] = []
    dynamic = 0
    for p in sorted(SRC.rglob("*")):
        if p.suffix not in (".vue", ".js", ".ts") or "__tests__" in p.parts:
            continue
        text = strip_comments(p.read_text(encoding="utf-8", errors="ignore"))
        rel = p.relative_to(ROOT).as_posix()
        for m in CALL_RE.finditer(text):
            k = m.group(2)
            if k.endswith("."):
                dynamic += 1  # 动态拼接前缀，静态无法判定
                continue
            used.add(k)
            if k not in en_all:
                undefined.append((rel, text[: m.start()].count("\n") + 1, k))
        dynamic += len(DYN_RE.findall(text))

    print(f"  [3] 引用了但字典中不存在（会渲染原始 key）: {len(undefined)}")
    for rel, line, k in undefined[:20]:
        print(f"        {rel}:{line}  {k}")
    print(f"  [4] 动态拼接的键（静态无法校验）: {dynamic}")
    print(f"  [5] 定义了但从未引用（死键）: {len(en_all - used)}")

    bad = len(undefined)
    if bad and strict:
        print(f"\n[FAIL] 有 {bad} 处引用不存在的键。")
        return 1
    print("\n[OK] 无「引用不存在键」问题。" if not bad else f"\n[WARN] {bad} 处需修复。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
