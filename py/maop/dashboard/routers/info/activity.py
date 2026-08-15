"""Activity feed endpoint for the Overview page.

Endpoints:
    GET /activity — recent system activity events timeline
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Request

from maop.dashboard.error_handler import handle_api_errors

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/info", tags=["info"])


def _fmt_age(seconds: float) -> str:
    """Format seconds into human-readable duration."""
    if seconds < 60:
        return f"{int(seconds)}秒"
    if seconds < 3600:
        return f"{int(seconds / 60)}分钟"
    if seconds < 86400:
        return f"{int(seconds / 3600)}小时"
    return f"{int(seconds / 86400)}天"


def _fmt_age_ago(seconds: float) -> str:
    """Format seconds into 'X ago' relative time string."""
    if seconds < 60:
        return f"{int(seconds)}秒前"
    if seconds < 3600:
        return f"{int(seconds / 60)}分钟前"
    if seconds < 86400:
        return f"{int(seconds / 3600)}小时前"
    return f"{int(seconds / 86400)}天前"


@router.get("/activity")
@handle_api_errors
async def get_activity(request: Request, limit: int = 10) -> dict[str, Any]:
    """Return recent system activity events for the Overview page feed.

    Aggregates data from delegation logs and system state into a
    timeline format compatible with the Overview activity feed.
    No admin required — available to any authenticated user.
    """
    import time as _time
    from pathlib import Path

    events: list[dict[str, Any]] = []
    now = _time.time()

    # 1. System startup event (always first if recently started)
    try:
        pid_file = Path(__file__).resolve().parents[4] / "data" / "maop.pid"
        if pid_file.exists():
            mtime = pid_file.stat().st_mtime
            age_s = now - mtime
            if age_s < 86400 * 7:  # within a week
                events.append({
                    "title": "系统启动完成",
                    "desc": f"MAOP 进程已运行 {_fmt_age(age_s)}",
                    "time": _fmt_age_ago(age_s),
                    "kind": "system",
                })
    except Exception:
        # Best-effort pid-file event; skip silently if unavailable.
        logger.debug("timeline: failed to read maop.pid startup event", exc_info=True)

    # 2. Delegation log entries (most recent N)
    try:
        from maop.dashboard.routers.data import get_bridge
        logs = await get_bridge().logs_get(name="delegations", limit=min(limit, 50))
        if isinstance(logs, list):
            for entry in reversed(logs[-limit:]):
                if not isinstance(entry, dict):
                    continue
                agent = entry.get("agent", "unknown")
                result = entry.get("result") if isinstance(entry.get("result"), dict) else {}
                ec = result.get("exit_code") if result else None
                ts = entry.get("timestamp") or entry.get("time") or entry.get("ts")
                # Try to parse timestamp for relative time
                age_str = "刚刚"
                if ts:
                    try:
                        if isinstance(ts, (int, float)):
                            age_s = now - ts
                            age_str = _fmt_age_ago(age_s)
                        elif isinstance(ts, str):
                            # ISO format or similar
                            from datetime import datetime, timezone
                            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                            if dt.tzinfo is None:
                                dt = dt.replace(tzinfo=timezone.utc)
                            age_s = (datetime.now(timezone.utc) - dt).total_seconds()
                            age_str = _fmt_age_ago(max(0, age_s))
                    except Exception:
                        age_str = "最近"

                status_label = "成功" if ec == 0 else ("失败" if ec is not None else "未知")
                task = entry.get("task", "") or entry.get("prompt", "") or ""
                desc = f"智能体「{agent}」执行{status_label}"
                if task:
                    desc += f"：{task[:60]}"

                # Varied title based on task content & status
                _task_key = (task[:30] if task else "").strip()
                if not _task_key:
                    title = f"{'✓' if ec == 0 else '✗'} {agent} 心跳检测"
                elif "healthcheck" in _task_key.lower() or "probe" in _task_key.lower():
                    title = f"{'✓' if ec == 0 else '✗'} {agent} 健康检查"
                elif "test" in _task_key.lower():
                    title = f"{'✓' if ec == 0 else '✗'} {agent} 测试任务"
                elif ec and ec != 0:
                    title = f"⚠ {agent} 执行异常"
                else:
                    title = f"→ {agent}：{_task_key[:20]}"

                events.append({
                    "title": title,
                    "desc": desc,
                    "time": age_str,
                    "kind": "delegation",
                    "_sort": now - age_s if ts else 0,
                })
    except Exception as exc:
        logger.warning("[info] Failed to load delegation logs for activity: %s", exc)

    # Sort by actual timestamp (newest first) and trim
    events.sort(key=lambda e: e.get("_sort", 0), reverse=True)
    # Strip internal key before response
    for ev in events:
        ev.pop("_sort", None)
    return {
        "status": "ok",
        "events": events[:limit],
        "count": len(events[:limit]),
    }