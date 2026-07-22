"""MAOP Monitoring - Structured logging and Prometheus-compatible metrics.

Provides:
  1. StructuredLogger: JSON-formatted structured logging with trace IDs
  2. MetricsCollector: Prometheus-compatible metrics (counter, gauge, histogram)

Integrates with the Dashboard for /api/metrics endpoint.
"""

from __future__ import annotations

import json
import logging
import threading
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any


logger = logging.getLogger(__name__)

# ── JSON Log Formatter (for ELK / Loki centralized collection) ──

class JsonLogFormatter(logging.Formatter):
    """A ``logging.Formatter`` that emits each ``LogRecord`` as a single JSON
    line, suitable for ingestion by centralized log collectors (ELK, Loki,
    Fluentd, Vector).

    Output schema::

        {"ts": "2026-07-17T...", "level": "INFO", "logger": "maop.core.foo",
         "msg": "Human message", "module": "foo", "func": "bar", "line": 42,
         "trace_id": "..."}

    Extra fields attached via ``logger.info("...", extra={"key": value})`` are
    transparently passed through into the JSON object (reserved keys —
    ``ts/level/logger/msg/module/func/line`` — are not overwritten by extras).
    """

    # Keys we always set ourselves; extras with these names are ignored to
    # protect the schema contract.
    _RESERVED_KEYS = frozenset(
        {"ts", "level", "logger", "msg", "module", "func", "line", "trace_id"}
    )

    def format(self, record: logging.LogRecord) -> str:
        # ISO8601 timestamp with timezone, millisecond precision.
        ts = datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat()

        payload: dict[str, Any] = {
            "ts": ts,
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "module": record.module,
            "func": record.funcName,
            "line": record.lineno,
        }

        # Trace-id propagation: prefer ``record.trace_id`` (set via extra or
        # StructuredLogger), fall back to a context-local value if present.
        trace_id = getattr(record, "trace_id", None)
        if trace_id:
            payload["trace_id"] = trace_id

        # Pass through arbitrary extra fields. ``__dict__`` of a LogRecord
        # contains many standard attributes; we filter to only the ones not
        # part of the LogRecord base contract.
        for key, value in record.__dict__.items():
            if key.startswith("_") or key in self._RESERVED_KEYS:
                continue
            if key in _STANDARD_LOGRECORD_ATTRS:
                continue
            payload[key] = value

        # Exception info, if requested via exc_info=True.
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)

        return json.dumps(payload, ensure_ascii=False, default=str)


# Attributes that ``logging.LogRecord`` always sets itself.  We use this to
# avoid leaking internal bookkeeping into the JSON output when callers pass
# ``extra={...}``.
_STANDARD_LOGRECORD_ATTRS = frozenset(
    {
        "args", "asctime", "created", "exc_info", "exc_text", "filename",
        "funcName", "levelname", "levelno", "lineno", "message", "module",
        "msecs", "msg", "name", "pathname", "process", "processName",
        "relativeCreated", "stack_info", "thread", "threadName",
        "taskName",  # Python 3.12+ adds taskName for asyncio task context.
    }
)


def setup_json_logging(
    level: str = "INFO",
    log_file: Any = None,
) -> logging.Logger:
    """Configure the root logger to emit JSON-formatted lines.

    Parameters
    ----------
    level:
        Logging level for the root logger (e.g. ``"INFO"``, ``"DEBUG"``).
    log_file:
        Optional path (str or Path) to a log file.  When given, a
        ``FileHandler`` writing JSON lines is attached in addition to the
        stderr ``StreamHandler``.

    Returns
    -------
    logging.Logger
        The root logger, now configured with :class:`JsonLogFormatter`.

    Notes
    -----
    * Existing handlers on the root logger are removed to avoid duplicate
      output when called multiple times (e.g. in tests).
    * The ``MAOP`` namespace logger is explicitly set to the requested level
      so that sub-loggers (``MAOP.core.*``, ``MAOP.dashboard.*`` …) inherit it.
    * :class:`StructuredLogger` continues to work unchanged — it formats its
      own JSON payload and passes it as the record message; the
      ``JsonLogFormatter`` will wrap that string in ``msg``.  To avoid double
      encoding, ``StructuredLogger`` loggers keep their plain
      ``%(message)s`` formatter and are not touched here.
    """
    root = logging.getLogger()

    # Remove existing handlers to make the function idempotent.
    for handler in list(root.handlers):
        root.removeHandler(handler)

    formatter = JsonLogFormatter()

    # stderr stream handler — always present.
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    root.addHandler(stream_handler)

    # Optional file handler.
    if log_file is not None:
        from pathlib import Path
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_path, encoding="utf-8")
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)

    numeric_level = getattr(logging, level.upper(), logging.INFO)
    root.setLevel(numeric_level)

    # Set the MAOP namespace logger level so sub-loggers inherit.
    MAOP_logger = logging.getLogger("MAOP")
    MAOP_logger.setLevel(numeric_level)

    return root


# ── Structured Logger ───────────────────────────────────────────

