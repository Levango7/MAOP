"""HTTP contracts for ``/api/feedback/*`` and the row vocabulary the view reads.

Unlike the earlier batches in this series, this page's key names already
matched the producer — the value this file adds is pinning that match so it
cannot drift back, and pinning the two request shapes the modal builds
(create vs. update: the update body must NOT carry ``target_type`` /
``target_id``, which the API does not accept).

The row shape is asserted against the service's own ``row_to_dict`` output
over an isolated SQLite file (``conftest`` points ``MAOP_DATA_DIR`` at a
per-test tmp dir), never against a hand-written mock.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from maop.dashboard.routers import feedback as feedback_router
from maop.dashboard.routers.feedback_schemas import (
    FeedbackCreateEnvelope,
    FeedbackDeleteEnvelope,
    FeedbackListEnvelope,
    FeedbackSummaryEnvelope,
    FeedbackUpdateEnvelope,
)
from maop.dashboard.services import chat_service

CONTRACT_ROUTES: list[tuple[str, str]] = [
    ("POST", "/api/feedback"),
    ("GET", "/api/feedback"),
    ("GET", "/api/feedback/summary"),
    ("PUT", "/api/feedback/{feedback_id}"),
    ("DELETE", "/api/feedback/{feedback_id}"),
]

VIEW_FILE = (
    Path(__file__).resolve().parents[2]
    / "dashboard-enterprise"
    / "src"
    / "views"
    / "Feedback.vue"
)

#: ``api.post('/api/feedback', payload)`` — keys of a flat object literal.
_LITERAL_RE = re.compile(r"const payload = \{([^}]*)\}")


@pytest.fixture(autouse=True)
def _reset_schema():
    feedback_router._reset_schema_for_tests()
    yield
    feedback_router._reset_schema_for_tests()


def _make_app(roles: list[str], user_id: str) -> FastAPI:
    app = FastAPI()

    @app.middleware("http")
    async def _inject_auth(request, call_next):
        request.state.auth_roles = roles
        request.state.auth_identity = user_id
        request.state.tenant_id = ""
        return await call_next(request)

    app.include_router(feedback_router.router)
    return TestClient(app)


@pytest.fixture
def client() -> TestClient:
    return _make_app(roles=["user"], user_id="u1")


def _submit(client: TestClient, **overrides: Any) -> dict[str, Any]:
    payload = {
        "target_type": "agent",
        "target_id": "coder",
        "rating": 4,
        "comment": "solid",
        "tags": ["fast", "accurate"],
    }
    payload.update(overrides)
    resp = client.post("/api/feedback", json=payload)
    assert resp.status_code == 200, resp.text
    return resp.json()


# ── the row speaks the vocabulary the view reads ──────────────────


def test_create_returns_the_id_the_view_uses(client) -> None:
    body = _submit(client)
    assert body["status"] == "ok"
    assert body["feedback_id"]


def test_the_row_carries_every_key_the_view_reads(client) -> None:
    """``feedback_id`` / ``target_type`` / ``target_id`` / ``rating`` /
    ``comment`` / ``tags`` — the six keys the table and the edit modal read."""
    _submit(client)
    row = client.get("/api/feedback").json()["data"][0]
    source = VIEW_FILE.read_text(encoding="utf-8")
    read_keys = set(re.findall(r"\bf\.([A-Za-z_][A-Za-z0-9_]*)", source))
    assert read_keys, "the scan found no row property access — the view moved?"
    assert read_keys <= set(row), f"view reads keys the API never sends: {read_keys - set(row)}"


def test_the_row_keeps_every_producer_column(client) -> None:
    """No column the service selects may be dropped on the way out.

    The expected set comes from the service's own ``row_to_dict``, not from a
    hand-written list — otherwise this test would only restate the envelope.
    """
    _submit(client)
    raw = chat_service.list_feedback(
        user_id="u1",
        is_admin=False,
        target_type="",
        target_id="",
        rating=None,
        date_from=None,
        date_to=None,
        page=1,
        page_size=20,
    )["data"][0]
    row = client.get("/api/feedback").json()["data"][0]
    assert set(raw) == set(row), set(raw) ^ set(row)
    assert row["target_type"] == "agent"
    assert row["target_id"] == "coder"
    assert row["rating"] == 4
    assert row["comment"] == "solid"
    assert row["tags"] == ["fast", "accurate"]


def test_the_list_envelope_matches_the_paging_the_view_computes(client) -> None:
    _submit(client)
    body = client.get("/api/feedback?page=1&page_size=20").json()
    assert body["total"] == 1
    assert body["page"] == 1
    assert body["page_size"] == 20


def test_the_summary_carries_the_keys_the_view_reads(client) -> None:
    _submit(client, rating=5, comment="great", tags=["fast"])
    _submit(client, rating=3, comment="", tags=["slow"])
    body = client.get("/api/feedback/summary").json()
    assert body["status"] == "ok"
    summary = body["summary"]
    source = VIEW_FILE.read_text(encoding="utf-8")
    # ``summaryData.value`` is the ref container, not a payload key.
    read_keys = set(re.findall(
        r"summaryData(?:\.value)?\.([A-Za-z_][A-Za-z0-9_]*)", source,
    )) - {"value"}
    assert read_keys, "the scan found no summary property access — the view moved?"
    assert read_keys <= set(summary), (
        f"view reads keys the API never sends: {read_keys - set(summary)}"
    )
    assert summary["total"] == 2
    assert summary["average_rating"] == 4.0
    assert summary["rating_distribution"] == {"3": 1, "5": 1}
    assert summary["comment_count"] == 1
    assert summary["tag_frequency"] == {"fast": 1, "slow": 1}


def test_the_update_returns_the_row_the_view_reloads(client) -> None:
    feedback_id = _submit(client)["feedback_id"]
    body = client.put(
        f"/api/feedback/{feedback_id}",
        json={"rating": 2, "comment": "changed", "tags": ["slow"]},
    ).json()
    assert body["status"] == "ok"
    assert body["feedback"]["feedback_id"] == feedback_id
    assert body["feedback"]["rating"] == 2
    assert body["feedback"]["comment"] == "changed"
    assert body["feedback"]["tags"] == ["slow"]


def test_the_update_body_cannot_move_the_target(client) -> None:
    """The modal disables target_type/target_id on edit, and the request model
    has no such fields — sending them must be rejected, not silently dropped."""
    feedback_id = _submit(client)["feedback_id"]
    resp = client.put(
        f"/api/feedback/{feedback_id}",
        json={"rating": 2, "target_type": "task", "target_id": "other"},
    )
    assert resp.status_code == 422
    row = client.get("/api/feedback").json()["data"][0]
    assert row["target_type"] == "agent"
    assert row["target_id"] == "coder"


def test_the_delete_confirms_what_it_removed(client) -> None:
    feedback_id = _submit(client)["feedback_id"]
    body = client.delete(f"/api/feedback/{feedback_id}").json()
    assert body["status"] == "ok"
    assert body["deleted"] is True
    assert client.get("/api/feedback").json()["total"] == 0


# ── the request shapes the modal builds ───────────────────────────


def test_the_view_payload_is_the_create_body_minus_the_target() -> None:
    """``payload`` carries rating/comment/tags; the create branch adds exactly
    the two identifying fields, the update branch sends ``payload`` as-is."""
    source = VIEW_FILE.read_text(encoding="utf-8")
    match = _LITERAL_RE.search(source)
    assert match, "the payload literal moved — update this scan"
    keys = {
        part.split(":", 1)[0].strip()
        for part in match.group(1).split(",")
        if ":" in part
    }
    assert keys == {"rating", "comment", "tags"}, keys
    assert "payload.target_type = form.target_type;" in source
    assert "payload.target_id = form.target_id.trim();" in source


def test_the_view_request_bodies_match_the_declared_request_models(client) -> None:
    """OpenAPI request schema vs. what the modal sends.

    A request model that declares fewer fields than the view sends would have
    the extras dropped in silence — the "edit silently re-targets the row"
    defect, in a new place.
    """
    schema = client.app.openapi()
    components = schema["components"]["schemas"]

    def _declared(method: str, template: str) -> dict:
        op = schema["paths"][template][method]
        body = op["requestBody"]["content"]["application/json"]["schema"]
        ref = body.get("$ref")
        return components[ref.rsplit("/", 1)[-1]] if ref else body

    create = _declared("post", "/api/feedback")
    assert set(create["properties"]) == {
        "target_type", "target_id", "rating", "comment", "tags",
    }
    assert create.get("additionalProperties") is False

    update = _declared("put", "/api/feedback/{feedback_id}")
    assert set(update["properties"]) == {"rating", "comment", "tags"}
    assert update.get("additionalProperties") is False


def test_the_view_filters_are_query_params_the_api_declares(client) -> None:
    """Every ``params.set(...)`` the view makes for the list must exist as a
    declared query parameter — a typo would be ignored by FastAPI."""
    source = VIEW_FILE.read_text(encoding="utf-8")
    sent = set(re.findall(r"params\.set\('([A-Za-z_][A-Za-z0-9_]*)'", source))
    # ``format`` belongs to the export endpoint, not the list.
    sent.discard("format")
    assert sent == {"target_type", "rating", "page", "page_size"}, sent

    op = client.app.openapi()["paths"]["/api/feedback"]["get"]
    declared = {
        p["name"] for p in op.get("parameters", []) if p.get("in") == "query"
    }
    assert sent <= declared, sent - declared


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
            problems.append(f"UNTYPED {method} {template}")
    assert problems == []


def test_envelope_defaults_are_the_error_branch_defaults() -> None:
    """A missing ``status`` must not default to ``"ok"``."""
    for model in (
        FeedbackCreateEnvelope,
        FeedbackListEnvelope,
        FeedbackSummaryEnvelope,
        FeedbackUpdateEnvelope,
        FeedbackDeleteEnvelope,
    ):
        assert model.model_fields["status"].default == "error", model.__name__
        assert model.model_fields["error"].default == "", model.__name__


def test_errors_bypass_the_envelope_and_keep_their_status(client) -> None:
    """Bare ``@handle_api_errors`` routes answer with ``error_response()`` —
    a JSONResponse that the response model never validates. The status code
    must still reach the client (the modal renders ``err.message``)."""
    resp = client.put("/api/feedback/does-not-exist", json={"rating": 3})
    assert resp.status_code == 404
    assert resp.json()["status"] == "error"
