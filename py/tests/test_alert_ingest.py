"""Tests for the bounded Alertmanager alert ledger (``maop.core.alert_ingest``).

The ledger exists because ``POST /api/alerts/webhook`` used to be a log line:
the monitoring chain was "wired" but nothing could be read back after a
restart. These tests pin the two properties the design depends on — repeat
notifications collapse into one row, and the table cannot grow without bound.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any

import pytest

from maop.core import alert_ingest


@pytest.fixture
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "alerts.db"
    monkeypatch.setattr(alert_ingest, "get_db_path", lambda *a, **k: path)
    return path


def _alert(**over: Any) -> dict[str, Any]:
    alert: dict[str, Any] = {
        "status": "firing",
        "labels": {
            "alertname": "MAOPHighLatency",
            "severity": "warning",
            "instance": "10.0.0.1:8080",
            "agent": "code-reviewer",
        },
        "annotations": {"summary": "P95 latency > 2s", "description": "P95 is 3.2s"},
        "startsAt": "2026-10-21T06:41:30.000Z",
        "endsAt": "0001-01-01T00:00:00.000Z",
        "fingerprint": "abc123",
    }
    alert.update(over)
    return alert


# ── 写入与读回 ──────────────────────────────────────────────────────


def test_record_then_list_returns_the_alert(db: Path) -> None:
    result = alert_ingest.record_alerts(
        [_alert()], receiver="webhook", external_url="http://am:9093",
    )
    assert result == {"received": 1, "stored": 1, "duplicate": 0}

    rows = alert_ingest.list_alerts()
    assert len(rows) == 1
    row = rows[0]
    assert row["alertname"] == "MAOPHighLatency"
    assert row["severity"] == "warning"
    assert row["status"] == "firing"
    assert row["instance"] == "10.0.0.1:8080"
    assert row["agent"] == "code-reviewer"
    assert row["summary"] == "P95 latency > 2s"
    assert row["receiver"] == "webhook"
    assert row["external_url"] == "http://am:9093"


def test_empty_batch_stores_nothing(db: Path) -> None:
    assert alert_ingest.record_alerts([]) == {"received": 0, "stored": 0, "duplicate": 0}
    assert alert_ingest.list_alerts() == []


def test_list_is_newest_first(db: Path) -> None:
    alert_ingest.record_alerts([_alert(fingerprint="old")])
    with sqlite3.connect(str(db)) as conn:
        conn.execute(
            "UPDATE alertmanager_alerts SET received_at = ? WHERE fingerprint = 'old'",
            (time.time() - 3600,),
        )
    alert_ingest.record_alerts([_alert(fingerprint="new")])

    assert [r["fingerprint"] for r in alert_ingest.list_alerts()] == ["new", "old"]


def test_filters_narrow_the_result(db: Path) -> None:
    alert_ingest.record_alerts([
        _alert(fingerprint="f1"),
        _alert(fingerprint="f2", status="resolved"),
        _alert(fingerprint="f3", labels={
            "alertname": "MAOPDown", "severity": "critical", "instance": "i", "agent": "a",
        }),
    ])
    assert {r["fingerprint"] for r in alert_ingest.list_alerts(status="resolved")} == {"f2"}
    assert {r["fingerprint"] for r in alert_ingest.list_alerts(severity="critical")} == {"f3"}
    assert {r["fingerprint"] for r in alert_ingest.list_alerts(alertname="MAOPHighLatency")} == {"f1", "f2"}
    assert len(alert_ingest.list_alerts(limit=1)) == 1


def test_since_hours_excludes_older_rows(db: Path) -> None:
    alert_ingest.record_alerts([_alert(fingerprint="stale")])
    with sqlite3.connect(str(db)) as conn:
        conn.execute(
            "UPDATE alertmanager_alerts SET received_at = ? WHERE fingerprint = 'stale'",
            (time.time() - 30 * 3600,),
        )
    assert alert_ingest.list_alerts(since_hours=24) == []
    assert len(alert_ingest.list_alerts(since_hours=0)) == 1


# ── 去重：repeat_interval 不得放大行数 ─────────────────────────────


def test_repeat_notification_collapses_and_refreshes(db: Path) -> None:
    """同一 (fingerprint, status, startsAt) 反复推送只留一行，但时间戳要刷新。

    刷新是保留期正确性的前提：一条持续 firing 的告警每 4h 被重推一次，若
    received_at 停在首次，30 天后会被裁掉，而它其实还活着。
    """
    first = alert_ingest.record_alerts([_alert()])
    assert first["stored"] == 1
    with sqlite3.connect(str(db)) as conn:
        conn.execute("UPDATE alertmanager_alerts SET received_at = ?", (time.time() - 7200,))

    again = alert_ingest.record_alerts([_alert()])
    assert again == {"received": 1, "stored": 0, "duplicate": 1}

    rows = alert_ingest.list_alerts()
    assert len(rows) == 1
    assert rows[0]["received_at"] > time.time() - 60


def test_firing_and_resolved_are_two_rows(db: Path) -> None:
    """状态翻转是一次新事件，不是重复通知。"""
    alert_ingest.record_alerts([_alert(status="firing"), _alert(status="resolved")])
    rows = alert_ingest.list_alerts()
    assert {r["status"] for r in rows} == {"firing", "resolved"}
    assert len(rows) == 2


def test_missing_fingerprint_does_not_collapse_distinct_alerts(db: Path) -> None:
    """fingerprint 缺失时按标签摘要兜底，否则两条不同告警会撞成同一 PK 被静默丢弃。"""
    result = alert_ingest.record_alerts([
        _alert(fingerprint="", labels={
            "alertname": "MAOPDown", "severity": "critical", "instance": "a", "agent": "x",
        }),
        _alert(fingerprint="", labels={
            "alertname": "MAOPNoActiveAgents", "severity": "warning", "instance": "b", "agent": "y",
        }),
    ])
    assert result["stored"] == 2
    assert len(alert_ingest.list_alerts()) == 2


def test_missing_fingerprint_is_stable_across_key_order(db: Path) -> None:
    a = _alert(fingerprint="", labels={"alertname": "A", "severity": "warning"})
    b = _alert(fingerprint="", labels={"severity": "warning", "alertname": "A"})
    result = alert_ingest.record_alerts([a, b])
    assert result["stored"] == 1
    assert result["duplicate"] == 1


def test_common_labels_used_when_alert_omits_them(db: Path) -> None:
    """Alertmanager 把同组共享的标签放在 commonLabels；单条告警可以不带。"""
    alert_ingest.record_alerts(
        [{"status": "firing", "startsAt": "2026-10-21T06:41:30.000Z", "fingerprint": "cf1"}],
        common_labels={"alertname": "MAOPFromCommon", "severity": "info"},
        common_annotations={"summary": "来自 commonAnnotations"},
    )
    row = alert_ingest.list_alerts()[0]
    assert row["alertname"] == "MAOPFromCommon"
    assert row["severity"] == "info"
    assert row["summary"] == "来自 commonAnnotations"


# ── 有界：字段裁剪 + 保留期 + 行数上界 ────────────────────────────


def test_long_text_is_clipped(db: Path) -> None:
    alert_ingest.record_alerts([
        _alert(annotations={"summary": "S" * 5000, "description": "D" * 5000}),
    ])
    row = alert_ingest.list_alerts()[0]
    assert len(row["summary"]) == alert_ingest.MAX_SUMMARY
    assert len(row["description"]) == alert_ingest.MAX_DESCRIPTION


def test_prune_removes_expired_rows(db: Path) -> None:
    alert_ingest.record_alerts([_alert(fingerprint="ancient"), _alert(fingerprint="fresh")])
    with sqlite3.connect(str(db)) as conn:
        conn.execute(
            "UPDATE alertmanager_alerts SET received_at = ? WHERE fingerprint = 'ancient'",
            (time.time() - 90 * 86400,),
        )
    result = alert_ingest.prune_alerts(older_than_days=30)
    assert result["expired"] == 1
    assert {r["fingerprint"] for r in alert_ingest.list_alerts()} == {"fresh"}


def test_prune_enforces_row_ceiling_exactly(db: Path) -> None:
    """超过上界时按最旧行裁，并且删掉的行数正好是超出量。

    按整行 (fingerprint,status,startsAt) 而不是按 fingerprint 裁：后者会把
    同一事件的 firing 与 resolved 一起删掉，实际删除行数就不等于超出量，
    行数上界不再可证。
    """
    for i in range(12):
        alert_ingest.record_alerts([
            _alert(fingerprint=f"p{i}", startsAt=f"2026-10-21T0{i}:00:00.000Z"),
        ])
    with sqlite3.connect(str(db)) as conn:
        for i in range(12):
            conn.execute(
                "UPDATE alertmanager_alerts SET received_at = ? WHERE fingerprint = ?",
                (1_700_000_000 + i, f"p{i}"),
            )
    assert alert_ingest.count_alerts() == 12

    result = alert_ingest.prune_alerts(older_than_days=9999, max_rows=5)
    assert result["capped"] == 7
    assert alert_ingest.count_alerts() == 5
    # 留下的一定是最新的 5 条
    assert {r["fingerprint"] for r in alert_ingest.list_alerts()} == {"p7", "p8", "p9", "p10", "p11"}


def test_prune_keeps_both_rows_of_one_event_when_capping(db: Path) -> None:
    """裁的是"最旧的整行"，不能因为 fingerprint 相同就顺带删掉兄弟行。

    三行分属两个 fingerprint：p0 的 firing/resolved（时间戳不同，删谁可预测）
    与 p1 的 firing。若按 fingerprint 裁，p0 两行会一起消失、只剩 1 行；按整行
    裁则剩 2 行且 p0/resolved 仍在。
    """
    alert_ingest.record_alerts([
        _alert(fingerprint="p0", status="firing", startsAt="2026-10-01T00:00:00.000Z"),
        _alert(fingerprint="p0", status="resolved", startsAt="2026-10-01T00:00:00.000Z"),
        _alert(fingerprint="p1", status="firing", startsAt="2026-10-02T00:00:00.000Z"),
    ])
    with sqlite3.connect(str(db)) as conn:
        conn.execute(
            "UPDATE alertmanager_alerts SET received_at = 1700000000"
            " WHERE fingerprint = 'p0' AND status = 'firing'"
        )
        conn.execute(
            "UPDATE alertmanager_alerts SET received_at = 1700000001"
            " WHERE fingerprint = 'p0' AND status = 'resolved'"
        )
        conn.execute(
            "UPDATE alertmanager_alerts SET received_at = 1800000000 WHERE fingerprint = 'p1'"
        )

    result = alert_ingest.prune_alerts(older_than_days=9999, max_rows=2)
    assert result["capped"] == 1
    rows = alert_ingest.list_alerts()
    assert len(rows) == 2
    assert {(r["fingerprint"], r["status"]) for r in rows} == {("p0", "resolved"), ("p1", "firing")}


# ── 记账失败不得杀掉告警入口 ──────────────────────────────────────


def test_record_alerts_swallows_storage_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(*a: Any, **k: Any) -> Path:
        raise OSError("disk gone")

    monkeypatch.setattr(alert_ingest, "get_db_path", boom)
    assert alert_ingest.record_alerts([_alert()]) == {
        "received": 1, "stored": 0, "duplicate": 0,
    }


def test_list_alerts_swallows_read_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(*a: Any, **k: Any) -> Path:
        raise OSError("disk gone")

    monkeypatch.setattr(alert_ingest, "get_db_path", boom)
    assert alert_ingest.list_alerts() == []
    assert alert_ingest.count_alerts() == 0
    assert alert_ingest.prune_alerts() == {"expired": 0, "capped": 0}


def test_prune_is_idempotent_on_an_empty_ledger(db: Path) -> None:
    assert alert_ingest.prune_alerts() == {"expired": 0, "capped": 0}
    assert alert_ingest.count_alerts() == 0