class StructuredLogger:
    """JSON-formatted structured logging with trace/correlation IDs.

    Output format:
        {"ts": "2026-07-13T07:30:00Z", "level": "INFO", "msg": "...",
         "trace_id": "...", "span_id": "...", "module": "...", ...}
    """

    def __init__(
        self,
        name: str = "MAOP",
        *,
        trace_id: str = "",
        span_id: str = "",
        log_dir: Any = None,
    ):
        self.name = name
        self.trace_id = trace_id
        self.span_id = span_id
        self._logger = logging.getLogger(name)
        if log_dir is not None:
            from pathlib import Path
            log_dir_path = Path(log_dir)
            log_dir_path.mkdir(parents=True, exist_ok=True)
            fh = logging.FileHandler(log_dir_path / "MAOP-structured.log", encoding="utf-8")
            fh.setFormatter(logging.Formatter("%(message)s"))
            self._logger.addHandler(fh)
            self._logger.setLevel(logging.DEBUG)

    def _format(self, level: str, msg: str, **kwargs: Any) -> str:
        """Format a structured log entry."""
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": level,
            "msg": msg,
            "module": self.name,
        }
        if self.trace_id:
            entry["trace_id"] = self.trace_id
        if self.span_id:
            entry["span_id"] = self.span_id
        entry.update(kwargs)
        return json.dumps(entry, ensure_ascii=False, default=str)

    def log(self, *, phase: str = "", level: str = "INFO", message: str = "", **kwargs: Any) -> None:
        """Generic log method matching maop_loop's calling convention."""
        level_upper = level.upper()
        formatted = self._format(level_upper, message, phase=phase, **kwargs)
        if level_upper == "ERROR":
            self._logger.error(formatted)
        elif level_upper in ("WARN", "WARNING"):
            self._logger.warning(formatted)
        elif level_upper == "DEBUG":
            self._logger.debug(formatted)
        else:
            self._logger.info(formatted)

    def info(self, msg: str, **kwargs: Any) -> None:
        self._logger.info(self._format("INFO", msg, **kwargs))

    def warning(self, msg: str, **kwargs: Any) -> None:
        self._logger.warning(self._format("WARN", msg, **kwargs))

    def error(self, msg: str, **kwargs: Any) -> None:
        self._logger.error(self._format("ERROR", msg, **kwargs))

    def debug(self, msg: str, **kwargs: Any) -> None:
        self._logger.debug(self._format("DEBUG", msg, **kwargs))

    def with_trace(self, trace_id: str, span_id: str = "") -> StructuredLogger:
        """Create a child logger with trace context."""
        return StructuredLogger(
            self.name,
            trace_id=trace_id or self.trace_id,
            span_id=span_id or self.span_id,
        )


# ── Metrics ─────────────────────────────────────────────────────

class Counter:
    """A monotonically increasing counter (Prometheus counter)."""

    def __init__(self, name: str, help_text: str = "", label_names: list[str] | None = None):
        self.name = name
        self.help_text = help_text
        self.label_names = label_names or []
        self._values: dict[str, float] = defaultdict(float)
        self._lock = threading.Lock()

    def inc(self, value: float = 1.0, labels: dict[str, str] | None = None) -> None:
        """Increment the counter."""
        key = self._label_key(labels)
        with self._lock:
            self._values[key] += value

    def get(self, labels: dict[str, str] | None = None) -> float:
        """Get the current value."""
        key = self._label_key(labels)
        with self._lock:
            return self._values[key]

    def _label_key(self, labels: dict[str, str] | None) -> str:
        if not labels:
            return ""
        return "|".join(f"{k}={v}" for k, v in sorted(labels.items()))

    def to_prometheus(self) -> str:
        """Export in Prometheus text format."""
        lines = []
        if self.help_text:
            lines.append(f"# HELP {self.name} {self.help_text}")
        lines.append(f"# TYPE {self.name} counter")
        for key, value in self._values.items():
            if key:
                lines.append(f"{self.name}{{{key}}} {value}")
            else:
                lines.append(f"{self.name} {value}")
        return "\n".join(lines)


class Gauge:
    """A value that can go up or down (Prometheus gauge)."""

    def __init__(self, name: str, help_text: str = "", label_names: list[str] | None = None):
        self.name = name
        self.help_text = help_text
        self.label_names = label_names or []
        self._values: dict[str, float] = defaultdict(float)
        self._lock = threading.Lock()

    def set(self, value: float, labels: dict[str, str] | None = None) -> None:
        """Set the gauge value."""
        key = self._label_key(labels)
        with self._lock:
            self._values[key] = value

    def inc(self, value: float = 1.0, labels: dict[str, str] | None = None) -> None:
        key = self._label_key(labels)
        with self._lock:
            self._values[key] += value

    def dec(self, value: float = 1.0, labels: dict[str, str] | None = None) -> None:
        key = self._label_key(labels)
        with self._lock:
            self._values[key] -= value

    def get(self, labels: dict[str, str] | None = None) -> float:
        key = self._label_key(labels)
        with self._lock:
            return self._values[key]

    def _label_key(self, labels: dict[str, str] | None) -> str:
        if not labels:
            return ""
        return "|".join(f"{k}={v}" for k, v in sorted(labels.items()))

    def to_prometheus(self) -> str:
        lines = []
        if self.help_text:
            lines.append(f"# HELP {self.name} {self.help_text}")
        lines.append(f"# TYPE {self.name} gauge")
        for key, value in self._values.items():
            if key:
                lines.append(f"{self.name}{{{key}}} {value}")
            else:
                lines.append(f"{self.name} {value}")
        return "\n".join(lines)


