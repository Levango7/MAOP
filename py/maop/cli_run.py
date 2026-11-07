"""``maop run`` command implementation (extracted from cli.py, M2).

拆分动因：cli.py 已在 file-size 门禁 800 行边缘；按 ``cli_evolution`` /
``cli_eval`` 先例把 ``cmd_run`` 抽为独立模块。门面：``maop.cli`` 继续
re-export ``cmd_run`` —— CLI 调用面与既有测试的 patch 目标
（``maop.cli.cmd_run``）保持不变。
"""

from __future__ import annotations

import logging
import sys
import time
from typing import Any

from maop.cli_run_outcome import explain_unsuccessful_run
from maop.core.agent.delegation.depth import (
    MAX_SELF_DELEGATION_DEPTH,
    current_depth,
)


def unknown_agent_warning(agent: str, root: Any) -> str | None:
    """``--agent`` 不在 agents.yaml 在册时返回告警文本，否则 None（2026-11-07）.

    动机（质量排查·静默换人）：``--agent claude`` 而 claude 不在册时，首轮
    dispatch 失败并沿 failover 链兜到别的 agent 执行——用户既白耗 turn，又
    「请求 claude 实际跑到 copilot」而毫不知情（实测 ``turns=4`` 的根因之一）。
    本函数只产出告警文本（调用方打印）；failover 机制本身保留（在册 agent
    的瞬时失败仍可兜底）。配置不可读等异常一律返回 None（告警尽力而为，
    绝不阻断运行）。
    """
    if not agent:
        return None
    try:
        from maop.config.loader import load_config

        cfg = load_config(root)
        known = set(getattr(cfg, "agents", {}) or {})
    except (OSError, ValueError, ImportError, TypeError) as exc:
        # 具体类型收窄（ADR-023 门禁：不新增宽口径 except Exception）——
        # loader 已把 YAML 解析错误吸收为告警，此处的现实失败面=路径/校验/
        # 导入/形状四类；真正的病态错误会由后续 loop 自身的加载路径暴露。
        logging.getLogger(__name__).debug("unknown-agent check skipped: %s", exc)
        return None
    if not known or agent in known:
        return None
    names = ", ".join(sorted(known)[:8])
    return (
        f"[WARN] --agent {agent!r} 不在 agents.yaml 在册条目中；"
        f"若该名无对应 CLI，将白耗 turn 后沿 failover 链兜到其他 agent 执行。"
        f"在册示例: {names} …"
    )


def cli_availability_error(agent: str, root: Any) -> str | None:
    """``--agent`` 在册但其 CLI 二进制不在 PATH 时返回错误文本（2026-11-08）.

    动机（首公里·CLI 未就绪）：``agents.yaml`` 注册了约 30 个外部 CLI，用户
    机器上通常只装了其中几个。被 ``--agent`` 指定的那个若没装，现状是走完
    plan → dispatch → ``FileNotFoundError`` → ``exit_code=-2``，用户先白等
    一轮编排，再拿到一句 ``Command not found``。把判断提到派发之前，零 token、
    零等待，并直接给出缺哪个可执行文件。

    只检查在册且 ``driver == "cli"`` 的条目。agent 不在册 / ``cli`` 为空 /
    配置不可读一律返回 None（交给调用方既有的告警与派发路径处理，本函数
    不改变那些分支的语义）。
    """
    if not agent:
        return None
    try:
        import shlex
        import shutil
        import sys as _sys

        from maop.config.loader import load_config

        cfg = load_config(root)
        entry = (getattr(cfg, "agents", {}) or {}).get(agent)
        if entry is None:
            return None
        driver = str(getattr(entry, "driver", "cli") or "cli")
        cli = str(getattr(entry, "cli", "") or "").strip()
    except (OSError, ValueError, ImportError, TypeError) as exc:
        logging.getLogger(__name__).debug("cli availability check skipped: %s", exc)
        return None
    if driver != "cli" or not cli:
        return None
    parts = shlex.split(cli, posix=(_sys.platform != "win32"))
    if not parts:
        return None
    base = parts[0]
    if base.lower() in {"python", "python3"}:
        # drivers.py 会把解释器换成 sys.executable，必然存在。
        return None
    if shutil.which(base):
        return None
    return (
        f"[BLOCK] --agent {agent!r} 已在 agents.yaml 注册，但其 CLI {base!r} "
        f"不在 PATH 中；已在派发前中止（未消耗 turn）。请先安装该 CLI，"
        f"或改用本机已安装的 agent（`maop validate` 可查看配置问题）。"
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

        _warn = unknown_agent_warning(agent, _runtime_root())
        if _warn:
            print(_warn, file=sys.stderr)
        # 派发前零成本自检：CLI 不存在就没必要进 plan/dispatch。
        _unavailable = cli_availability_error(agent, _runtime_root())
        if _unavailable:
            print(_unavailable, file=sys.stderr)
            sys.exit(1)

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
        # 事后核对：入口告警说的是「可能会兜到别的 agent」，这里给出**事实**。
        # 实测 `--agent no-such-agent` 会真的换一个 agent 执行并打印
        # `[OK] run 完成`——只看成功行的人会以为自己的 agent 跑过了。
        _actual_agent = str(getattr(result.execution, "agent", "") or "") if result.execution else ""
        if agent and _actual_agent and _actual_agent != agent:
            print(
                f"[WARN] 实际执行者={_actual_agent!r}，与 --agent {agent!r} 不一致"
                f"（已沿 failover 链兜底，本结果不代表 {agent} 的行为）",
                file=sys.stderr,
            )
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
