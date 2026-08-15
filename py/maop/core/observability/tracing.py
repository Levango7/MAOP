"""OpenTelemetry tracing — auto spans + W3C Trace Context propagation.

Bridges MAOP's high-level observability API onto the lower-level
``maop.core.monitoring.otel`` SDK shim.  Provides:

  * ``setup_tracing()``        — one-shot provider initialisation
  * ``auto_span()``            — decorator that wraps any callable in an
                                  OTel span (sync or async, class method
                                  or free function)
  * ``trace_context()``        — context manager form of ``auto_span``
  * ``TraceContextMiddleware`` — FastAPI middleware that extracts the
                                  W3C ``traceparent`` header on ingress
                                  and injects it on egress, so every
                                  inbound HTTP request joins (or starts)
                                  a distributed trace
  * ``inject_trace_context`` / ``extract_trace_context`` — low-level
                                  carrier helpers re-exported from the
                                  OTel shim for outbound HTTP calls

Edition behaviour
------------------
* **Personal** — OTel SDK is optional; when absent, every API degrades
  to a zero-overhead no-op (``_NoopSpan`` / ``_NoopTracer``).
* **Enterprise** — full OTel pipeline: OTLP gRPC exporter → Collector →
  Jaeger/Tempo, with W3C Trace Context propagation across every HTTP
  hop.

The auto-span targets named in the F1-04 spec —
``Orchestrator.execute()``, ``Dispatcher.dispatch()``, ``Agent.run()``
— are wired via :func:`auto_span` at import time of the host modules;
see :func:`install_auto_spans`.
"""
from __future__ import annotations

import functools
import inspect
import logging
import os
from collections.abc import Callable, Generator
from contextlib import contextmanager
from typing import Any, TypeVar

from maop.config.edition import Edition, FeatureFlag, get_edition, has_feature
from maop.core.monitoring.otel import (
    _NoopSpan,
    _NoopTracer,
)
from maop.core.monitoring.otel import (
    extract_trace_context as _otel_extract,
)
from maop.core.monitoring.otel import (
    get_tracer as _otel_get_tracer,
)
from maop.core.monitoring.otel import (
    inject_trace_context as _otel_inject,
)
from maop.core.monitoring.otel import (
    is_enabled as _otel_is_enabled,
)
from maop.core.monitoring.otel import (
    setup_provider as _otel_setup_provider,
)
from maop.core.monitoring.otel import (
    span as _otel_span,
)

logger = logging.getLogger(__name__)

T = TypeVar("T")

# ── W3C Trace Context header ────────────────────────────────────────
W3C_TRACEPARENT = "traceparent"
W3C_TRACESTATE = "tracestate"

# Module-level tracer handle; lazily initialised by ``setup_tracing``.
_tracer: Any = None
_setup_done = False


# ── Edition-aware mode detection ────────────────────────────────────
def is_enterprise_mode() -> bool:
    """Return True when the running edition enables full OTel tracing."""
    return get_edition() == Edition.ENTERPRISE or has_feature(FeatureFlag.AUDIT_LOG)


def is_personal_mode() -> bool:
    """Return True when running in lightweight Personal mode."""
    return not is_enterprise_mode()


def tracing_enabled() -> bool:
    """Return True when tracing is active (env flag set AND SDK present).

    In Personal mode this is almost always False — the env flag defaults
    off and the SDK is an optional extra, so the no-op path is taken.
    """
    return _otel_is_enabled()


# ── Setup ──────────────────────────────────────────────────────────
def setup_tracing(
    *,
    service_name: str = "",
    endpoint: str = "",
    exporter: str = "",
    force: bool = False,
) -> bool:
    """Initialise the OTel TracerProvider + MeterProvider.

    Reads defaults from environment variables (see
    :mod:`maop.core.monitoring.otel`) but allows explicit overrides for
    test harnesses.  Safe to call multiple times — only the first call
    with ``force=False`` takes effect.

    Returns ``True`` when tracing was actually enabled, ``False`` when
    the no-op path is in use (Personal mode / SDK missing / env off).
    """
    global _tracer, _setup_done
    if _setup_done and not force:
        return _tracer is not None and not isinstance(_tracer, _NoopTracer)

    if service_name:
        os.environ.setdefault("MAOP_OTEL_SERVICE_NAME", service_name)
    if endpoint:
        os.environ.setdefault("MAOP_OTEL_ENDPOINT", endpoint)
    if exporter:
        os.environ.setdefault("MAOP_OTEL_EXPORTER", exporter)

    # Personal mode: skip provider setup entirely unless the operator
    # explicitly enabled OTel via MAOP_OTEL_ENABLED=1.  This keeps the
    # default Personal footprint at zero imported SDK modules.
    if is_personal_mode() and not _otel_is_enabled():
        _tracer = _NoopTracer()
        _setup_done = True
        logger.debug("[observability.tracing] Personal mode — tracing no-op")
        return False

    try:
        _otel_setup_provider()
    except Exception as exc:
        logger.warning("[observability.tracing] OTel setup failed: %s", exc)

    _tracer = _otel_get_tracer("maop.observability")
    _setup_done = True
    active = not isinstance(_tracer, _NoopTracer)
    logger.info(
        "[observability.tracing] setup complete | enabled=%s | edition=%s",
        active, get_edition().value,
    )
    return active


