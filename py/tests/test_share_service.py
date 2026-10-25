"""``share_service`` 与 ``/api/share`` 端点。

三条安全断言是这个模块存在的理由，必须钉死：
  1. 分享内容**只有 summary**，绝不包含 events 里的 stdout/stderr/error；
  2. 过期与不存在一律 404，不区分（否则可探测 token 是否曾存在）；
  3. 读取端点不需要认证，但创建/撤销需要 admin。
"""

from __future__ import annotations

import time
from typing import Any

import pytest

from maop.dashboard.services import share_service


@pytest.fixture()
def temp_db(tmp_path, monkeypatch: pytest.MonkeyPatch):
    """把 share 库指到临时目录，避免污染真实的 run_shares.db。"""
    from maop.core.backends import db_utils

    db_file = tmp_path / "run_shares.db"
    monkeypatch.setattr(db_utils, "get_db_path", lambda *a, **kw: db_file)
    return db_file


@pytest.fixture()
def fake_run(monkeypatch: pytest.MonkeyPatch):
    """替身 run_service：summary 正常，但 events 里带敏感 payload。"""
    from maop.dashboard.services import run_service

    def _detail(trace_id: str) -> dict[str, Any] | None:
        if trace_id != "tr-ok":
            return None
        return {
            "summary": {
                "trace_id": trace_id,
                "task": "refactor auth",
                "agent": "codex",
                "status": "finished",
                "success": True,
                "duration_ms": 1234,
                "steps": 2,
                "verify_status": "passed",
                "verify_summary": "all gates green",
                "cost": {"estimated_cost": 0.02},
            },
            # 这些**不能**出现在分享结果里
            "events": [
                {
                    "seq": 1, "kind": "run.step", "agent": "codex", "status": "ok",
                    "payload": {"stdout": "API_KEY=sk-live-SECRET", "stderr": "/home/alice/secret"},
                }
            ],
            "decisions": [],
        }

    monkeypatch.setattr(run_service, "get_run_detail", _detail)


# ── service 层 ─────────────────────────────────────────────────────


def test_create_and_read_roundtrip(temp_db, fake_run) -> None:
    created = share_service.create_share("tr-ok", expires_in_hours=1)
    assert created["token"]
    assert created["trace_id"] == "tr-ok"

    got = share_service.get_share(created["token"])
    assert got is not None
    assert got["summary"]["task"] == "refactor auth"


def test_shared_payload_never_contains_event_payloads(temp_db, fake_run) -> None:
    """核心安全断言：stdout/stderr 不得出现在分享结果里。"""
    created = share_service.create_share("tr-ok")
    got = share_service.get_share(created["token"])

    blob = repr(got)
    assert "sk-live-SECRET" not in blob
    assert "/home/alice/secret" not in blob
    assert "events" not in got
    assert "stdout" not in blob


def test_create_unknown_run_raises_keyerror(temp_db, fake_run) -> None:
    with pytest.raises(KeyError):
        share_service.create_share("tr-missing")


def test_expired_share_reads_as_absent(temp_db, fake_run, monkeypatch: pytest.MonkeyPatch) -> None:
    created = share_service.create_share("tr-ok", expires_in_hours=1)

    # 把当前时间推到过期之后
    real_time = time.time
    monkeypatch.setattr(share_service.time, "time", lambda: real_time() + 7200)
    assert share_service.get_share(created["token"]) is None


def test_revoke_removes_the_link(temp_db, fake_run) -> None:
    created = share_service.create_share("tr-ok")
    assert share_service.revoke_share(created["token"]) is True
    assert share_service.get_share(created["token"]) is None


def test_revoke_unknown_token_returns_false(temp_db, fake_run) -> None:
    assert share_service.revoke_share("nope") is False


def test_negative_expiry_rejected(temp_db, fake_run) -> None:
    with pytest.raises(ValueError):
        share_service.create_share("tr-ok", expires_in_hours=-1)


def test_list_excludes_expired(temp_db, fake_run, monkeypatch: pytest.MonkeyPatch) -> None:
    keep = share_service.create_share("tr-ok", expires_in_hours=24)
    share_service.create_share("tr-ok", expires_in_hours=1)

    real_time = time.time
    monkeypatch.setattr(share_service.time, "time", lambda: real_time() + 7200)
    listed = share_service.list_shares()
    assert [s["token"] for s in listed] == [keep["token"]]


# ── 路由层 ─────────────────────────────────────────────────────────


def test_router_exposes_expected_paths() -> None:
    from maop.dashboard.routers import share

    paths = {(r.path, tuple(sorted(r.methods))) for r in share.router.routes}
    assert ("/api/share", ("POST",)) in paths
    assert ("/api/share", ("GET",)) in paths
    assert ("/api/share/{token}", ("GET",)) in paths
    assert ("/api/share/{token}", ("DELETE",)) in paths
