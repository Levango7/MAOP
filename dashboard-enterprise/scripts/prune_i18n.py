#!/usr/bin/env python3
"""删除 i18n 死键（定义了但全项目零引用）。

## 安全判据（保守口径 —— 宁可少删，不可误删）

一个键只有在**下列全部条件**成立时才可删：

1. 在 src/ + e2e/ + public/ + docs/ + scripts/ 的**任何**位置（含注释以外的
   所有字符串字面量）都不出现；
2. 不落在动态拼接的命名空间下（如 ``t('view.quotas.' + k)`` 会在运行时拼出键）；
3. 源码中不存在多段模板拼接（``t(`view.${a}.${b}`)``）—— 若有则整体放弃。

## 为什么不能只看 t('key')

实测踩过两个坑，任一都会造成**界面显示原始 key** 的严重回归：

- ``src/nav.js`` 把导航键存成**数据结构里的裸字符串**（``section: 'nav.agents'``），
  由 ``t(item.section)`` 消费。只匹配 ``t('key')`` 会把 90 个导航键误判为死键
  —— 删掉整个侧边栏就崩了。
- ``e2e/`` 的 Playwright 测试引用 51 个键用于定位界面文案。只扫 src 会漏掉。

用法:
    python scripts/prune_i18n.py --dry-run   # 只报告（默认）
    python scripts/prune_i18n.py --apply     # 实际删除
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
I18N = ROOT / "src" / "i18n"

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_i18n import extract_block, keys_of, strip_comments  # noqa: E402

# ── 动态拼接的命名空间 —— 其下键可能被运行时拼出来，禁止删除 ──────────────
# 生成方式：扫描 src/ + e2e/ 下 (a) `'prefix.' + var` 拼接、(b) `prefix.${var}`
# 模板字面量两种写法。
#
# 踩过的坑（2026-09-22）：只匹配 `t(` 紧跟模板的写法会漏掉这一类——
#     const key = `view.routing.stage.${stage}`;   // 先赋变量
#     t(key)                                        // 再传入
# 漏掉 `view.routing.stage.` 与 `view.scheduling.status.` 后，删除导致
# RoutingRules.test.js / Scheduling.test.js 两个测试失败。故前缀检测必须
# **无视是否紧跟 t(**，只要模板以 i18n 命名空间开头就纳入保护。
DYN_NAMESPACES = (
    # 模板字面量 `` `prefix.${var}` ``
    "view.agentRegistry.auth.",
    "view.agentRegistry.billing.",
    "view.agentRegistry.capability.",
    "view.evolutionHistory.decision.",
    "view.evolve.milestones.type.",
    "view.kg.filter.type.",
    "view.routing.stage.",
    "view.scheduling.status.",
    # 字符串拼接 `` 'prefix.' + var ``
    "view.apikeys.scopeGroup.",
    "view.overview.edition.",
    "view.quotas.",
)

SCAN_ROOTS = ("src", "e2e", "public", "docs", "scripts")
SCAN_EXTS = (".vue", ".js", ".ts", ".html", ".json")

# 任何形如 i18n 键的字符串字面量（不限于 t() 内）
LITERAL_RE = re.compile(r"['\"`]([a-zA-Z][a-zA-Z0-9_]*(?:\.[a-zA-Z0-9_]+)+)['\"`]")
# 多段模板拼接的**键**：形如 `view.${a}.${b}`。只对以 i18n 命名空间开头的
# 模板告警 —— 否则 CSS transform / rgba() / 元素 id 等（本仓库有 90 处）
# 会误触发中止。已验证这些非键模板与 i18n 无关。
MULTI_VAR_RE = re.compile(
    r"`(?:view|nav|common|status|auth|footer|settings|a11y|action)\."
    r"[^`]*\$\{[^}]*\}[^`]*\$\{"
)
ENTRY_RE = re.compile(r"^\s*(['\"])(?P<key>[^'\"]+)\1\s*:\s*(?P<val>.+?),?\s*$")


def collect() -> tuple[set[str], set[str]]:
    en: set[str] = set()
    for f in I18N.glob("*.js"):
        en |= keys_of(extract_block(f.read_text(encoding="utf-8"), "en"))

    mentioned: set[str] = set()
    multi_var = 0
    for name in SCAN_ROOTS:
        root = ROOT / name
        if not root.exists():
            continue
        for p in root.rglob("*"):
            if p.suffix not in SCAN_EXTS or "__tests__" in p.parts:
                continue
            if I18N in p.parents:
                continue  # 排除字典自身，否则每个键都"被提及"
            t = strip_comments(p.read_text(encoding="utf-8", errors="ignore"))
            mentioned |= set(LITERAL_RE.findall(t))
            multi_var += len(MULTI_VAR_RE.findall(t))

    if multi_var:
        print(f"[中止] 发现 {multi_var} 处多段模板拼接，无法保证静态分析完备。")
        return en, set()

    dead = en - mentioned
    removable = {k for k in dead if not any(k.startswith(d) for d in DYN_NAMESPACES)}
    return dead, removable


def main() -> int:
    apply = "--apply" in sys.argv
    dead, removable = collect()
    print(f"en 键总数: {len(dead) + 0 if not dead else len(dead) + 0}")
    print(f"死键: {len(dead)}；其中落在动态命名空间（保留）: {len(dead) - len(removable)}")
    print(f"可安全删除: {len(removable)}{'' if apply else '（dry-run）'}")
    print()

    total = 0
    for f in sorted(I18N.glob("*.js")):
        lines = f.read_text(encoding="utf-8").split("\n")
        keep, removed = [], 0
        for ln in lines:
            m = ENTRY_RE.match(ln)
            if m and m.group("key") in removable:
                removed += 1
                continue
            keep.append(ln)
        if removed:
            print(f"  {f.name:<34} -{removed}")
            total += removed
            if apply:
                f.write_text("\n".join(keep), encoding="utf-8")

    print(f"\n合计删除 {total} 条")
    if not apply:
        print("（dry-run：加 --apply 才写入）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
