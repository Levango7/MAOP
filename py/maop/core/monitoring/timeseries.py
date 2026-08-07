"""MAOP Time-Series - Status logging with automatic downsampling.

Stores time-series data points (metrics, status changes) in SQLite
with automatic downsampling to keep storage bounded:
  - Raw data: 1-minute granularity, retained for 24h
  - 5-min aggregate: retained for 7 days
  - 1-hour aggregate: retained for 90 days

Supports:
  - Recording data points with tags
  - Querying with time range and aggregation
  - Automatic downsampling on insert (when thresholds are met)
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from maop.core.backends.db_utils import ConnectionPool, get_pool

logger = logging.getLogger(__name__)

# ── Models ──────────────────────────────────────────────────────


class DataPoint(BaseModel):
    """A single time-series data point."""
    timestamp: float = Field(default_factory=time.time)
    metric: str = ""
    value: float = 0.0
    tags: dict[str, str] = Field(default_factory=dict)


class TimeSeriesQuery(BaseModel):
    """Query parameters for time-series data."""
    metric: str = ""
    start: float = 0.0     # Unix timestamp
    end: float = 0.0       # Unix timestamp
    aggregation: str = "avg"  # avg / sum / min / max / count
    interval_s: float = 0.0  # 0 = raw, >0 = aggregate into intervals
    tags: dict[str, str] = Field(default_factory=dict)


class TimeSeriesStats(BaseModel):
    """Statistics about the time-series store."""
    total_points: int = 0
    metrics: list[str] = Field(default_factory=list)
    oldest_point: float = 0.0
    newest_point: float = 0.0
    db_size_bytes: int = 0


# ── Retention policy ────────────────────────────────────────────

class RetentionPolicy(BaseModel):
    """Retention periods for different granularities."""
    raw_retention_s: float = 86400.0       # 24h
    min5_retention_s: float = 604800.0     # 7 days
    hour1_retention_s: float = 7776000.0   # 90 days
    downsample_threshold: int = 1000       # Downsample after this many raw points


# ── TimeSeriesStore ─────────────────────────────────────────────

class TimeSeriesStore:
    """SQLite-backed time-series store with automatic downsampling."""

    def __init__(
        self,
        db_path: str | Path = "data/timeseries.db",
        retention: RetentionPolicy | None = None,
    ):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.retention = retention or RetentionPolicy()
        self._pool: ConnectionPool = get_pool(self.db_path)
        self._init_db()

    def _init_db(self) -> None:
        conn = self._pool.acquire()
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS ts_raw (
                    timestamp REAL NOT NULL,
                    metric TEXT NOT NULL,
                    value REAL NOT NULL,
                    tags TEXT NOT NULL DEFAULT '{}',
                    PRIMARY KEY (metric, timestamp)
                ) WITHOUT ROWID
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS ts_5min (
                    timestamp REAL NOT NULL,
                    metric TEXT NOT NULL,
                    avg_value REAL NOT NULL,
                    min_value REAL NOT NULL,
                    max_value REAL NOT NULL,
                    sum_value REAL NOT NULL,
                    count INTEGER NOT NULL DEFAULT 1,
                    tags TEXT NOT NULL DEFAULT '{}',
                    PRIMARY KEY (metric, timestamp)
                ) WITHOUT ROWID
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS ts_1hour (
                    timestamp REAL NOT NULL,
                    metric TEXT NOT NULL,
                    avg_value REAL NOT NULL,
                    min_value REAL NOT NULL,
                    max_value REAL NOT NULL,
                    sum_value REAL NOT NULL,
                    count INTEGER NOT NULL DEFAULT 1,
                    tags TEXT NOT NULL DEFAULT '{}',
                    PRIMARY KEY (metric, timestamp)
                ) WITHOUT ROWID
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_ts_raw_ts ON ts_raw(timestamp)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_ts_5min_ts ON ts_5min(timestamp)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_ts_1hour_ts ON ts_1hour(timestamp)")
            conn.commit()
        finally:
            self._pool.release(conn)

    # ── Record ───────────────────────────────────────────────

    def record(
        self,
        metric: str,
        value: float,
        *,
        tags: dict[str, str] | None = None,
        timestamp: float | None = None,
    ) -> None:
        ts = timestamp or time.time()
        tags_json = json.dumps(tags or {}, ensure_ascii=False)

        conn = self._pool.acquire()
        try:
            conn.execute("""
                INSERT OR REPLACE INTO ts_raw (timestamp, metric, value, tags)
                VALUES (?, ?, ?, ?)
            """, (ts, metric, value, tags_json))
            conn.commit()

            count = conn.execute(
                "SELECT COUNT(*) as c FROM ts_raw WHERE metric = ?",
                (metric,),
            ).fetchone()["c"]

            if count >= self.retention.downsample_threshold:
                self._downsample(metric, conn)
        finally:
            self._pool.release(conn)

    def record_batch(self, points: list[DataPoint]) -> int:
        conn = self._pool.acquire()
        try:
            count = 0
            for dp in points:
                tags_json = json.dumps(dp.tags, ensure_ascii=False)
                conn.execute("""
                    INSERT OR REPLACE INTO ts_raw (timestamp, metric, value, tags)
                    VALUES (?, ?, ?, ?)
                """, (dp.timestamp, dp.metric, dp.value, tags_json))
                count += 1
            conn.commit()

            metrics = {dp.metric for dp in points}
            for m in metrics:
                cnt = conn.execute(
                    "SELECT COUNT(*) as c FROM ts_raw WHERE metric = ?",
                    (m,),
                ).fetchone()["c"]
                if cnt >= self.retention.downsample_threshold:
                    self._downsample(m, conn)

            return count
        finally:
            self._pool.release(conn)

    # ── Query ────────────────────────────────────────────────

    def query(self, q: TimeSeriesQuery) -> list[dict[str, Any]]:
        conn = self._pool.acquire()
        try:
            if q.interval_s <= 0:
                sql = """
                    SELECT timestamp, metric, value, tags
                    FROM ts_raw
                    WHERE metric = ? AND timestamp >= ? AND timestamp <= ?
                    ORDER BY timestamp ASC
                """
                rows = conn.execute(sql, (q.metric, q.start, q.end)).fetchall()
                return [
                    {
                        "timestamp": r["timestamp"],
                        "metric": r["metric"],
                        "value": r["value"],
                        "tags": json.loads(r["tags"]),
                    }
                    for r in rows
                ]

            table = "ts_5min" if q.interval_s <= 300 else "ts_1hour"

            agg_map = {
                "avg": "avg_value", "min": "min_value",
                "max": "max_value", "sum": "sum_value", "count": "count",
            }
            agg_col = agg_map.get(q.aggregation, "avg_value")

            sql = f"""
                SELECT timestamp, metric, {agg_col} as value, count
                FROM {table}
                WHERE metric = ? AND timestamp >= ? AND timestamp <= ?
                ORDER BY timestamp ASC
            """
            rows = conn.execute(sql, (q.metric, q.start, q.end)).fetchall()
            return [
                {
                    "timestamp": r["timestamp"],
                    "metric": r["metric"],
                    "value": r["value"],
                    "count": r["count"],
                }
                for r in rows
            ]
        finally:
            self._pool.release(conn)

    # ── Downsampling ────────────────────────────────────────

    def _downsample(self, metric: str, conn: sqlite3.Connection | None = None) -> None:
        own_conn = conn is None
        if own_conn:
            conn = self._pool.acquire()
        assert conn is not None
        try:
            now = time.time()

            conn.execute("""
                INSERT OR REPLACE INTO ts_5min (timestamp, metric, avg_value, min_value, max_value, sum_value, count, tags)
                SELECT
                    CAST((timestamp / 300) AS INTEGER) * 300 AS bucket_ts,
                    ?,
                    AVG(value),
                    MIN(value),
                    MAX(value),
                    SUM(value),
                    COUNT(*),
                    '{}'
                FROM ts_raw
                WHERE metric = ?
                GROUP BY bucket_ts
            """, (metric, metric))

            conn.execute("""
                INSERT OR REPLACE INTO ts_1hour (timestamp, metric, avg_value, min_value, max_value, sum_value, count, tags)
                SELECT
                    CAST((timestamp / 3600) AS INTEGER) * 3600 AS bucket_ts,
                    ?,
                    AVG(avg_value),
                    MIN(min_value),
                    MAX(max_value),
                    SUM(sum_value),
                    SUM(count),
                    '{}'
                FROM ts_5min
                WHERE metric = ?
                GROUP BY bucket_ts
            """, (metric, metric))

            raw_cutoff = now - self.retention.raw_retention_s
            conn.execute(
                "DELETE FROM ts_raw WHERE metric = ? AND timestamp < ?",
                (metric, raw_cutoff),
            )

            min5_cutoff = now - self.retention.min5_retention_s
            conn.execute(
                "DELETE FROM ts_5min WHERE metric = ? AND timestamp < ?",
                (metric, min5_cutoff),
            )

            hour1_cutoff = now - self.retention.hour1_retention_s
            conn.execute(
                "DELETE FROM ts_1hour WHERE metric = ? AND timestamp < ?",
                (metric, hour1_cutoff),
            )

            conn.commit()
            logger.debug("[ts] Downsampled metric: %s", metric)
        finally:
            if own_conn:
                self._pool.release(conn)

    # ── Read Recent ────────────────────────────────────────

    def read_recent(self, hours: float = 24.0, limit: int = 1000) -> list[dict[str, Any]]:
        conn = self._pool.acquire()
        try:
            cutoff = time.time() - hours * 3600.0

            rows = conn.execute(
                "SELECT timestamp, metric, value, tags FROM ts_raw "
                "WHERE timestamp >= ? ORDER BY timestamp ASC LIMIT ?",
                (cutoff, limit),
            ).fetchall()

            if rows:
                return [
                    {
                        "timestamp": r["timestamp"],
                        "metric": r["metric"],
                        "value": r["value"],
                        "tags": json.loads(r["tags"]),
                    }
                    for r in rows
                ]

            rows = conn.execute(
                "SELECT timestamp, metric, avg_value as value, tags FROM ts_5min "
                "WHERE timestamp >= ? ORDER BY timestamp ASC LIMIT ?",
                (cutoff, limit),
            ).fetchall()

            return [
                {
                    "timestamp": r["timestamp"],
                    "metric": r["metric"],
                    "value": r["value"],
                    "tags": json.loads(r["tags"]) if r["tags"] else {},
                }
                for r in rows
            ]
        finally:
            self._pool.release(conn)

    # ── Stats ───────────────────────────────────────────────

    def stats(self) -> TimeSeriesStats:
        conn = self._pool.acquire()
        try:
            total = conn.execute("SELECT COUNT(*) as c FROM ts_raw").fetchone()["c"]
            metrics_rows = conn.execute(
                "SELECT DISTINCT metric FROM ts_raw ORDER BY metric"
            ).fetchall()
            oldest = conn.execute(
                "SELECT MIN(timestamp) as t FROM ts_raw"
            ).fetchone()["t"] or 0.0
            newest = conn.execute(
                "SELECT MAX(timestamp) as t FROM ts_raw"
            ).fetchone()["t"] or 0.0
            db_size = self.db_path.stat().st_size if self.db_path.exists() else 0

            return TimeSeriesStats(
                total_points=total,
                metrics=[r["metric"] for r in metrics_rows],
                oldest_point=oldest,
                newest_point=newest,
                db_size_bytes=db_size,
            )
        finally:
            self._pool.release(conn)

    def close(self) -> None:
        self._pool.close_all()