def get_tracer() -> Any:
    """Return the module-level tracer, initialising on first access."""
    if _tracer is None:
        setup_tracing()
    return _tracer


# ── Auto-span decorator ─────────────────────────────────────────────
def auto_span(
    name: str | None = None,
    *,
    attributes: dict[str, Any] | None = None,
    kind: Any = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorate a function/method so it runs inside an OTel span.

    Works for both sync and async callables (detected via
    ``inspect.iscoroutinefunction``).  When tracing is disabled the
    callable is returned unwrapped — zero runtime overhead.

    Parameters
    ----------
    name:
        Span name.  Defaults to ``"<module>.<qualname>"`` of the wrapped
        callable, which is what Jaeger/Tempo will display.
    attributes:
        Static attributes stamped on every span.  Dynamic attributes
        can be added inside the wrapped body via ``current_span()``.
    kind:
        OTel ``SpanKind`` (INTERNAL / SERVER / CLIENT / PRODUCER /
        CONSUMER).  Defaults to INTERNAL.
    """
    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        span_name = name or f"{fn.__module__}.{getattr(fn, '__qualname__', fn.__name__)}"
        static_attrs = dict(attributes or {})

        if inspect.iscoroutinefunction(fn):
            @functools.wraps(fn)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                tracer = get_tracer()
                if isinstance(tracer, _NoopTracer):
                    return await fn(*args, **kwargs)
                with _otel_span(tracer, span_name, kind=kind, attributes=static_attrs) as s:
                    _stamp_caller_attrs(s, fn, args)
                    try:
                        return await fn(*args, **kwargs)
                    except Exception as exc:
                        _record_exception(s, exc)
                        raise
            return async_wrapper

        @functools.wraps(fn)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            tracer = get_tracer()
            if isinstance(tracer, _NoopTracer):
                return fn(*args, **kwargs)
            with _otel_span(tracer, span_name, kind=kind, attributes=static_attrs) as s:
                _stamp_caller_attrs(s, fn, args)
                try:
                    return fn(*args, **kwargs)
                except Exception as exc:
                    _record_exception(s, exc)
                    raise
        return sync_wrapper

    return decorator


def _stamp_caller_attrs(span_obj: Any, fn: Callable[..., Any], args: tuple) -> None:
    """Attach lightweight call-site attributes (function name, self type)."""
    if not getattr(span_obj, "is_recording", False):
        return
    try:
        span_obj.set_attribute("code.function", getattr(fn, "__qualname__", fn.__name__))
        span_obj.set_attribute("code.module", fn.__module__)
        if args and hasattr(args[0], "__class__"):
            span_obj.set_attribute("code.class", args[0].__class__.__name__)
    except Exception:
        pass


def _record_exception(span_obj: Any, exc: BaseException) -> None:
    """Mark a span as ERROR and record the exception."""
    if not getattr(span_obj, "is_recording", False):
        return
    try:
        span_obj.record_exception(exc)
        from opentelemetry.trace import Status, StatusCode
        span_obj.set_status(Status(StatusCode.ERROR, str(exc)))
    except Exception:
        try:
            span_obj.set_status("ERROR")
        except Exception:
            pass


@contextmanager
def trace_context(
    name: str,
    *,
    attributes: dict[str, Any] | None = None,
    kind: Any = None,
) -> Generator[Any, None, None]:
    """Context-manager form of :func:`auto_span`.

    >>> with trace_context("manual.step", attributes={"k": "v"}) as s:
    ...     do_work()
    """
    tracer = get_tracer()
    if isinstance(tracer, _NoopTracer):
        yield _NoopSpan()
        return
    with _otel_span(tracer, name, kind=kind, attributes=attributes) as s:
        yield s


def current_span() -> Any:
    """Return the currently active OTel span, or a no-op span."""
    try:
        from opentelemetry import trace as otel_trace
        s = otel_trace.get_current_span()
        if s is not None and getattr(s, "is_recording", False):
            return s
    except ImportError:
        pass
    return _NoopSpan()


# ── W3C Trace Context propagation ───────────────────────────────────
def inject_trace_context(carrier: dict[str, str]) -> None:
    """Inject the active trace context into a carrier (outbound HTTP)."""
    _otel_inject(carrier)


def extract_trace_context(carrier: dict[str, str]) -> Any:
    """Extract a trace context from a carrier (inbound HTTP)."""
    return _otel_extract(carrier)


def attach_trace_context(carrier: dict[str, str]) -> Any:
    """Extract *and* attach a trace context as the active context.

    Returns a detach token that must be passed to
    :func:`detach_trace_context` when the request scope ends.
    """
    ctx = extract_trace_context(carrier)
    if ctx is None:
        return None
    try:
        from opentelemetry import trace as otel_trace
        return otel_trace.set_span_in_context(None)
    except ImportError:
        return None


def detach_trace_context(token: Any) -> None:
    """Detach a previously attached trace context."""
    if token is None:
        return
    try:
        from opentelemetry import context as otel_context
        otel_context.detach(token)
    except ImportError:
        pass


# ── FastAPI middleware ──────────────────────────────────────────────
class TraceContextMiddleware:
    """ASGI middleware that propagates W3C Trace Context.

    On ingress: reads ``traceparent`` from the request headers and
    attaches it as the active context, so every downstream span joins
    the caller's trace.

    On egress: injects the (possibly updated) ``traceparent`` into the
    response headers so the client can correlate.

    The middleware is a class so it can be installed via
    ``app.add_middleware(TraceContextMiddleware)`` or used directly as
    an ASGI(3) wrapper.  When tracing is disabled it short-circuits to
    the wrapped app with zero overhead.
    """

    def __init__(self, app: Any, *, enabled: bool | None = None) -> None:
        self.app = app
        # Default to "enabled when tracing is enabled"; allow explicit override.
        self._enabled = tracing_enabled() if enabled is None else enabled

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        if scope.get("type") != "http" or not self._enabled:
            await self.app(scope, receive, send)
            return

        # Extract traceparent from request headers.
        headers = dict(scope.get("headers", []))
        traceparent = headers.get(W3C_TRACEPARENT.encode(), b"")
        carrier: dict[str, str] = {}
        if traceparent:
            carrier[W3C_TRACEPARENT] = traceparent.decode("latin-1", "replace")
        tracestate = headers.get(W3C_TRACESTATE.encode(), b"")
        if tracestate:
            carrier[W3C_TRACESTATE] = tracestate.decode("latin-1", "replace")

        token = None
        if carrier:
            try:
                from opentelemetry import context as otel_context
                ctx = extract_trace_context(carrier)
                if ctx is not None:
                    token = otel_context.attach(ctx)
            except ImportError:
                pass
            except Exception:
                pass

        # Wrap send so we can inject traceparent on egress.
        async def send_wrapper(message: dict) -> None:
            if message.get("type") == "http.response.start":
                try:
                    out_carrier: dict[str, str] = {}
                    inject_trace_context(out_carrier)
                    if out_carrier:
                        msg_headers = list(message.get("headers", []))
                        for k, v in out_carrier.items():
                            msg_headers.append((k.encode(), v.encode("latin-1")))
                        message["headers"] = msg_headers
                except Exception:
                    pass
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            if token is not None:
                try:
                    from opentelemetry import context as otel_context
                    otel_context.detach(token)
                except ImportError:
                    pass
                except Exception:
                    pass


# ── Auto-span installation for spec-named targets ───────────────────
_AUTO_SPAN_TARGETS = (
    # (module_path, class_name, method_name, span_name)
    ("maop.maop_loop", "MaopLoop", "run", "Orchestrator.execute"),
    ("maop.delegate.dispatcher", "Dispatcher", "dispatch", "Dispatcher.dispatch"),
    ("maop.core.agent.llm_chat.react_loop", "ReactLoop", "run", "Agent.run"),
)


def install_auto_spans() -> int:
    """Wrap the spec-named methods with :func:`auto_span`.

    Called once at startup (see
    :func:`maop.core.observability.setup_observability`).  Idempotent:
    re-calling on already-wrapped methods is a no-op.

    Returns the number of methods actually wrapped.
    """
    if not tracing_enabled():
        logger.debug("[observability.tracing] auto-span install skipped (tracing disabled)")
        return 0

    wrapped = 0
    for mod_path, cls_name, meth_name, span_name in _AUTO_SPAN_TARGETS:
        try:
            import importlib
            mod = importlib.import_module(mod_path)
            cls = getattr(mod, cls_name, None)
            if cls is None:
                continue
            original = getattr(cls, meth_name, None)
            if original is None or getattr(original, "__maop_otel_wrapped__", False):
                continue
            decorated = auto_span(span_name)(original)
            decorated.__maop_otel_wrapped__ = True  # type: ignore[attr-defined]
            setattr(cls, meth_name, decorated)
            wrapped += 1
            logger.debug("[observability.tracing] auto-span installed: %s.%s", cls_name, meth_name)
        except ImportError:
            continue
        except Exception as exc:
            logger.warning(
                "[observability.tracing] auto-span failed for %s.%s: %s",
                cls_name, meth_name, exc,
            )
    if wrapped:
        logger.info("[observability.tracing] auto-span installed on %d methods", wrapped)
    return wrapped


# ── Re-exports for convenience ──────────────────────────────────────
__all__ = [
    "W3C_TRACEPARENT",
    "W3C_TRACESTATE",
    "TraceContextMiddleware",
    "attach_trace_context",
    "auto_span",
    "current_span",
    "detach_trace_context",
    "extract_trace_context",
    "get_tracer",
    "inject_trace_context",
    "install_auto_spans",
    "is_enterprise_mode",
    "is_personal_mode",
    "setup_tracing",
    "trace_context",
    "tracing_enabled",
]