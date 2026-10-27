"""File-size ratchet — 单文件不得超过阈值，存量进 allowlist（可拆不可长）。

Why: after the mcp_hub / maop_loop split, new >800-line files appeared
(dashboard/services/data_service.py 1148, chat_service.py 1145, …) because
the split was a one-off push rather than a standing rule. This gate makes
growth visible: any file above ``MAX_LINES`` must be either split or listed
in ``ALLOWED_OVERSIZE`` with a reason and a split schedule.

Conventions (pinned so measurements agree with reviewers):

* line count = ``len(text.splitlines())``, read as ``utf-8-sig`` (17 files in
  this repo carry a UTF-8 BOM);
* ``migrations/`` is exempt wholesale — a 900-line Alembic revision is SQL
  data, and splitting it would make the migration harder to audit;
* allowlist is bidirectional: an entry that no longer exceeds the threshold
  must be removed (stale entries fail the gate, so the list can only shrink);
* a path may not be both exempt and allowlisted.

Usage::

    python scripts/check_file_size.py            # gate
    python scripts/check_file_size.py --max-lines 500   # local experiment
"""

from __future__ import annotations

import argparse
import pathlib
import sys

REPO_PY_ROOT = pathlib.Path(__file__).resolve().parent.parent
SCAN_ROOT = REPO_PY_ROOT / "maop"

MAX_LINES_DEFAULT = 800

#: Directory prefixes exempt from the size gate (see module docstring).
EXEMPT_PREFIXES: tuple[str, ...] = ("maop/migrations/",)

#: Files above the threshold at gate-introduction time (2026-10-12).
#: Each entry needs a reason and a split schedule; entries may only be REMOVED.
ALLOWED_OVERSIZE: dict[str, str] = {
    "maop/core/memory/plan_quality.py":
        "拆分排期：抽取器+模型 / 版本链 / 数据管道 / 评估器四块（参照 core/mcp/ 子包先例）。"
        "先决条件：批量训练特征要改成执行期观测来的真值（现在由 _infer_gates 重建），"
        "并为持久化模型加 feature_schema 标记 —— 详见 tests/test_plan_quality.py::"
        "test_online_learning_flips_structural_ranking 的 xfail 说明",
    "maop/core/agent/registry/preset_agents.py":
        "数据表为主的注册清单；如继续增长改为 YAML/JSON 数据文件",
    "maop/dashboard/_register_routes.py":
        "路由注册聚合点；拆分需先拆 router 模块（与 ROUTES 清单联动）",
    "maop/core/scheduling/distributed_scheduler.py":
        "分布式调度核心；拆分排期：结果流/失败检测/选工三块",
    "maop/dashboard/services/agent_service.py":
        "拆分排期：下一批 services 层拆分运动（chat_service 已完成）",
    "maop/dashboard/services/evolution_service.py":
        "拆分排期：下一批 services 层拆分运动（chat_service 已完成）",
}


def _rel(path: pathlib.Path) -> str:
    for base in (REPO_PY_ROOT, SCAN_ROOT.parent):
        try:
            return path.relative_to(base).as_posix()
        except ValueError:
            continue
    return path.as_posix()


def _line_count(path: pathlib.Path) -> int:
    return len(path.read_text(encoding="utf-8-sig").splitlines())


def _is_exempt(rel: str) -> bool:
    return any(rel.startswith(prefix) for prefix in EXEMPT_PREFIXES)


def scan(max_lines: int) -> tuple[dict[str, int], list[str]]:
    """Return ({rel_path: lines} above threshold, notes)."""
    oversize: dict[str, int] = {}
    notes: list[str] = []
    for path in sorted(SCAN_ROOT.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        rel = _rel(path)
        if _is_exempt(rel):
            continue
        try:
            lines = _line_count(path)
        except (OSError, UnicodeDecodeError) as exc:
            notes.append(f"unreadable: {rel} ({exc})")
            continue
        if lines > max_lines:
            oversize[rel] = lines
    return oversize, notes


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-lines", type=int, default=MAX_LINES_DEFAULT)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    failed = False

    # Mutual exclusion: an exempt path may not also be allowlisted.
    for rel in ALLOWED_OVERSIZE:
        if _is_exempt(rel):
            print(f"FAIL: {rel} is exempt (migrations) yet also allowlisted")
            failed = True

    oversize, notes = scan(args.max_lines)
    for note in notes:
        print(f"WARN: {note}")

    new = sorted(set(oversize) - set(ALLOWED_OVERSIZE))
    stale = sorted(set(ALLOWED_OVERSIZE) - set(oversize))

    if new:
        failed = True
        print(f"FAIL: {len(new)} file(s) above {args.max_lines} lines and not allowlisted:")
        for rel in new:
            print(f"  {rel}: {oversize[rel]} lines")
        print(
            "  处置：按职责拆分（参照 core/mcp/ 子包先例），或加进 "
            "ALLOWED_OVERSIZE 并写明理由与拆分排期。"
        )
    if stale:
        failed = True
        print(
            f"FAIL: {len(stale)} allowlist entr(ies) no longer above "
            f"{args.max_lines} lines — remove them:"
        )
        for rel in stale:
            print(f"  {rel}")

    if not args.quiet:
        print(
            f"oversize files: {len(oversize)} (allowlisted {len(ALLOWED_OVERSIZE)}), "
            f"threshold {args.max_lines}"
        )
    if failed:
        return 1
    print("OK: no unexpected oversized files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
