"""MAOP Kubernetes Operator（M-T3.3 M0）—— MaopTask → `maop run` 子进程 → status 回写.

M0 边界（章程见 ROADMAP「M-T3.3」）：
- **已实现**：spec 校验（与 ``deploy/k8s/operator/crds/maoptask.yaml`` 对齐）、
  命令构造（``maop run <prompt>``）、Reconcile 状态机（Pending → Running →
  Succeeded/Failed/TimedOut/Cancelled）、status patch 形状（phase/conditions/
  startTime/completionTime，与 CRD status 子模式对齐）、kopf 事件层（可选导入，
  镜像内安装）。
- **M0 未实现（章程明示顺延）**：真集群 kind 验证；``agent`` → MaopAgent CR
  解析（spec.agent 仅校验必填并透传记录）；多租户/配额/插件（CRD 标注 planned）。

分层：``spec``/``reconcile`` 是纯函数（无 k8s 依赖）；``controller`` 用注入的
gateway/runner（测试用 fake）；``handlers``/``__main__`` 是 kopf 薄层。
"""

from __future__ import annotations

from maop.operator.controller import OperatorController
from maop.operator.gateway import AsyncSubprocessRunner, KubernetesStatusGateway
from maop.operator.reconcile import plan_finish, plan_launch
from maop.operator.spec import InvalidSpecError, MaopTaskSpec, command_for

__all__ = [
    "AsyncSubprocessRunner",
    "InvalidSpecError",
    "KubernetesStatusGateway",
    "MaopTaskSpec",
    "OperatorController",
    "command_for",
    "plan_finish",
    "plan_launch",
]
