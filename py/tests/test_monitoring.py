"""Tests for MAOP.core.monitoring — StructuredLogger, metrics, MetricsCollector."""

from __future__ import annotations

import json
import logging
import threading

import pytest

from maop.core.monitoring import (
    Counter,
    Gauge,
    Histogram,
    MetricsCollector,
    StructuredLogger,
)


# ── StructuredLogger ──────────────────────────────────────────────


class TestStructuredLogger:
    def _make_logger(self, **kw):
        return StructuredLogger("MAOP_test", **kw)

    def test_init_defaults(self):
        sl = self._make_logger()
        assert sl.name == "MAOP_test"
        assert sl.trace_id == ""
        assert sl.span_id == ""

    def test_init_with_trace(self):
        sl = self._make_logger(trace_id="t-1", span_id="s-1")
        assert sl.trace_id == "t-1"
        assert sl.span_id == "s-1"

    def test_format_includes_required_fields(self):
        sl = self._make_logger()
        out = sl._format("INFO", "hello", extra="x")
        entry = json.loads(out)
        assert entry["level"] == "INFO"
        assert entry["msg"] == "hello"
        assert entry["module"] == "MAOP_test"
        assert entry["extra"] == "x"
        assert "ts" in entry

    def test_format_omits_empty_trace(self):
        sl = self._make_logger()
        entry = json.loads(sl._format("INFO", "m"))
        assert "trace_id" not in entry
        assert "span_id" not in entry

    def test_format_includes_trace_when_set(self):
        sl = self._make_logger(trace_id="t1", span_id="s1")
        entry = json.loads(sl._format("INFO", "m"))
        assert entry["trace_id"] == "t1"
        assert entry["span_id"] == "s1"

    def test_info_calls_underlying_logger(self, caplog):
        sl = self._make_logger()
        with caplog.at_level(logging.INFO, logger="MAOP_test"):
            sl.info("hello info", k="v")
        assert any("hello info" in r.message for r in caplog.records)

    def test_warning_calls_underlying_logger(self, caplog):
        sl = self._make_logger()
        with caplog.at_level(logging.WARNING, logger="MAOP_test"):
            sl.warning("warn msg")
        assert any("warn msg" in r.message for r in caplog.records)

    def test_error_calls_underlying_logger(self, caplog):
        sl = self._make_logger()
        with caplog.at_level(logging.ERROR, logger="MAOP_test"):
            sl.error("err msg")
        assert any("err msg" in r.message for r in caplog.records)

    def test_debug_calls_underlying_logger(self, caplog):
        sl = self._make_logger()
        with caplog.at_level(logging.DEBUG, logger="MAOP_test"):
            sl.debug("dbg msg")
        assert any("dbg msg" in r.message for r in caplog.records)

    def test_log_generic_info(self, caplog):
        sl = self._make_logger()
        with caplog.at_level(logging.INFO, logger="MAOP_test"):
            sl.log(phase="exec", level="info", message="running")
        assert any("running" in r.message for r in caplog.records)

    def test_log_generic_error_level(self, caplog):
        sl = self._make_logger()
        with caplog.at_level(logging.ERROR, logger="MAOP_test"):
            sl.log(level="ERROR", message="boom")
        assert any("boom" in r.message for r in caplog.records)
        assert any(r.levelno == logging.ERROR for r in caplog.records)

    def test_log_generic_warning_level(self, caplog):
        sl = self._make_logger()
        with caplog.at_level(logging.WARNING, logger="MAOP_test"):
            sl.log(level="WARN", message="careful")
        assert any(r.levelno == logging.WARNING for r in caplog.records)

    def test_with_trace_creates_child(self):
        sl = self._make_logger(trace_id="parent")
        child = sl.with_trace("child-t", "child-s")
        assert child.trace_id == "child-t"
        assert child.span_id == "child-s"
        assert child.name == sl.name

    def test_with_trace_inherits_when_empty(self):
        sl = self._make_logger(trace_id="parent", span_id="parent-s")
        child = sl.with_trace("")
        assert child.trace_id == "parent"
        assert child.span_id == "parent-s"

    def test_log_dir_creates_file_handler(self, tmp_path):
        sl = StructuredLogger("MAOP_file_test", log_dir=tmp_path)
        assert (tmp_path / "MAOP-structured.log").exists() or (tmp_path / "MAOP-structured.log").parent.exists()
        sl.info("file test")
        # Force flush handlers
        for h in sl._logger.handlers:
            h.flush()
        log_file = tmp_path / "MAOP-structured.log"
        assert log_file.exists()
        content = log_file.read_text(encoding="utf-8")
        assert "file test" in content


# ── Counter ───────────────────────────────────────────────────────


