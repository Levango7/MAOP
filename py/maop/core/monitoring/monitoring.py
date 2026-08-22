"""MAOP Monitoring - Structured logging and Prometheus-compatible metrics.

Provides:
  1. StructuredLogger: JSON-formatted structured logging with trace IDs
  2. MetricsCollector: Prometheus-compatible metrics (counter, gauge, histogram)

Integrates with the Dashboard for /api/metrics endpoint.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, ClassVar

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
    # H3 fix: 敏感数据脱敏正则模式（与 guardrail.py sensitive-patterns 对齐）
    # 匹配常见密钥格式，命中时替换为 [REDACTED:<type>]
    _SENSITIVE_PATTERNS: ClassVar[list[tuple[re.Pattern[str], str]]] = [
        (re.compile(r"sk-[a-zA-Z0-9]{20,}"), "[REDACTED:openai_key]"),
        (re.compile(r"AKIA[0-9A-Z]{16}"), "[REDACTED:aws_key]"),
        (re.compile(r"(?i)(api[_-]?key|apikey)\s*[:=]\s*[\"\']?[A-Za-z0-9_\-]{16,}"), "[REDACTED:api_key]"),
        (re.compile(r"(?i)(password|passwd|pwd)\s*[:=]\s*[\"\']?[^\s\"\',}]{4,}"), "[REDACTED:password]"),
        (re.compile(r"(?i)(secret|token)\s*[:=]\s*[\"\']?[A-Za-z0-9_\-\.]{16,}"), "[REDACTED:secret]"),
        (re.compile(r"(?i)bearer\s+[A-Za-z0-9_\-\.]{20,}"), "[REDACTED:bearer_token]"),
    ]

    @classmethod
    def _redact_sensitive(cls, value: Any) -> Any:
        """对字符串值进行敏感数据脱敏，非字符串原样返回。

        仅对常见密钥格式做正则替换，避免对结构化数据深度遍历的
        性能开销。命中时替换为 ``[REDACTED:<type>]`` 占位符。
        """
        if not isinstance(value, str) or not value:
            return value
        redacted = value
        for pattern, replacement in cls._SENSITIVE_PATTERNS:
            if pattern.search(redacted):
                redacted = pattern.sub(replacement, redacted)
        return redacted

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
            "msg": self._redact_sensitive(record.getMessage()),
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
            payload[key] = self._redact_sensitive(value) if isinstance(value, str) else value

        # Exception info, if requested via exc_info=True.
        if record.exc_info:
            payload["exc"] = self._redact_sensitive(self.formatException(record.exc_info))

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

# OPS-25 fix: cap per-metric label cardinality. Unbounded label values
# (e.g. task ids, user ids) would grow _values forever — a memory leak and
# a Prometheus scrape-size explosion. New label sets beyond the cap are
# folded into a single overflow series.
_MAX_LABEL_CARDINALITY = int(os.environ.get("MAOP_METRIC_MAX_CARDINALITY", "1000"))
_OVERFLOW_KEY = 'overflow="true"'


def _bounded_key(values: dict[str, float], key: str, metric_name: str) -> str:
    """Return key, or the overflow key if adding it would exceed the cap."""
    if key in values or len(values) < _MAX_LABEL_CARDINALITY:
        return key
    if _OVERFLOW_KEY not in values:
        logger.warning(
            "Metric %s exceeded max label cardinality (%d); folding new "
            "label sets into overflow series",
            metric_name, _MAX_LABEL_CARDINALITY,
        )
    return _OVERFLOW_KEY


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
            # OPS-25 fix: bound label cardinality
            key = _bounded_key(self._values, key, self.name)
            self._values[key] += value

    def get(self, labels: dict[str, str] | None = None) -> float:
        """Get the current value."""
        key = self._label_key(labels)
        with self._lock:
            # OPS-25 fix: read must not insert (defaultdict side effect)
            return self._values.get(key, 0.0)

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
        # OPS-27 fix: iterate under the same lock as inc()/set()/dec() so a
        # concurrent metric update cannot raise RuntimeError during dict
        # iteration (which would 500 the /api/prometheus endpoint).
        with self._lock:
            for key, value in list(self._values.items()):
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
            key = _bounded_key(self._values, key, self.name)  # OPS-25 fix
            self._values[key] = value

    def inc(self, value: float = 1.0, labels: dict[str, str] | None = None) -> None:
        key = self._label_key(labels)
        with self._lock:
            key = _bounded_key(self._values, key, self.name)  # OPS-25 fix
            self._values[key] += value

    def dec(self, value: float = 1.0, labels: dict[str, str] | None = None) -> None:
        key = self._label_key(labels)
        with self._lock:
            key = _bounded_key(self._values, key, self.name)  # OPS-25 fix
            self._values[key] -= value

    def get(self, labels: dict[str, str] | None = None) -> float:
        key = self._label_key(labels)
        with self._lock:
            # OPS-25 fix: read must not insert (defaultdict side effect)
            return self._values.get(key, 0.0)

    def _label_key(self, labels: dict[str, str] | None) -> str:
        if not labels:
            return ""
        return "|".join(f"{k}={v}" for k, v in sorted(labels.items()))

    def to_prometheus(self) -> str:
        lines = []
        if self.help_text:
            lines.append(f"# HELP {self.name} {self.help_text}")
        lines.append(f"# TYPE {self.name} gauge")
        # OPS-27 fix: iterate under the same lock as inc()/set()/dec().
        with self._lock:
            for key, value in list(self._values.items()):
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
        self._counts: dict[float, int] = dict.fromkeys(self.buckets, 0)
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

        # OPS-27 fix: read consistent snapshot under the observe() lock.
        with self._lock:
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
        # OPS-27 fix: snapshot the metric collections under the registry lock
        # so a concurrent counter()/gauge()/histogram() registration cannot
        # mutate the dict mid-iteration.
        with self._lock:
            counters = list(self._counters.values())
            gauges = list(self._gauges.values())
            histograms = list(self._histograms.values())
        parts = []
        for c in counters:
            parts.append(c.to_prometheus())
        for g in gauges:
            parts.append(g.to_prometheus())
        for h in histograms:
            parts.append(h.to_prometheus())
        return "\n".join(parts)

    def to_json(self) -> dict[str, Any]:
        """Export metrics as JSON (for Dashboard)."""
        # OPS-27 fix: snapshot collections under the registry lock, then copy
        # each metric's internal dict under its own lock to avoid RuntimeError
        # from concurrent inc()/observe() updates.
        with self._lock:
            counter_items = list(self._counters.items())
            gauge_items = list(self._gauges.items())
            hist_items = list(self._histograms.items())
        result: dict[str, Any] = {}
        for name, c in counter_items:
            with c._lock:
                result[name] = {"type": "counter", "values": dict(c._values)}
        for name, g in gauge_items:
            with g._lock:
                result[name] = {"type": "gauge", "values": dict(g._values)}
        for name, h in hist_items:
            with h._lock:
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
# H8 修复：为以下 8 个核心业务指标补充文档注释与调用方说明。
# 原问题：这些指标仅在 monitoring.py 中定义，全代码树无任何 .inc/.set/.observe
# 调用，导致运维盲区。现补充注释并在对应业务逻辑处添加调用。

# MAOP_DELEGATIONS_TOTAL — 任务委派总数计数器。
# 预期调用位置：dispatch_core.py 任务派发时 .inc()；maop_plan.py plan 执行时 .inc()。
MAOP_DELEGATIONS_TOTAL = metrics.counter("MAOP_delegations_total", "Total task delegations")

# MAOP_DELEGATIONS_SUCCESS — 任务委派成功数计数器。
# 预期调用位置：dispatch_core.py 任务成功完成时 .inc()。
MAOP_DELEGATIONS_SUCCESS = metrics.counter("MAOP_delegations_success", "Successful delegations")

# MAOP_DELEGATIONS_FAILED — 任务委派失败数计数器。
# 预期调用位置：dispatch_core.py 任务失败时 .inc()。
MAOP_DELEGATIONS_FAILED = metrics.counter("MAOP_delegations_failed", "Failed delegations")

# MAOP_DELEGATION_DURATION — 任务委派耗时直方图（秒）。
# 预期调用位置：dispatch_core.py 任务完成时 .observe(duration)；
# maop_plan.py plan 执行完成时 .observe(duration)。
MAOP_DELEGATION_DURATION = metrics.histogram("MAOP_delegation_duration_seconds", "Delegation duration")

# MAOP_ACTIVE_AGENTS — 活跃 agent 数量仪表。
# 预期调用位置：agent lifecycle 模块在 agent 启停时 .set(count) 或 .inc()/.dec()。
MAOP_ACTIVE_AGENTS = metrics.gauge("MAOP_active_agents", "Number of active agents")

# MAOP_MEMORY_ENTRIES — 记忆条目数量仪表。
# 预期调用位置：memory 模块在写入/删除条目时 .set(count) 或 .inc()/.dec()。
MAOP_MEMORY_ENTRIES = metrics.gauge("MAOP_memory_entries", "Number of memory entries")

# MAOP_QUEUE_PENDING — 队列待处理消息数仪表。
# 预期调用位置：queue 模块在入队/出队时 .set(count) 或 .inc()/.dec()。
MAOP_QUEUE_PENDING = metrics.gauge("MAOP_queue_pending", "Pending messages in queue")

# MAOP_CIRCUIT_BREAKER_STATE — 熔断器状态仪表（1=闭合, 0.5=半开, 0=断开）。
# 预期调用位置：circuit_breaker 模块在状态转换时 .set(state)。
MAOP_CIRCUIT_BREAKER_STATE = metrics.gauge("MAOP_circuit_breaker_state", "Circuit breaker state (1=closed, 0.5=half, 0=open)")

# Phase γ-1: SLA-aware scheduling metrics.
# Follows the existing registration pattern (factory methods without
# label_names); gauge labels are passed at runtime via the ``labels``
# dict in inc/dec/set, which works regardless of the label_names metadata.
MAOP_TASK_DEADLINE_SECONDS = metrics.histogram(
    "MAOP_task_deadline_seconds",
    "Remaining time to task deadline at completion (negative = missed)",
)
MAOP_TASK_SLA_VIOLATION_TOTAL = metrics.counter(
    "MAOP_task_sla_violation_total",
    "Total SLA deadline violations",
)
MAOP_TASK_PRIORITY_DISTRIBUTION = metrics.gauge(
    "MAOP_task_priority_distribution",
    "In-flight task count per priority level (label=priority)",
)
MAOP_TASK_SLA_TIER_DISTRIBUTION = metrics.gauge(
    "MAOP_task_sla_tier_distribution",
    "In-flight task count per SLA tier (label=tier)",
)

# Routing decision-mode metric (set by RouteScorer).
# MAOP_route_decision_mode — label=mode, currently always "weighted_sum"
#   (the multi-objective TOPSIS path was removed in the P0-3 cleanup).
MAOP_ROUTE_DECISION_MODE = metrics.gauge(
    "MAOP_route_decision_mode",
    "Routing decision mode (currently only weighted_sum)",
)

# Phase γ-2: Priority queue + soft preemption metrics.
#
# MAOP_task_preemption_total — under soft preemption this counts
#   "would-be preemption" events: a higher-priority task arrived while
#   all workers were busy and at least one running task had a lower
#   priority. The running task is *not* cancelled (checkpoint is not
#   wired into the execution path, so true cancellation would lose
#   mid-task progress); the high-priority task is queued ahead and
#   the event is recorded so monitoring demand for true preemption is
#   visible. When/if the checkpoint is integrated, this same counter
#   will record actual cancellations.
# MAOP_priority_queue_size — gauge per priority level (label=priority).
# MAOP_priority_queue_wait_seconds — histogram of queue residency time
#   per priority level. The Histogram class does not carry labels, so
#   the priority is encoded by observing into a per-priority histogram
#   registered under ``MAOP_priority_queue_wait_seconds_<prio>``.
MAOP_TASK_PREEMPTION_TOTAL = metrics.counter(
    "MAOP_task_preemption_total",
    "Task preemption events (soft preemption: would-be preemptions; "
    "true preemption: actual cancellations)",
)
MAOP_PRIORITY_QUEUE_SIZE = metrics.gauge(
    "MAOP_priority_queue_size",
    "Priority queue length per priority level (label=priority)",
)
# Per-priority wait-time histograms. Histograms do not accept labels in
# this registry, so we expose one histogram per priority level. Callers
# fetch them via ``get_priority_wait_histogram(priority)``.
_MAOP_PRIORITY_WAIT_HISTOGRAMS: dict[int, Histogram] = {}


def get_priority_wait_histogram(priority: int) -> Histogram:
    """Return (creating if needed) the wait-time histogram for a priority level."""
    if priority not in _MAOP_PRIORITY_WAIT_HISTOGRAMS:
        _MAOP_PRIORITY_WAIT_HISTOGRAMS[priority] = metrics.histogram(
            f"MAOP_priority_queue_wait_seconds_p{priority}",
            f"Queue wait time for priority-{priority} tasks (seconds)",
        )
    return _MAOP_PRIORITY_WAIT_HISTOGRAMS[priority]


# Phase γ-5: ModelSelector ↔ LoadBalancer/Quota linkage metrics.
#
# MAOP_model_selection_quota_rejected_total — counter incremented each time a
#   model selection is rejected because the provider's quota (RPM/TPM) is
#   exhausted. Labelled by provider so dashboards can break down which
#   provider is the bottleneck.
# MAOP_model_selection_load_aware_total — counter incremented each time the
#   load-aware preference actually changes the selected model (i.e. the
#   picked model differs from the strategy-only winner because of load).
# MAOP_sticky_session_hit_total — counter incremented on each sticky
#   session cache hit (session_id maps to a non-expired agent).
# MAOP_sticky_session_miss_total — counter incremented on each sticky
#   session lookup that misses (no entry or expired).
# MAOP_sticky_session_active — gauge reflecting the current number of
#   non-expired sticky sessions tracked by the LoadBalancer.
MAOP_MODEL_SELECTION_QUOTA_REJECTED = metrics.counter(
    "MAOP_model_selection_quota_rejected_total",
    "Model selections rejected due to provider quota exhaustion (label=provider)",
)
MAOP_MODEL_SELECTION_LOAD_AWARE = metrics.counter(
    "MAOP_model_selection_load_aware_total",
    "Model selections where load-aware preference changed the outcome",
)
MAOP_STICKY_SESSION_HIT = metrics.counter(
    "MAOP_sticky_session_hit_total",
    "Sticky session cache hits",
)
MAOP_STICKY_SESSION_MISS = metrics.counter(
    "MAOP_sticky_session_miss_total",
    "Sticky session cache misses",
)
MAOP_STICKY_SESSION_ACTIVE = metrics.gauge(
    "MAOP_sticky_session_active",
    "Number of currently active sticky sessions",
)

# Phase δ-3: MCP permission scope + audit integration metrics.
#
# MAOP_mcp_call_audited_total — counter incremented for every MCP tool call
#   that passed through the MCPHub.call_tool permission/audit hook (regardless
#   of allow/deny outcome). Lets operators verify the hook is wired up; if this
#   counter is zero while MCP traffic is happening, the hub was constructed
#   without a permission_checker (legacy mode).
# MAOP_mcp_call_denied_total — counter labelled by ``reason`` (the matched
#   permission rule, e.g. ``denied_tools blacklist`` / ``user whitelist``).
#   Use this to spot spikes in a specific rejection class — e.g. a new server
#   with too-narrow allowed_users would show up as a step in one label.
# MAOP_mcp_call_allowed_total — counter of authorised MCP calls (success of
#   the call itself is tracked in the audit log, not in this counter, because
#   permission allow != runtime success).
MAOP_MCP_CALL_AUDITED_TOTAL = metrics.counter(
    "MAOP_mcp_call_audited_total",
    "Total MCP tool calls that went through the δ-3 permission/audit hook",
)
MAOP_MCP_CALL_DENIED_TOTAL = metrics.counter(
    "MAOP_mcp_call_denied_total",
    "MCP tool calls denied by the permission checker (label=reason)",
)
MAOP_MCP_CALL_ALLOWED_TOTAL = metrics.counter(
    "MAOP_mcp_call_allowed_total",
    "MCP tool calls authorised by the permission checker",
)

# Phase δ-4: MCP observability metrics.
#
# These complement the δ-3 permission/audit counters with transport-level
# telemetry: call volume / latency / errors per server+tool, plus the
# connected-server gauge and health-check outcome counter.
#
# The Counter and Gauge classes accept a ``labels`` dict at inc/dec/set
# time, so we register them without explicit ``label_names`` metadata
# (mirroring how the rest of the file is wired). Histograms in this
# module do not carry labels, so MAOP_mcp_call_duration_seconds is a
# single global distribution across all servers — per-server breakdown
# requires the labels-extension which is intentionally out of scope.
MAOP_MCP_CALLS_TOTAL = metrics.counter(
    "MAOP_mcp_calls_total",
    "Total MCP tool invocations (label=server,tool)",
)
MAOP_MCP_CALL_DURATION_SECONDS = metrics.histogram(
    "MAOP_mcp_call_duration_seconds",
    "MCP tool call latency in seconds (global; per-server label not supported)",
)
MAOP_MCP_SERVERS_CONNECTED = metrics.gauge(
    "MAOP_mcp_servers_connected",
    "Number of MCP servers currently connected",
)
MAOP_MCP_CALL_ERRORS_TOTAL = metrics.counter(
    "MAOP_mcp_call_errors_total",
    "MCP tool invocations that returned an error (label=server,tool)",
)
MAOP_MCP_HEALTH_CHECK_TOTAL = metrics.counter(
    "MAOP_mcp_health_check_total",
    "MCP server health checks (label=server,result healthy|unhealthy)",
)

# Phase δ-5: MCP tool result cache + per-server concurrency + RPM limiting.
#
# These five metrics give operators visibility into the δ-5 resilience
# layer added on top of MCPHub.call_tool:
#
# MAOP_mcp_cache_hit_total / MAOP_mcp_cache_miss_total — counters labelled
#   by ``server``. A rising hit ratio means repeated identical calls are
#   being served from cache instead of re-hitting the external MCP server.
# MAOP_mcp_cache_eviction_total — counter (no labels) of LRU evictions
#   from the MCP result cache; sustained growth suggests max_entries is
#   too small for the working set.
# MAOP_mcp_concurrent_active — gauge labelled by ``server`` reflecting the
#   current in-flight call count per server; if this plateaus at the
#   configured limit, callers are blocking on the concurrency semaphore.
# MAOP_mcp_rate_limited_total — counter labelled by ``server`` of calls
#   rejected by the per-server RPM limiter before reaching the transport.
MAOP_MCP_CACHE_HIT_TOTAL = metrics.counter(
    "MAOP_mcp_cache_hit_total",
    "MCP tool call cache hits (label=server)",
)
MAOP_MCP_CACHE_MISS_TOTAL = metrics.counter(
    "MAOP_mcp_cache_miss_total",
    "MCP tool call cache misses (label=server)",
)
MAOP_MCP_CACHE_EVICTION_TOTAL = metrics.counter(
    "MAOP_mcp_cache_eviction_total",
    "MCP cache entry evictions (LRU)",
)
MAOP_MCP_CONCURRENT_ACTIVE = metrics.gauge(
    "MAOP_mcp_concurrent_active",
    "Active concurrent MCP tool calls per server (label=server)",
)
MAOP_MCP_RATE_LIMITED_TOTAL = metrics.counter(
    "MAOP_mcp_rate_limited_total",
    "MCP tool calls rejected by per-server rate limiter (label=server)",
)

# Phase γ-4: Scheduling decision trace metrics.
#
# MAOP_routing_decision_total — counter incremented each time a routing
#   subsystem (route_scorer / load_balancer / model_selector /
#   dispatcher) records a decision. Labelled by ``stage`` so dashboards
#   can break down volume per subsystem and spot, e.g., a sudden drop in
#   route_scorer decisions (indicating fallback to legacy routing).
# MAOP_routing_decision_duration_ms — histogram of per-stage decision
#   latency in milliseconds. The Histogram class does not carry labels
#   in this registry, so this is a single global distribution across
#   all stages; per-stage breakdown requires the labels-extension which
#   is intentionally out of scope. Use the dashboard
#   ``/api/routing/decisions/recent?stage=...`` endpoint for per-stage
#   latency analysis.
MAOP_ROUTING_DECISION_TOTAL = metrics.counter(
    "MAOP_routing_decision_total",
    "Total scheduling decisions per subsystem (label=stage)",
)
MAOP_ROUTING_DECISION_DURATION_MS = metrics.histogram(
    "MAOP_routing_decision_duration_ms",
    "Scheduling decision latency in milliseconds (all stages)",
)
