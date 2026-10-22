"""Central retention policies for the append-only SQLite tables.

Why this module exists
----------------------
The repo already knows how to bound a growing table — ``run_events``,
``alertmanager_alerts`` and ``quota_usage_samples`` all prune. But a sweep of
every ``CREATE TABLE`` under ``maop/`` found 14 tables that are written on hot
paths and had **no delete statement anywhere in the tree**. Those grow for the
lifetime of the install: 24/7 deployments had no ceiling on error logs, hook
invocations, routing audits or subagent transcripts.

Design constraints
------------------
* **One policy table, one delete site.** Adding a bounded table means adding a
  ``RetentionPolicy`` row, not hand-writing another prune function.
* **The DB path resolver mirrors the writer.** Modules resolve their SQLite
  file through different helpers (``unified_db_path``, ``get_db_path("<mod>")``,
  a private ``_default_db_path``). Re-using the writer's own resolver is what
  keeps pruning from silently targeting a *different* file than the one that
  grows — the exact failure already recorded once in this project (the circuit
  breaker wrote one DB while the dashboard read another).
* **Never raises.** Maintenance runs on a shutdown/interval path; one missing
  table or one locked file must not abort the other policies.
* **Two bounds.** A time cutoff plus an optional row cap: the cap still holds
  when a clock is broken or a burst writes millions of rows in one minute.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from maop.core.backends.db_utils import (
    get_db_path,
    sqlite_connect,
    unified_db_path,
    validate_identifier,
)

logger = logging.getLogger("maop.retention")

#: How the time column is stored. ``iso`` = ISO-8601 text written by
#: ``datetime.now(timezone.utc).isoformat()``; ``epoch_s`` = ``time.time()`` float.
ISO = "iso"
EPOCH_S = "epoch_s"

DAY_SECONDS = 86400.0


@dataclass(frozen=True)
class RetentionPolicy:
    """One bounded table: which column ages it out, and where the file lives."""

    table: str
    time_col: str
    kind: str
    db: str
    days: int
    max_rows: int | None = None


def _backend_data_db() -> Path:
    from maop.core.backends.data import _default_db_path

    return _default_db_path()


def _config_history_db() -> Path:
    from maop.core.config.config_history import _default_db_path

    return _default_db_path()


def _memory_db() -> Path:
    from maop.memory.shared_db import get_memory_db_path

    return get_memory_db_path()


def _subagent_db() -> Path:
    from maop.core.agent.delegation.subagent_db import get_subagent_db_path

    return get_subagent_db_path()


#: Policy key → SQLite file. Each entry resolves through the *writer's* helper.
_DB_RESOLVERS: dict[str, Callable[[], Path]] = {
    "unified": unified_db_path,
    "backend_data": _backend_data_db,
    "config_history": _config_history_db,
    "memory": _memory_db,
    "subagent": _subagent_db,
    "hook_manager": lambda: get_db_path("hook_manager"),
    "usage_stats": lambda: get_db_path("usage_stats"),
    "change_tracker": lambda: get_db_path("change_tracker"),
    "agent_registry": lambda: get_db_path("agent_registry"),
    "routing_audit": lambda: get_db_path(),
    "cost_tracker": lambda: get_db_path("cost_tracker"),
    "protocol": lambda: get_db_path("protocol"),
    "agent_performance": lambda: get_db_path("agent_performance"),
}

#: The 14 tables that had a confirmed writer and no delete path anywhere.
#: Retention windows are deliberately generous for anything that feeds a
#: billing or rollback story (cost/usage 90d, config snapshots cap-only).
POLICIES: tuple[RetentionPolicy, ...] = (
    RetentionPolicy("error_log", "timestamp", ISO, "backend_data", 30, max_rows=50_000),
    RetentionPolicy("hook_logs", "created_at", ISO, "hook_manager", 30, max_rows=50_000),
    RetentionPolicy("usage_calls", "recorded_at", ISO, "usage_stats", 90, max_rows=200_000),
    RetentionPolicy("cost_entries", "created_at", ISO, "cost_tracker", 90, max_rows=200_000),
    RetentionPolicy("breaker_events", "timestamp", EPOCH_S, "unified", 30, max_rows=50_000),
    RetentionPolicy("change_log", "created_at", ISO, "change_tracker", 30, max_rows=50_000),
    RetentionPolicy("config_snapshots", "changed_at", ISO, "config_history", 0, max_rows=100),
    RetentionPolicy("health_log", "checked_at", ISO, "agent_registry", 14, max_rows=50_000),
    RetentionPolicy("routing_audit_logs", "timestamp", ISO, "routing_audit", 30, max_rows=50_000),
    RetentionPolicy("memory_traces", "created", ISO, "memory", 30, max_rows=20_000),
    RetentionPolicy("consolidation_log", "started_at", ISO, "memory", 30, max_rows=10_000),
    RetentionPolicy("subagent_transcripts", "timestamp", EPOCH_S, "subagent", 30, max_rows=20_000),
    RetentionPolicy("protocol_messages", "created_at", ISO, "protocol", 30, max_rows=50_000),
    RetentionPolicy("agent_performance", "created_at", EPOCH_S, "agent_performance", 30, max_rows=50_000),
)


def _validate_policies() -> None:
    """Fail at import, not inside a 3am maintenance tick.

    The identifiers are interpolated into DELETE statements, so they are checked
    here even though POLICIES is an in-repo constant; a future edit that pastes a
    caller-supplied name gets rejected before it can execute.
    """
    for policy in POLICIES:
        validate_identifier(policy.table, "retention table")
        validate_identifier(policy.time_col, "retention column")
        if policy.db not in _DB_RESOLVERS:
            raise ValueError(f"{policy.table}: unknown retention db key {policy.db!r}")
        if policy.kind not in {ISO, EPOCH_S}:
            raise ValueError(f"{policy.table}: unknown time kind {policy.kind!r}")
        if policy.days < 0:
            raise ValueError(f"{policy.table}: negative retention window")
        if policy.days == 0 and policy.max_rows is None:
            raise ValueError(f"{policy.table}: cap-only policy needs max_rows")


_validate_policies()


def resolve_db_path(key: str) -> Path:
    """Resolve a policy's DB key to its SQLite file (same helper the writer uses)."""
    return _DB_RESOLVERS[key]()


