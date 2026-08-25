"""MCPHub — 指标/审计/缓存助手 mixin。

T2 架构债治理：从 ``mcp_hub.py`` 拆分。公开 API 不变。
依赖宿主的 ``_metrics`` / ``_audit_logger`` / ``_otel`` 状态。
"""

from __future__ import annotations

import logging
import time as _time
from typing import TYPE_CHECKING, Any

logger = logging.getLogger(__name__)


class MCPHubMetricsMixin:
    """指标/审计/缓存助手（_record_*/_inc_*/_dec_*）。"""

    if TYPE_CHECKING:
        # 宿主类（MCPHub）提供的属性 —— 仅用于类型检查
        _audit_logger: Any


    def _record_audit(
        self,
        *,
        server_name: str,
        tool_name: str,
        user_context: dict[str, Any] | None,
        arguments: dict[str, Any] | None,
        allowed: bool,
        decision_reason: str,
        success: bool,
        duration_ms: float,
        error: str | None,
    ) -> None:
        """Write one MCP audit record (if an audit_logger is injected)."""
        audit = self._audit_logger
        if audit is None:
            return
        # Lazy import keeps the module-load graph acyclic.
        from maop.core.mcp.mcp_audit import MCPAuditRecord, hash_arguments

        user_id = ""
        if user_context:
            user_id = str(user_context.get("user_id", "") or "")
        record = MCPAuditRecord(
            timestamp=_time.time(),
            server_name=server_name,
            tool_name=tool_name,
            user_id=user_id,
            arguments_hash=hash_arguments(arguments),
            allowed=allowed,
            decision_reason=decision_reason,
            success=success,
            duration_ms=duration_ms,
            error=error,
        )
        try:
            audit.log_call(record)
        except Exception as exc:  # pragma: no cover — defensive
            logger.warning("[mcp_hub] audit log_call failed: %s", exc)

    def _inc_metrics(self, *, allowed: bool, reason: str) -> None:
        """Update the δ-3 MCP metrics counters.

        Imports are local so a missing/optional monitoring module never
        breaks the call_tool hot path. The three counters are
        pre-registered in monitoring.py at module load; we just increment
        them here.
        """
        try:
            from maop.core.monitoring.monitoring import (
                MAOP_MCP_CALL_ALLOWED_TOTAL,
                MAOP_MCP_CALL_AUDITED_TOTAL,
                MAOP_MCP_CALL_DENIED_TOTAL,
            )
        except Exception:
            logger.debug("Silent exception in core/mcp_hub.py:1279", exc_info=True)
            return
        MAOP_MCP_CALL_AUDITED_TOTAL.inc()
        if allowed:
            MAOP_MCP_CALL_ALLOWED_TOTAL.inc()
        else:
            MAOP_MCP_CALL_DENIED_TOTAL.inc(labels={"reason": reason or "unknown"})

    def _record_call_attempt(self, server_name: str, tool_name: str) -> None:
        """Increment MAOP_mcp_calls_total (label=server,tool)."""
        try:
            from maop.core.monitoring.monitoring import MAOP_MCP_CALLS_TOTAL
        except Exception:
            logger.debug("Silent exception in core/mcp_hub.py:1293", exc_info=True)
            return
        MAOP_MCP_CALLS_TOTAL.inc(labels={"server": server_name, "tool": tool_name})

    def _record_call_error(self, server_name: str, tool_name: str) -> None:
        """Increment MAOP_mcp_call_errors_total (label=server,tool)."""
        try:
            from maop.core.monitoring.monitoring import MAOP_MCP_CALL_ERRORS_TOTAL
        except Exception:
            logger.debug("Silent exception in core/mcp_hub.py:1301", exc_info=True)
            return
        MAOP_MCP_CALL_ERRORS_TOTAL.inc(labels={"server": server_name, "tool": tool_name})

    def _record_call_duration(self, started_monotonic: float) -> None:
        """Observe MAOP_mcp_call_duration_seconds (no labels; Histogram
        class in monitoring.py does not carry labels)."""
        try:
            from maop.core.monitoring.monitoring import MAOP_MCP_CALL_DURATION_SECONDS
        except Exception:
            logger.debug("Silent exception in core/mcp_hub.py:1310", exc_info=True)
            return
        elapsed = _time.monotonic() - started_monotonic
        if elapsed < 0:
            elapsed = 0.0
        MAOP_MCP_CALL_DURATION_SECONDS.observe(elapsed)

    def _record_health_check(self, server_name: str, *, healthy: bool) -> None:
        """Increment MAOP_mcp_health_check_total (label=server,result)."""
        try:
            from maop.core.monitoring.monitoring import MAOP_MCP_HEALTH_CHECK_TOTAL
        except Exception:
            logger.debug("Silent exception in core/mcp_hub.py:1321", exc_info=True)
            return
        MAOP_MCP_HEALTH_CHECK_TOTAL.inc(
            labels={"server": server_name, "result": "healthy" if healthy else "unhealthy"},
        )

    def _inc_connected_servers(self) -> None:
        """+1 on MAOP_mcp_servers_connected (no labels)."""
        try:
            from maop.core.monitoring.monitoring import MAOP_MCP_SERVERS_CONNECTED
        except Exception:
            logger.debug("Silent exception in core/mcp_hub.py:1331", exc_info=True)
            return
        MAOP_MCP_SERVERS_CONNECTED.inc()

    def _dec_connected_servers(self) -> None:
        """-1 on MAOP_mcp_servers_connected (no labels)."""
        try:
            from maop.core.monitoring.monitoring import MAOP_MCP_SERVERS_CONNECTED
        except Exception:
            logger.debug("Silent exception in core/mcp_hub.py:1339", exc_info=True)
            return
        MAOP_MCP_SERVERS_CONNECTED.dec()

    def _record_cache_hit(self, server_name: str) -> None:
        """Increment MAOP_mcp_cache_hit_total (label=server)."""
        try:
            from maop.core.monitoring.monitoring import MAOP_MCP_CACHE_HIT_TOTAL
        except Exception:
            logger.debug("Silent exception in core/mcp_hub.py:1349", exc_info=True)
            return
        MAOP_MCP_CACHE_HIT_TOTAL.inc(labels={"server": server_name})

    def _record_cache_miss(self, server_name: str) -> None:
        """Increment MAOP_mcp_cache_miss_total (label=server)."""
        try:
            from maop.core.monitoring.monitoring import MAOP_MCP_CACHE_MISS_TOTAL
        except Exception:
            logger.debug("Silent exception in core/mcp_hub.py:1357", exc_info=True)
            return
        MAOP_MCP_CACHE_MISS_TOTAL.inc(labels={"server": server_name})

    def _record_rate_limited(self, server_name: str) -> None:
        """Increment MAOP_mcp_rate_limited_total (label=server)."""
        try:
            from maop.core.monitoring.monitoring import MAOP_MCP_RATE_LIMITED_TOTAL
        except Exception:
            logger.debug("Silent exception in core/mcp_hub.py:1365", exc_info=True)
            return
        MAOP_MCP_RATE_LIMITED_TOTAL.inc(labels={"server": server_name})

