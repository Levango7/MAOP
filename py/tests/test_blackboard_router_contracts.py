"""HTTP contracts for ``/api/blackboard/*`` — envelopes vs the real producers.

The Blackboard view is the clearest case of a stub freezing a bug: its vitest
mocks used ``id``/``op``/``actor`` and the domains ``tasks``/``results``/``metrics``,
none of which ``BlackboardEntry.to_dict()`` or ``Blackboard._record_history()``
ever emit — and the template's ``h.op || h.operation || h.action`` fallback
chains made both sides look self-consistent. These tests run against a real
``Blackboard`` so the envelope is checked against what actually goes on the wire.

Error paths here are ``JSONResponse`` (``handle_api_errors`` without
``error_value``), so they bypass ``response_model``; the envelopes therefore
declare the success shape only, with a required ``status``.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from maop.core.reliability.blackboard import (
    BlackboardDomain,
    BlackboardEntry,
    reset_blackboard,
)
from maop.dashboard.routers import blackboard as blackboard_router
from maop.dashboard.routers.blackboard_schemas import (
    BlackboardEntryOut,
    HistoryRowOut,
    StatsPayload,
)
from maop.dashboard.services import blackboard_service

CONTRACT_ROUTES: list[tuple[str, str]] = [
    ("GET", "/api/blackboard/snapshot"),
    ("GET", "/api/blackboard/domains"),
    ("GET", "/api/blackboard/domains/{domain}"),
    ("POST", "/api/blackboard/write"),
    ("POST", "/api/blackboard/clear/{domain}"),
    ("GET", "/api/blackboard/history"),
    ("GET", "/api/blackboard/stats"),
]

ENTRY_KEYS = {
    "entry_id", "domain", "content", "contributor", "timestamp", "confidence", "metadata",
}


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    reset_blackboard()
    monkeypatch.setattr(blackboard_router, "require_admin", lambda request: None)
    app = FastAPI()
    app.include_router(blackboard_router.router)
    yield TestClient(app)
    reset_blackboard()


def _write(client: TestClient, domain: str = "problem", **kw: Any) -> dict[str, Any]:
    payload = {"domain": domain, "content": kw.pop("content", "find root cause"),
               "contributor": kw.pop("contributor", "agent-1")}
    payload.update(kw)
    resp = client.post("/api/blackboard/write", json=payload)
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def _assert_keeps_every_payload_field(body: Any, payload: Any) -> None:
    """Response models may fill declared defaults, never drop or retype a key."""
    if isinstance(payload, dict):
        assert isinstance(body, dict), f"envelope changed {payload!r} into {body!r}"
        for key, value in payload.items():
            assert key in body, f"dropped by response_model: {key}"
            _assert_keeps_every_payload_field(body[key], value)
    elif isinstance(payload, list):
        assert isinstance(body, list) and len(body) == len(payload)
        for got, sent in zip(body, payload, strict=True):
            _assert_keeps_every_payload_field(got, sent)
    else:
        assert body == payload, f"altered by response_model: {payload!r} -> {body!r}"


# ── entries ────────────────────────────────────────────────────────


def test_entry_model_matches_domain_to_dict() -> None:
    """One source of truth: the envelope's keys are ``to_dict()``'s keys."""
    real = set(BlackboardEntry(content="x").to_dict())
    assert real == ENTRY_KEYS
    assert set(BlackboardEntryOut.model_fields) == ENTRY_KEYS


def test_write_returns_entry_id_not_id(client: TestClient) -> None:
    """The view keyed rows on ``e.id`` — a key that never existed."""
    data = _write(client, metadata={"run": "r1"}, confidence=0.4)
    assert set(data) == ENTRY_KEYS
    assert "id" not in data, "the UI read e.id; the producer only ever sends entry_id"
    assert data["entry_id"]
    assert data["domain"] == "problem"
    assert data["metadata"] == {"run": "r1"}
    assert data["confidence"] == 0.4
    assert data["content"] == "find root cause"


def test_snapshot_keeps_every_domain_and_entry(client: TestClient) -> None:
    _write(client, "problem", content="p1")
    _write(client, "evidence", content="e1", contributor="agent-2")
    service_payload = blackboard_service.get_snapshot()
    body = client.get("/api/blackboard/snapshot").json()
    assert body["status"] == "ok"
    _assert_keeps_every_payload_field(body["data"], service_payload)
    assert set(body["data"]) == {"problem", "evidence"}
    assert [e["content"] for e in body["data"]["evidence"]] == ["e1"]


def test_domain_rows_and_count(client: TestClient) -> None:
    _write(client, "problem")
    _write(client, "problem", content="second")
    body = client.get("/api/blackboard/domains/problem").json()
    assert body["count"] == 2
    assert len(body["data"]) == 2
    assert {e["domain"] for e in body["data"]} == {"problem"}
    assert all(set(e) == ENTRY_KEYS for e in body["data"])


def test_invalid_domain_still_answers_the_error_shape(client: TestClient) -> None:
    """``error_response`` is a JSONResponse, so it is not re-serialised."""
    resp = client.get("/api/blackboard/domains/nope")
    assert resp.status_code == 400
    assert resp.json()["status"] == "error"


# ── domains ────────────────────────────────────────────────────────


def test_allowed_domains_are_the_enum_values(client: TestClient) -> None:
    """The service flattens ``BlackboardDomain`` via ``.value``; the view feeds
    these into the write form, so a renamed enum member has to show up here."""
    body = client.get("/api/blackboard/domains").json()
    assert body["data"]["allowed"] == [d.value for d in BlackboardDomain]
    assert body["data"]["active"] == []
    _write(client, "hypothesis")
    assert client.get("/api/blackboard/domains").json()["data"]["active"] == ["hypothesis"]


# ── history ────────────────────────────────────────────────────────


def test_history_rows_use_the_producer_vocabulary(client: TestClient) -> None:
    """``_record_history`` writes action/domain/entry_id/contributor/timestamp.

    ``op`` / ``operation`` / ``actor`` / ``target`` / ``id`` were the names the
    template reached for first; they are not on the wire, so the assertions here
    are what keeps a future rename from silently blanking the timeline.
    """
    entry = _write(client, "problem")
    client.post("/api/blackboard/clear/problem")
    body = client.get("/api/blackboard/history").json()
    actions = [row["action"] for row in body["data"]]
    assert actions == ["write", "clear"]
    write_row = body["data"][0]
    assert set(write_row) >= {"action", "domain", "entry_id", "contributor", "timestamp"}
    assert write_row["entry_id"] == entry["entry_id"]
    assert write_row["contributor"] == "agent-1"
    for row in body["data"]:
        assert not {"op", "operation", "actor", "target", "id"} & set(row)


def test_clear_history_extra_keys_survive_the_envelope(client: TestClient) -> None:
    """``cleared_count`` comes from ``_record_history(extra=...)`` — a declared
    response model with default filtering would drop it."""
    _write(client, "problem")
    client.post("/api/blackboard/clear/problem")
    row = client.get("/api/blackboard/history").json()["data"][-1]
    assert row["action"] == "clear"
    assert row["cleared_count"] == 1, "extra=allow must pass producer extras through"
    assert "cleared_count" not in HistoryRowOut.model_fields


# ── stats ──────────────────────────────────────────────────────────


def test_stats_envelope_matches_service_payload(client: TestClient) -> None:
    _write(client, "decision")
    payload = blackboard_service.get_stats()
    body = client.get("/api/blackboard/stats").json()
    assert set(StatsPayload.model_fields) == set(payload)
    _assert_keeps_every_payload_field(body["data"], payload)
    assert body["data"]["total_entries"] == 1
    assert body["data"]["active_domains"] == ["decision"]
    assert body["data"]["event_bus_enabled"] is False


# ── clear ──────────────────────────────────────────────────────────


def test_clear_envelope_is_flat_where_the_view_reads_it(client: TestClient) -> None:
    """``clearDomain()`` reads ``data.cleared`` at the top level, not under ``data``."""
    _write(client, "problem")
    _write(client, "problem", content="second")
    body = client.post("/api/blackboard/clear/problem").json()
    assert set(body) == {"status", "domain", "cleared"}
    assert body["domain"] == "problem"
    assert body["cleared"] == 2


# ── wiring ─────────────────────────────────────────────────────────


def test_all_contract_routes_declare_a_response_model(client: TestClient) -> None:
    schema = client.app.openapi()
    problems: list[str] = []
    for method, template in CONTRACT_ROUTES:
        op = (schema["paths"].get(template) or {}).get(method.lower())
        if op is None:
            problems.append(f"MISSING {method} {template}")
            continue
        body = ((op.get("responses") or {}).get("200", {}).get("content") or {}).get(
            "application/json", {},
        ).get("schema") or {}
        if "$ref" not in body and "properties" not in body:
            problems.append(f"UNtyped {method} {template}")
    assert problems == []