def _cutoff(policy: RetentionPolicy, now: float) -> float | str:
    """Threshold value for the policy's time column, in the column's own encoding."""
    if policy.kind == EPOCH_S:
        return now - policy.days * DAY_SECONDS
    return (datetime.fromtimestamp(now, tz=timezone.utc) - timedelta(days=policy.days)).isoformat()


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
    ).fetchone() is not None


def prune_policy(policy: RetentionPolicy, *, db_path: Path | None = None,
                 now: float | None = None) -> int:
    """Drop expired rows and enforce the row cap for one table. Rows deleted."""
    path = Path(db_path) if db_path is not None else resolve_db_path(policy.db)
    now = time.time() if now is None else now
    deleted = 0
    try:
        with sqlite_connect(path) as conn:
            if not _table_exists(conn, policy.table):
                return 0
            if policy.days > 0:
                cutoff = _cutoff(policy, now)
                # 表名与列名只取 POLICIES 里的仓库内常量，且导入时已过
                # validate_identifier；筛选值一律走 params 绑定 ⇒ 无注入路径。
                # typeof() 不是装饰：SQLite 里"数字 < 文本"恒真。kind 与列真实
                # 类型配错时（把 epoch 列按 ISO 比较），没有这个守卫整表会被当成
                # "早已过期"一次删光；有它，配错 ⇒ 这次什么都不删。
                if policy.kind == ISO:
                    sql = (f"DELETE FROM {policy.table} "  # nosec B608
                           f"WHERE typeof({policy.time_col}) = 'text' "
                           f"AND {policy.time_col} <> '' AND {policy.time_col} < ?")
                else:
                    sql = (f"DELETE FROM {policy.table} "  # nosec B608
                           f"WHERE typeof({policy.time_col}) IN ('real', 'integer') "
                           f"AND {policy.time_col} < ?")
                deleted += conn.execute(sql, (cutoff,)).rowcount
            if policy.max_rows is not None:
                total = conn.execute(f"SELECT COUNT(*) FROM {policy.table}").fetchone()[0]  # nosec B608
                surplus = int(total) - policy.max_rows
                if surplus > 0:
                    # rowid survives TEXT primary keys (none of these tables is
                    # WITHOUT ROWID), and it keeps the newest N by write order.
                    deleted += conn.execute(
                        f"DELETE FROM {policy.table} WHERE rowid NOT IN "  # nosec B608
                        f"(SELECT rowid FROM {policy.table} ORDER BY rowid DESC LIMIT ?)",
                        (policy.max_rows,),
                    ).rowcount
            conn.commit()
    except (sqlite3.Error, OSError) as exc:
        logger.warning("[retention] %s skipped: %s", policy.table, exc)
        return 0
    if deleted:
        logger.info("[retention] %s: dropped %d rows", policy.table, deleted)
    return deleted


def prune_all(*, db_paths: dict[str, Path] | None = None,
              now: float | None = None) -> dict[str, int]:
    """Run every policy. Returns ``{table: rows_deleted}`` for tables that exist."""
    results: dict[str, int] = {}
    seen: set[str] = set()
    for policy in POLICIES:
        if policy.table in seen:
            raise ValueError(f"duplicate retention policy for {policy.table}")
        seen.add(policy.table)
        path = None
        if db_paths is not None:
            path = db_paths.get(policy.db) or db_paths.get(policy.table)
        results[policy.table] = prune_policy(policy, db_path=path, now=now)
    return results


def summarize(results: dict[str, int]) -> dict[str, object]:
    """Flatten ``prune_all`` output for the maintenance endpoint's response."""
    return {
        "retention_tables_checked": len(results),
        "retention_rows_pruned": sum(results.values()),
        "retention_by_table": {t: n for t, n in results.items() if n},
    }
