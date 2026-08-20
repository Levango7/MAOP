"""MAOP Delegate Dispatcher — Tool / lazy-subsystem helpers.

Split out from ``dispatcher.py`` for maintainability. This module hosts
the retry helper and the lazy-import factories for optional subsystems
(LoadBalancer / Runtime / SandboxManager / SubAgentManager).

These helpers are re-exported from ``maop.delegate.dispatcher`` so that
existing callers and tests (which ``patch("maop.delegate.dispatcher._get_subagent_manager")``)
continue to work without changes.

Implementation is unchanged — only the module location has moved. The
logger name is pinned to ``maop.delegate.dispatcher`` to preserve log
output exactly as before the split.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

# Pinned to the original module name so log records are identical to the
# pre-split behaviour (tests / dashboards key on this logger name).
logger = logging.getLogger("maop.delegate.dispatcher")


# ── Retry helpers (P2 fix: exponential backoff) ─────────────────

async def _retry_with_backoff(
    coro_factory,
    *,
    max_retries: int = 3,
    base_delay_ms: int = 500,
    retryable_exceptions: tuple = (Exception,),
) -> Any:
    """Execute an async operation with exponential backoff retry.

    Args:
        coro_factory: A callable that returns a coroutine to execute.
        max_retries: Maximum number of retry attempts.
        base_delay_ms: Base delay in milliseconds (doubles each retry).
        retryable_exceptions: Tuple of exception types that trigger retry.

    Returns:
        The result of the coroutine.

    Raises:
        The last exception if all retries fail.
    """
    last_exc = None
    for attempt in range(max_retries + 1):
        try:
            return await coro_factory()
        except retryable_exceptions as exc:
            last_exc = exc
            if attempt < max_retries:
                delay_s = (base_delay_ms * (2 ** attempt)) / 1000.0
                logger.warning(
                    "[dispatch] Attempt %d/%d failed: %s. Retrying in %.1fs...",
                    attempt + 1, max_retries + 1, exc, delay_s,
                )
                await asyncio.sleep(delay_s)
            else:
                logger.error(
                    "[dispatch] All %d attempts failed. Last error: %s",
                    max_retries + 1, exc,
                )
    raise last_exc  # type: ignore


# ── Optional subsystems (lazy import to avoid hard deps) ──────

def _get_load_balancer():
    """Lazy import LoadBalancer."""
    try:
        from maop.core.routing.load_balancer import get_load_balancer
        return get_load_balancer()
    except ImportError:
        return None
    except Exception:
        # P2-6 fix: upgrade to error — runtime init failures should be visible
        logger.exception("Failed to load driver LoadBalancer")
        return None

def _get_runtime(config=None):
    """Lazy import Runtime."""
    try:
        from maop.core.agent.lifecycle.runtime import RuntimeConfig, RuntimeType, create_runtime
        if config:
            return create_runtime(config)
        return create_runtime(RuntimeConfig(type=RuntimeType.LOCAL))
    except ImportError:
        return None
    except Exception:
        logger.exception("Failed to load driver Runtime")
        return None

def _get_sandbox_manager(root_dir=None):
    """Lazy import SandboxManager."""
    try:
        from maop.core.security.sandbox import SandboxManager
        return SandboxManager(root_dir=root_dir)
    except ImportError:
        return None
    except Exception:
        logger.exception("Failed to load driver SandboxManager")
        return None

def _get_subagent_manager(root_dir=None):
    """Lazy import SubagentManager."""
    try:
        from maop.core.agent.delegation.subagent_lifecycle import SubAgentManager as SubagentManager
        return SubagentManager(root_dir=root_dir)
    except ImportError:
        return None
    except Exception:
        logger.exception("Failed to load driver SubagentManager")
        return None