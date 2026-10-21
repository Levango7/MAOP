"""Alertmanager webhook 落库 + GET /api/alerts 读回的端到端契约。

``test_alertmanager_e2e.py`` 钉的是"能不能收"；这里钉的是"收了之后能不能查
回来"，以及两处会让人白干的细节：重复通知必须收敛成一行、批量大小必须有上界。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

_HEADERS = {"X-Webhook-Secret": "test-secret"}


@pytest.fixture
def client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setenv("ALERTS_WEBHOOK_SECRET", "test-secret")
    from maop.core import alert_ingest

    monkeypatch.setattr(
        alert_ingest, "get_db_path", lambda *a, **k: tmp_path / "alerts.db",
    )
    from maop.dashboard.routers.alerts import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _alert(i: int = 0, *, status: str = "firing", alertname: str = "MAOPHighLatency",
           severity: str = "warning") -> dict[str, Any]:
    return {
        "status": status,
        "labels": {
            "alertname": alertname,
            "severity": severity,
            "instance": f"10.0.0.{i}:8080",
            "agent": "code-reviewer",
        },
        "annotations": {"summary": f"P95 latency > 2s #{i}", "description": "still slow"},
        "startsAt": "2026-10-21T06:41:30.000Z",
        "endsAt": "0001-01-01T00:00:00.000Z",
        "fingerprint": f"fp-{i}",
    }


def _payload(*alerts: dict[str, Any]) -> dict[str, Any]:
    first = alerts[0] if alerts else _alert()
    return {
        "receiver": "webhook",
        "status": "firing",
        "alerts": list(alerts) or [],
        "commonLabels": first["labels"],
        "commonAnnotations": first["annotations"],
        "externalURL": "http://maop-alertmanager:9093",
        "version": "4",
    }


# ── 写了就要能读回来 ─────────────────────────────────────────────


def test_get_route_is_registered(client: TestClient) -> None:
    from maop.dashboard.routers.alerts import router

    get_routes = [
        r for r in router.routes
        if getattr(r, "path", "") == "/api/alerts" and "GET" in getattr(r, "methods", set())
    ]
    assert len(get_routes) == 1


def test_webhook_reports_stored_counts(client: TestClient) -> None:
    resp = client.post("/api/alerts/webhook", json=_payload(_alert(1)), headers=_HEADERS)
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "received": 1, "stored": 1, "duplicate": 0}


def test_stored_alert_is_readable_with_a_stable_shape(client: TestClient) -> None:
    client.post(
        "/api/alerts/webhook",
        json=_payload(_alert(1), _alert(2, severity="critical")),
        headers=_HEADERS,
    )
    data = client.get("/api/alerts").json()
    assert data["count"] == 2
    assert data["total"] == 2
    names = {a["alertname"] for a in data["alerts"]}
    assert names == {"MAOPHighLatency"}
    severities = {a["severity"] for a in data["alerts"]}
    assert severities == {"warning", "critical"}
    row = data["alerts"][0]
    for key in ("fingerprint", "status", "starts_at", "instance", "agent",
                "summary", "description", "receiver", "external_url", "received_at"):
        assert key in row, f"GET /api/alerts 缺字段 {key}"
    assert row["external_url"] == "http://maop-alertmanager:9093"


def test_read_history_survives_a_second_batch(client: TestClient) -> None:
    """两次独立推送都留在账上 —— 这正是"重启后还能查"的前提。"""
    client.post("/api/alerts/webhook", json=_payload(_alert(1)), headers=_HEADERS)
    client.post("/api/alerts/webhook", json=_payload(_alert(2)), headers=_HEADERS)
    data = client.get("/api/alerts").json()
    assert data["total"] == 2
    assert {a["fingerprint"] for a in data["alerts"]} == {"fp-1", "fp-2"}


# ── 去重：repeat_interval 不得放大历史 ──────────────────────────


def test_repeat_notification_reports_duplicate(client: TestClient) -> None:
    """同一条告警被重推（Alertmanager repeat_interval=4h）→ duplicate，不新增行。"""
    body = _payload(_alert(1))
    first = client.post("/api/alerts/webhook", json=body, headers=_HEADERS).json()
    again = client.post("/api/alerts/webhook", json=body, headers=_HEADERS).json()
    assert first["stored"] == 1
    assert again == {"status": "ok", "received": 1, "stored": 0, "duplicate": 1}
    assert client.get("/api/alerts").json()["total"] == 1


def test_status_flip_is_not_a_duplicate(client: TestClient) -> None:
    client.post("/api/alerts/webhook", json=_payload(_alert(1)), headers=_HEADERS)
    resolved = client.post(
        "/api/alerts/webhook", json=_payload(_alert(1, status="resolved")), headers=_HEADERS,
    ).json()
    assert resolved["stored"] == 1
    rows = client.get("/api/alerts").json()["alerts"]
    assert {r["status"] for r in rows} == {"firing", "resolved"}


def test_batch_with_one_repeat_counts_it_once(client: TestClient) -> None:
    resp = client.post(
        "/api/alerts/webhook",
        json=_payload(_alert(1), _alert(1), _alert(2)),
        headers=_HEADERS,
    ).json()
    assert resp == {"status": "ok", "received": 3, "stored": 2, "duplicate": 1}


# ── 查询参数必须真的被后端接受 ──────────────────────────────────


def test_query_filters_are_applied_server_side(client: TestClient) -> None:
    client.post(
        "/api/alerts/webhook",
        json=_payload(_alert(1), _alert(2, alertname="MAOPDown", severity="critical")),
        headers=_HEADERS,
    )
    assert client.get("/api/alerts", params={"severity": "critical"}).json()["count"] == 1
    assert client.get("/api/alerts", params={"alertname": "MAOPDown"}).json()["count"] == 1
    assert client.get("/api/alerts", params={"status": "resolved"}).json()["count"] == 0
    assert client.get("/api/alerts", params={"limit": 1}).json()["count"] == 1


def test_out_of_range_query_params_are_rejected(client: TestClient) -> None:
    """未知/越界参数不能静默忽略 —— 静默忽略会让前端以为筛选生效了。"""
    assert client.get("/api/alerts", params={"limit": 0}).status_code == 422
    assert client.get("/api/alerts", params={"limit": 10 ** 6}).status_code == 422
    assert client.get("/api/alerts", params={"since_hours": -1}).status_code == 422


def test_batch_status_flows_into_rows_without_their_own(client: TestClient) -> None:
    """send_resolved=true 时批次里每条都带 status，但 schema 允许单条省略 ——
    省略就必须退回批次状态，否则历史里全是 firing，日志里却是 resolved。"""
    body = _payload({"labels": {"alertname": "MAOPDown", "severity": "critical"},
                     "annotations": {"summary": "back up"},
                     "startsAt": "2026-10-21T06:41:30.000Z",
                     "fingerprint": "fp-nostatus"})
    body["status"] = "resolved"
    client.post("/api/alerts/webhook", json=body, headers=_HEADERS)
    rows = client.get("/api/alerts").json()["alerts"]
    assert [r["status"] for r in rows] == ["resolved"]


# ── 上界：批量大小 ──────────────────────────────────────────────


def test_oversized_batch_is_rejected_not_accumulated(client: TestClient) -> None:
    """``max_alerts: 0`` 让 Alertmanager 不截断，所以内存上界只能由接收端设。"""
    from maop.core.alert_ingest import MAX_ALERTS_PER_WEBHOOK

    body = _payload(*[_alert(i) for i in range(MAX_ALERTS_PER_WEBHOOK + 1)])
    assert client.post("/api/alerts/webhook", json=body, headers=_HEADERS).status_code == 422
    assert client.get("/api/alerts").json()["total"] == 0


def test_batch_at_the_ceiling_is_accepted(client: TestClient) -> None:
    from maop.core.alert_ingest import MAX_ALERTS_PER_WEBHOOK

    body = _payload(*[_alert(i) for i in range(MAX_ALERTS_PER_WEBHOOK)])
    resp = client.post("/api/alerts/webhook", json=body, headers=_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["stored"] == MAX_ALERTS_PER_WEBHOOK


# ── 记账失败不得影响告警入口本身 ────────────────────────────────


def test_webhook_still_200_when_storage_is_broken(client, monkeypatch) -> None:
    """落库失败只降级为 stored=0：回 4xx/5xx 会让 Alertmanager 无限重试。"""
    from maop.core import alert_ingest

    def boom(*a: Any, **k: Any) -> None:
        raise OSError("disk gone")

    monkeypatch.setattr(alert_ingest, "get_db_path", boom)
    resp = client.post("/api/alerts/webhook", json=_payload(_alert(1)), headers=_HEADERS)
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "received": 1, "stored": 0, "duplicate": 0}


# ── 认证边界：读端点不能变成第二个公开口子 ──────────────────────


def test_read_path_is_not_exempted_by_the_public_webhook() -> None:
    """/api/alerts 必须鉴权；公开的只有 /api/alerts/webhook。

    钉两层，缺一层都可能悄悄放开读端点：
      1) AuthMiddleware 的匹配语义 —— public_paths 走精确匹配，所以同前缀的
         /api/alerts 不会被 /api/alerts/webhook 带入（P0-3 之前是每个公开路径
         都做 startswith，那时这里会直接返回 True）；
      2) 注册进中间件的那份清单里确实没有 "/api/alerts"。
    """
    from maop.core.security.middleware import AuthMiddleware

    mw = AuthMiddleware(
        lambda *a, **k: None,
        public_paths=["/api/health", "/api/alerts/webhook"],
        public_prefix_paths=[],
    )
    assert mw._is_public_path("/api/alerts/webhook") is True
    assert mw._is_public_path("/api/alerts") is False
    assert mw._is_public_path("/api/alerts/") is False
    assert mw._is_public_path("/api/alerts/123") is False

    stack = Path(__file__).resolve().parents[1] / "maop" / "dashboard" / "_middleware_stack.py"
    block = stack.read_text(encoding="utf-8").split("public_paths=[", 1)[1].split("],", 1)[0]
    assert '"/api/alerts/webhook"' in block
    assert '"/api/alerts"' not in block
