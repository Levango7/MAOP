"""``maop workflow`` 子命令 —— M-X2 E2-a 生产接线入口（cli_eval 先例）.

用法::

    maop workflow run <name> [--backend temporal] [--timeout N]

后端决议：``--backend`` 显式 > ``MAOP_ORCHESTRATION_BACKEND``（settings
收敛点，见 ``config.settings.orchestration_backend``）> legacy 规划路径。
temporal 后端经 ``orchestration.wiring.run_workflow`` 提交 Temporal
适配器（节点执行体 = ``maop run`` 子进程，E3-a）；legacy 保持
``maop_plan.execute_workflow`` 纯规划语义（行为逐字节不变）。
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any


def cmd_workflow(argv: list[str]) -> Any:
    """``maop workflow`` 入口（argv 为子命令后的参数列表）。"""
    parser = argparse.ArgumentParser(
        prog="maop workflow", description="Workflow execution (M-X2)"
    )
    sub = parser.add_subparsers(dest="wf_action", required=True)
    run_parser = sub.add_parser("run", help="Execute a named workflow from config")
    run_parser.add_argument("name", help="Workflow name in config.workflows")
    run_parser.add_argument(
        "--backend", default="",
        help="'' (legacy planning path) | 'temporal' "
        "(default: MAOP_ORCHESTRATION_BACKEND)",
    )
    run_parser.add_argument(
        "--timeout", type=float, default=None,
        help="Overall workflow timeout in seconds (temporal backend)",
    )
    args = parser.parse_args(argv)

    from maop.config.loader import load_config
    from maop.orchestration.wiring import run_workflow_sync

    try:
        result = run_workflow_sync(
            args.name, load_config(), backend=args.backend, timeout_s=args.timeout,
        )
    except ValueError as exc:
        print(f"[FAIL] workflow 失败: {exc}", file=sys.stderr)
        return 2

    print(
        json.dumps(
            {
                "workflow": result.workflow_name,
                "steps_total": result.steps_total,
                "steps_planned": result.steps_planned,
                "steps_completed": result.steps_completed,
                "steps_skipped": result.steps_skipped,
                "step_results": [r.model_dump() for r in result.step_results],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    # 退出码按后端语义：legacy 规划路径做完规划即 0（"不执行任何 agent"
    # 是它的合同，completed=0 如实出现在 JSON）；temporal 后端以
    # completed==planned 为成功判据。
    from maop.orchestration.wiring import resolve_backend

    if resolve_backend(override=args.backend) == "temporal":
        return 0 if (
            result.steps_planned > 0
            and result.steps_completed == result.steps_planned
        ) else 1
    return 0
