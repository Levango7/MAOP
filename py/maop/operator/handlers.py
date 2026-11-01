"""kopf 事件层 —— 把 MaopTask 事件接到 :class:`OperatorController`.

本模块是薄的：校验/状态机在 ``spec``/``reconcile``，k8s IO 在注入的 gateway。
kopf 是**可选依赖**（镜像内由 Dockerfile 安装）；在无 kopf 的环境 import 本
模块得到带安装指引的 RuntimeError，而不是裸 ImportError。

M3.2 跨仓桥（2026-11-01）：单例构造时经 ``_enterprise_quota_checker``
可选接入企业版配额门（MAOS ``maop.enterprise.quota_operator``，PR MAOS#27）
——个人版/不可用时注入 ``None``（行为同社区版基线，fail-open 且告警可见）。
"""

from __future__ import annotations

import logging
from typing import Any

from maop.operator.controller import OperatorController
from maop.operator.gateway import AsyncSubprocessRunner, KubernetesStatusGateway

logger = logging.getLogger(__name__)

_controller: OperatorController | None = None


def _require_kopf() -> Any:
    try:
        import kopf
    except ImportError as exc:  # pragma: no cover - 镜像内必装
        raise RuntimeError(
            "maop.operator.handlers requires kopf — install with "
            "'pip install kopf' or use the maop-operator image "
            "(deploy/k8s/operator/Dockerfile)"
        ) from exc
    return kopf


def _enterprise_quota_checker() -> Any | None:
    """企业版配额门（M3.2 跨仓桥，MAOS PR #27）——可用则构造，否则 ``None``.

    try-import MAOS 侧 ``maop.enterprise.quota_operator.OperatorQuotaChecker``
    （内部要求 ``FeatureFlag.TENANT_ISOLATION`` 企业特性门）；个人版 import
    即失败 → ``None``。构造失败（quota SQLite 不可用等）同样 ``None``——
    fail-open 与 ``QuotaManager`` 自身哲学一致，但**告警可见**（配额静默
    失效会出现在日志里，不吞）。
    """
    try:
        from maop.config.home import ensure_runtime_root
        from maop.enterprise.quota import QuotaManager
        from maop.enterprise.quota_operator import OperatorQuotaChecker

        db_path = ensure_runtime_root() / "data" / "maop.db"
        checker = OperatorQuotaChecker(QuotaManager(db_path=str(db_path)))
        logger.info("[operator] enterprise quota checker wired (db=%s)", db_path)
        return checker
    except Exception as exc:
        logger.warning(
            "[operator] enterprise quota checker unavailable — quota gate "
            "disabled (fail-open): %s",
            exc,
        )
        return None


def get_controller() -> OperatorController:
    """进程级单例（惰性构造；gateway/runner 用默认实现）."""
    global _controller
    if _controller is None:
        # KubernetesStatusGateway 同时实现 AgentResolver（M1）——kind 烟测
        # 2026-11-01 发现 resolver 一直没布线（生产恒 pinned=False，任务落
        # 集群默认 agent 必炸），这里补上。
        gateway = KubernetesStatusGateway()
        _controller = OperatorController(
            gateway=gateway,
            runner=AsyncSubprocessRunner(),
            resolver=gateway,
            quota_checker=_enterprise_quota_checker(),
        )
    return _controller


def register(kopf: Any = None, *, controller: OperatorController | None = None) -> None:
    """注册 kopf 事件处理器（kopf 缺失时抛带指引的 RuntimeError）.

    ``kopf`` 参数供测试注入 stub；生产路径传 None 走 ``_require_kopf``。
    """
    kopf = kopf or _require_kopf()
    ctrl = controller or get_controller()

    @kopf.on.event("maoptasks", group="maop.io", version="v1alpha1")
    async def _on_maoptask_event(
        meta: Any, spec: Any, status: Any, body: dict[str, Any], **_: Any
    ) -> None:
        phase = (status or {}).get("phase")
        name = (meta or {}).get("name", "")
        namespace = (meta or {}).get("namespace", "default")
        # kopf 事件回调里吞异常会把 reconcile 失败变成静默 —— 只留日志，
        # 不捕获；kopf 自身按事件重试。
        await ctrl.handle_task(
            name=name, namespace=namespace, body=body, status_phase=phase
        )

    logger.info("[operator] kopf handlers registered (maoptasks.maop.io v1alpha1)")
