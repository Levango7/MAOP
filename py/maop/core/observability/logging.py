"""Structured logging with trace correlation.

Wraps :class:`~maop.core.monitoring.monitoring.StructuredLogger` and
:class:`~maop.core.monitoring.monitoring.JsonLogFormatter` to add
automatic trace-id / span-id correlation.  When an OTel span is active,
its trace-id is stamped onto every log record so logs can be filtered
by trace in Loki/ELK.

Edition behaviour
-----------------
* **Personal** — plain JSON structured logging (no OTel dependency).
* **Enterprise** — JSON logging + OTel trace correlation via the
  ``LogRecord`` ``trace_id`` / ``span_id`` extras.
"""
from __future__ import annotations

import logging
import os
from collections.abc import Generator
from contextlib import contextmanager
from typing import Any

from maop.config.edition import Edition, get_edition
from maop.core.monitoring.monitoring import (
    StructuredLogger,
    setup_json_logging,
)

logger = logging.getLogger(__name__)

# ── Trace correlation filter ───────────────────────────────────────
class TraceCorrelationFilter(logging.Filter):
    """Inject the active OTel trace_id / span_id onto every LogRecord.

    When no span is active (or OTel is not installed), the filter is a
    no-op — the record passes through unchanged.  This keeps the
    Personal-mode logging path at zero overhead.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if not getattr(record, "trace_id", None):
            trace_id, span_id = _current_otel_ids()
            if trace_id:
                record.trace_id = trace_id
            if span_id:
                record.span_id = span_id
        return True


def _current_otel_ids() -> tuple[str, str]:
    """Return (trace_id_hex, span_id_hex) of the active OTel span."""
    try:
        from opentelemetry import trace as otel_trace  # type: ignore[attr-defined]
        span = otel_trace.get_current_span()
        ctx = span.get_span_context() if span else None
        if ctx and ctx.is_valid:
            return f"{ctx.trace_id:032x}", f"{ctx.span_id:016x}"
    except ImportError:
        pass
    except Exception:
        pass
    return "", ""


# ── Setup ──────────────────────────────────────────────────────────
_setup_done = False


def setup_logging(
    level: str = "INFO",
    log_file: str | os.PathLike[str] | None = None,
    *,
    force: bool = False,
) -> logging.Logger:
    """Configure structured JSON logging + trace correlation.

    Idempotent: subsequent calls with ``force=False`` are no-ops.

    Returns the root logger.
    """
    global _setup_done
    if _setup_done and not force:
        return logging.getLogger()

    root = setup_json_logging(level=level, log_file=log_file)

    # Attach the trace-correlation filter to every handler.
    trace_filter = TraceCorrelationFilter()
    for handler in root.handlers:
        if not any(isinstance(f, TraceCorrelationFilter) for f in handler.filters):
            handler.addFilter(trace_filter)

    _setup_done = True
    logger.debug(
        "[observability.logging] setup complete | level=%s | edition=%s",
        level, get_edition().value,
    )
    return root


# ── Logger factory ─────────────────────────────────────────────────
def get_logger(name: str = "MAOP") -> StructuredLogger:
    """Return a :class:`StructuredLogger` with trace correlation enabled.

    The returned logger stamps ``trace_id`` / ``span_id`` onto every
    record when a span is active.  In Personal mode (no OTel) the
    fields are simply absent — the JSON schema is forward-compatible.
    """
    return StructuredLogger(name)


# ── Log context manager ────────────────────────────────────────────
@contextmanager
def log_context(
    *,
    trace_id: str = "",
    span_id: str = "",
    **extra: Any,
) -> Generator[StructuredLogger, None, None]:
    """Bind trace context + extra fields for a block of log calls.

    >>> with log_context(trace_id="abc123", user="alice") as log:
    ...     log.info("processing request")
    ...     log.error("something failed", retry=3)

    The extra kwargs are stamped onto every record emitted inside the
    ``with`` block via a per-call merge — we return a
    :class:`StructuredLogger` pre-bound to the context.
    """
    log = StructuredLogger("MAOP", trace_id=trace_id, span_id=span_id)
    # Stash extras so callers can use log.info(msg) and still get them.
    # StructuredLogger.info accepts **kwargs, so we wrap the methods.
    if extra:
        _wrap_with_extras(log, extra)
    yield log


def _wrap_with_extras(log: StructuredLogger, extra: dict[str, Any]) -> None:
    """Wrap the logger's emit methods to merge ``extra`` into every call."""
    for method_name in ("info", "warning", "error", "debug"):
        original = getattr(log, method_name)

        def make_wrapper(orig: Any, extras: dict[str, Any]) -> Any:
            def wrapper(msg: str, **kwargs: Any) -> None:
                merged = {**extras, **kwargs}
                orig(msg, **merged)
            return wrapper

        setattr(log, method_name, make_wrapper(original, extra))


# ── Edition-aware summary ──────────────────────────────────────────
def logging_summary() -> dict[str, Any]:
    """Return a JSON-serialisable summary for the dashboard panel."""
    root = logging.getLogger()
    return {
        "edition": get_edition().value,
        "level": logging.getLevelName(root.level),
        "handlers": [
            {
                "type": type(h).__name__,
                "level": logging.getLevelName(h.level),
            }
            for h in root.handlers
        ],
        "trace_correlation": _otel_available(),
        "enterprise_mode": get_edition() == Edition.ENTERPRISE,
    }


def _otel_available() -> bool:
    """Return True when the OTel API package is importable."""
    try:
        import opentelemetry  # noqa: F401
        return True
    except ImportError:
        return False


__all__ = [
    "TraceCorrelationFilter",
    "get_logger",
    "log_context",
    "logging_summary",
    "setup_logging",
]