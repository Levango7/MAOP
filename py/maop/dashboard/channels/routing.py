"""Delegation / chain / queue routing endpoints for :class:`DataProxy`."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, cast

logger = logging.getLogger(__name__)


class RoutingMixin:
    """Delegation trend, failover chain, and queue statistics endpoints.

    Provides:
        - ``delegation_period_stats`` — MoM/YoY trend for delegation volume
        - ``_read_delegations_file``  — static helper reading delegations.json
        - ``chain``                  — failover chain info
        - ``queue_stats``            — message queue statistics (async)
        - ``_queue_stats_sync``      — sync queue stats (for run_in_executor)
    """

    @staticmethod
    def _read_delegations_file(log_path: Path) -> Any:
        """Read logs/delegations.json — blocking I/O, run via asyncio.to_thread."""
        with open(log_path, encoding="utf-8") as fh:
            return json.load(fh)

    async def delegation_period_stats(
        self, now: datetime | None = None
    ) -> dict[str, Any]:
        """Compute MoM / YoY trend for delegation volume and success rate.

        The genuine delegation history lives in ``logs/delegations.json``
        (the SQL ``delegations`` table is not populated by the current
        pipeline). This method reads that file and returns, for the trailing
        30-day window (the natural base for 环比/MoM) and the trailing 365-day
        window (同比/YoY):
          - ``total`` / ``success_rate`` for the current window
          - ``delegations_mom`` / ``delegations_yoy`` : % change vs previous
            window (None when the previous window has no data)
          - ``success_rate_mom`` / ``success_rate_yoy`` : percentage-point delta

        Returning None (not 0) for a missing previous period lets the UI skip
        the trend pill instead of showing a misleading 0%.
        """

        def _parse_ts(s: str) -> datetime | None:
            if not s:
                return None
            s = str(s).strip()
            if s.endswith("Z"):
                s = s[:-1] + "+00:00"
            m = re.match(r"^(.*\.\d+)([+\-]\d{2}:?\d{2})$", s)
            if m:
                frac = m.group(1)
                dot = frac.rfind(".")
                frac6 = frac[: dot + 1] + frac[dot + 1 : dot + 7].ljust(6, "0")[:6]
                s = frac6 + m.group(2)
            try:
                return datetime.fromisoformat(s)
            except Exception as exc:
                logger.warning("data_proxy._parse_ts failed: %s", exc)
                return None

        now_dt = now or datetime.now(timezone.utc)
        empty = {
            "total": 0, "success_rate": 0.0,
            "delegations_mom": None, "delegations_yoy": None,
            "success_rate_mom": None, "success_rate_yoy": None,
        }
        log_path = Path(self._root) / "logs" / "delegations.json"
        if not log_path.exists():
            return empty
        try:
            # 文件读取放线程池执行，避免阻塞事件循环（ASYNC230）。
            records = await asyncio.to_thread(self._read_delegations_file, log_path)
        except Exception as exc:
            logger.warning("[bridge] delegation_period_stats read failed: %s", exc)
            return empty
        if not isinstance(records, list):
            return empty


        def _window(since: datetime, until: datetime) -> tuple[int, float]:
            total = 0
            succ = 0
            for rec in records:
                if not isinstance(rec, dict):
                    continue
                ts = _parse_ts(cast(str, rec.get("timestamp")))
                if ts is None or not (since <= ts < until):
                    continue
                total += 1
                ec = rec.get("exit_code")
                if ec is None:
                    ec = (rec.get("result") or {}).get("exit_code")
                if ec == 0:
                    succ += 1
            rate = round(succ / total * 100, 1) if total else 0.0
            return total, rate

        cur30_s, cur30_e = now_dt - timedelta(days=30), now_dt
        prev30_s, prev30_e = now_dt - timedelta(days=60), now_dt - timedelta(days=30)
        cur365_s, cur365_e = now_dt - timedelta(days=365), now_dt
        prev365_s, prev365_e = now_dt - timedelta(days=730), now_dt - timedelta(days=365)

        cur30_t, cur30_r = _window(cur30_s, cur30_e)
        prev30_t, prev30_r = _window(prev30_s, prev30_e)
        cur365_t, cur365_r = _window(cur365_s, cur365_e)
        prev365_t, prev365_r = _window(prev365_s, prev365_e)

        def _pct(cur: int, prev: int) -> float | None:
            return round((cur - prev) / prev * 100, 1) if prev else None

        return {
            "total": cur30_t,
            "success_rate": cur30_r,
            "delegations_mom": _pct(cur30_t, prev30_t),
            "delegations_yoy": _pct(cur365_t, prev365_t),
            "success_rate_mom": round(cur30_r - prev30_r, 1) if prev30_r else None,
            "success_rate_yoy": round(cur365_r - prev365_r, 1) if prev365_r else None,
        }

    async def chain(self) -> list[dict[str, Any]]:
        """Fallback chain info — replaces correlation.ps1 -Action chain."""
        start = time.monotonic()

        rows = await self._query_maop(
            "SELECT name, agents, current_index FROM failover_chains"
        )

        self._record_latency(start)
        return rows

    def _queue_stats_sync(self) -> dict[str, int]:
        """Sync queue stats — for run_in_executor."""
        pool = self._pool_queue()
        conn = pool.acquire()
        try:
            rows = conn.execute(
                "SELECT status, COUNT(*) as cnt FROM queue_messages GROUP BY status"
            ).fetchall()
            counts = {r["status"]: r["cnt"] for r in rows}
            dead = conn.execute(
                "SELECT COUNT(*) as cnt FROM queue_dead_letters"
            ).fetchone()["cnt"]
            return {"pending": counts.get("pending", 0), "processing": counts.get("processing", 0), "dead_letters": dead}
        except Exception as exc:
            logger.warning("data_proxy._queue_stats_sync failed: %s", exc)
            return {"pending": 0, "processing": 0, "dead_letters": 0}
        finally:
            pool.release(conn)

    async def queue_stats(self) -> dict[str, Any]:
        """Message queue statistics — from queue.db."""
        start = time.monotonic()
        result = await asyncio.get_running_loop().run_in_executor(
            None, self._queue_stats_sync
        )
        self._record_latency(start)
        return result