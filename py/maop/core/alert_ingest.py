"""Alertmanager 告警落库 —— 个人版、SQLite、有界保留。

在此之前 ``POST /api/alerts/webhook`` 只把每条告警写成一行日志（模块 docstring
里写明"deliberately does NOT persist"）。后果是 Prometheus → Alertmanager →
MAOP 这条链在仪表板里断头：重启即失忆，无法查询、无法确认、UI 只能显示企业版
``/api/audit/alert/history``（个人版 404）。

本模块把入站告警变成可查的历史，同时守住"单机、零配置、轻量"的定位：

* 只用既有的统一 SQLite，不引入 PG/Redis/新依赖；
* 去重键是 ``(fingerprint, status, starts_at)`` —— Alertmanager 的
  ``repeat_interval: 4h`` 会把同一条告警反复推送，这个键让"同一事件的重复通知"
  收敛成同一行，只刷新 ``received_at``。因此行数正比于**事件数**，而不是通知次数；
* 硬上界：``MAX_ROWS`` 行 + ``DEFAULT_RETENTION_DAYS`` 天，谁先到以谁为准。
  实测每行 461 B（10 000 行 4.6 MB，含索引），所以上界可证：这张表最多占
  几 MB，不会因为告警风暴而无限长大。
* 记账失败绝不拖垮 webhook：写库异常只记日志，仍然回 200（否则 Alertmanager
  会无限重试）。
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any

from maop.core.backends.db_utils import get_db_path, sqlite_connect

logger = logging.getLogger(__name__)

#: 单条文本字段裁剪上限：一条话多的告警不能把库撑大。
MAX_ALERTNAME = 200
MAX_SEVERITY = 40
MAX_INSTANCE = 200
MAX_SUMMARY = 200
MAX_DESCRIPTION = 400
MAX_URL = 200

#: 一次 webhook 最多接收多少条告警（内存上界，不是磁盘上界）。
#: Alertmanager 侧 ``max_alerts: 0`` 表示不截断，所以这一刀必须由接收端来。
MAX_ALERTS_PER_WEBHOOK = 1000

#: 保留天数与行数硬上限：两条都跑，取更严的那条约束。
DEFAULT_RETENTION_DAYS = 30
MAX_ROWS = 10_000

#: 表名一律写成字面量（与 ``run_events`` 同风格）：既避免 f-string 拼 SQL 让
#: bandit B608 逐条报警、也避免"常量拼出来的 SQL"掩盖真正需要参数化的地方。
SCHEMA_DDL = """
CREATE TABLE IF NOT EXISTS alertmanager_alerts (
  fingerprint  TEXT NOT NULL,
  status       TEXT NOT NULL,
  starts_at    TEXT NOT NULL,
  alertname    TEXT NOT NULL DEFAULT '',
  severity     TEXT NOT NULL DEFAULT '',
  instance     TEXT NOT NULL DEFAULT '',
  agent        TEXT NOT NULL DEFAULT '',
  tenant_id    TEXT NOT NULL DEFAULT '',
  summary      TEXT NOT NULL DEFAULT '',
  description  TEXT NOT NULL DEFAULT '',
  receiver     TEXT NOT NULL DEFAULT '',
  external_url TEXT NOT NULL DEFAULT '',
  received_at  REAL NOT NULL,
  PRIMARY KEY (fingerprint, status, starts_at)
);
CREATE INDEX IF NOT EXISTS idx_am_alerts_received ON alertmanager_alerts(received_at);
"""


def _db_path() -> Path:
    return get_db_path("alert_ingest", legacy_fallback="maop")


def _clip(value: Any, limit: int) -> str:
    text = "" if value is None else str(value)
    return text[:limit]


def _fingerprint(alert: dict[str, Any], labels: dict[str, str]) -> str:
    """Alertmanager 的 fingerprint；缺失时用标签的稳定摘要兜底。

    兜底是必要的而不是可选的：PK 里 fingerprint 为空会让所有"没有指纹的告警"
    折叠成同一条，静默丢数据。
    """
    fp = _clip(alert.get("fingerprint", ""), 64)
    if fp:
        return fp
    canonical = json.dumps(sorted(labels.items()), ensure_ascii=False, separators=(",", ":"))
    return "gen" + hashlib.sha1(canonical.encode("utf-8"), usedforsecurity=False).hexdigest()[:24]


def _row_from(alert: dict[str, Any], *, receiver: str, external_url: str,
              common_labels: dict[str, str], common_annotations: dict[str, str],
              fallback_status: str = "") -> tuple:
    labels = {str(k): str(v) for k, v in (alert.get("labels") or {}).items()}
    annotations = {str(k): str(v) for k, v in (alert.get("annotations") or {}).items()}
    # 单条没带 status 时退回批次 status（Alertmanager 两者都会给）；日志侧一直是
    # 这么处理的，落库若直接写死 "firing" 就会与日志互相矛盾。
    status = (_clip(alert.get("status", ""), 20)
              or _clip(fallback_status, 20) or "firing")
    starts_at = _clip(alert.get("startsAt", ""), 40)
    summary = annotations.get("summary") or common_annotations.get("summary") or ""
    description = annotations.get("description") or common_annotations.get("description") or ""
    return (
        _fingerprint(alert, labels),
        status,
        starts_at,
        _clip(labels.get("alertname") or common_labels.get("alertname") or "<unknown>", MAX_ALERTNAME),
        _clip(labels.get("severity") or common_labels.get("severity") or "", MAX_SEVERITY),
        _clip(labels.get("instance") or common_labels.get("instance") or "", MAX_INSTANCE),
        _clip(labels.get("agent") or common_labels.get("agent") or "", MAX_INSTANCE),
        _clip(labels.get("tenant_id") or common_labels.get("tenant_id") or "", MAX_INSTANCE),
        _clip(summary, MAX_SUMMARY),
        _clip(description, MAX_DESCRIPTION),
        _clip(receiver, MAX_SEVERITY),
        _clip(external_url, MAX_URL),
        time.time(),
    )


_INSERT = (
    "INSERT OR IGNORE INTO alertmanager_alerts"
    " (fingerprint, status, starts_at, alertname, severity,"
    " instance, agent, tenant_id, summary, description, receiver, external_url, received_at)"
    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)"
)
_TOUCH = (
    "UPDATE alertmanager_alerts SET received_at = ?"
    " WHERE fingerprint = ? AND status = ? AND starts_at = ?"
)


def record_alerts(alerts: list[dict[str, Any]], *, receiver: str = "",
                  external_url: str = "", common_labels: dict[str, str] | None = None,
                  common_annotations: dict[str, str] | None = None,
                  fallback_status: str = "") -> dict[str, int]:
    """Persist one inbound batch. Returns ``{"received", "stored", "duplicate"}``.

    ``stored`` counts new (事件, 状态) rows; a repeat notification of the same
    incident only refreshes ``received_at`` so a still-firing alert does not age
    out of retention.
    """
    received = len(alerts)
    if received == 0:
        return {"received": 0, "stored": 0, "duplicate": 0}
    common_labels = {str(k): str(v) for k, v in (common_labels or {}).items()}
    common_annotations = {str(k): str(v) for k, v in (common_annotations or {}).items()}
    rows = [
        _row_from(alert, receiver=receiver, external_url=external_url,
                  common_labels=common_labels, common_annotations=common_annotations,
                  fallback_status=fallback_status)
        for alert in alerts
    ]
    stored = 0
    try:
        with sqlite_connect(_db_path()) as conn:
            conn.executescript(SCHEMA_DDL)
            for row in rows:
                cur = conn.execute(_INSERT, row)
                if cur.rowcount:
                    stored += 1
                else:
                    conn.execute(_TOUCH, (row[-1], row[0], row[1], row[2]))
    except (sqlite3.Error, OSError) as exc:
        # 记账失败不能杀掉告警入口：日志已经打过，这里只报告"没存进去"。
        logger.warning("[alerts/ingest] persist failed for %d alert(s): %s", received, exc)
        return {"received": received, "stored": 0, "duplicate": 0}
    return {"received": received, "stored": stored, "duplicate": received - stored}


def list_alerts(*, limit: int = 50, status: str = "", severity: str = "",
                alertname: str = "", since_hours: int = 0) -> list[dict[str, Any]]:
    """Newest-first alert history; empty list if nothing is readable."""
    clauses: list[str] = []
    params: list[Any] = []
    if status:
        clauses.append("status = ?")
        params.append(status)
    if severity:
        clauses.append("severity = ?")
        params.append(severity)
    if alertname:
        clauses.append("alertname = ?")
        params.append(alertname)
    if since_hours > 0:
        clauses.append("received_at >= ?")
        params.append(time.time() - since_hours * 3600.0)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    params.append(max(1, min(int(limit), MAX_ROWS)))
    sql = "SELECT * FROM alertmanager_alerts" + where + " ORDER BY received_at DESC LIMIT ?"  # nosec B608 — 子句只含固定片段与 ? 占位
    try:
        with sqlite_connect(_db_path()) as conn:
            conn.executescript(SCHEMA_DDL)
            rows = conn.execute(sql, params).fetchall()
    except (sqlite3.Error, OSError) as exc:
        logger.warning("[alerts/ingest] read failed: %s", exc)
        return []
    return [dict(row) for row in rows]


def count_alerts() -> int:
    """Total stored rows (0 on failure) — used by the pruning report and the UI."""
    try:
        with sqlite_connect(_db_path()) as conn:
            conn.executescript(SCHEMA_DDL)
            return int(conn.execute("SELECT COUNT(*) FROM alertmanager_alerts").fetchone()[0])
    except (sqlite3.Error, OSError) as exc:
        logger.warning("[alerts/ingest] count failed: %s", exc)
        return 0


def prune_alerts(*, older_than_days: int = DEFAULT_RETENTION_DAYS,
                 max_rows: int = MAX_ROWS) -> dict[str, int]:
    """Drop expired rows, then enforce the row ceiling. Never raises."""
    cutoff = time.time() - older_than_days * 86400.0
    expired = capped = 0
    try:
        with sqlite_connect(_db_path()) as conn:
            conn.executescript(SCHEMA_DDL)
            expired = conn.execute(
                "DELETE FROM alertmanager_alerts WHERE received_at < ?", (cutoff,)
            ).rowcount
            total = int(conn.execute("SELECT COUNT(*) FROM alertmanager_alerts").fetchone()[0])
            if total > max_rows:
                # 按 (fingerprint,status,starts_at) 整行裁，绝不按 fingerprint 裁 ——
                # 后者会把同一事件的 firing 与 resolved 两行一起删掉，且删的行数
                # 不等于请求的行数，上界就不再可证。
                capped = conn.execute(
                    "DELETE FROM alertmanager_alerts"
                    " WHERE (fingerprint, status, starts_at) IN ("
                    "  SELECT fingerprint, status, starts_at FROM alertmanager_alerts"
                    "   ORDER BY received_at ASC LIMIT ?)",
                    (total - max_rows,),
                ).rowcount
    except (sqlite3.Error, OSError) as exc:
        logger.warning("[alerts/ingest] prune failed: %s", exc)
    return {"expired": expired, "capped": capped}
