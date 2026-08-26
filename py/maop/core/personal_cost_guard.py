"""MAOP Personal Cost Guard — P1-1 个人版成本兜底护栏。

个人版在无企业配额中间件（QuotaMiddleware 属企业版）时，仍具备硬性
LLM 花费保护。本模块实现软/硬两档熔断：

- **软熔断**（默认，``personal_cost_hard=False``）：达到阈值 → 告警（日志）
  + 拒绝新 LLM 调用，运行中任务允许跑完。
- **硬熔断**（``personal_cost_hard=True``）：达到阈值 → 告警 + 中断运行中任务。

配置通过 ``MAOPSettings.personal_cost_cap`` / ``personal_cost_hard`` 读取
（环境变量 ``MAOP_PERSONAL_COST_CAP`` / ``MAOP_PERSONAL_COST_HARD``）。

累计花费查询直接读取 ``cost_entries`` 表的 ``SUM(cost_usd)``（全局累计，
与 ``CostTracker`` 同一 SQLite 库），避免 ``summary()`` 的 10000 条截断。

集成点：``maop_execute.py`` 在发起 LLM 调用前调用 ``check_new_call()``。
"""

from __future__ import annotations

import logging
from typing import Any

from maop.core.backends.db_utils import sqlite_connect

logger = logging.getLogger(__name__)


class PersonalCostGuard:
    """个人版全局累计花费熔断护栏。

    Parameters
    ----------
    cost_tracker : CostTracker | None
        可选的 CostTracker 实例。若 ``None``，首次查询时延迟获取
        ``get_cost_tracker()`` 单例。
    cost_cap : float | None
        可选的花费阈值覆盖（USD）。若 ``None``，从 ``MAOPSettings`` 读取。
    cost_hard : bool | None
        可选的硬熔断开关覆盖。若 ``None``，从 ``MAOPSettings`` 读取。

    设计要点
    --------
    - 告警去重：首次触发熔断时告警，避免每次调用都刷日志（``_tripped`` 标志）。
    - 线程安全：SQLite 连接通过 ``sqlite_connect`` 上下文管理器保证。
    - ``cap=0`` 表示不限（向后兼容，本地工具默认不强制）。
    """

    def __init__(
        self,
        *,
        cost_tracker: Any | None = None,
        cost_cap: float | None = None,
        cost_hard: bool | None = None,
    ) -> None:
        self._tracker = cost_tracker
        # 从 settings 读取默认值，允许参数覆盖（测试便利 + 运行时灵活）。
        default_cap = 0.0
        default_hard = False
        try:
            from maop.config.settings import get_settings
            settings = get_settings()
            default_cap = float(settings.personal_cost_cap)
            default_hard = bool(settings.personal_cost_hard)
        except Exception as exc:  # pragma: no cover - settings 加载失败时回退默认
            logger.debug("[personal-cost] Failed to load settings, using defaults: %s", exc)
        self._cap: float = float(cost_cap) if cost_cap is not None else default_cap
        self._hard: bool = bool(cost_hard) if cost_hard is not None else default_hard
        # 告警去重标志：首次触发熔断时告警，后续不再重复刷日志。
        self._tripped: bool = False

    # ── 内部辅助 ────────────────────────────────────────────────

    def _get_tracker(self) -> Any:
        """延迟获取 CostTracker 单例（避免 import 时副作用）。"""
        if self._tracker is not None:
            return self._tracker
        from maop.core.cost_tracker import get_cost_tracker
        self._tracker = get_cost_tracker()
        return self._tracker

    def _get_total_spent(self) -> float:
        """查询全局累计 LLM 花费（USD）。

        直接读取 ``cost_entries`` 表的 ``SUM(cost_usd)``，与
        :class:`~maop.core.cost_tracker.CostTracker` 同一 SQLite 库。
        相比 ``summary().total_cost_usd`` 避免了 10000 条截断，且语义
        明确为"全局累计"而非"当日/当月"。
        """
        tracker = self._get_tracker()
        db_path = tracker._db_path
        with sqlite_connect(db_path) as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(cost_usd), 0) as total FROM cost_entries"
            ).fetchone()
        return float(row["total"]) if row else 0.0

    # ── 公开 API ────────────────────────────────────────────────

    def check_new_call(self) -> tuple[bool, str]:
        """检查是否允许发起新的 LLM 调用。

        Returns
        -------
        (allowed, reason) : tuple[bool, str]
            - ``cap=0``（不限）→ ``(True, "")``
            - 累计花费 < cap → ``(True, "")``
            - 累计花费 >= cap → ``(False, "Personal cost cap exceeded: $X / $Y")``
              同时首次触发时 ``logger.warning`` 告警（去重）。
        """
        if self._cap <= 0:
            return True, ""
        spent = self._get_total_spent()
        if spent < self._cap:
            return True, ""
        # 熔断
        reason = f"Personal cost cap exceeded: ${spent:.2f} / ${self._cap:.2f}"
        if not self._tripped:
            self._tripped = True
            mode = "hard" if self._hard else "soft"
            logger.warning(
                "[personal-cost] %s circuit breaker tripped: $%.4f / $%.4f",
                mode, spent, self._cap,
            )
        return False, reason

    def should_interrupt_running(self) -> bool:
        """判断是否应中断运行中任务（仅硬熔断模式）。

        Returns
        -------
        bool
            - 硬熔断模式且累计花费 >= cap → ``True``
            - 否则（软熔断 / 未超限 / cap=0）→ ``False``
        """
        if not self._hard:
            return False
        if self._cap <= 0:
            return False
        spent = self._get_total_spent()
        return spent >= self._cap

    def get_status(self) -> dict[str, Any]:
        """返回当前护栏状态快照。

        Returns
        -------
        dict
            包含 ``total_spent_usd`` / ``cap_usd`` / ``tripped`` /
            ``mode`` / ``hard`` 字段，供 Dashboard / 日志诊断使用。
        """
        spent = self._get_total_spent() if self._cap > 0 else 0.0
        return {
            "total_spent_usd": round(spent, 4),
            "cap_usd": self._cap,
            "tripped": self._tripped or (self._cap > 0 and spent >= self._cap),
            "mode": "hard" if self._hard else "soft",
            "hard": self._hard,
        }