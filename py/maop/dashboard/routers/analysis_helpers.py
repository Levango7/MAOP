"""MAOP Dashboard — Analysis helpers (extracted from analysis.py).

Pure helper functions used by the analysis router endpoints. Kept in a
separate module so ``analysis.py`` stays focused on route handlers.

These functions are intentionally side-effect-free with respect to the
FastAPI router — they only read data from backends / audit logs / psutil.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ── Project root (for locating logs/audit.jsonl) ───────────────────
try:
    from maop.dashboard.routers.state import MAOP_ROOT as _PROJECT_ROOT
except ImportError:  # pragma: no cover — state 模块不可用时回退
    _PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent


# ── Helpers ────────────────────────────────────────────────────────


def _get_cost_tracker() -> Any:
    """Lazy accessor for the process-wide CostTracker singleton."""
    from maop.core.cost_tracker import get_cost_tracker
    return get_cost_tracker()


def _parse_date_range(date_from: str, date_to: str) -> tuple[float, float, str, str]:
    """Parse ISO date strings to (start_ts, end_ts, iso_from, iso_to).

    Falls back to "last 24h" when either side is blank. Raises ValueError
    on malformed input — callers wrap in try/except to emit a 400.
    """
    now = datetime.now(timezone.utc)
    if date_from and date_to:
        try:
            start_dt = datetime.fromisoformat(date_from.replace("Z", "+00:00"))
            end_dt = datetime.fromisoformat(date_to.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"Invalid date format: {exc}") from exc
    else:
        end_dt = now
        start_dt = now - timedelta(days=1)
    if start_dt > end_dt:
        start_dt, end_dt = end_dt, start_dt
    # Ensure timezone-aware for consistent .timestamp()
    if start_dt.tzinfo is None:
        start_dt = start_dt.replace(tzinfo=timezone.utc)
    if end_dt.tzinfo is None:
        end_dt = end_dt.replace(tzinfo=timezone.utc)
    return start_dt.timestamp(), end_dt.timestamp(), start_dt.isoformat(), end_dt.isoformat()


def _iso_to_sqlite_str(iso: str) -> str:
    """Convert ISO timestamp to the string format used in cost_entries.created_at.

    CostTracker stores ``created_at`` as ISO strings; we just pass through.
    """
    return iso


def _bucket_index(granularity: str, ts: float) -> str:
    """Return a bucket key for a Unix timestamp given a granularity."""
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    if granularity == "hour":
        return dt.strftime("%Y-%m-%dT%H:00:00")
    if granularity == "week":
        # ISO week: Monday as the bucket start
        monday = dt - timedelta(days=dt.weekday())
        return monday.strftime("%Y-%m-%d")
    # default: day
    return dt.strftime("%Y-%m-%d")


def _bucket_seconds(granularity: str) -> int:
    """Bucket size in seconds for the given granularity."""
    if granularity == "hour":
        return 3600
    if granularity == "week":
        return 7 * 86400
    return 86400  # day


def _safe_psutil() -> Any | None:
    """Return the psutil module if importable, else None."""
    try:
        import psutil
        return psutil
    except ImportError:  # pragma: no cover — psutil is a hard dep in pyproject
        return None


def _get_audit_events(limit: int = 5000) -> list[dict[str, Any]]:
    """Read recent audit events from the personal-edition JSONL log.

    Returns a list of dicts (already model_dump'd). On any failure returns
    an empty list — the caller treats this as "no audit data available".
    """
    try:
        from maop.control.audit import AuditLog
        log = AuditLog(_PROJECT_ROOT / "logs" / "audit.jsonl")
        events = log.read_recent(limit=limit)
        return [e.model_dump() for e in events]
    except Exception as exc:
        logger.debug("[analysis] audit events unavailable: %s", exc)
        return []


def _get_cache_stats() -> dict[str, Any]:
    """Return cache hit/miss stats from the global LRU cache backend.

    Returns an empty dict if the cache backend is unavailable.
    """
    try:
        from maop.core.backends.backends import get_cache_backend
        backend = get_cache_backend()
        # MemoryCacheBackend wraps an LRUCache in _cache
        inner = getattr(backend, "_cache", None)
        if inner is not None and hasattr(inner, "stats"):
            stats = inner.stats()
            return {
                "hits": stats.hits,
                "misses": stats.misses,
                "evictions": stats.evictions,
                "size": stats.size,
                "max_size": stats.max_size,
                "hit_rate": round(stats.hit_rate, 4),
            }
    except Exception as exc:
        logger.debug("[analysis] cache stats unavailable: %s", exc)
    return {}


def _get_db_pool_stats() -> dict[str, Any]:
    """Return DB connection pool stats (size + in-use) if available."""
    try:
        from maop.core.backends.db_utils import get_db_path, get_pool
        pool = get_pool(get_db_path())
        return {
            "max_size": getattr(pool, "_max_size", 0),
            "in_use": len(getattr(pool, "_in_use", [])) if hasattr(pool, "_in_use") else 0,
            "available": len(getattr(pool, "_available", [])) if hasattr(pool, "_available") else 0,
        }
    except Exception as exc:
        logger.debug("[analysis] db pool stats unavailable: %s", exc)
    return {}


def _percentile(values: list[int], pct: int) -> float:
    """Compute the pct-th percentile of a list of integers (linear interpolation)."""
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * (pct / 100.0)
    f = int(k)
    c = f + 1 if f + 1 < len(s) else f
    if f == c:
        return float(s[f])
    return round(s[f] + (s[c] - s[f]) * (k - f), 1)


__all__ = [
    "_PROJECT_ROOT",
    "_get_cost_tracker",
    "_parse_date_range",
    "_iso_to_sqlite_str",
    "_bucket_index",
    "_bucket_seconds",
    "_safe_psutil",
    "_get_audit_events",
    "_get_cache_stats",
    "_get_db_pool_stats",
    "_percentile",
]