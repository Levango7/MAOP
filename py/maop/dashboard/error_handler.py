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
"""

import functools
import logging
import types
from typing import Any, Callable, Union

from fastapi import HTTPException
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)


def handle_api_errors(
    operation: Union[str, Callable[..., Any]] = "",
    *,
    error_value: Any = None,
) -> Any:
    """Decorator that catches all exceptions in dashboard API endpoints.

    Can be used with or without arguments::

        @handle_api_errors
        async def api_foo(): ...

        @handle_api_errors("foo operation")
        async def api_bar(): ...
    """
    func: Callable[..., Any] | None = None
    op_name: str = ""

    if callable(operation):
        func = operation
        op_name = ""
    else:
        op_name = operation

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        # Capture HTTPException in closure so types.FunctionType rebuild below
        # (which uses fn.__globals__) can still reference it.
        _HTTPException = HTTPException
        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return await fn(*args, **kwargs)
            except _HTTPException:
                raise  # Preserve HTTP status code (e.g., 400/422) instead of swallowing
            except Exception as exc:
                op = op_name or fn.__name__
                logger.error("%s failed: %s", op, exc, exc_info=True)
                if error_value is not None:
                    return error_value
                return JSONResponse(
                    status_code=500,
                    content={"status": "error", "error": f"{op} unavailable"},
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
