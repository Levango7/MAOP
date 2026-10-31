"""MAOP Kubernetes Operator（M-T3.3 M0）—— MaopTask → `maop run` 子进程 → status 回写.

M0 边界（章程见 ROADMAP「M-T3.3」）：
- **M0 已实现**：spec 校验（与 ``deploy/k8s/operator/crds/maoptask.yaml`` 对齐）、
  命令构造（``maop run <prompt>``）、Reconcile 状态机（Pending → Running →
  Succeeded/Failed/TimedOut/Cancelled）、status patch 形状（phase/conditions/
  startTime/completionTime，与 CRD status 子模式对齐）、kopf 事件层（可选导入，
  镜像内安装）。
- **M1 已实现**：``spec.agent`` → MaopAgent CR 解析 + agent pinning（``resolver``
  注入；CR 存在 → 命令带 ``--agent``（MaopLoop forced_agent）并置
  ``AgentResolved=True`` 条件；未声明 → M0 行为 + ``False`` 条件注记）。
- **M2 已实现**：``spec.maxTurns`` → 命令恒带 ``--max-turns``（一个 turn = 一次
  dispatch 尝试；loop 侧硬上限 + 计数）；子进程 ``[USAGE] turns=N`` 解析回写
  ``status.turns``。MaopAgent 解析到的 model/tools 仍不消费。
- **未实现（章程明示顺延）**：真集群 kind 验证；tokensUsed 与配额/多租户；
  插件（CRD 标注 planned）。

分层：``spec``/``reconcile`` 是纯函数（无 k8s 依赖）；``controller`` 用注入的
gateway/runner/resolver（测试用 fake）；``handlers``/``__main__`` 是 kopf 薄层。
"""

from __future__ import annotations

from maop.operator.controller import OperatorController
from maop.operator.gateway import (
    AgentResolver,
    AsyncSubprocessRunner,
    KubernetesStatusGateway,
)
from maop.operator.reconcile import plan_finish, plan_launch
from maop.operator.spec import InvalidSpecError, MaopTaskSpec, command_for

__all__ = [
    "AgentResolver",
    "AsyncSubprocessRunner",
    "InvalidSpecError",
    "KubernetesStatusGateway",
    "MaopTaskSpec",
    "OperatorController",
    "command_for",
    "plan_finish",
    "plan_launch",
]
