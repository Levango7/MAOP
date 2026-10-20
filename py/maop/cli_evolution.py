"""``maop evolution`` subcommands (self-evolution closed loop)."""

from __future__ import annotations

import json
import os
import sys
from typing import Any

from maop.config.home import ensure_runtime_root


def _root() -> str:
    root = ensure_runtime_root()
    os.environ["MAOP_ROOT_DIR"] = str(root)
    return str(root)


def cmd_evolution(args: list[str]) -> Any:
    """``maop evolution <subcommand>`` dispatcher.

    Subcommands
        inject-degradation            注入一条必然导致 VALIDATE 失败的劣化建议
                                      （AC-05 自动回滚的演练入口；此前该能力
                                      仅存在于测试代码里，生产无入口）
        trigger [--dry-run]           手动跑一轮自演化闭环
        status                        闭环状态机 + 最近 cycle + 统计
    """
    if not args:
        print("usage: maop evolution {inject-degradation|trigger|status}")
        return
    sub = args[0]
    if sub == "inject-degradation":
        _evolution_inject_degradation()
    elif sub == "trigger":
        dry_run = "--dry-run" in args[1:]
        _evolution_trigger(dry_run=dry_run)
    elif sub == "status":
        _evolution_status()
    else:
        print(f"unknown evolution subcommand: {sub}")
        sys.exit(1)


def _evolution_inject_degradation() -> None:
    """把一条必然失败的劣化建议**落盘**到建议队列（演练 AC-05 自动回滚）。

    T3.1: 此前这条路径只存在于测试代码（"生产无入口"）。现在落盘后紧跟
    ``maop evolution trigger`` 即完成一次真实演练：EVALUATE 放行 HIGH 级
    建议 → APPLY 真改 agents.yaml → VALIDATE 判定失败 → 真 ChangeTracker 回滚。
    """
    from maop.core.evolution.evolution_loop import EvolutionLoop

    loop = EvolutionLoop(root_dir=_root())
    suggestion = loop.inject_degradation_suggestion()
    print(
        "injected degradation suggestion (queued): "
        f"id={suggestion.id} type={suggestion.mutation_type} "
        f"severity={suggestion.severity} params={suggestion.mutation_params}"
    )
    print("next 'maop evolution trigger' will validate-fail and auto-rollback (<300s SLA)")


def _evolution_trigger(*, dry_run: bool) -> None:
    from maop.core.evolution.evolution_loop import EvolutionLoop

    loop = EvolutionLoop(root_dir=_root())
    report = loop.run_cycle(dry_run=dry_run, auto_rollback=True)
    print(json.dumps(report.summary(), ensure_ascii=False, indent=2))
    print(
        f"cycle={report.cycle_id} applied={report.suggestions_applied} "
        f"improved={report.validation_improved} rolled_back={report.rolled_back}"
    )


def _evolution_status() -> None:
    from maop.core.evolution.evolution_loop import EvolutionLoop

    loop = EvolutionLoop(root_dir=_root())
    print(json.dumps(loop.get_stats(), ensure_ascii=False, indent=2))
    for report in loop.get_cycle_history(limit=5):
        print(
            f"- {report.cycle_id} applied={report.suggestions_applied} "
            f"improved={report.validation_improved} rolled_back={report.rolled_back}"
        )
