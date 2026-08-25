"""Log retrieval endpoints for :class:`DataProxy`."""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

logger = logging.getLogger(__name__)


class SecurityMixin:
    """Log-stream retrieval endpoints.

    Provides:
        - ``logs_get``             — routed log retrieval (delegations/checker/error_log)
        - ``_read_delegations_json`` — read logs/delegations.json
        - ``_read_checker_logs``   — parse logs/checker_*.log into structured entries
    """

    if TYPE_CHECKING:
        # 宿主类（DataProxy）提供的属性与方法 —— 仅用于类型检查
        _root: Path
        _record_latency: Callable[..., None]
        _query_maop: Callable[..., Any]


    async def logs_get(
        self, name: str = "dashboard", limit: int = 50
    ) -> list[dict[str, Any]]:
        """Get log entries for the named log stream.

        Routes by ``name`` to the correct source. Previously every call
        returned the ``error_log`` table regardless of ``name``, so
        ``logs_get(name="delegations")`` returned the wrong data.

        * ``delegations`` → ``logs/delegations.json`` (the genuine delegation history)
        * ``checker``     → ``logs/checker_*.log`` parsed into structured entries
        * anything else (default ``dashboard``) → ``error_log`` table
        """
        import asyncio

        start = time.monotonic()
        if name == "delegations":
            result = await asyncio.to_thread(self._read_delegations_json, limit)
        elif name == "checker":
            result = await asyncio.to_thread(self._read_checker_logs, limit)
        else:
            result = await self._query_maop(
                "SELECT * FROM error_log ORDER BY timestamp DESC LIMIT ?",
                (limit,),
            )
        self._record_latency(start)
        return result

    # ── log readers ───────────────────────────────────────────

    def _read_delegations_json(self, limit: int) -> list[dict[str, Any]]:
        """Read the genuine delegation history from logs/delegations.json."""
        path = self._root / "logs" / "delegations.json"
        if not path.exists():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except Exception as exc:
            logger.warning("data_proxy._read_delegations_json failed: %s", exc)
            return []
        if not isinstance(data, list):
            return []
        if limit and limit > 0:
            return data[-limit:]
        return data

    def _read_checker_logs(self, limit: int) -> list[dict[str, Any]]:
        """Read and parse checker log files into structured entries."""
        log_dir = self._root / "logs"
        if not log_dir.is_dir():
            return []
        files = sorted(log_dir.glob("checker_*.log"), reverse=True)
        entries: list[dict[str, Any]] = []
        _log_re = re.compile(
            r"^\[(?P<ts>\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2})(?:\.\d+)?\]\s*"
            r"\[(?P<agent>[^\]]+)\]\s*"
            r"(?P<level>\w+):?\s*"
            r"(?P<msg>.*)$"
        )
        for f in files:
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
            except Exception as exc:
                logger.debug("[bridge] log scan: failed to read %s: %s", f.name, exc)
                continue
            for raw in text.splitlines():
                line = raw.rstrip("\r\n")
                if not line:
                    continue
                m = _log_re.match(line)
                if m:
                    entries.append({
                        "ts": m.group("ts"),
                        "level": (m.group("level") or "info").lower(),
                        "agent": m.group("agent") or "checker",
                        "msg": m.group("msg") or line,
                    })
                else:
                    entries.append({"ts": None, "level": "info", "agent": "checker", "msg": line})
                if limit and len(entries) >= limit:
                    break
            if limit and len(entries) >= limit:
                break
        return entries[:limit] if limit else entries