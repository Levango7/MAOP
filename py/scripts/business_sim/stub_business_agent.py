"""M-SIM 确定性业务 stub agent。

被 agents.yaml 的 ``sim-*`` 条目以 ``cli/cli_args`` 方式调用（与真实 CLI
驱动同一条 subprocess 路径），零外部 LLM / 网络：

- 输出确定性：同 ``--task`` 恒定同输出（token 数 = crc32(task) 取模，
  不用 random / 时间）；
- 按协议回填计量：``[USAGE] turns=N tokens=T source=estimated``（与
  ``maop/cli_run.py`` 的协议行同形，供 MaopLoop / operator 解析）；
- 故障注入：``--fail-on SUBSTR`` 命中即非零退出（演练失败分支用）。

用法::

    python stub_business_agent.py --task "工单 1042 分诊" [--fail-on BUG]
"""
from __future__ import annotations

import argparse
import sys
import zlib


def _canned_reply(task: str, role: str) -> str:
    """确定性剧本台词：按角色给一行可断言的固定格式回执。"""
    digest = zlib.crc32(task.encode("utf-8")) % 100000
    return f"[sim:{role}] ack task={digest} :: {task[:60]}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="M-SIM deterministic stub agent")
    parser.add_argument("--task", required=True, help="task text")
    parser.add_argument("--role", default="worker", help="sim role name (for output tag)")
    parser.add_argument("--turns", type=int, default=1, help="turns to report")
    parser.add_argument("--tokens", type=int, default=0, help="token override (0 = derive from task)")
    parser.add_argument("--fail-on", default="", help="exit 2 when this substring appears in --task")
    ns = parser.parse_args(argv)

    if ns.fail_on and ns.fail_on in ns.task:
        print(f"[sim:{ns.role}] FAIL-INJECTED (matched --fail-on {ns.fail_on!r})", file=sys.stderr)
        return 2

    tokens = ns.tokens if ns.tokens > 0 else 100 + zlib.crc32(ns.task.encode("utf-8")) % 900
    print(_canned_reply(ns.task, ns.role))
    print(f"[USAGE] turns={ns.turns} tokens={tokens} source=estimated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
