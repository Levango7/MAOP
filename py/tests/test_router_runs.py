"""Tests for the run-review endpoints (M1: /api/runs, /api/runs/{trace_id})."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from maop.core.run_events import record_run_event
from maop.dashboard.routers import runs as runs_mod


def _make_app(roles: list[str]) -> FastAPI:
    app = FastAPI()

    @app.middleware("http")
    async def _inject_roles(request, call_next):
        request.state.auth_roles = roles
        request.state.auth_identity = "test"
        return await call_next(request)

    app.include_router(runs_mod.router)
    return app


@pytest.fixture
def client() -> TestClient:
    # MAOP_DATA_DIR is already per-test tmp (session conftest), so the ledger
    # written here cannot reach the repository data dir.
    return TestClient(_make_app(["admin"]))


def _seed(trace_id: str = "tr-a") -> None:
    record_run_event(trace_id, "run.started", payload={"task": "重构 auth 模块"})
    record_run_event(trace_id, "run.plan", agent="claude",
                     payload={"routing_key": "code", "plan": {"selected_agent": "claude"}})
    record_run_event(trace_id, "run.step", agent="claude", step=1, status="ok",
                     duration_ms=800, payload={"stdout": "done", "exit_code": 0})
    record_run_event(trace_id, "run.verify", agent="claude", status="passed",
                     payload={"summary": "全部通过", "gates": [{"name": "exit_code", "passed": True}]})
    record_run_event(trace_id, "run.cost", agent="claude",
                     payload={"estimated_cost": 0.012, "tokens_in": 10, "tokens_out": 20})
    record_run_event(trace_id, "run.finished", agent="claude", status="success",
                     duration_ms=1234, payload={"success": True, "routing_key": "code"})


class TestRunsList:
    def test_empty_ledger(self, client: TestClient) -> None:
        resp = client.get("/api/runs")
        assert resp.status_code == 200
        assert resp.json()["data"]["items"] == []

    def test_lists_recorded_run(self, client: TestClient) -> None:
        _seed()
        data = client.get("/api/runs").json()["data"]
        assert data["total"] == 1
        item = data["items"][0]
        assert item["trace_id"] == "tr-a"
        assert item["task"] == "重构 auth 模块"
        assert item["steps"] == 1
        assert item["duration_ms"] == 1234
        assert item["success"] is True


class TestRunDetail:
    def test_timeline_and_summary(self, client: TestClient) -> None:
        _seed()
        resp = client.get("/api/runs/tr-a")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert [e["kind"] for e in data["events"]] == [
            "run.started", "run.plan", "run.step", "run.verify", "run.cost", "run.finished",
        ]
        summary = data["summary"]
        assert summary["agent"] == "claude"
        assert summary["status"] == "finished"
        assert summary["success"] is True
        assert summary["verify_status"] == "passed"
        assert summary["verify_summary"] == "全部通过"
        assert summary["cost"]["estimated_cost"] == 0.012
        assert summary["routing_key"] == "code"

    def test_unknown_trace_is_404(self, client: TestClient) -> None:
        assert client.get("/api/runs/nope").status_code == 404

    def test_incomplete_run_has_no_success(self, client: TestClient) -> None:
        record_run_event("tr-b", "run.started", payload={"task": "跑一半"})
        data = client.get("/api/runs/tr-b").json()["data"]
        assert data["summary"]["status"] == "incomplete"
        assert data["summary"]["success"] is None


class TestRunAuth:
    def test_read_role_is_rejected(self) -> None:
        guest = TestClient(_make_app(["read"]))
        assert guest.get("/api/runs").status_code == 403
        assert guest.get("/api/runs/any").status_code == 403
