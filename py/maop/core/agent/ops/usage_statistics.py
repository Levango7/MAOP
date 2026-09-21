"""UsageStatistics — Agent 使用统计与智能推荐模块.

记录每次 Agent 调用的结果（成功/失败、耗时、成本、token 数、任务类型），
提供按时间段的统计聚合、排名、成本分析，以及基于多指标的智能推荐。

推荐算法（综合评分，0-1 范围）：
  - **成功率权重 40%**：``success_rate``
  - **成本权重 30%**：``1 / (1 + avg_cost_per_call)``（免费=1.0，越贵越低）
  - **速度权重 20%**：``1 / (1 + avg_duration_s)``（即时=1.0，越慢越低）
  - **能力匹配权重 10%**：``capability_match``（要求能力与 Agent 声明能力的交集比例）

  ``prefer_free=True`` 时，FREE/SUBSCRIPTION 计费模式的 Agent 获得额外加分。

设计要点：
  - **SQL 聚合优先**：统计查询使用 ``GROUP BY`` + 聚合函数，避免全表读入。
    （来源：2026-09-10-python-stats-sql-aggregate-replace-full-table-load）
  - **线程安全**：``threading.RLock`` 保护 SQLite 操作。
  - **持久化**：SQLite 表 ``usage_calls``，每行一条调用记录。

持久化：SQLite 表 ``usage_calls``，每行一条调用记录。
线程安全：``threading.RLock`` 保护内存状态；SQLite 由 WAL + busy_timeout 保证并发。
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from maop.core.backends.db_utils import get_db_path, sqlite_connect

logger = logging.getLogger(__name__)


# ── Pydantic 模型 ────────────────────────────────────────────────
class AgentStats(BaseModel):
    """单个 Agent 的使用统计结果.

    各字段含义：
      - ``total_calls``：总调用次数（含失败）。
      - ``success_count`` / ``fail_count``：成功 / 失败次数。
      - ``success_rate``：成功率（0.0-1.0）。
      - ``avg_duration_s``：平均耗时（秒）。
      - ``total_cost``：总成本（美元）。
      - ``total_tokens``：总 token 数。
      - ``avg_cost_per_call``：平均每次调用成本。
      - ``last_used``：最后使用时间（ISO 8601 UTC）。
    """

    agent_name: str = Field(description="Agent 名称")
    total_calls: int = Field(ge=0, description="总调用次数")
    success_count: int = Field(ge=0, description="成功次数")
    fail_count: int = Field(ge=0, description="失败次数")
    success_rate: float = Field(ge=0.0, le=1.0, description="成功率 (0.0-1.0)")
    avg_duration_s: float = Field(ge=0.0, description="平均耗时（秒）")
    total_cost: float = Field(ge=0.0, description="总成本（美元）")
    total_tokens: int = Field(ge=0, description="总 token 数")
    avg_cost_per_call: float = Field(ge=0.0, description="平均每次调用成本")
    last_used: str = Field(default="", description="最后使用时间（ISO 8601）")


class AgentRecommendation(BaseModel):
    """单个 Agent 的推荐结果.

    各字段含义：
      - ``score``：综合评分（0.0-1.0，越高越推荐）。
      - ``reason``：推荐理由（人类可读）。
      - ``estimated_cost``：预估单次调用成本。
    """

    agent_name: str = Field(description="Agent 名称")
    score: float = Field(ge=0.0, le=1.0, description="综合评分 (0.0-1.0)")
    reason: str = Field(default="", description="推荐理由")
    estimated_cost: float = Field(ge=0.0, description="预估单次调用成本")


class CostAnalysis(BaseModel):
    """成本分析结果.

    各字段含义：
      - ``total_cost``：所有 Agent 总成本。
      - ``total_calls``：所有 Agent 总调用次数。
      - ``avg_cost_per_call``：全局平均每次调用成本。
      - ``most_expensive`` / ``cheapest``：最贵 / 最便宜的 Agent 名称。
      - ``by_agent``：按 Agent 名称分组的成本明细 ``{agent_name: cost}``。
    """

    total_cost: float = Field(ge=0.0, description="总成本")
    total_calls: int = Field(ge=0, description="总调用次数")
    avg_cost_per_call: float = Field(ge=0.0, description="全局平均每次调用成本")
    most_expensive: str = Field(default="", description="最贵的 Agent")
    cheapest: str = Field(default="", description="最便宜的 Agent")
    by_agent: dict[str, float] = Field(
        default_factory=dict, description="按 Agent 分组的成本",
    )


# ── UsageStatistics ─────────────────────────────────────────────
class UsageStatistics:
    """Agent 使用统计与智能推荐管理器.

    记录每次 Agent 调用的结果，提供统计聚合、排名、成本分析与智能推荐。

    Parameters
    ----------
    db_path : Path | None
        SQLite 持久化路径。``None`` 时由 ``get_db_path("usage_stats")`` 解析。
    catalog : AgentCatalog | None
        Agent 元数据注册中心，用于推荐算法查询能力声明与计费模式。
        ``None`` 时推荐仅基于统计数据，不考量能力匹配与计费偏好。
    """

    def __init__(
        self,
        db_path: Path | None = None,
        catalog: Any | None = None,
    ) -> None:
        self._db_path: Path = Path(db_path) if db_path else get_db_path("usage_stats")
        self._catalog = catalog
        self._lock = threading.RLock()
        self._init_db()

    # ── 初始化 ──────────────────────────────────────────────────
    def _init_db(self) -> None:
        """初始化 SQLite 表（幂等）。"""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite_connect(self._db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS usage_calls (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    agent_name TEXT NOT NULL,
                    success INTEGER NOT NULL,
                    duration_s REAL NOT NULL,
                    cost REAL NOT NULL,
                    tokens INTEGER NOT NULL DEFAULT 0,
                    task_type TEXT NOT NULL DEFAULT '',
                    recorded_at TEXT NOT NULL
                )
            """)
            # 按 agent_name 建索引，加速聚合查询
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_usage_agent "
                "ON usage_calls(agent_name)",
            )
            # 按 recorded_at 建索引，加速时间过滤
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_usage_time "
                "ON usage_calls(recorded_at)",
            )

    # ── 记录 ────────────────────────────────────────────────────
    def record_call(
        self,
        agent_name: str,
        success: bool,
        duration_s: float,
        cost: float,
        tokens: int = 0,
        task_type: str = "",
    ) -> None:
        """记录一次 Agent 调用.

        Parameters
        ----------
        agent_name : str
            Agent 名称。
        success : bool
            调用是否成功。
        duration_s : float
            耗时（秒），必须 >= 0。
        cost : float
            成本（美元），必须 >= 0。
        tokens : int
            消耗的 token 数，默认 0。
        task_type : str
            任务类型标签（如 "code_generation"），默认空。

        Raises
        ------
        ValueError
            ``duration_s`` 或 ``cost`` 为负。
        """
        if duration_s < 0:
            raise ValueError(f"duration_s 不能为负，收到 {duration_s}")
        if cost < 0:
            raise ValueError(f"cost 不能为负，收到 {cost}")

        now_iso = datetime.now(timezone.utc).isoformat()
        with self._lock, sqlite_connect(self._db_path) as conn:
            conn.execute(
                "INSERT INTO usage_calls "
                "(agent_name, success, duration_s, cost, tokens, task_type, recorded_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    agent_name,
                    1 if success else 0,
                    duration_s,
                    cost,
                    tokens,
                    task_type,
                    now_iso,
                ),
            )
        logger.debug(
            "[usage_stats] recorded %s: success=%s dur=%.3fs cost=%.4f tokens=%d type=%s",
            agent_name, success, duration_s, cost, tokens, task_type,
        )

    # ── 统计查询 ────────────────────────────────────────────────
    def get_agent_stats(
        self,
        agent_name: str,
        period: str = "month",
    ) -> AgentStats:
        """获取指定 Agent 的统计.

        Parameters
        ----------
        agent_name : str
            Agent 名称。
        period : str
            时间窗口：``"day"`` / ``"week"`` / ``"month"`` / ``"all"``，默认 ``"month"``。

        Returns
        -------
        AgentStats
            统计结果。若无记录，返回零值统计。
        """
        since = self._period_to_since(period)
        with self._lock, sqlite_connect(self._db_path) as conn:
            row = conn.execute(
                """SELECT
                    COUNT(*) AS total_calls,
                    SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) AS success_count,
                    AVG(duration_s) AS avg_duration_s,
                    SUM(cost) AS total_cost,
                    SUM(tokens) AS total_tokens,
                    MAX(recorded_at) AS last_used
                   FROM usage_calls
                   WHERE agent_name = ? AND recorded_at >= ?""",
                (agent_name, since),
            ).fetchone()

        total_calls = int(row["total_calls"] or 0)
        if total_calls == 0:
            return AgentStats(
                agent_name=agent_name,
                total_calls=0,
                success_count=0,
                fail_count=0,
                success_rate=0.0,
                avg_duration_s=0.0,
                total_cost=0.0,
                total_tokens=0,
                avg_cost_per_call=0.0,
                last_used="",
            )

        success_count = int(row["success_count"] or 0)
        total_cost = float(row["total_cost"] or 0.0)
        return AgentStats(
            agent_name=agent_name,
            total_calls=total_calls,
            success_count=success_count,
            fail_count=total_calls - success_count,
            success_rate=success_count / total_calls,
            avg_duration_s=float(row["avg_duration_s"] or 0.0),
            total_cost=total_cost,
            total_tokens=int(row["total_tokens"] or 0),
            avg_cost_per_call=total_cost / total_calls,
            last_used=row["last_used"] or "",
        )

    def get_all_stats(self, period: str = "month") -> list[AgentStats]:
        """获取所有 Agent 的统计.

        Parameters
        ----------
        period : str
            时间窗口，同 :meth:`get_agent_stats`。

        Returns
        -------
        list[AgentStats]
            所有有调用记录的 Agent 统计列表（按 agent_name 排序）。
        """
        since = self._period_to_since(period)
        # 使用 GROUP BY 聚合，一次查询获取所有 Agent 统计，避免全表读入
        # （来源：2026-09-10-python-stats-sql-aggregate-replace-full-table-load）
        with self._lock, sqlite_connect(self._db_path) as conn:
            rows = conn.execute(
                """SELECT
                    agent_name,
                    COUNT(*) AS total_calls,
                    SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) AS success_count,
                    AVG(duration_s) AS avg_duration_s,
                    SUM(cost) AS total_cost,
                    SUM(tokens) AS total_tokens,
                    MAX(recorded_at) AS last_used
                   FROM usage_calls
                   WHERE recorded_at >= ?
                   GROUP BY agent_name
                   ORDER BY agent_name""",
                (since,),
            ).fetchall()

        results: list[AgentStats] = []
        for row in rows:
            total_calls = int(row["total_calls"])
            success_count = int(row["success_count"] or 0)
            total_cost = float(row["total_cost"] or 0.0)
            results.append(AgentStats(
                agent_name=row["agent_name"],
                total_calls=total_calls,
                success_count=success_count,
                fail_count=total_calls - success_count,
                success_rate=success_count / total_calls,
                avg_duration_s=float(row["avg_duration_s"] or 0.0),
                total_cost=total_cost,
                total_tokens=int(row["total_tokens"] or 0),
                avg_cost_per_call=total_cost / total_calls,
                last_used=row["last_used"] or "",
            ))
        return results

    def get_ranking(self, metric: str = "success_rate") -> list[AgentStats]:
        """按指定指标对所有 Agent 排名.

        Parameters
        ----------
        metric : str
            排名指标，支持：
              - ``"success_rate"``：成功率（默认）
              - ``"avg_duration_s"``：平均耗时（升序，越快越好）
              - ``"total_cost"``：总成本（升序，越便宜越好）
              - ``"total_calls"``：总调用次数（降序，越常用越好）
              - ``"avg_cost_per_call"``：平均每次成本（升序）

        Returns
        -------
        list[AgentStats]
            按指定指标排序的统计列表。
        """
        all_stats = self.get_all_stats(period="all")
        reverse = metric in ("success_rate", "total_calls")
        # avg_duration_s / total_cost / avg_cost_per_call 升序（越小越好）
        return sorted(all_stats, key=lambda s: getattr(s, metric), reverse=reverse)

    # ── 智能推荐 ────────────────────────────────────────────────
    def recommend_agent(
        self,
        task_type: str = "",
        capabilities: list[str] | None = None,
        prefer_free: bool = True,
    ) -> list[AgentRecommendation]:
        """智能推荐 Agent.

        .. note::
           ``capabilities`` 原先用 ``[]`` 作默认值（B006 可变默认参数）——
           可变对象作默认值会在多次调用间共享，一旦被就地修改就串味。
           已改为 ``None`` 并在函数内建新列表。

        基于历史统计与 Agent 元数据，综合评分推荐最适合的 Agent。

        评分公式（0-1 范围）::

            score = success_rate * 0.4
                  + cost_score * 0.3
                  + speed_score * 0.2
                  + capability_match * 0.1

        其中：
          - ``cost_score = 1 / (1 + avg_cost_per_call)``（免费=1.0）
          - ``speed_score = 1 / (1 + avg_duration_s)``（即时=1.0）
          - ``capability_match``：要求能力与 Agent 声明能力的交集比例

        ``prefer_free=True`` 时，FREE/SUBSCRIPTION 计费模式的 Agent 额外加分 0.1。

        Parameters
        ----------
        task_type : str
            任务类型标签，用于过滤有相关经验的 Agent（空则不过滤）。
        capabilities : list[str]
            要求的能力列表（如 ``["code_generation", "tool_use"]``），空则不考量。
        prefer_free : bool
            是否优先免费 / 包月计费模式的 Agent，默认 True。

        Returns
        -------
        list[AgentRecommendation]
            按综合评分降序排列的推荐列表。
        """
        if capabilities is None:      # B006：避免可变默认参数
            capabilities = []
        all_stats = self.get_all_stats(period="all")
        if not all_stats:
            return []

        recommendations: list[AgentRecommendation] = []

        for stats in all_stats:
            # ── 成功率得分（40%）──
            success_score = stats.success_rate

            # ── 成本得分（30%）：1/(1+cost)，免费=1.0 ──
            cost_score = 1.0 / (1.0 + stats.avg_cost_per_call)

            # ── 速度得分（20%）：1/(1+duration)，即时=1.0 ──
            speed_score = 1.0 / (1.0 + stats.avg_duration_s)

            # ── 能力匹配得分（10%）──
            capability_match = self._compute_capability_match(
                stats.agent_name, capabilities,
            )

            # ── 综合评分 ──
            score = (
                success_score * 0.4
                + cost_score * 0.3
                + speed_score * 0.2
                + capability_match * 0.1
            )

            # ── prefer_free 加分 ──
            is_free = False
            if prefer_free and self._catalog is not None:
                desc = self._catalog.get(stats.agent_name)
                if desc is not None:
                    billing = str(desc.billing_model)
                    if billing in ("free", "subscription"):
                        is_free = True
                        score += 0.1

            # 确保评分在 0-1 范围
            score = max(0.0, min(1.0, score))

            # ── 推荐理由 ──
            reason_parts: list[str] = []
            reason_parts.append(f"成功率 {stats.success_rate:.0%}")
            reason_parts.append(f"平均耗时 {stats.avg_duration_s:.2f}s")
            if stats.avg_cost_per_call > 0:
                reason_parts.append(f"平均成本 ${stats.avg_cost_per_call:.4f}")
            else:
                reason_parts.append("免费")
            if is_free:
                reason_parts.append("免费/包月优先")
            if capabilities and capability_match > 0:
                reason_parts.append(f"能力匹配 {capability_match:.0%}")
            reason = "；".join(reason_parts)

            recommendations.append(AgentRecommendation(
                agent_name=stats.agent_name,
                score=score,
                reason=reason,
                estimated_cost=stats.avg_cost_per_call,
            ))

        # 按评分降序排列
        recommendations.sort(key=lambda r: r.score, reverse=True)
        return recommendations

    # ── 成本分析 ────────────────────────────────────────────────
    def get_cost_analysis(self, period: str = "month") -> CostAnalysis:
        """获取成本分析.

        Parameters
        ----------
        period : str
            时间窗口，同 :meth:`get_agent_stats`。

        Returns
        -------
        CostAnalysis
            成本分析结果。
        """
        all_stats = self.get_all_stats(period=period)
        if not all_stats:
            return CostAnalysis(
                total_cost=0.0,
                total_calls=0,
                avg_cost_per_call=0.0,
                most_expensive="",
                cheapest="",
                by_agent={},
            )

        by_agent = {s.agent_name: s.total_cost for s in all_stats}
        total_cost = sum(s.total_cost for s in all_stats)
        total_calls = sum(s.total_calls for s in all_stats)
        avg_cost = total_cost / total_calls if total_calls > 0 else 0.0

        # 最贵 / 最便宜（按总成本）
        sorted_by_cost = sorted(all_stats, key=lambda s: s.total_cost)
        cheapest = sorted_by_cost[0].agent_name
        most_expensive = sorted_by_cost[-1].agent_name

        return CostAnalysis(
            total_cost=total_cost,
            total_calls=total_calls,
            avg_cost_per_call=avg_cost,
            most_expensive=most_expensive,
            cheapest=cheapest,
            by_agent=by_agent,
        )

    # ── 内部工具 ────────────────────────────────────────────────
    @staticmethod
    def _period_to_since(period: str) -> str:
        """将时间段标识转换为 ISO 时间戳下界.

        Parameters
        ----------
        period : str
            ``"day"`` / ``"week"`` / ``"month"`` / ``"all"``。

        Returns
        -------
        str
            ISO 8601 时间戳。``"all"`` 返回极早时间以保证全量匹配。
        """
        now = datetime.now(timezone.utc)
        period_lower = period.lower().strip()
        if period_lower == "day":
            since = now - timedelta(days=1)
        elif period_lower == "week":
            since = now - timedelta(weeks=1)
        elif period_lower == "month":
            since = now - timedelta(days=30)
        elif period_lower in ("all", "", "*"):
            # 极早时间，保证全量匹配
            return "0000-01-01T00:00:00+00:00"
        else:
            # 未知 period 默认按 month 处理
            logger.warning("[usage_stats] 未知 period=%r，按 month 处理", period)
            since = now - timedelta(days=30)
        return since.isoformat()

    def _compute_capability_match(
        self,
        agent_name: str,
        required_capabilities: list[str],
    ) -> float:
        """计算 Agent 能力与需求能力的匹配比例.

        Parameters
        ----------
        agent_name : str
            Agent 名称。
        required_capabilities : list[str]
            需求能力列表。

        Returns
        -------
        float
            匹配比例（0.0-1.0）。无需求时返回 1.0（不扣分）；
            无 catalog 时返回 1.0（无法判断，不扣分）。
        """
        if not required_capabilities:
            return 1.0
        if self._catalog is None:
            return 1.0

        desc = self._catalog.get(agent_name)
        if desc is None:
            return 0.0

        # Agent 声明的能力（枚举转字符串比较）
        agent_caps: set[str] = set()
        for cap in desc.capabilities:
            agent_caps.add(cap.value if hasattr(cap, "value") else str(cap))

        required_set = set(required_capabilities)
        matched = len(agent_caps & required_set)
        return matched / len(required_set)