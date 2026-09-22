"""对比度审计：解析 tokens.css / themes.css，计算关键"文字 on 背景"组合的 WCAG 比值。

临时审计脚本，用完即删。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

STYLES = Path(__file__).resolve().parent.parent / "src" / "styles"


def parse_vars(text: str) -> dict[str, str]:
    """提取 CSS 自定义属性（含 var() 引用，后续解析）。"""
    out: dict[str, str] = {}
    for m in re.finditer(r"--([a-zA-Z0-9-]+)\s*:\s*([^;]+);", text):
        out[m.group(1)] = m.group(2).strip()
    return out


def parse_rgb(value: str) -> tuple[int, int, int, float] | None:
    v = value.strip()
    h = re.fullmatch(r"#([0-9a-fA-F]{6})", v)
    if h:
        n = int(h.group(1), 16)
        return (n >> 16) & 255, (n >> 8) & 255, n & 255, 1.0
    h3 = re.fullmatch(r"#([0-9a-fA-F]{3})", v)
    if h3:
        s = h3.group(1)
        n = int(s[0] * 2 + s[1] * 2 + s[2] * 2, 16)
        return (n >> 16) & 255, (n >> 8) & 255, n & 255, 1.0
    m = re.fullmatch(r"rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)\s*(?:,\s*([\d.]+)\s*)?\)", v)
    if m:
        return (
            int(float(m.group(1))),
            int(float(m.group(2))),
            int(float(m.group(3))),
            float(m.group(4)) if m.group(4) is not None else 1.0,
        )
    return None


def resolve(name: str, table: dict[str, str], depth: int = 0) -> str:
    """解析 var() 引用链，返回最终字面量。"""
    if depth > 12:
        return table.get(name, "")
    v = table.get(name)
    if v is None:
        return ""
    m = re.fullmatch(r"var\(\s*--([a-zA-Z0-9-]+)\s*(?:,\s*(.+?)\s*)?\)", v)
    if m:
        return resolve(m.group(1), table, depth + 1)
    return v


def over(fg: tuple[int, int, int, float], bg: tuple[int, int, int, float]) -> tuple[int, int, int]:
    """把带 alpha 的前景合成到背景上。"""
    a = fg[3]
    b = bg[3]
    if a >= 1.0:
        return fg[0], fg[1], fg[2]
    # 背景若也半透明，先假设其已合成到不透明底（这里按自身处理）
    return (
        round(fg[0] * a + bg[0] * (1 - a)),
        round(fg[1] * a + bg[1] * (1 - a)),
        round(fg[2] * a + bg[2] * (1 - a)),
    )


def lum(c: tuple[int, int, int]) -> float:
    def f(ch: int) -> float:
        s = ch / 255
        return s / 12.92 if s <= 0.03928 else ((s + 0.055) / 1.055) ** 2.4

    r, g, b = f(c[0]), f(c[1]), f(c[2])
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def ratio(fg: tuple[int, int, int], bg: tuple[int, int, int]) -> float:
    l1, l2 = lum(fg), lum(bg)
    if l1 < l2:
        l1, l2 = l2, l1
    return (l1 + 0.05) / (l2 + 0.05)


# 需要检查的「文字令牌 on 背景令牌」组合
PAIRS: list[tuple[str, str, str]] = [
    # (文字令牌, 背景令牌, 说明)
    ("text", "bg", "正文 / 页面底"),
    ("text", "surface", "正文 / 卡片"),
    ("text", "surface-2", "正文 / 二级面"),
    ("text2", "bg", "次要文字 / 页面底"),
    ("text2", "surface", "次要文字 / 卡片"),
    ("text-muted", "bg", "muted / 页面底"),
    ("text-muted", "surface", "muted / 卡片"),
    ("text-faint", "bg", "faint / 页面底"),
    ("text-faint", "surface", "faint / 卡片"),
    ("text3", "bg", "legacy text3 / 页面底"),
    ("brand", "bg", "品牌色文字 / 页面底"),
    ("brand", "surface", "品牌色文字 / 卡片"),
    ("success", "bg", "成功色文字 / 页面底"),
    ("success", "surface", "成功色文字 / 卡片"),
    ("warn", "bg", "警告色文字 / 页面底"),
    ("warn", "surface", "警告色文字 / 卡片"),
    ("fail", "bg", "失败色文字 / 页面底"),
    ("fail", "surface", "失败色文字 / 卡片"),
    ("danger", "bg", "危险色文字 / 页面底"),
    ("danger", "surface", "危险色文字 / 卡片"),
    ("info", "bg", "信息色文字 / 页面底"),
    ("info", "surface", "信息色文字 / 卡片"),
    ("accent-warm", "bg", "琥珀强调 / 页面底"),
    ("accent-warm", "surface", "琥珀强调 / 卡片"),
    ("success-strong", "success-bg", "状态强色 on 状态底"),
    ("warn-strong", "warn-bg", "状态强色 on 状态底"),
    ("fail-strong", "fail-bg", "状态强色 on 状态底"),
    ("info-strong", "info-bg", "状态强色 on 状态底"),
    ("neutral-strong", "neutral-bg", "中性强色 on 中性底"),
    ("brand-contrast", "brand", "按钮文字 on 品牌底"),
    ("brand-contrast", "fail", "按钮文字 on 危险底"),
    ("brand-contrast", "success", "按钮文字 on 成功底"),
    ("brand-contrast", "warn", "按钮文字 on 警告底"),
    ("text", "bg-code", "正文 / 代码块底"),
    ("text-code", "bg-code", "代码文字 / 代码块底"),
]


def main() -> int:
    root = parse_vars((STYLES / "tokens.css").read_text(encoding="utf-8"))
    light_raw = (STYLES / "themes.css").read_text(encoding="utf-8")
    # 只取 [data-theme="light"] 块
    m = re.search(r'\[data-theme="light"\]\s*\{(.*?)\n\}', light_raw, re.S)
    light = dict(root)
    if m:
        light.update(parse_vars(m.group(1)))

    themes = (("DARK (:root)", root), ("LIGHT ([data-theme=light])", light))
    fails = 0
    warns = 0

    for label, table in themes:
        print(f"\n{'=' * 74}\n{label}\n{'=' * 74}")
        print(f"  {'组合':<38} {'比值':>7}  {'AA正文':>6} {'AA大字':>6}")
        print(f"  {'-' * 38} {'-' * 7}  {'-' * 6} {'-' * 6}")
        for fg_name, bg_name, desc in PAIRS:
            fg_raw, bg_raw = resolve(fg_name, table), resolve(bg_name, table)
            fg_c, bg_c = parse_rgb(fg_raw), parse_rgb(bg_raw)
            if fg_c is None or bg_c is None:
                print(f"  {desc:<38} {'解析失败':>7}   ({fg_name}={fg_raw!r} bg={bg_raw!r})")
                continue
            # 背景先视作不透明（若半透明则合成到页面底 --bg）
            if bg_c[3] < 1.0:
                base = parse_rgb(resolve("bg", table)) or (255, 255, 255, 1.0)
                bg_c = (*over(bg_c, base), 1.0)
            composed = over(fg_c, bg_c)
            r = ratio(composed, (bg_c[0], bg_c[1], bg_c[2]))
            aa_body = r >= 4.5
            aa_large = r >= 3.0
            mark = "OK" if aa_body else ("大字OK" if aa_large else "FAIL")
            if not aa_body:
                fails += 1
                if aa_large:
                    warns += 1
            print(
                f"  {desc:<38} {r:>6.2f}:1  "
                f"{'✓' if aa_body else '✗':>6} {'✓' if aa_large else '✗':>6}   {mark}"
            )

    print(f"\n{'=' * 74}")
    print(f"低于 AA 正文标准(4.5:1)的组合: {fails} 个（其中 {warns} 个仍满足大字 3:1）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
