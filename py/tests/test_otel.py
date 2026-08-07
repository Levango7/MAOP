"""Tests for OpenTelemetry integration (maop.core.otel)."""
from __future__ import annotations

import os
from unittest.mock import patch

from maop.core.monitoring.otel import (
    _NoopSpan,
    _NoopTracer,
    extract_trace_context,
    get_tracer,
    inject_trace_context,
    is_enabled,
    span,
)


class TestNoopTracer:
    def test_start_span_returns_noop(self):
        tracer = _NoopTracer()
        s = tracer.start_span("test")
        assert isinstance(s, _NoopSpan)

    def test_noop_span_is_recording(self):
        s = _NoopSpan()
        assert s.is_recording is False

    def test_noop_span_methods_noop(self):
        s = _NoopSpan()
        s.set_attribute("k", "v")
        s.add_event("e")
        s.record_exception(Exception())
        s.set_status("ok")
        s.end()
        with s:
            pass


class TestIsEnabled:
    def test_default_disabled(self):
        with patch.dict(os.environ, {}, clear=True):
            assert is_enabled() is False

    def test_enabled_via_env(self):
        with patch.dict(os.environ, {"MAOP_OTEL_ENABLED": "1"}):
            assert is_enabled() is True

    def test_enabled_via_true(self):
        with patch.dict(os.environ, {"MAOP_OTEL_ENABLED": "true"}):
            assert is_enabled() is True

    def test_disabled_via_other(self):
        with patch.dict(os.environ, {"MAOP_OTEL_ENABLED": "0"}):
            assert is_enabled() is False


class TestGetTracer:
    def test_returns_noop_when_disabled(self):
        with patch.dict(os.environ, {}, clear=True):
            tracer = get_tracer("test")
            assert isinstance(tracer, _NoopTracer)

    def test_returns_noop_when_otel_not_installed(self):
        with patch.dict(os.environ, {"MAOP_OTEL_ENABLED": "1"}), \
             patch("maop.core.monitoring.otel._OTELE_AVAILABLE", False):
            tracer = get_tracer("test")
            assert isinstance(tracer, _NoopTracer)


class TestSpan:
    def test_noop_span_context_manager(self):
        tracer = _NoopTracer()
        with span(tracer, "test") as s:
            assert isinstance(s, _NoopSpan)

    def test_span_with_trace_id(self):
        tracer = _NoopTracer()
        with span(tracer, "test", trace_id="abc123") as s:
            assert isinstance(s, _NoopSpan)

    def test_span_with_attributes(self):
        tracer = _NoopTracer()
        with span(tracer, "test", attributes={"key": "val"}) as s:
            assert isinstance(s, _NoopSpan)


class TestInjectExtractContext:
    def test_inject_noop_when_disabled(self):
        carrier: dict = {}
        inject_trace_context(carrier)
        assert "traceparent" not in carrier

    def test_extract_noop_when_disabled(self):
        result = extract_trace_context({"traceparent": "00-abc-123-01"})
        assert result is None


# --- Merged from test_core_coverage2.py (OTel part) ---

# ── OTel disabled branches ──────────────────────────────────────────

class TestOtelDisabled:
    def test_is_enabled_false_default(self, monkeypatch):
        monkeypatch.delenv("MAOP_OTEL_ENABLED", raising=False)
        from maop.core.monitoring.otel import is_enabled
        assert is_enabled() is False

    def test_is_enabled_true(self, monkeypatch):
        monkeypatch.setenv("MAOP_OTEL_ENABLED", "1")
        from maop.core.monitoring.otel import is_enabled
        assert is_enabled() is True

    def test_get_tracer_returns_noop_when_disabled(self, monkeypatch):
        monkeypatch.delenv("MAOP_OTEL_ENABLED", raising=False)
        from maop.core.monitoring.otel import get_tracer, _NoopTracer
        assert isinstance(get_tracer("maop"), _NoopTracer)

    def test_span_with_noop_tracer(self, monkeypatch):
        monkeypatch.delenv("MAOP_OTEL_ENABLED", raising=False)
        from maop.core.monitoring.otel import get_tracer, span, _NoopSpan
        tracer = get_tracer("maop")
        with span(tracer, "op", attributes={"a": 1}, trace_id="t1") as s:
            assert isinstance(s, _NoopSpan)

    def test_setup_provider_disabled(self, monkeypatch):
        monkeypatch.delenv("MAOP_OTEL_ENABLED", raising=False)
        from maop.core.monitoring.otel import setup_provider
        setup_provider()  # should no-op without raising

    def test_inject_trace_context_disabled(self, monkeypatch):
        monkeypatch.delenv("MAOP_OTEL_ENABLED", raising=False)
        from maop.core.monitoring.otel import inject_trace_context
        carrier: dict[str, str] = {}
        inject_trace_context(carrier)
        assert carrier == {}

    def test_extract_trace_context_disabled(self, monkeypatch):
        monkeypatch.delenv("MAOP_OTEL_ENABLED", raising=False)
        from maop.core.monitoring.otel import extract_trace_context
        assert extract_trace_context({}) is None

    def test_noop_span_methods(self):
        from maop.core.monitoring.otel import _NoopSpan
        s = _NoopSpan()
        with s as ctx:
            assert ctx is s
        s.set_attribute("k", 1)
        s.add_event("e")
        s.record_exception(Exception())
        s.set_status(0)
        s.end()
        assert s.is_recording is False

