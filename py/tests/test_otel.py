"""Tests for OpenTelemetry integration (maop.core.otel)."""
from __future__ import annotations

import os
from unittest.mock import patch

from maop.core.otel import (
    _NoopSpan,
    _NoopTracer,
    get_tracer,
    inject_trace_context,
    is_enabled,
    span,
    extract_trace_context,
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
        with patch.dict(os.environ, {"MAOP_OTEL_ENABLED": "1"}):
            with patch("maop.core.otel._OTELE_AVAILABLE", False):
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