class Histogram:
    """A distribution of values (Prometheus histogram).

    Uses exponential bucket boundaries by default.
    """

    DEFAULT_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, float("inf"))

    def __init__(
        self,
        name: str,
        help_text: str = "",
        buckets: tuple[float, ...] | None = None,
    ):
        self.name = name
        self.help_text = help_text
        self.buckets = buckets or self.DEFAULT_BUCKETS
        self._counts: dict[float, int] = {b: 0 for b in self.buckets}
        self._sum: float = 0.0
        self._total: int = 0
        self._lock = threading.Lock()

    def observe(self, value: float) -> None:
        """Record an observation."""
        with self._lock:
            self._sum += value
            self._total += 1
            for b in self.buckets:
                if value <= b:
                    self._counts[b] += 1

    def to_prometheus(self) -> str:
        lines = []
        if self.help_text:
            lines.append(f"# HELP {self.name} {self.help_text}")
        lines.append(f"# TYPE {self.name} histogram")

        for b in self.buckets:
            if b == float("inf"):
                lines.append(f"{self.name}_bucket{{le=\"+Inf\"}} {self._total}")
            else:
                lines.append(f"{self.name}_bucket{{le=\"{b}\"}} {self._counts[b]}")

        lines.append(f"{self.name}_sum {self._sum}")
        lines.append(f"{self.name}_count {self._total}")
        return "\n".join(lines)


# ── Metrics Collector ───────────────────────────────────────────

class MetricsCollector:
    """Central registry for all metrics."""

    def __init__(self):
        self._counters: dict[str, Counter] = {}
        self._gauges: dict[str, Gauge] = {}
        self._histograms: dict[str, Histogram] = {}
        self._lock = threading.Lock()

    def counter(self, name: str, help_text: str = "") -> Counter:
        """Get or create a counter."""
        with self._lock:
            if name not in self._counters:
                self._counters[name] = Counter(name, help_text)
            return self._counters[name]

    def gauge(self, name: str, help_text: str = "") -> Gauge:
        """Get or create a gauge."""
        with self._lock:
            if name not in self._gauges:
                self._gauges[name] = Gauge(name, help_text)
            return self._gauges[name]

    def histogram(
        self,
        name: str,
        help_text: str = "",
        buckets: tuple[float, ...] | None = None,
    ) -> Histogram:
        """Get or create a histogram."""
        with self._lock:
            if name not in self._histograms:
                self._histograms[name] = Histogram(name, help_text, buckets)
            return self._histograms[name]

    def to_prometheus(self) -> str:
        """Export all metrics in Prometheus text format."""
        parts = []
        for c in self._counters.values():
            parts.append(c.to_prometheus())
        for g in self._gauges.values():
            parts.append(g.to_prometheus())
        for h in self._histograms.values():
            parts.append(h.to_prometheus())
        return "\n".join(parts)

    def to_json(self) -> dict[str, Any]:
        """Export metrics as JSON (for Dashboard)."""
        result: dict[str, Any] = {}
        for name, c in self._counters.items():
            result[name] = {"type": "counter", "values": dict(c._values)}
        for name, g in self._gauges.items():
            result[name] = {"type": "gauge", "values": dict(g._values)}
        for name, h in self._histograms.items():
            result[name] = {
                "type": "histogram",
                "sum": h._sum,
                "count": h._total,
                "buckets": {str(b): c for b, c in h._counts.items()},
            }
        return result


# ── Global instance ─────────────────────────────────────────────

metrics = MetricsCollector()

# Pre-defined MAOP metrics
MAOP_DELEGATIONS_TOTAL = metrics.counter("MAOP_delegations_total", "Total task delegations")
MAOP_DELEGATIONS_SUCCESS = metrics.counter("MAOP_delegations_success", "Successful delegations")
MAOP_DELEGATIONS_FAILED = metrics.counter("MAOP_delegations_failed", "Failed delegations")
MAOP_DELEGATION_DURATION = metrics.histogram("MAOP_delegation_duration_seconds", "Delegation duration")
MAOP_ACTIVE_AGENTS = metrics.gauge("MAOP_active_agents", "Number of active agents")
MAOP_MEMORY_ENTRIES = metrics.gauge("MAOP_memory_entries", "Number of memory entries")
MAOP_QUEUE_PENDING = metrics.gauge("MAOP_queue_pending", "Pending messages in queue")
MAOP_CIRCUIT_BREAKER_STATE = metrics.gauge("MAOP_circuit_breaker_state", "Circuit breaker state (1=closed, 0.5=half, 0=open)")
