"""M-X2 M0 验收 demo：3 节点 WorkflowDef → Temporal workflow → dev server 跑通一次.

章程（ROADMAP「M-X2 多后端编排器适配」）验收原文：
"demo workflow（3 节点）在 Temporal dev server 上跑通一次"。

用法（本地验收配方）::

    pip install temporalio                       # 可选依赖（不进 pyproject）
    temporal server start-dev                    # 或 docker run temporalio/auto-setup
    python scripts/temporal_m0_demo.py           # 默认 localhost:7233

DAG 形状（3 节点，验证分支并行）::

    0 规划 ──┬─> 1 分支 A（与 2 同层并行）
             └─> 2 分支 B

退出码：全部节点 success → 0；否则 1。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from maop.config.loader import WorkflowDef, WorkflowStepDef
from maop.orchestration.temporal_adapter import TemporalAdapter

DEMO_STEPS = [
    WorkflowStepDef(agent="planner", task="规划：拆解需求并产出 DAG", timeout_s=20),
    WorkflowStepDef(
        agent="worker-a", task="分支 A：实现冒烟脚本", depends_on=["0"], timeout_s=20,
    ),
    WorkflowStepDef(
        agent="worker-b", task="分支 B：准备验收清单", depends_on=["0"], timeout_s=20,
    ),
]


def _recording_executor(node) -> dict:  # noqa: ANN001 — demo 脚本从简
    """M0 记录型执行体：回显节点语义（真实 agent 调度 = 生产集成里程碑）。"""
    return {"step_id": node.step_id, "agent": node.agent, "status": "success"}


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--address", default=None, help="Temporal 前端地址（默认适配器内置）")
    parser.add_argument("--task-queue", default="maop-m0-demo")
    args = parser.parse_args()

    workflow = WorkflowDef(steps=DEMO_STEPS, description="m0-3-node-demo")
    adapter = (
        TemporalAdapter(server_address=args.address, task_queue=args.task_queue)
        if args.address
        else TemporalAdapter(task_queue=args.task_queue)
    )

    compiled = adapter.compile_workflow(workflow)
    print(f"[compile] backend={compiled.backend} nodes={len(compiled.nodes)}")
    print(f"[compile] levels={compiled.levels}")
    print("[compile] artifact（前 6 行）:")
    for line in compiled.artifact.splitlines()[:6]:
        print(f"    {line}")

    print(f"[run] connecting {adapter.server_address} ns={adapter.namespace} …")
    result = await adapter.run(compiled, executor=_recording_executor, timeout_s=60)
    print(f"[run] levels={result['levels']}")
    for node_id, outcome in result["nodes"].items():
        print(f"[run] node {node_id}: {json.dumps(outcome, ensure_ascii=False)}")

    all_ok = all(
        o.get("status") == "success" for o in result["nodes"].values()
    ) and result["levels"] == 2
    print("[result]", "SUCCESS" if all_ok else "FAILED")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
