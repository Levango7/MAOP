"""MAOP Dashboard — Unified exception handling decorator for router endpoints.

Eliminates the repeated try/except pattern across 15+ router files.
Usage::

    from maop.dashboard.error_handler import handle_api_errors

    @router.get("/api/example")
    @handle_api_errors("example operation")
    async def api_example():
        ...

    @router.get("/api/example2")
    @handle_api_errors
    async def api_example2():
        ...

Unified error responses (2026-07-24): all errors flowing through
``handle_api_errors`` — including ``HTTPException`` — are now rendered
with the ``ErrorSchema`` shape via ``error_response()``. Existing
endpoints that returned ad-hoc ``{"error": "..."}`` payloads continue
to work; callers may opt into the unified schema by raising
``HTTPException`` or by returning ``error_response(...)`` directly.
"""

import functools
import logging
import types
from collections.abc import Callable
from typing import Any

from fastapi import HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

logger = logging.getLogger(__name__)


class ErrorSchema(BaseModel):
    """Unified error response schema.

    Every field besides ``status`` defaults to an empty string so the
    payload is always JSON-serializable and backward compatible with
    callers that only read ``error``.
    """
    status: str = "error"
    error: str = ""
    code: str = ""
    detail: str = ""
    request_id: str = ""


def error_response(
    error: str,
    code: str = "",
    detail: str = "",
    status_code: int = 400,
) -> JSONResponse:
    """Return a unified ``JSONResponse`` using the ``ErrorSchema`` shape.

    All optional fields default to empty strings so the body always
    contains the full set of keys (``status``, ``error``, ``code``,
    ``detail``, ``request_id``) for consistent client-side parsing.
    """
    payload = ErrorSchema(error=error, code=code, detail=detail).model_dump()
    return JSONResponse(status_code=status_code, content=payload)


def handle_api_errors(
    operation: str | Callable[..., Any] = "",
    *,
    error_value: Any = None,
) -> Any:
    """Decorator that catches all exceptions in dashboard API endpoints.

    Can be used with or without arguments::

        @handle_api_errors
        async def api_foo(): ...

        @handle_api_errors("foo operation")
        async def api_bar(): ...

    All caught errors (including ``HTTPException``) are returned as a
    unified ``ErrorSchema`` JSON body via ``error_response()``. The HTTP
    status code is preserved from ``HTTPException`` when present
    (defaulting to 500 for unexpected errors). When ``error_value`` is
    supplied, it is returned as-is for endpoints that prefer a
    non-error fallback value (e.g. an empty list).
    """
    func: Callable[..., Any] | None = None
    op_name: str = ""

    if callable(operation):
        func = operation
        op_name = ""
    else:
        op_name = operation

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        # Capture HTTPException and error_response in the closure so the
        # types.FunctionType rebuild below (which rebinds globals to
        # fn.__globals__) can still resolve them at call time.
        _HTTPException = HTTPException
        _error_response = error_response
        # Capture logger in the closure too: the types.FunctionType rebuild below
        # rebinds wrapper.__globals__ to fn.__globals__ (the endpoint module),
        # which does NOT define `logger`. Referencing a closure cell avoids the
        # NameError that previously turned every handled error into a 500.
        _logger = logger
        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return await fn(*args, **kwargs)
            except _HTTPException as exc:
                op = op_name or fn.__name__
                _logger.warning("%s raised HTTPException: %s", op, exc.detail)
                return _error_response(
                    error=str(exc.detail) if exc.detail is not None else "",
                    code=f"HTTP_{exc.status_code}",
                    status_code=exc.status_code,
                )
            except Exception:
                op = op_name or fn.__name__
                _logger.exception("%s failed", op)
                if error_value is not None:
                    return error_value
                return _error_response(
                    error=f"{op} unavailable",
                    code="INTERNAL",
                    status_code=500,
                )
        wrapper = types.FunctionType(  # type: ignore[assignment]
            wrapper.__code__,  # type: ignore[attr-defined]
            fn.__globals__,
            name=wrapper.__name__,
            argdefs=wrapper.__defaults__,  # type: ignore[attr-defined]
            closure=wrapper.__closure__,  # type: ignore[attr-defined]
        )
        functools.update_wrapper(wrapper, fn)
        return wrapper

    if func is not None:
        return decorator(func)
    return decorator
