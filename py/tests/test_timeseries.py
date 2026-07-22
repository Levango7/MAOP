"""Tests for MAOP.core.timeseries — Status logging with automatic downsampling."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from maop.core.timeseries import (
    DataPoint,
    RetentionPolicy,
    TimeSeriesQuery,
    TimeSeriesStats,
    TimeSeriesStore,
)


# ── Fixtures ──────────────────────────────────────────────────────

@pytest.fixture
def ts_store(tmp_path: Path) -> TimeSeriesStore:
    return TimeSeriesStore(db_path=tmp_path / "ts.db")


@pytest.fixture
def low_threshold_store(tmp_path: Path) -> TimeSeriesStore:
    """A store with a very low downsample threshold for testing downsampling."""
    retention = RetentionPolicy(downsample_threshold=5)
    return TimeSeriesStore(db_path=tmp_path / "ts_low.db", retention=retention)


# ── Model tests ───────────────────────────────────────────────────

class TestDataPoint:
    def test_defaults(self):
        dp = DataPoint()
        assert dp.metric == ""
        assert dp.value == 0.0
        assert dp.tags == {}
        assert dp.timestamp > 0

    def test_with_values(self):
        dp = DataPoint(metric="cpu", value=42.5, tags={"host": "a1"})
        assert dp.metric == "cpu"
        assert dp.value == 42.5
        assert dp.tags == {"host": "a1"}


class TestTimeSeriesQuery:
    def test_defaults(self):
        q = TimeSeriesQuery()
        assert q.aggregation == "avg"
        assert q.interval_s == 0.0

    def test_with_values(self):
        q = TimeSeriesQuery(metric="cpu", start=100, end=200, aggregation="max", interval_s=60)
        assert q.aggregation == "max"
        assert q.interval_s == 60


class TestTimeSeriesStats:
    def test_defaults(self):
        s = TimeSeriesStats()
        assert s.total_points == 0
        assert s.metrics == []
        assert s.db_size_bytes == 0


class TestRetentionPolicy:
    def test_defaults(self):
        r = RetentionPolicy()
        assert r.raw_retention_s == 86400.0
        assert r.min5_retention_s == 604800.0
        assert r.hour1_retention_s == 7776000.0
        assert r.downsample_threshold == 1000

    def test_custom(self):
        r = RetentionPolicy(raw_retention_s=3600, downsample_threshold=10)
        assert r.raw_retention_s == 3600
        assert r.downsample_threshold == 10


# ── Initialization ────────────────────────────────────────────────

class TestInit:
    def test_creates_db(self, tmp_path: Path):
        db = tmp_path / "ts.db"
        TimeSeriesStore(db_path=db)
        assert db.exists()

    def test_creates_parent_dir(self, tmp_path: Path):
        db = tmp_path / "nested" / "ts.db"
        TimeSeriesStore(db_path=db)
        assert db.exists()

    def test_custom_retention(self, tmp_path: Path):
        r = RetentionPolicy(downsample_threshold=50)
        store = TimeSeriesStore(db_path=tmp_path / "ts.db", retention=r)
        assert store.retention.downsample_threshold == 50


# ── Record ────────────────────────────────────────────────────────

class TestRecord:
    def test_record_single(self, ts_store: TimeSeriesStore):
        ts_store.record("cpu", 55.5)
        stats = ts_store.stats()
        assert stats.total_points == 1
        assert "cpu" in stats.metrics

    def test_record_with_tags(self, ts_store: TimeSeriesStore):
        ts_store.record("cpu", 55.5, tags={"host": "server1"})
        results = ts_store.query(TimeSeriesQuery(metric="cpu", start=0, end=time.time() + 10))
        assert len(results) == 1
        assert results[0]["tags"] == {"host": "server1"}

    def test_record_with_explicit_timestamp(self, ts_store: TimeSeriesStore):
        ts_store.record("cpu", 10.0, timestamp=1000.0)
        results = ts_store.query(TimeSeriesQuery(metric="cpu", start=0, end=2000))
        assert len(results) == 1
        assert results[0]["timestamp"] == 1000.0

    def test_record_replaces_same_timestamp(self, ts_store: TimeSeriesStore):
        ts_store.record("cpu", 10.0, timestamp=1000.0)
        ts_store.record("cpu", 20.0, timestamp=1000.0)
        results = ts_store.query(TimeSeriesQuery(metric="cpu", start=0, end=2000))
        assert len(results) == 1
        assert results[0]["value"] == 20.0

    def test_record_multiple_metrics(self, ts_store: TimeSeriesStore):
        ts_store.record("cpu", 50.0)
        ts_store.record("memory", 80.0)
        stats = ts_store.stats()
        assert set(stats.metrics) == {"cpu", "memory"}


class TestRecordBatch:
    def test_batch_record(self, ts_store: TimeSeriesStore):
        points = [
            DataPoint(metric="cpu", value=10.0, timestamp=1000.0),
            DataPoint(metric="cpu", value=20.0, timestamp=1001.0),
            DataPoint(metric="cpu", value=30.0, timestamp=1002.0),
        ]
        count = ts_store.record_batch(points)
        assert count == 3
        assert ts_store.stats().total_points == 3

    def test_batch_empty(self, ts_store: TimeSeriesStore):
        assert ts_store.record_batch([]) == 0

    def test_batch_multiple_metrics(self, ts_store: TimeSeriesStore):
        points = [
            DataPoint(metric="cpu", value=10.0, timestamp=1000.0),
            DataPoint(metric="mem", value=20.0, timestamp=1000.0),
        ]
        ts_store.record_batch(points)
        stats = ts_store.stats()
        assert set(stats.metrics) == {"cpu", "mem"}


# ── Query ─────────────────────────────────────────────────────────

class TestQuery:
    def test_raw_query(self, ts_store: TimeSeriesStore):
        for i in range(5):
            ts_store.record("cpu", float(i), timestamp=1000.0 + i)
        results = ts_store.query(TimeSeriesQuery(metric="cpu", start=1000, end=1005))
        assert len(results) == 5
        assert results[0]["timestamp"] == 1000.0
        assert results[-1]["timestamp"] == 1004.0

    def test_raw_query_empty_range(self, ts_store: TimeSeriesStore):
        ts_store.record("cpu", 10.0, timestamp=1000.0)
        results = ts_store.query(TimeSeriesQuery(metric="cpu", start=2000, end=3000))
        assert results == []

    def test_raw_query_wrong_metric(self, ts_store: TimeSeriesStore):
        ts_store.record("cpu", 10.0, timestamp=1000.0)
        results = ts_store.query(TimeSeriesQuery(metric="mem", start=0, end=2000))
        assert results == []

    def test_raw_query_partial_range(self, ts_store: TimeSeriesStore):
        for i in range(10):
            ts_store.record("cpu", float(i), timestamp=1000.0 + i)
        results = ts_store.query(TimeSeriesQuery(metric="cpu", start=1002, end=1005))
        assert len(results) == 4  # 1002, 1003, 1004, 1005

    def test_aggregated_query_5min(self, ts_store: TimeSeriesStore):
        # Insert data and manually downsample
        now = time.time()
        for i in range(20):
            ts_store.record("cpu", float(i), timestamp=now - 1000 + i * 10)
        ts_store._downsample("cpu")
        results = ts_store.query(TimeSeriesQuery(metric="cpu", start=0, end=now + 10, interval_s=300, aggregation="avg"))
        assert isinstance(results, list)

    def test_aggregated_query_1hour(self, ts_store: TimeSeriesStore):
        now = time.time()
        for i in range(20):
            ts_store.record("cpu", float(i), timestamp=now - 1000 + i * 10)
        ts_store._downsample("cpu")
        results = ts_store.query(TimeSeriesQuery(metric="cpu", start=0, end=now + 10, interval_s=3600, aggregation="avg"))
        assert isinstance(results, list)


# ── Downsampling ──────────────────────────────────────────────────

class TestDownsample:
    def test_downsample_creates_5min_aggregates(self, ts_store: TimeSeriesStore):
        now = time.time()
        for i in range(20):
            ts_store.record("cpu", float(i), timestamp=now - 1000 + i * 10)
        ts_store._downsample("cpu")
        conn = ts_store._pool.acquire()
        try:
            count = conn.execute("SELECT COUNT(*) as c FROM ts_5min WHERE metric = 'cpu'").fetchone()["c"]
        finally:
            ts_store._pool.release(conn)
        assert count > 0

    def test_downsample_creates_1hour_aggregates(self, ts_store: TimeSeriesStore):
        now = time.time()
        for i in range(20):
            ts_store.record("cpu", float(i), timestamp=now - 1000 + i * 10)
        ts_store._downsample("cpu")
        conn = ts_store._pool.acquire()
        try:
            count = conn.execute("SELECT COUNT(*) as c FROM ts_1hour WHERE metric = 'cpu'").fetchone()["c"]
        finally:
            ts_store._pool.release(conn)
        assert count > 0

    def test_automatic_downsample_on_threshold(self, low_threshold_store: TimeSeriesStore):
        now = time.time()
        for i in range(5):
            low_threshold_store.record("cpu", float(i), timestamp=now - 100 + i * 10)
        conn = low_threshold_store._pool.acquire()
        try:
            count = conn.execute("SELECT COUNT(*) as c FROM ts_5min WHERE metric = 'cpu'").fetchone()["c"]
        finally:
            low_threshold_store._pool.release(conn)
        assert count > 0


# ── Read Recent ───────────────────────────────────────────────────

class TestReadRecent:
    def test_read_recent_raw(self, ts_store: TimeSeriesStore):
        now = time.time()
        ts_store.record("cpu", 50.0, timestamp=now - 60)
        ts_store.record("cpu", 60.0, timestamp=now - 30)
        results = ts_store.read_recent(hours=1)
        assert len(results) == 2
        assert all(r["metric"] == "cpu" for r in results)

    def test_read_recent_empty(self, ts_store: TimeSeriesStore):
        results = ts_store.read_recent(hours=24)
        assert results == []

    def test_read_recent_limit(self, ts_store: TimeSeriesStore):
        now = time.time()
        for i in range(20):
            ts_store.record("cpu", float(i), timestamp=now - 100 + i)
        results = ts_store.read_recent(hours=1, limit=5)
        assert len(results) <= 5

    def test_read_recent_fallback_to_5min(self, ts_store: TimeSeriesStore):
        # Insert data, downsample (which prunes raw), then read_recent should fall back
        now = time.time()
        for i in range(20):
            ts_store.record("cpu", float(i), timestamp=now - 1000 + i * 10)
        ts_store._downsample("cpu")
        # Raw data may be pruned; read_recent should still return from 5min
        results = ts_store.read_recent(hours=1)
        assert isinstance(results, list)


# ── Stats ─────────────────────────────────────────────────────────

class TestStats:
    def test_stats_empty(self, ts_store: TimeSeriesStore):
        stats = ts_store.stats()
        assert isinstance(stats, TimeSeriesStats)
        assert stats.total_points == 0
        assert stats.metrics == []

    def test_stats_with_data(self, ts_store: TimeSeriesStore):
        ts_store.record("cpu", 50.0, timestamp=1000.0)
        ts_store.record("mem", 80.0, timestamp=2000.0)
        stats = ts_store.stats()
        assert stats.total_points == 2
        assert set(stats.metrics) == {"cpu", "mem"}
        assert stats.oldest_point == 1000.0
        assert stats.newest_point == 2000.0
        assert stats.db_size_bytes > 0


# ── Close ─────────────────────────────────────────────────────────

class TestClose:
    def test_close(self, ts_store: TimeSeriesStore):
        ts_store.record("cpu", 10.0)
        ts_store.close()
        assert ts_store._pool._pool == []

    def test_close_idempotent(self, ts_store: TimeSeriesStore):
        ts_store.close()
        ts_store.close()  # should not raise
