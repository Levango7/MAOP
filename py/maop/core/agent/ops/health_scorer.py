"""HealthScorer — Agent 健康度评分模块.

基于滑动窗口记录每个 Agent 的调用结果（成功/失败、响应时间、用户评分），
综合计算 0-100 的健康度评分，用于运维监控与路由决策。

评分算法（总分 100）：
  - **成功率权重 40%**：``success_rate * 40``
  - **响应时间权重 30%**：快速(<=1s)=30, 中速(<=5s)=20, 慢速(<=10s)=10, 超慢(>10s)=0
  - **用户评分权重 20%**：``avg_rating / 5 * 20``
  - **调用量权重 10%**：``min(total_calls / 100, 1) * 10``（调用越多越可信）

设计要点：
  - **滑动窗口**：用 ``collections.deque`` 实现，仅保留最近 ``window_size`` 次调用，
    避免历史污染、快速反映最新状态。
  - **线程安全**：``threading.RLock`` 保护内存状态与 SQLite 操作。
  - **持久化**：SQLite 表 ``health_records`` 存储调用记录，重启后可恢复。

持久化：SQLite 表 ``health_records``，每行一条调用记录。
线程安全：``threading.RLock`` 保护内存滑动窗口；SQLite 由 WAL + busy_timeout 保证并发。
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field

from maop.core.backends.db_utils import get_db_path, sqlite_connect

logger = logging.getLogger(__name__)


# ── Pydantic 模型 ────────────────────────────────────────────────
class HealthScore(BaseModel):
    """单个 Agent 的健康度评分结果.

    各字段含义：
      - ``score``：0-100 综合评分，越高越健康。
      - ``success_rate``：成功率（0.0-1.0）。
      - ``avg_response_time_s``：平均响应时间（秒）。
      - ``avg_user_rating``：平均用户评分（0-5）。
      - ``total_calls``：总调用次数（含失败）。
      - ``last_updated``：最后更新时间（ISO 8601 UTC）。
    """

    agent_name: str = Field(description="Agent 名称")
    score: int = Field(ge=0, le=100, description="0-100 综合评分")
    success_rate: float = Field(ge=0.0, le=1.0, description="成功率 (0.0-1.0)")
    avg_response_time_s: float = Field(ge=0.0, description="平均响应时间（秒）")
    avg_user_rating: float = Field(ge=0.0, le=5.0, description="平均用户评分 (0-5)")
    total_calls: int = Field(ge=0, description="总调用次数")
    last_updated: str = Field(description="最后更新时间（ISO 8601）")


# ── 内部数据结构 ────────────────────────────────────────────────
@dataclass
class _CallRecord:
    """单次调用记录（内存滑动窗口中使用）.

    用 dataclass 而非 Pydantic 以减少开销——每次调用都会创建一条记录。
    """

    success: bool
    response_time_s: float
    user_rating: int


# ── HealthScorer ─────────────────────────────────────────────────
class HealthScorer:
    """Agent 健康度评分器.

    为每个 Agent 维护一个滑动窗口（``deque``），记录最近的调用结果，
    并据此计算综合健康度评分。

    Parameters
    ----------
    db_path : Path | None
        SQLite 持久化路径。``None`` 时由 ``get_db_path("health_scorer")`` 解析。
    window_size : int
        滑动窗口大小（保留最近 N 次调用），默认 100。
    """

    def __init__(
        self,
        db_path: Path | None = None,
        window_size: int = 100,
    ) -> None:
        if window_size <= 0:
            raise ValueError(f"window_size 必须为正整数，收到 {window_size}")
        self._db_path: Path = Path(db_path) if db_path else get_db_path("health_scorer")
        self._window_size: int = window_size
        self._lock = threading.RLock()
        # agent_name -> deque[_CallRecord]
        self._windows: dict[str, deque[_CallRecord]] = {}
        self._init_db()
        self._load_from_db()

    # ── 初始化 ──────────────────────────────────────────────────
    def _init_db(self) -> None:
        """初始化 SQLite 表（幂等）。"""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite_connect(self._db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS health_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    agent_name TEXT NOT NULL,
                    success INTEGER NOT NULL,
                    response_time_s REAL NOT NULL,
                    user_rating INTEGER NOT NULL DEFAULT 0,
                    recorded_at TEXT NOT NULL
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_health_agent "
                "ON health_records(agent_name)",
            )

    def _load_from_db(self) -> None:
        """从 DB 加载历史记录到内存滑动窗口.

        每个 Agent 仅加载最近的 ``window_size`` 条记录，避免全量加载耗内存。
        采用 cursor + fetchmany 分批读取，平衡查询次数与单批内存。
        （来源：2026-09-10-python-sqlite-batch-fetchmany-init-memory-peak）
        """
        batch_size = 1000  # BATCH_SIZE 取 1000~5000，平衡查询次数和单批内存
        with sqlite_connect(self._db_path) as conn:
            # 先获取所有 agent_name
            agent_names = [
                r[0] for r in conn.execute(
                    "SELECT DISTINCT agent_name FROM health_records",
                ).fetchall()
            ]
            for agent_name in agent_names:
                # 按 id 降序取最近 window_size 条，再反转为时间正序
                cursor = conn.execute(
                    "SELECT success, response_time_s, user_rating "
                    "FROM health_records WHERE agent_name = ? "
                    "ORDER BY id DESC LIMIT ?",
                    (agent_name, self._window_size),
                )
                records: list[_CallRecord] = []
                while True:
                    batch = cursor.fetchmany(batch_size)
                    if not batch:
                        break
                    for row in batch:
                        # r[0] 索引访问兼容 tuple 和 sqlite3.Row
                        # （来源：2026-09-10-python-sqlite-batch-fetchmany-init-memory-peak）
                        records.append(_CallRecord(
                            success=bool(row[0]),
                            response_time_s=float(row[1]),
                            user_rating=int(row[2]),
                        ))
                # 反转为时间正序（DB 是 DESC）
                records.reverse()
                self._windows[agent_name] = deque(records, maxlen=self._window_size)

    # ── 记录 ────────────────────────────────────────────────────
    def record_result(
        self,
        agent_name: str,
        success: bool,
        response_time_s: float,
        user_rating: int = 0,
    ) -> None:
        """记录一次 Agent 调用结果.

        Parameters
        ----------
        agent_name : str
            Agent 名称。
        success : bool
            调用是否成功。
        response_time_s : float
            响应时间（秒），必须 >= 0。
        user_rating : int
            用户评分（0-5），默认 0 表示未评分。

        Raises
        ------
        ValueError
            ``response_time_s`` 为负或 ``user_rating`` 不在 0-5 范围。
        """
        if response_time_s < 0:
            raise ValueError(f"response_time_s 不能为负，收到 {response_time_s}")
        if not 0 <= user_rating <= 5:
            raise ValueError(f"user_rating 必须在 0-5 范围，收到 {user_rating}")

        now_iso = datetime.now(timezone.utc).isoformat()
        record = _CallRecord(
            success=success,
            response_time_s=response_time_s,
            user_rating=user_rating,
        )

        with self._lock:
            # 更新内存滑动窗口
            if agent_name not in self._windows:
                self._windows[agent_name] = deque(maxlen=self._window_size)
            self._windows[agent_name].append(record)

            # 持久化到 SQLite
            with sqlite_connect(self._db_path) as conn:
                conn.execute(
                    "INSERT INTO health_records "
                    "(agent_name, success, response_time_s, user_rating, recorded_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        agent_name,
                        1 if success else 0,
                        response_time_s,
                        user_rating,
                        now_iso,
                    ),
                )
        logger.debug(
            "[health_scorer] recorded %s: success=%s rt=%.3fs rating=%d",
            agent_name, success, response_time_s, user_rating,
        )

    # ── 评分计算 ────────────────────────────────────────────────
    def get_score(self, agent_name: str) -> HealthScore:
        """计算指定 Agent 的健康度评分.

        Parameters
        ----------
        agent_name : str
            Agent 名称。

        Returns
        -------
        HealthScore
            健康度评分结果。若 Agent 无调用记录，返回零分评分。
        """
        with self._lock:
            window = self._windows.get(agent_name)
            if window is None or len(window) == 0:
                return HealthScore(
                    agent_name=agent_name,
                    score=0,
                    success_rate=0.0,
                    avg_response_time_s=0.0,
                    avg_user_rating=0.0,
                    total_calls=0,
                    last_updated=datetime.now(timezone.utc).isoformat(),
                )
            return self._compute_score(agent_name, list(window))

    def get_all_scores(self) -> list[HealthScore]:
        """获取所有 Agent 的健康度评分.

        Returns
        -------
        list[HealthScore]
            所有有调用记录的 Agent 评分列表（无特定顺序）。
        """
        with self._lock:
            results: list[HealthScore] = []
            for agent_name, window in self._windows.items():
                if len(window) == 0:
                    continue
                results.append(self._compute_score(agent_name, list(window)))
            return results

    def get_ranking(self) -> list[HealthScore]:
        """按评分降序排列所有 Agent 的健康度评分.

        Returns
        -------
        list[HealthScore]
            按评分从高到低排序的评分列表。
        """
        return sorted(
            self.get_all_scores(),
            key=lambda s: s.score,
            reverse=True,
        )

    # ── 重置 ────────────────────────────────────────────────────
    def reset_score(self, agent_name: str) -> None:
        """重置指定 Agent 的评分（清空滑动窗口与 DB 记录）.

        Parameters
        ----------
        agent_name : str
            Agent 名称。不存在则静默忽略。
        """
        with self._lock:
            # 清空内存窗口
            if agent_name in self._windows:
                self._windows[agent_name].clear()
            # 删除 DB 记录
            with sqlite_connect(self._db_path) as conn:
                conn.execute(
                    "DELETE FROM health_records WHERE agent_name = ?",
                    (agent_name,),
                )
        logger.debug("[health_scorer] reset score for %s", agent_name)

    # ── 内部计算 ────────────────────────────────────────────────
    @staticmethod
    def _compute_score(agent_name: str, records: list[_CallRecord]) -> HealthScore:
        """根据调用记录列表计算健康度评分.

        评分算法：
          - 成功率权重 40%：``success_rate * 40``
          - 响应时间权重 30%：快速(<=1s)=30, 中速(<=5s)=20, 慢速(<=10s)=10, 超慢(>10s)=0
          - 用户评分权重 20%：``avg_rating / 5 * 20``
          - 调用量权重 10%：``min(total_calls / 100, 1) * 10``
        """
        total = len(records)
        success_count = sum(1 for r in records if r.success)
        success_rate = success_count / total

        avg_response_time = sum(r.response_time_s for r in records) / total

        # 有评分的记录才参与用户评分均值计算
        rated = [r.user_rating for r in records if r.user_rating > 0]
        avg_user_rating = sum(rated) / len(rated) if rated else 0.0

        # ── 各维度得分 ──
        # 1. 成功率得分（40%）
        success_score = success_rate * 40

        # 2. 响应时间得分（30%）
        if avg_response_time <= 1.0:
            response_time_score = 30.0
        elif avg_response_time <= 5.0:
            response_time_score = 20.0
        elif avg_response_time <= 10.0:
            response_time_score = 10.0
        else:
            response_time_score = 0.0

        # 3. 用户评分得分（20%）
        user_rating_score = (avg_user_rating / 5.0) * 20

        # 4. 调用量得分（10%）：调用越多越可信
        # 注意：这里用窗口内记录数，而非 DB 全量
        call_volume_score = min(total / 100, 1.0) * 10

        # 综合评分（四舍五入到整数）
        raw_score = (
            success_score
            + response_time_score
            + user_rating_score
            + call_volume_score
        )
        score = round(raw_score)
        # 确保在 0-100 范围内（浮点误差防护）
        score = max(0, min(100, score))

        # 最后更新时间：取最后一条记录的时间（从 DB 侧获取更准确，
        # 但为避免额外查询，这里用当前时间近似）
        last_updated = datetime.now(timezone.utc).isoformat()

        return HealthScore(
            agent_name=agent_name,
            score=score,
            success_rate=success_rate,
            avg_response_time_s=avg_response_time,
            avg_user_rating=avg_user_rating,
            total_calls=total,
            last_updated=last_updated,
        )