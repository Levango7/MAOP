"""HTTP contracts for ``/api/subagent/*`` and the row vocabulary the view reads.

The defect this file exists for: the dashboard reads ``a.agent_id`` and
``a.agent`` for every subagent row, but ``SubAgentManager.list_agents()``
returns the raw DB columns ``id`` and ``name``. The view therefore rendered a
blank id column and sent ``agent_id: undefined`` with every row action — and
the router rejects that with 400 "missing agent_id", so wait / transcript /
cancel were three dead buttons.

The row shape is asserted against the real ``SubAgentManager`` over a
temporary SQLite file (rows inserted through the manager's own connection)
rather than against a hand-written mock: a mock would only restate the
assumption under test. The pre-existing coverage test mocked
``list_agents`` with ``{"id": ..., "agent": ...}`` — a shape the manager has
never produced — and mocked the transcript with plain strings, so both
mocks agreed with the view and nobody noticed.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from maop.dashboard.routers import subagent as subagent_router
from maop.dashboard.routers.subagent_schemas import (
    SubAgentIdEnvelope,
    SubAgentListEnvelope,
    SubAgentTranscriptEnvelope,
    SubAgentWaitEnvelope,
)

CONTRACT_ROUTES: list[tuple[str, str]] = [
    ("POST", "/api/subagent/spawn"),
    ("POST", "/api/subagent/wait"),
    ("POST", "/api/subagent/cancel"),
    ("GET", "/api/subagent/list"),
    ("GET", "/api/subagent/transcript"),
]

#: The two columns the list renames so the row speaks the words the rest of
#: this API uses for the same entity. Every other column keeps its name.
RENAMED_COLUMNS = {"id": "agent_id", "name": "agent"}

VIEW_FILE = (
    Path(__file__).resolve().parents[2]
    / "dashboard-enterprise"
    / "src"
    / "views"
    / "Subagents.vue"
)


@pytest.fixture
def mgr(tmp_path):
    from maop.core.agent.delegation.subagent_lifecycle import SubAgentManager

    return SubAgentManager(root_dir=str(tmp_path))


@pytest.fixture
def client(mgr, monkeypatch):
    monkeypatch.setattr(subagent_router, "_get_subagent_mgr", lambda: mgr)
    app = FastAPI()

    @app.middleware("http")
    async def _inject_admin(request, call_next):
        request.state.auth_roles = ["admin"]
        request.state.auth_identity = "admin"
        return await call_next(request)

    app.include_router(subagent_router.router)
    return TestClient(app)


def _seed_row(mgr, agent_id: str = "sa-1", name: str = "coder") -> None:
    """Insert one row the way the manager stores it.

    ``spawn()`` is not usable here: it starts a task that calls a real LLM.
    The row is written through the manager's own connection so the SELECT in
    ``list_agents()`` reads exactly the columns it reads in production.
    """
    with mgr._connect() as conn:
        conn.execute(
            """INSERT INTO subagents
               (id, name, role, model, task, status, created_at, output)
               VALUES (?, ?, 'leaf', 'claude-3', 'refactor module', 'running',
                       '1700000000.0', 'done')""",
            (agent_id, name),
        )


# ── the list speaks the vocabulary the view reads ─────────────────


def test_list_uses_the_vocabulary_the_view_reads(client, mgr) -> None:
    """The id must be ``agent_id`` and the name ``agent`` — the words the
    view (and the rest of this API) uses for the same entity."""
    _seed_row(mgr)
    body = client.get("/api/subagent/list").json()
    assert body["status"] == "ok"
    assert body["count"] == 1
    row = body["agents"][0]
    assert row["agent_id"] == "sa-1"
    assert row["agent"] == "coder"


def test_list_row_keeps_every_producer_column(client, mgr) -> None:
    """No column the manager selects may be dropped on the way out.

    The expected key set is derived from the manager's own row, not from a
    hand-written list — otherwise this test would only restate the envelope.
    """
    _seed_row(mgr)
    raw = mgr.list_agents()[0]
    row = client.get("/api/subagent/list").json()["agents"][0]
    for column in raw:
        assert RENAMED_COLUMNS.get(column, column) in row, f"column dropped: {column}"
    assert row["agent_id"] == raw["id"]
    assert row["agent"] == raw["name"]
    assert row["role"] == "leaf"
    assert row["model"] == "claude-3"
    assert row["task"] == "refactor module"
    assert row["status"] == "running"
    assert row["created_at"] == "1700000000.0"


def test_the_id_from_the_list_is_usable_by_the_row_actions(client, mgr) -> None:
    """A row action must not answer 400 "missing agent_id".

    The real manager reports "not running" as False, so the route answers
    404 — a different code, and the proof that the id was understood.
    """
    _seed_row(mgr)
    agent_id = client.get("/api/subagent/list").json()["agents"][0]["agent_id"]
    resp = client.post("/api/subagent/cancel", json={"agent_id": agent_id})
    assert resp.status_code == 404
    assert "missing agent_id" not in resp.json()["error"]


def test_the_view_reads_no_key_the_producer_does_not_emit(client, mgr) -> None:
    """Static guard against the live response: the view must not read a key
    the endpoint never sends, and every key it reads must arrive with the
    producer's value.

    Key names alone are not enough: when the endpoint emitted the DB columns
    while the envelope declared ``agent_id``, the response still *had* an
    ``agent_id`` key — empty. The view rendered a blank id and sent
    ``agent_id: undefined``. So the values are checked too.
    """
    _seed_row(mgr)
    raw = mgr.list_agents()[0]
    row = client.get("/api/subagent/list").json()["agents"][0]
    source = {RENAMED_COLUMNS.get(k, k): v for k, v in raw.items()}

    source_text = VIEW_FILE.read_text(encoding="utf-8")
    read_keys = set(re.findall(r"\ba\.([A-Za-z_][A-Za-z0-9_]*)", source_text))
    assert read_keys, "the scan found no row property access — the view moved?"
    assert read_keys <= set(row), f"view reads keys the API never sends: {read_keys - set(row)}"
    for key in read_keys:
        assert row[key] == source[key], (
            f"the view reads {key}, but the endpoint sent {row[key]!r} "
            f"instead of the producer's {source[key]!r}"
        )


def test_the_view_binds_the_producer_keys_it_reads() -> None:
    """Both directions, by hand: every key the view reads must be one the
    endpoint sends, and the ones the table shows must be spelled the same."""
    source = VIEW_FILE.read_text(encoding="utf-8")
    for expression in (
        "a.agent_id",
        "a.agent ||",
        "a.task",
        "a.model",
        "a.status",
    ):
        assert expression in source, f"view no longer reads {expression!r}"
    assert "a.agent_name" not in source, "the producer never sent agent_name"


# ── transcript / wait carry the producer's own models ─────────────


def test_transcript_returns_the_producer_entries(client, mgr) -> None:
    mgr._append_transcript("sa-1", "started", {"task": "refactor module"})
    body = client.get("/api/subagent/transcript?agent_id=sa-1").json()
    assert body["status"] == "ok"
    entry = body["transcript"][0]
    assert entry["agent_id"] == "sa-1"
    assert entry["event"] == "started"
    assert entry["data"] == {"task": "refactor module"}


def test_wait_returns_the_agent_result_fields(client, mgr) -> None:
    _seed_row(mgr)
    body = client.post("/api/subagent/wait", json={"agent_id": "sa-1"}).json()
    assert body["status"] == "ok"
    result = body["result"]
    assert result["agent_id"] == "sa-1"
    assert result["output"] == "done"
    assert result["status"] == "running"


# ── the envelopes ─────────────────────────────────────────────────


def test_all_contract_routes_declare_a_response_model(client) -> None:
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


def test_envelope_defaults_are_the_error_branch_defaults() -> None:
    """A missing ``status`` must not default to ``"ok"``."""
    for model in (
        SubAgentIdEnvelope,
        SubAgentListEnvelope,
        SubAgentTranscriptEnvelope,
        SubAgentWaitEnvelope,
    ):
        assert model.model_fields["status"].default == "error", model.__name__
        assert model.model_fields["error"].default == "", model.__name__


def test_list_fallback_is_labelled_error_not_ok(client, monkeypatch) -> None:
    """The ``/api/subagent/list`` fallback omits ``status`` entirely."""
    from maop.dashboard.services import agent_service

    def _boom(*_a: Any, **_kw: Any) -> dict[str, Any]:
        raise RuntimeError("db down")

    monkeypatch.setattr(agent_service, "subagent_list", _boom)
    body = client.get("/api/subagent/list").json()
    assert body["status"] == "error"
    assert body["error"] == "List failed"
    assert body["agents"] == []
    assert body["count"] == 0


def test_the_id_envelope_is_shared_by_spawn_and_cancel(client) -> None:
    """``spawn`` and ``cancel`` answer with the same shape — the id of the
    subagent they acted on."""
    assert set(SubAgentIdEnvelope.model_fields) == {"status", "agent_id", "error"}
