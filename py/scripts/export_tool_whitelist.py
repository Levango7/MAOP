"""导出工具白名单 allow 清单（阶段二：从部署环境 DB 生成初始 allow）。

用法（部署环境，MAOP 已初始化 tools 表）:
  python export_tool_whitelist.py                 # 生成 config/tool_whitelist.generated.yaml
  python export_tool_whitelist.py --out /path/x.yaml
  python export_tool_whitelist.py --review        # 仅输出高危命令清单，不写文件

行为:
  1. 连接统一 DB（get_db_path("tool_manager")）读取 tools 表
  2. enabled=1 的工具进入 allow（id 精确匹配）
  3. 按仓库 config/tool_whitelist.yaml 的 deny 模式集扫描命令，
     命中者默认排除出 allow（deny 优先，放行无效）并注释说明
  4. 生成 yaml：mode: audit + allow + deny（保留高危规则）

产出由人工评审后合并到 config/tool_whitelist.yaml 再切 enforce。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 脚本位于 py/scripts/，上上级是仓库根；上级 py/ 需入 sys.path 以 import maop
SCRIPTS_DIR = Path(__file__).resolve().parent
PY_DIR = SCRIPTS_DIR.parent  # py/
ROOT = PY_DIR.parent          # 仓库根
sys.path.insert(0, str(PY_DIR))

import sqlite3  # noqa: E402

from maop.core.backends.db_utils import get_db_path  # noqa: E402

CONFIG_YAML = ROOT / "config" / "tool_whitelist.yaml"
DEFAULT_OUT = ROOT / "config" / "tool_whitelist.generated.yaml"

# 内置兜底 deny 模式集（仓库 yaml 不可读时使用，与 yaml 保持同步）
_FALLBACK_DENY_PATTERNS = [
    "rm*",
    "mkfs*",
    "dd*",
    "shutdown*",
    "reboot*",
    "halt*",
    "poweroff*",
    "sudo*",
]


def _load_deny_patterns() -> list[str]:
    """从仓库 config/tool_whitelist.yaml 读取 deny 规则中的 pattern 列表。"""
    try:
        import yaml

        with open(CONFIG_YAML, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        deny = data.get("deny") or []
        patterns = []
        for rule in deny:
            if isinstance(rule, dict) and rule.get("pattern"):
                patterns.append(str(rule["pattern"]))
            elif isinstance(rule, str):
                patterns.append(rule)
        if patterns:
            return patterns
    except Exception as exc:  # noqa: BLE001 - 兜底不阻断导出
        print(f"[export_tool_whitelist] WARN: 读取 {CONFIG_YAML.name} 失败: {exc}；使用内置 deny 集")
    return list(_FALLBACK_DENY_PATTERNS)


def _fetch_tools() -> list[dict]:
    """读取统一 DB 中全部工具（id/name/command/category/enabled）。"""
    db_path = get_db_path("tool_manager")
    print(f"[export_tool_whitelist] DB: {db_path}")
    if not db_path.exists():
        print(f"[export_tool_whitelist] ERROR: DB 不存在（{db_path}）。"
              "请确认已在部署环境初始化 tools 表。")
        sys.exit(2)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT id, name, command, category, enabled FROM tools ORDER BY category, name"
        ).fetchall()
    except sqlite3.OperationalError as exc:
        print(f"[export_tool_whitelist] ERROR: 读取 tools 表失败: {exc}")
        sys.exit(2)
    finally:
        conn.close()
    return [dict(r) for r in rows]


def _flag_high_risk(tools: list[dict], deny_patterns: list[str]) -> dict[str, str]:
    """返回 {tool_id: 命中的 deny pattern}。用 fnmatch 匹配 command。"""
    import fnmatch

    flagged: dict[str, str] = {}
    for t in tools:
        cmd = t.get("command") or ""
        for pat in deny_patterns:
            if fnmatch.fnmatchcase(cmd, pat) or fnmatch.fnmatchcase(t["id"], pat):
                flagged[t["id"]] = pat
                break
    return flagged


def _render_yaml(tools: list[dict], deny_patterns: list[str], flagged: dict[str, str]) -> str:
    """生成 yaml 文本（含注释）。"""
    lines = [
        "# MAOP 工具白名单 — 由 py/scripts/export_tool_whitelist.py 自动生成",
        "#",
        "# 用法：部署环境执行后，人工评审下方 allow 清单，确认无误后",
        "# 合并到 config/tool_whitelist.yaml（mode 改为 enforce 即阶段三）。",
        "#",
        "# 注意：命中 deny 模式集的工具已自动排除出 allow（deny 优先），",
        "# 如需放行必须先移除对应 deny 规则。",
        "",
        "mode: audit",
        "",
        "allow:",
    ]
    allowed = 0
    for t in tools:
        if not t.get("enabled"):
            continue
        tid = t["id"]
        if tid in flagged:
            lines.append(f"  # !! 高危: 命中 deny 模式 {flagged[tid]!r} (command={t.get('command', '')!r})")
            lines.append(f"  # - id: \"{tid}\"   # 已排除，需先移除 deny 规则")
            continue
        lines.append(f"  - id: \"{tid}\"")
        allowed += 1
    if allowed == 0:
        lines.append("  # (无 enabled 工具)")
    lines.append("")
    lines.append("deny:")
    for pat in deny_patterns:
        lines.append(f"  - pattern: \"{pat}\"")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="导出工具白名单 allow 清单")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="输出 yaml 路径")
    parser.add_argument("--review", action="store_true", help="仅输出高危命令清单，不写文件")
    args = parser.parse_args()

    deny_patterns = _load_deny_patterns()
    tools = _fetch_tools()
    if not tools:
        print("[export_tool_whitelist] WARN: tools 表为空，无可导出工具。")
        if not args.review:
            print(f"[export_tool_whitelist] 已生成空模板: {args.out}")
            Path(args.out).parent.mkdir(parents=True, exist_ok=True)
            Path(args.out).write_text(_render_yaml([], deny_patterns, {}), encoding="utf-8")
        return 0

    flagged = _flag_high_risk(tools, deny_patterns)

    if args.review:
        print("=== 高危命令评审清单（命中 deny 模式，已排除出 allow）===")
        if not flagged:
            print("  (无)")
        for tid, pat in sorted(flagged.items()):
            cmd = next((t.get("command", "") for t in tools if t["id"] == tid), "")
            print(f"  {tid}: command={cmd!r} -> 命中 deny pattern {pat!r}")
        print("=== 其余 enabled 工具将全部进入 allow ===")
        return 0

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(_render_yaml(tools, deny_patterns, flagged), encoding="utf-8")
    total = sum(1 for t in tools if t.get("enabled"))
    print(f"[export_tool_whitelist] 完成: 工具 {len(tools)} 个, enabled {total} 个, "
          f"高危排除 {len(flagged)} 个")
    print(f"[export_tool_whitelist] 输出: {out_path}")
    print("[export_tool_whitelist] 下一步: 人工评审后合并到 config/tool_whitelist.yaml, "
          "按 docs/tool-whitelist-enforce-checklist.md 切 enforce")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
