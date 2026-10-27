"""Log endpoints split out of ``data_service`` (file-size ratchet).

Timestamps/levels/agents are parsed from raw log tails here; the parent
module forwards every public name below, so callers keep importing from
``maop.dashboard.services.data_service`` unchanged.
"""

from __future__ import annotations

import asyncio
import collections
import logging
import re
from typing import Any

# 共享运行时状态经 ``state`` 模块取——测试 monkeypatch
# ``state.MAOP_ROOT`` / ``state.get_bridge`` 时此处同样生效。
from maop.dashboard.routers import state

logger = logging.getLogger(__name__)

__all__ = [
    "_LOG_RE",
    "_read_log_tail",
    "_validate_log_name",
    "get_logs",
    "get_logs_analysis",
    "get_logs_checker",
    "get_logs_delegations",
]


def _read_log_tail(path, limit: int) -> list[str]:
    """Read last `limit` lines from a log file (bounded read, P2-9 fix)."""
    with open(path, encoding="utf-8", errors="replace") as fh:
        return list(collections.deque(fh, maxlen=limit))


_LOG_RE = re.compile(
    r'^(?P<ts>\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?)\s*'
    r'(?:\[(?P<level>\w+)\])?\s*'
    r'(?:\[(?P<agent>[^\]]+)\])?\s*'
    r'(?P<msg>.*)$'
)


def _validate_log_name(log_name: str) -> bool:
    """Validate log name to prevent glob injection (P0-3 fix)."""
    return bool(re.match(r"^[a-zA-Z0-9_\-\.]+$", log_name))


async def get_logs(log_name: str, limit: int) -> dict[str, Any]:
    """Read log files with bounded size (P2-9 fix).

    Handles delegations/checker specially, then falls back to globbing
    the logs directory for files matching ``log_name``.

    Raises ``ValueError`` if ``log_name`` fails validation.
    Raises ``Exception`` on other failures (router maps to HTTP 500).
    """
    if not _validate_log_name(log_name):
        raise ValueError("invalid log type: only alphanumeric, dots, hyphens, underscores allowed")
    if log_name == "delegations":
        entries = await state.get_bridge().logs_get(name="delegations", limit=limit)
        return {"logs": entries, "count": len(entries), "source": "logs/delegations.json", "type": "delegations"}
    if log_name == "checker":
        entries = await state.get_bridge().logs_get(name="checker", limit=limit)
        return {"logs": entries, "count": len(entries), "source": "logs/checker_*.log", "type": "checker"}
    result = await state.get_bridge().logs_get(name=log_name, limit=limit)
    log_dir = state.MAOP_ROOT / "logs"
    if log_dir.exists():
        for f in sorted(log_dir.glob(f"*{log_name}*"), reverse=True):
            # P2-23: glob 可能匹配目录，只处理文件
            if not f.is_file():
                continue
            try:
                # P2-9 fix: bounded read — only tail last `limit` lines
                tail = await asyncio.to_thread(_read_log_tail, f, limit)
                content = "\n".join(tail)
                if content:
                    entries = []
                    for raw in tail:
                        line = raw.rstrip('\r\n')
                        m = _LOG_RE.match(line)
                        if m:
                            entries.append({
                                "ts": m.group("ts"),
                                "level": (m.group("level") or "info").lower(),
                                "agent": m.group("agent") or "system",
                                "msg": m.group("msg") or line,
                            })
                        else:
                            entries.append({"ts": None, "level": "info", "agent": "system", "msg": line})
                    return {"logs": entries, "count": len(entries), "source": str(f), "type": log_name}
            except Exception as exc:
                logger.warning('Failed to read log file: %s', exc)
    return {"logs": result, "count": len(result), "source": f"error_log:{log_name}", "type": log_name}


async def get_logs_delegations(limit: int) -> Any:
    """Fetch delegations log."""
    return await state.get_bridge().logs_get(name="delegations", limit=limit)


async def get_logs_checker(limit: int) -> Any:
    """Fetch checker log."""
    return await state.get_bridge().logs_get(name="checker", limit=limit)


async def get_logs_analysis(log_type: str) -> dict[str, Any]:
    """Analyze log entries: count by agent/status, extract error patterns.

    ``log_type`` follows the frontend's selected log stream. For
    ``delegations``, uses exit_code→success/failure; for other types,
    uses level→success/error and agent dimension distribution.

    Raises ``Exception`` on failure so the router can map to HTTP 500.
    """
    logs = await state.get_bridge().logs_get(name=log_type or "delegations", limit=10000)
    if not isinstance(logs, list):
        logs = []
    total = len(logs)
    by_agent: dict[str, int] = {}
    by_status: dict[str, int] = {"success": 0, "failure": 0, "timeout": 0, "other": 0}
    error_patterns: dict[str, int] = {}
    is_delegations = (log_type or "delegations") == "delegations"
    for e in logs:
        if not isinstance(e, dict):
            continue
        ag = e.get("agent") or e.get("ts_agent") or "unknown"
        by_agent[ag] = by_agent.get(ag, 0) + 1
        if is_delegations:
            res = e.get("result") if isinstance(e.get("result"), dict) else {}
            ec = res.get("exit_code") if res else None
            if ec == 0:
                st = "success"
            elif ec is not None:
                st = "failure"
            else:
                st = e.get("status", "other")
        else:
            # dashboard / checker rows carry level + ts/msg fields
            lvl = str(e.get("level", "")).lower()
            st = "failure" if lvl in ("error", "critical", "fatal") else (
                "success" if lvl in ("success", "info", "ok") else "other")
            if e.get("status"):
                st = e["status"]
        if st in by_status:
            by_status[st] += 1
        else:
            by_status["other"] += 1
        if st == "failure":
            ek = str((e.get("result") or {}).get("error") or e.get("error") or e.get("msg") or "unknown")[:80]
            error_patterns[ek] = error_patterns.get(ek, 0) + 1
    return {
        "total": total,
        "by_agent": by_agent,
        "by_status": by_status,
        "error_patterns": sorted(error_patterns.items(), key=lambda x: -x[1])[:10],
        "type": log_type or "delegations",
    }