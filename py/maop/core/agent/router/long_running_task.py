"""MAOP 长任务管理器 — 支持自主 Agent 运行数十分钟到数小时的任务.

为 Devin / OpenHands 等自主 Agent 提供长任务的提交、状态追踪、
进度更新、取消、超时清理等全生命周期管理能力。所有状态持久化到
SQLite，进程重启后仍可恢复。

核心组件：
  - ``LongRunningTask``        — 长任务数据模型（Pydantic）
  - ``LongRunningTaskManager`` — 长任务管理器（线程安全）

Usage::

    from maop.core.agent.router.long_running_task import (
        LongRunningTaskManager,
    )

    mgr = LongRunningTaskManager()
    task_id = mgr.submit("devin", "重构认证模块", timeout_s=7200)
    mgr.update_progress(task_id, 0.5, "已完成一半")
    task = mgr.get_status(task_id)
    if task.status == "running":
        ...
    mgr.complete(task_id, result="重构完成，测试全通过")

设计要点：
  - 线程安全：使用 ``threading.RLock`` 保护所有公共方法
  - 持久化：SQLite WAL 模式，task_id 为主键
  - 状态机：pending → running → completed/failed/cancelled/timeout
  - 超时清理：``cleanup_expired()`` 将超时的 running/pending 标记为 timeout
"""

from __future__ import annotations

import calendar
import json
import logging
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from maop.core.backends.db_utils import sqlite_connect

logger = logging.getLogger(__name__)

# ── 任务状态常量 ──────────────────────────────────────────────────
# 状态机：pending → running → completed / failed / cancelled / timeout
STATUS_PENDING: str = "pending"
STATUS_RUNNING: str = "running"
STATUS_COMPLETED: str = "completed"
STATUS_FAILED: str = "failed"
STATUS_CANCELLED: str = "cancelled"
STATUS_TIMEOUT: str = "timeout"

# 终态集合：一旦进入终态，任务不可再变更
_TERMINAL_STATUSES: frozenset[str] = frozenset(
    {STATUS_COMPLETED, STATUS_FAILED, STATUS_CANCELLED, STATUS_TIMEOUT},
)

# 活跃状态集合：pending / running 视为活跃
_ACTIVE_STATUSES: frozenset[str] = frozenset({STATUS_PENDING, STATUS_RUNNING})


# ── Pydantic 模型 ─────────────────────────────────────────────────
class LongRunningTask(BaseModel):
    """长任务数据模型.

    封装一个长任务的完整状态快照。所有时间戳为 ISO 8601 字符串，
    便于 JSON 序列化与跨语言互操作。``metadata`` 存储任意扩展信息
    （如 Agent 配置、任务参数等），使用 ``Field(default_factory=dict)``
    避免 Pydantic 共享可变默认值的陷阱。
    """

    task_id: str = Field(..., description="任务唯一标识（UUID）")
    agent_name: str = Field(..., description="执行任务的 Agent 名称")
    task: str = Field(..., description="任务描述/指令")
    status: str = Field(
        default=STATUS_PENDING,
        description="任务状态: pending/running/completed/failed/cancelled/timeout",
    )
    progress: float = Field(default=0.0, ge=0.0, le=1.0, description="进度 0.0~1.0")
    progress_message: str = Field(default="", description="进度消息")
    result: str = Field(default="", description="任务结果（完成时填充）")
    error: str = Field(default="", description="错误信息（失败时填充）")
    submitted_at: str = Field(..., description="提交时间（ISO 8601）")
    started_at: str = Field(default="", description="开始执行时间（ISO 8601）")
    completed_at: str = Field(default="", description="完成时间（ISO 8601）")
    timeout_s: float = Field(default=3600.0, description="超时阈值（秒）")
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="扩展元数据",
    )


# ── 辅助函数 ──────────────────────────────────────────────────────
def _now_iso() -> str:
    """返回当前时间的 ISO 8601 字符串（UTC）."""
    # time.time() 返回 UTC 时间戳，格式化为 ISO 8601
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _new_task_id() -> str:
    """生成新的任务 ID（UUID4 hex）."""
    return uuid.uuid4().hex