class TestCounter:
    def test_init_defaults(self):
        c = Counter("reqs")
        assert c.name == "reqs"
        assert c.help_text == ""
        assert c.label_names == []

    def test_inc_default(self):
        c = Counter("reqs")
        c.inc()
        assert c.get() == 1.0

    def test_inc_with_value(self):
        c = Counter("reqs")
        c.inc(5.0)
        c.inc(2.5)
        assert c.get() == 7.5

    def test_inc_with_labels(self):
        c = Counter("reqs", label_names=["method"])
        c.inc(labels={"method": "GET"})
        c.inc(labels={"method": "GET"})
        c.inc(labels={"method": "POST"})
        assert c.get(labels={"method": "GET"}) == 2.0
        assert c.get(labels={"method": "POST"}) == 1.0

    def test_get_unset_label_is_zero(self):
        c = Counter("reqs")
        assert c.get(labels={"x": "y"}) == 0.0

    def test_to_prometheus_no_labels(self):
        c = Counter("reqs", help_text="Total requests")
        c.inc(3)
        out = c.to_prometheus()
        assert "# HELP reqs Total requests" in out
        assert "# TYPE reqs counter" in out
        assert "reqs 3.0" in out

    def test_to_prometheus_with_labels(self):
        c = Counter("reqs")
        c.inc(labels={"method": "GET"})
        out = c.to_prometheus()
        assert "reqs{method=GET} 1.0" in out

    def test_thread_safety(self):
        c = Counter("reqs")

        def worker():
            for _ in range(100):
                c.inc()

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert c.get() == 1000.0


# ── Gauge ─────────────────────────────────────────────────────────


class TestGauge:
    def test_set_and_get(self):
        g = Gauge("temp")
        g.set(42.0)
        assert g.get() == 42.0

    def test_inc(self):
        g = Gauge("temp")
        g.set(10.0)
        g.inc(5.0)
        assert g.get() == 15.0

    def test_dec(self):
        g = Gauge("temp")
        g.set(10.0)
        g.dec(3.0)
        assert g.get() == 7.0

    def test_labels(self):
        g = Gauge("temp", label_names=["host"])
        g.set(50.0, labels={"host": "a"})
        g.set(60.0, labels={"host": "b"})
        assert g.get(labels={"host": "a"}) == 50.0
        assert g.get(labels={"host": "b"}) == 60.0

    def test_to_prometheus(self):
        g = Gauge("temp", help_text="Temperature")
        g.set(23.5)
        out = g.to_prometheus()
        assert "# TYPE temp gauge" in out
        assert "temp 23.5" in out


# ── Histogram ─────────────────────────────────────────────────────


class TestHistogram:
    def test_observe_updates_sum_and_count(self):
        h = Histogram("latency")
        h.observe(0.1)
        h.observe(0.2)
        assert h._sum == pytest.approx(0.3)
        assert h._total == 2

    def test_observe_bucket_counts(self):
        h = Histogram("latency", buckets=(0.1, 0.5, float("inf")))
        h.observe(0.05)
        h.observe(0.2)
        h.observe(0.6)
        assert h._counts[0.1] == 1  # 0.05 <= 0.1
        assert h._counts[0.5] == 2  # 0.05, 0.2 <= 0.5
        assert h._counts[float("inf")] == 3

    def test_to_prometheus_format(self):
        h = Histogram("latency", help_text="Latency")
        h.observe(0.01)
        out = h.to_prometheus()
        assert "# TYPE latency histogram" in out
        assert "latency_count 1" in out
        assert "latency_sum 0.01" in out
        assert 'le="+Inf"' in out

    def test_default_buckets_present(self):
        h = Histogram("latency")
        assert float("inf") in h.buckets
        assert 0.005 in h.buckets

    def test_zero_observations(self):
        h = Histogram("latency")
        out = h.to_prometheus()
        assert "latency_count 0" in out


# ── MetricsCollector ──────────────────────────────────────────────


class TestMetricsCollector:
    def test_counter_create_and_reuse(self):
        mc = MetricsCollector()
        c1 = mc.counter("reqs", "help")
        c2 = mc.counter("reqs")
        assert c1 is c2

    def test_gauge_create_and_reuse(self):
        mc = MetricsCollector()
        g1 = mc.gauge("temp")
        g2 = mc.gauge("temp")
        assert g1 is g2

    def test_histogram_create_and_reuse(self):
        mc = MetricsCollector()
        h1 = mc.histogram("lat")
        h2 = mc.histogram("lat")
        assert h1 is h2

    def test_to_prometheus_combines_all(self):
        mc = MetricsCollector()
        mc.counter("c1").inc()
        mc.gauge("g1").set(5)
        mc.histogram("h1").observe(0.1)
        out = mc.to_prometheus()
        assert "# TYPE c1 counter" in out
        assert "# TYPE g1 gauge" in out
        assert "# TYPE h1 histogram" in out

    def test_to_json_structure(self):
        mc = MetricsCollector()
        mc.counter("c1").inc(3)
        mc.gauge("g1").set(7)
        mc.histogram("h1").observe(0.2)
        data = mc.to_json()
        assert data["c1"]["type"] == "counter"
        assert data["c1"]["values"][""] == 3.0
        assert data["g1"]["type"] == "gauge"
        assert data["h1"]["type"] == "histogram"
        assert data["h1"]["count"] == 1
        assert data["h1"]["sum"] == pytest.approx(0.2)

    def test_empty_collector_to_prometheus(self):
        mc = MetricsCollector()
        assert mc.to_prometheus() == ""

    def test_empty_collector_to_json(self):
        mc = MetricsCollector()
        assert mc.to_json() == {}

    def test_histogram_custom_buckets(self):
        mc = MetricsCollector()
        h = mc.histogram("custom", buckets=(1.0, float("inf")))
        h.observe(0.5)
        out = h.to_prometheus()
        assert 'le="1.0"' in out
