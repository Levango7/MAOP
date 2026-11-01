"""``maop run`` command implementation (extracted from cli.py, M2).

拆分动因：cli.py 已在 file-size 门禁 800 行边缘；按 ``cli_evolution`` /
``cli_eval`` 先例把 ``cmd_run`` 抽为独立模块。门面：``maop.cli`` 继续
re-export ``cmd_run`` —— CLI 调用面与既有测试的 patch 目标
（``maop.cli.cmd_run``）保持不变。
"""

from __future__ import annotations

import sys
import time
from typing import Any

from maop.cli_run_outcome import explain_unsuccessful_run
from maop.core.agent.delegation.depth import (
    MAX_SELF_DELEGATION_DEPTH,
    current_depth,
)


def _emit_fail(name: str, exc: BaseException | str) -> None:
    """输出命令失败信息到 stderr（与 ``maop.cli._emit_fail`` 同语义）。"""
    print(f"[FAIL] {name} 失败: {exc}", file=sys.stderr)


def cmd_run(task: str, depth: int = 0, agent: str = "", max_turns: int = 0) -> Any:
    """Run a task through Python-native MaopLoop.

    Depth arrives from ``--depth`` or from the inherited
    ``MAOP_DELEGATION_DEPTH`` marker (set by every spawned agent).  A
    subprocess gets a fresh counter, so the CLI-level cap is what stops
    a self-referential agent from forking itself indefinitely.  At the
    cap the run is refused with a non-zero exit instead of re-entering
    the orchestration loop.

    ``agent`` (``--agent``) pins the executing agent via
    ``MaopLoop.run(agent=...)`` (F6a forced_agent); empty keeps previous
    behaviour. Used by the k8s operator for MaopAgent CR resolution.

    ``max_turns`` (``--max-turns``, M2) caps orchestration turns
    (dispatch attempts) for this run; ``0`` = unlimited (default —
    previous behaviour). 实际消耗经 ``[USAGE] turns=N`` 行向调用方
    报告（k8s operator 解析该行写回 ``status.turns``）。
    """
    depth = max(depth, current_depth())
    if depth >= MAX_SELF_DELEGATION_DEPTH:
        print(
            f"[BLOCK] self-delegation depth {depth} reached hard cap "
            f"({MAX_SELF_DELEGATION_DEPTH}); refusing to re-enter orchestration loop",
            file=sys.stderr,
        )
        sys.exit(1)
    start = time.monotonic()
    try:
        import asyncio

        # 延迟导入：cli.py 是本模块的门面（re-export），模块级导入会成环；
        # 复用其 _runtime_root 也避免在 config-drift 门禁里重复一次 env 写。
        from maop.cli import _runtime_root
        from maop.maop_loop import MaopLoop

        loop = MaopLoop(root_dir=str(_runtime_root()))
        result = asyncio.run(loop.run(task, agent=agent, max_turns=max_turns or None))
        elapsed = (time.monotonic() - start) * 1000
        # 输出执行结果
        if result.trace_id:
            # Machine-readable handle: the dashboard parses this line to link a
            # control job to its run_events timeline (GET /api/runs/{trace_id}).
            print(f"[TRACE] {result.trace_id}")
        # M2/M3.1: turn 与 token 会计的机器可读出口——成功/失败路径都先打印，
        # 保证调用方（operator）总能读到；与 [TRACE] 同为稳定的单行协议。
        # tokens 为实测值（LLM 直连路径）；0/estimated = 全程无实测来源。
        print(f"[USAGE] turns={result.turns} tokens={result.tokens_used} source={result.tokens_source}")
        if result.execution:
            if result.execution.stdout:
                print(result.execution.stdout)
            if result.execution.error:
                print(f"[stderr] {result.execution.error}", file=sys.stderr)
        if result.block_reason:
            print(f"[BLOCK] 任务被阻塞：{result.block_reason}", file=sys.stderr)
            sys.exit(1)
        # 2026-10-24：`LoopResult.success` 是权威信号（exec_result 非空 + is_success + verify_ok），
        # 此前只把 block_reason 当失败，导致 agent 找不到时仍打印 `[OK] run 完成` 并退出 0 ——
        # 假成功。这里改为 fail-loud：非成功即非零退出，并说明原因。
        if not result.success:
            _emit_fail("run", explain_unsuccessful_run(result) or "未提供进一步信息")
            sys.exit(1)
        print(f"[OK] run 完成 ({elapsed:.1f}ms)")
    except ImportError as exc:
        # 依赖模块验证提示：明确提示缺失模块名称（小修复，不修改核心逻辑）
        _emit_fail("run", f"缺失依赖模块（可能缺失：maop.migrations.sqlite_to_pg 或相关依赖）: {exc}")
        sys.exit(1)
    except Exception as exc:
        _emit_fail("run", exc)
        sys.exit(1)