def _row_to_task(row: sqlite3.Row) -> LongRunningTask:
    """将 SQLite 行转换为 ``LongRunningTask`` 实例.

    ``metadata`` 列存储 JSON 字符串，反序列化为 dict。
    解析失败时回退为空 dict，避免单条损坏数据导致整体查询失败。
    """
    d = dict(row)
    # 反序列化 metadata JSON
    raw_meta = d.pop("metadata_json", "{}")
    try:
        d["metadata"] = json.loads(raw_meta) if raw_meta else {}
    except (json.JSONDecodeError, TypeError):
        logger.warning("任务 %s 的 metadata 解析失败，回退为空 dict", d.get("task_id"))
        d["metadata"] = {}
    return LongRunningTask(**d)


# ── LongRunningTaskManager ────────────────────────────────────────
class LongRunningTaskManager:
    """长任务管理器（线程安全，SQLite 持久化）.

    管理自主 Agent 长任务的全生命周期：提交 → 运行 → 完成/失败/取消/超时。
    所有公共方法均通过 ``threading.RLock`` 保护，可在多线程环境下安全调用。

    Parameters
    ----------
    db_path : Path | None
        SQLite 数据库文件路径。``None`` 时使用内存数据库（``:memory:``），
        仅适用于测试或临时场景——进程退出后数据丢失。
    """

    # SQL 建表语句（task_id 为主键）
    _CREATE_TABLE_SQL = """
        CREATE TABLE IF NOT EXISTS long_running_tasks (
            task_id          TEXT    PRIMARY KEY,
            agent_name       TEXT    NOT NULL,
            task             TEXT    NOT NULL,
            status           TEXT    NOT NULL DEFAULT 'pending',
            progress         REAL    NOT NULL DEFAULT 0.0,
            progress_message TEXT    NOT NULL DEFAULT '',
            result           TEXT    NOT NULL DEFAULT '',
            error            TEXT    NOT NULL DEFAULT '',
            submitted_at     TEXT    NOT NULL,
            started_at       TEXT    NOT NULL DEFAULT '',
            completed_at     TEXT    NOT NULL DEFAULT '',
            timeout_s        REAL    NOT NULL DEFAULT 3600.0,
            metadata_json    TEXT    NOT NULL DEFAULT '{}'
        )
    """

    # 索引：按 agent_name + submitted_at 查询
    _CREATE_INDEX_AGENT_SQL = (
        "CREATE INDEX IF NOT EXISTS idx_lrt_agent "
        "ON long_running_tasks(agent_name, submitted_at)"
    )

    # 索引：按 status 查询活跃任务
    _CREATE_INDEX_STATUS_SQL = (
        "CREATE INDEX IF NOT EXISTS idx_lrt_status "
        "ON long_running_tasks(status)"
    )

    def __init__(self, db_path: Path | None = None) -> None:
        # 数据库路径：None 时使用内存数据库
        # 注意：内存数据库的连接必须复用同一 conn 对象，否则数据隔离。
        # 此处对 :memory: 使用 check_same_thread=False + 单连接 + RLock 保护。
        self._db_path: str = str(db_path) if db_path is not None else ":memory:"
        self._is_memory: bool = self._db_path == ":memory:"
        # 可重入锁：保护所有公共方法的并发访问
        self._lock: threading.RLock = threading.RLock()
        # 内存数据库专用连接（复用以保持数据可见性）
        self._memory_conn: sqlite3.Connection | None = None

        # 初始化 schema
        self._ensure_schema()

    # ── 内部：连接管理 ────────────────────────────────────────────
    def _ensure_schema(self) -> None:
        """幂等创建 ``long_running_tasks`` 表与索引."""
        if self._is_memory:
            # 内存数据库：创建专用连接并建表
            self._memory_conn = sqlite3.connect(
                ":memory:", check_same_thread=False,
            )
            self._memory_conn.row_factory = sqlite3.Row
            self._memory_conn.execute(self._CREATE_TABLE_SQL)
            self._memory_conn.execute(self._CREATE_INDEX_AGENT_SQL)
            self._memory_conn.execute(self._CREATE_INDEX_STATUS_SQL)
            self._memory_conn.commit()
        else:
            # 文件数据库：使用 sqlite_connect 上下文管理器
            with sqlite_connect(self._db_path) as conn:
                conn.execute(self._CREATE_TABLE_SQL)
                conn.execute(self._CREATE_INDEX_AGENT_SQL)
                conn.execute(self._CREATE_INDEX_STATUS_SQL)

    def _get_connection(self) -> sqlite3.Connection:
        """获取数据库连接.

        - 文件数据库：每次新建连接（WAL 模式支持并发读写）
        - 内存数据库：返回复用的单连接（RLock 保护并发访问）

        调用方负责在文件模式下关闭连接（通过 ``sqlite_connect`` 上下文）。
        内存模式下返回的连接不应关闭。
        """
        if self._is_memory:
            assert self._memory_conn is not None
            return self._memory_conn
        # 文件模式由调用方使用 sqlite_connect 上下文
        raise RuntimeError("文件模式应使用 _execute / _query 上下文方法")

    def _execute(
        self,
        sql: str,
        params: tuple[Any, ...] = (),
    ) -> None:
        """执行写操作（INSERT/UPDATE/DELETE）.

        文件模式使用 ``sqlite_connect`` 上下文（自动 commit/rollback/close）；
        内存模式直接在复用连接上执行（RLock 已由公共方法持有）。
        """
        if self._is_memory:
            assert self._memory_conn is not None
            self._memory_conn.execute(sql, params)
            self._memory_conn.commit()
        else:
            with sqlite_connect(self._db_path) as conn:
                conn.execute(sql, params)

    def _query_one(
        self,
        sql: str,
        params: tuple[Any, ...] = (),
    ) -> sqlite3.Row | None:
        """查询单行."""
        if self._is_memory:
            assert self._memory_conn is not None
            cursor = self._memory_conn.execute(sql, params)
            # Cursor.fetchone() 在 sqlite3 stub 中返回 Any，显式落到声明的
            # Row | None 上再返回。
            row: sqlite3.Row | None = cursor.fetchone()
            return row
        with sqlite_connect(self._db_path) as conn:
            cursor = conn.execute(sql, params)
            # 变量名与上方分支的 row 区分开（同一函数内重复注解会触发 no-redef）
            disk_row: sqlite3.Row | None = cursor.fetchone()
            return disk_row

    def _query_all(
        self,
        sql: str,
        params: tuple[Any, ...] = (),
    ) -> list[sqlite3.Row]:
        """查询多行."""
        if self._is_memory:
            assert self._memory_conn is not None
            cursor = self._memory_conn.execute(sql, params)
            return list(cursor.fetchall())
        with sqlite_connect(self._db_path) as conn:
            cursor = conn.execute(sql, params)
            return list(cursor.fetchall())

    # ── 公共接口 ──────────────────────────────────────────────────
    def submit(
        self,
        agent_name: str,
        task: str,
        timeout_s: float = 3600.0,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """提交长任务，返回 ``task_id``.

        任务创建后状态为 ``pending``，等待 Agent 执行器拉取并调用
        ``update_progress`` 将状态推进为 ``running``。

        Parameters
        ----------
        agent_name : str
            执行任务的 Agent 名称（如 ``"devin"``、``"openhands"``）。
        task : str
            任务描述/指令文本。
        timeout_s : float
            超时阈值（秒），默认 3600（1 小时）。
        metadata : dict | None
            扩展元数据（如 Agent 配置、任务参数），``None`` 视为空 dict。

        Returns
        -------
        str
            任务唯一标识 ``task_id``（UUID hex）。
        """
        with self._lock:
            task_id = _new_task_id()
            now = _now_iso()
            meta = metadata if metadata is not None else {}
            metadata_json = json.dumps(meta, ensure_ascii=False)

            self._execute(
                """
                INSERT INTO long_running_tasks
                    (task_id, agent_name, task, status, progress,
                     progress_message, result, error,
                     submitted_at, started_at, completed_at,
                     timeout_s, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    agent_name,
                    task,
                    STATUS_PENDING,
                    0.0,
                    "",
                    "",
                    "",
                    now,
                    "",
                    "",
                    timeout_s,
                    metadata_json,
                ),
            )
            logger.info(
                "长任务已提交: task_id=%s, agent=%s, timeout=%ss",
                task_id, agent_name, timeout_s,
            )
            return task_id

    def get_status(self, task_id: str) -> LongRunningTask:
        """查询任务状态.

        Parameters
        ----------
        task_id : str
            任务唯一标识。

        Returns
        -------
        LongRunningTask
            任务完整状态快照。

        Raises
        ------
        KeyError
            任务不存在时抛出。
        """
        with self._lock:
            row = self._query_one(
                "SELECT * FROM long_running_tasks WHERE task_id = ?",
                (task_id,),
            )
            if row is None:
                raise KeyError(f"任务不存在: {task_id}")
            return _row_to_task(row)

    def update_progress(
        self,
        task_id: str,
        progress: float,
        message: str = "",
    ) -> None:
        """更新任务进度.

        首次调用时若任务处于 ``pending``，自动推进为 ``running`` 并记录
        ``started_at``。进度值会被夹取到 [0.0, 1.0] 范围内。

        对已进入终态（completed/failed/cancelled/timeout）的任务调用
        此方法会被静默忽略并记录警告日志，避免竞态更新。

        Parameters
        ----------
        task_id : str
            任务唯一标识。
        progress : float
            进度值（0.0~1.0），超出范围会被夹取。
        message : str
            进度消息（可选），用于展示当前步骤描述。
        """
        with self._lock:
            row = self._query_one(
                "SELECT status, started_at FROM long_running_tasks WHERE task_id = ?",
                (task_id,),
            )
            if row is None:
                raise KeyError(f"任务不存在: {task_id}")

            current_status = row["status"]
            # 终态任务拒绝进度更新
            if current_status in _TERMINAL_STATUSES:
                logger.warning(
                    "任务 %s 已处于终态 %s，忽略进度更新",
                    task_id, current_status,
                )
                return

            # 夹取进度到 [0.0, 1.0]
            clamped_progress = max(0.0, min(1.0, progress))

            # pending → running：首次更新进度时记录开始时间
            if current_status == STATUS_PENDING and not row["started_at"]:
                now = _now_iso()
                self._execute(
                    """
                    UPDATE long_running_tasks
                    SET status = ?, progress = ?, progress_message = ?,
                        started_at = ?
                    WHERE task_id = ?
                    """,
                    (STATUS_RUNNING, clamped_progress, message, now, task_id),
                )
            else:
                self._execute(
                    """
                    UPDATE long_running_tasks
                    SET progress = ?, progress_message = ?
                    WHERE task_id = ?
                    """,
                    (clamped_progress, message, task_id),
                )

    def cancel(self, task_id: str) -> bool:
        """取消任务.

        仅对 ``pending`` / ``running`` 状态的任务有效。已进入终态的任务
        无法取消。

        Parameters
        ----------
        task_id : str
            任务唯一标识。

        Returns
        -------
        bool
            ``True`` 表示取消成功；``False`` 表示任务不存在或已处于终态。
        """
        with self._lock:
            row = self._query_one(
                "SELECT status FROM long_running_tasks WHERE task_id = ?",
                (task_id,),
            )
            if row is None:
                return False
            if row["status"] in _TERMINAL_STATUSES:
                return False

            now = _now_iso()
            self._execute(
                """
                UPDATE long_running_tasks
                SET status = ?, completed_at = ?
                WHERE task_id = ?
                """,
                (STATUS_CANCELLED, now, task_id),
            )
            logger.info("长任务已取消: task_id=%s", task_id)
            return True

    def list_active(self) -> list[LongRunningTask]:
        """列出所有活跃任务（pending / running）.

        Returns
        -------
        list[LongRunningTask]
            活跃任务列表，按提交时间升序排列。
        """
        with self._lock:
            rows = self._query_all(
                """
                SELECT * FROM long_running_tasks
                WHERE status IN (?, ?)
                ORDER BY submitted_at ASC
                """,
                (STATUS_PENDING, STATUS_RUNNING),
            )
            return [_row_to_task(r) for r in rows]

    def list_by_agent(
        self,
        agent_name: str,
        limit: int = 50,
    ) -> list[LongRunningTask]:
        """按 Agent 名称查询任务历史.

        Parameters
        ----------
        agent_name : str
            Agent 名称。
        limit : int
            返回上限，默认 50。按提交时间降序（最新优先）。

        Returns
        -------
        list[LongRunningTask]
            该 Agent 的任务列表。
        """
        with self._lock:
            rows = self._query_all(
                """
                SELECT * FROM long_running_tasks
                WHERE agent_name = ?
                ORDER BY submitted_at DESC
                LIMIT ?
                """,
                (agent_name, limit),
            )
            return [_row_to_task(r) for r in rows]

    def complete(self, task_id: str, result: str) -> None:
        """标记任务完成.

        Parameters
        ----------
        task_id : str
            任务唯一标识。
        result : str
            任务结果文本。

        Raises
        ------
        KeyError
            任务不存在时抛出。
        ValueError
            任务已处于终态时抛出，防止非法状态转换。
        """
        with self._lock:
            row = self._query_one(
                "SELECT status FROM long_running_tasks WHERE task_id = ?",
                (task_id,),
            )
            if row is None:
                raise KeyError(f"任务不存在: {task_id}")
            if row["status"] in _TERMINAL_STATUSES:
                raise ValueError(
                    f"任务 {task_id} 已处于终态 {row['status']}，无法标记完成",
                )

            now = _now_iso()
            self._execute(
                """
                UPDATE long_running_tasks
                SET status = ?, progress = ?, result = ?, completed_at = ?
                WHERE task_id = ?
                """,
                (STATUS_COMPLETED, 1.0, result, now, task_id),
            )
            logger.info("长任务已完成: task_id=%s", task_id)

    def fail(self, task_id: str, error: str) -> None:
        """标记任务失败.

        Parameters
        ----------
        task_id : str
            任务唯一标识。
        error : str
            错误信息文本。

        Raises
        ------
        KeyError
            任务不存在时抛出。
        ValueError
            任务已处于终态时抛出。
        """
        with self._lock:
            row = self._query_one(
                "SELECT status FROM long_running_tasks WHERE task_id = ?",
                (task_id,),
            )
            if row is None:
                raise KeyError(f"任务不存在: {task_id}")
            if row["status"] in _TERMINAL_STATUSES:
                raise ValueError(
                    f"任务 {task_id} 已处于终态 {row['status']}，无法标记失败",
                )

            now = _now_iso()
            self._execute(
                """
                UPDATE long_running_tasks
                SET status = ?, error = ?, completed_at = ?
                WHERE task_id = ?
                """,
                (STATUS_FAILED, error, now, task_id),
            )
            logger.info("长任务已失败: task_id=%s, error=%s", task_id, error[:200])

    def cleanup_expired(self) -> int:
        """清理超时任务，返回清理数量.

        扫描所有 ``pending`` / ``running`` 状态的任务，若
        ``submitted_at + timeout_s`` 已过期（当前时间超过截止时间），
        将其状态标记为 ``timeout``。

        Returns
        -------
        int
            被清理（标记为 timeout）的任务数量。
        """
        with self._lock:
            # 查询所有活跃任务
            rows = self._query_all(
                """
                SELECT task_id, submitted_at, timeout_s
                FROM long_running_tasks
                WHERE status IN (?, ?)
                """,
                (STATUS_PENDING, STATUS_RUNNING),
            )
            now_epoch = time.time()
            expired_ids: list[str] = []
            for row in rows:
                # 解析 submitted_at（ISO 8601 UTC）为 epoch。
                # 使用 calendar.timegm 而非 time.mktime：mktime 将 struct_time
                # 当作本地时间解析，而 submitted_at 是 UTC 时间，会导致时区
                # 偏移使超时判断错误（如 UTC+8 下提前 8 小时误判超时）。
                # calendar.timegm 是 mktime 的 UTC 版本，正确转换 UTC struct_time。
                try:
                    submitted_epoch = calendar.timegm(
                        time.strptime(row["submitted_at"], "%Y-%m-%dT%H:%M:%SZ"),
                    )
                except ValueError:
                    # 时间格式异常：跳过，避免误清理
                    logger.warning(
                        "任务 %s 的 submitted_at 格式异常: %s",
                        row["task_id"], row["submitted_at"],
                    )
                    continue
                deadline = submitted_epoch + row["timeout_s"]
                if now_epoch > deadline:
                    expired_ids.append(row["task_id"])

            if not expired_ids:
                return 0

            now_iso = _now_iso()
            for tid in expired_ids:
                self._execute(
                    """
                    UPDATE long_running_tasks
                    SET status = ?, completed_at = ?
                    WHERE task_id = ?
                    """,
                    (STATUS_TIMEOUT, now_iso, tid),
                )
            logger.info("清理了 %d 个超时长任务", len(expired_ids))
            return len(expired_ids)