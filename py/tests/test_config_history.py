"""Tests for configuration history & rollback — ConfigHistory + API endpoints.

Covers:
  * ConfigHistory.save_snapshot (version increment, audit fields, payload)
  * ConfigHistory.list_history (ordering, limit clamping)
  * ConfigHistory.get_version (hit / miss / payload round-trip)
  * ConfigHistory.rollback (new snapshot, event bus, error on missing version)
  * Serialisation (dict / pydantic / arbitrary object)
  * FastAPI router endpoints (list / detail / rollback / 404 / admin guard)
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from maop.core.config.config_history import (
    CONFIG_CHANGED_TOPIC,
    ConfigHistory,
    reset_config_history,
)


# ── Fixtures ──────────────────────────────────────────────────────


@pytest.fixture
def history(tmp_path: Any) -> ConfigHistory:
    """Fresh ConfigHistory backed by an isolated temp DB."""
    reset_config_history()
    inst = ConfigHistory(db_path=tmp_path / "config_history.db")
    yield inst
    inst.close()
    reset_config_history()


@pytest.fixture
def app_with_history(history: ConfigHistory) -> FastAPI:
    """Minimal FastAPI app with the config router + history on app.state."""
    from maop.dashboard.routers.config import router as config_router

    app = FastAPI()
    app.state.config_history = history
    # Stub auth state so require_admin passes: tests set auth_roles=["admin"].
    @app.middleware("http")
    async def _stub_auth(request: Request, call_next):
        if not hasattr(request.state, "auth_roles"):
            request.state.auth_roles = ["admin"]
            request.state.auth_identity = "test-admin"
        return await call_next(request)

    app.include_router(config_router)
    return app


@pytest.fixture
def client(app_with_history: FastAPI) -> TestClient:
    return TestClient(app_with_history)


@pytest.fixture
def app_no_admin(history: ConfigHistory) -> FastAPI:
    """App where the caller has no admin role (for 403 tests)."""
    from maop.dashboard.routers.config import router as config_router

    app = FastAPI()
    app.state.config_history = history

    @app.middleware("http")
    async def _stub_no_admin(request: Request, call_next):
        request.state.auth_roles = []
        request.state.auth_identity = "anonymous"
        return await call_next(request)

    app.include_router(config_router)
    return app


@pytest.fixture
def client_no_admin(app_no_admin: FastAPI) -> TestClient:
    return TestClient(app_no_admin)


# ── ConfigHistory.save_snapshot ───────────────────────────────────


class TestSaveSnapshot:
    def test_save_returns_version_1_for_first_snapshot(self, history: ConfigHistory):
        result = history.save_snapshot({"agents": {"claude": {}}}, changed_by="alice")
        assert result["version"] == 1
        assert result["changed_by"] == "alice"
        assert result["changed_at"]  # ISO string

    def test_save_increments_version_monotonically(self, history: ConfigHistory):
        v1 = history.save_snapshot({"a": 1})["version"]
        v2 = history.save_snapshot({"a": 2})["version"]
        v3 = history.save_snapshot({"a": 3})["version"]
        assert (v1, v2, v3) == (1, 2, 3)

    def test_save_records_changed_by_and_at(self, history: ConfigHistory):
        result = history.save_snapshot({"x": True}, changed_by="bob")
        assert result["changed_by"] == "bob"
        assert "T" in result["changed_at"]  # ISO-8601 contains 'T'

    def test_save_snapshot_round_trips_payload(self, history: ConfigHistory):
        cfg = {"agents": {"claude": {"model": "step-3.7"}}, "routing": {}}
        result = history.save_snapshot(cfg)
        assert result["snapshot"] == cfg


# ── ConfigHistory.list_history ────────────────────────────────────


class TestListHistory:
    def test_list_returns_newest_first(self, history: ConfigHistory):
        history.save_snapshot({"v": 1})
        history.save_snapshot({"v": 2})
        history.save_snapshot({"v": 3})
        items = history.list_history()
        assert [i["version"] for i in items] == [3, 2, 1]

    def test_list_respects_limit(self, history: ConfigHistory):
        for i in range(10):
            history.save_snapshot({"i": i})
        items = history.list_history(limit=3)
        assert len(items) == 3
        assert [i["version"] for i in items] == [10, 9, 8]

    def test_list_clamps_limit_to_max_500(self, history: ConfigHistory):
        # limit > 500 should be clamped to 500, not error.
        items = history.list_history(limit=1000)
        assert items == []  # no snapshots yet, but no exception either

    def test_list_excludes_snapshot_payload(self, history: ConfigHistory):
        history.save_snapshot({"big": "payload"})
        items = history.list_history()
        assert len(items) == 1
        assert "snapshot" not in items[0]
        assert set(items[0].keys()) == {"version", "changed_by", "changed_at"}


# ── ConfigHistory.get_version ─────────────────────────────────────


class TestGetVersion:
    def test_get_version_returns_full_record(self, history: ConfigHistory):
        history.save_snapshot({"agents": {"claude": {"model": "x"}}}, changed_by="alice")
        record = history.get_version(1)
        assert record is not None
        assert record["version"] == 1
        assert record["changed_by"] == "alice"
        assert record["snapshot"] == {"agents": {"claude": {"model": "x"}}}

    def test_get_version_returns_none_for_missing(self, history: ConfigHistory):
        history.save_snapshot({"a": 1})
        assert history.get_version(999) is None

    def test_latest_version(self, history: ConfigHistory):
        assert history.latest_version() is None
        history.save_snapshot({"a": 1})
        history.save_snapshot({"a": 2})
        assert history.latest_version() == 2


# ── ConfigHistory.rollback ────────────────────────────────────────


class TestRollback:
    def test_rollback_creates_new_snapshot_with_old_payload(self, history: ConfigHistory):
        history.save_snapshot({"state": "v1"}, changed_by="alice")
        history.save_snapshot({"state": "v2"}, changed_by="bob")
        # Roll back to v1 → should create v3 with v1's payload.
        restored = history.rollback(1)
        assert restored["version"] == 3
        assert restored["snapshot"] == {"state": "v1"}
        assert "rollback:v1" in restored["changed_by"]

    def test_rollback_raises_value_error_for_missing_version(self, history: ConfigHistory):
        history.save_snapshot({"a": 1})
        with pytest.raises(ValueError, match="not found"):
            history.rollback(999)

    def test_rollback_fires_config_changed_event(self, history: ConfigHistory):
        from maop.core.reliability.event_bus import get_event_bus

        bus = get_event_bus()
        bus.clear()
        seen_topics: list[str] = []

        def _handler(event):
            seen_topics.append(event.topic)

        bus.subscribe(CONFIG_CHANGED_TOPIC, _handler)

        history.save_snapshot({"s": 1})
        history.save_snapshot({"s": 2})
        history.rollback(1)

        assert CONFIG_CHANGED_TOPIC in seen_topics

    def test_rollback_event_payload_carries_versions(self, history: ConfigHistory):
        from maop.core.reliability.event_bus import get_event_bus

        bus = get_event_bus()
        bus.clear()
        captured: list[dict] = []

        def _handler(event):
            captured.append(event.data)

        bus.subscribe(CONFIG_CHANGED_TOPIC, _handler)

        history.save_snapshot({"s": 1})
        history.save_snapshot({"s": 2})
        history.rollback(1)

        assert len(captured) == 1
        assert captured[0]["action"] == "rollback"
        assert captured[0]["restored_from_version"] == 1
        assert captured[0]["new_version"] == 3


# ── Serialisation ─────────────────────────────────────────────────


class TestSerialisation:
    def test_serialise_dict(self, history: ConfigHistory):
        result = history.save_snapshot({"a": 1, "b": [1, 2, 3]})
        assert result["snapshot"] == {"a": 1, "b": [1, 2, 3]}

    def test_serialise_pydantic_model(self, history: ConfigHistory):
        from maop.config.loader import AgentDef

        agent = AgentDef(cli="claude", driver="cli", model="step-3.7")
        result = history.save_snapshot({"agent": agent})
        # Pydantic model_dump → dict with all fields.
        assert result["snapshot"]["agent"]["cli"] == "claude"
        assert result["snapshot"]["agent"]["model"] == "step-3.7"

    def test_serialise_nested_structure(self, history: ConfigHistory):
        cfg = {
            "agents": {"claude": {"model": "x"}, "kimi": {"model": "y"}},
            "routing": {"codegen": {"primary": "claude"}},
        }
        result = history.save_snapshot(cfg)
        assert result["snapshot"] == cfg


# ── API endpoints ─────────────────────────────────────────────────


class TestApiEndpoints:
    def test_api_list_history_empty(self, client: TestClient):
        resp = client.get("/api/config/history")
        assert resp.status_code == 200
        data = resp.json()
        assert data["history"] == []
        assert data["count"] == 0

    def test_api_list_history_with_snapshots(self, client: TestClient, history: ConfigHistory):
        history.save_snapshot({"v": 1}, changed_by="alice")
        history.save_snapshot({"v": 2}, changed_by="bob")
        resp = client.get("/api/config/history")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 2
        assert data["history"][0]["version"] == 2
        assert data["history"][0]["changed_by"] == "bob"

    def test_api_list_history_limit_query(self, client: TestClient, history: ConfigHistory):
        for i in range(5):
            history.save_snapshot({"i": i})
        resp = client.get("/api/config/history?limit=2")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 2
        assert [h["version"] for h in data["history"]] == [5, 4]

    def test_api_get_version_found(self, client: TestClient, history: ConfigHistory):
        history.save_snapshot({"agents": {"claude": {"model": "x"}}}, changed_by="alice")
        resp = client.get("/api/config/history/1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["version"] == 1
        assert data["changed_by"] == "alice"
        assert data["snapshot"] == {"agents": {"claude": {"model": "x"}}}

    def test_api_get_version_404(self, client: TestClient, history: ConfigHistory):
        resp = client.get("/api/config/history/999")
        assert resp.status_code == 404

    def test_api_rollback_success(self, client: TestClient, history: ConfigHistory):
        history.save_snapshot({"state": "v1"}, changed_by="alice")
        history.save_snapshot({"state": "v2"}, changed_by="bob")
        resp = client.post("/api/config/rollback/1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["restored_from_version"] == 1
        assert data["new_version"] == 3

    def test_api_rollback_404_for_missing_version(self, client: TestClient, history: ConfigHistory):
        resp = client.post("/api/config/rollback/999")
        assert resp.status_code == 404

    def test_api_rollback_creates_new_snapshot_visible_in_list(
        self, client: TestClient, history: ConfigHistory,
    ):
        history.save_snapshot({"s": 1})
        history.save_snapshot({"s": 2})
        client.post("/api/config/rollback/1")
        resp = client.get("/api/config/history")
        data = resp.json()
        # Should now have 3 versions: 1, 2, 3(=rollback to 1).
        assert data["count"] == 3
        assert data["history"][0]["version"] == 3

    def test_api_requires_admin_for_list(self, client_no_admin: TestClient):
        resp = client_no_admin.get("/api/config/history")
        assert resp.status_code == 403

    def test_api_requires_admin_for_rollback(self, client_no_admin: TestClient):
        resp = client_no_admin.post("/api/config/rollback/1")
        assert resp.status_code == 403

    def test_api_requires_admin_for_detail(self, client_no_admin: TestClient):
        resp = client_no_admin.get("/api/config/history/1")
        assert resp.status_code == 403