"""HTTP contracts for the 15 ``/api/evolution`` endpoints the dashboard calls.

Why this file exists: the nine ``evolution_experiment`` endpoints had **no
HTTP-level test at all** (``test_evolution_loop_ac07.py`` calls the handler
functions directly, which skips FastAPI's response serialisation), and the
``evolve_insights`` ones were only asserted with ``MagicMock`` rows that
auto-create any attribute. Both leave a ``response_model`` able to pass
vacuously. These tests go through the ASGI app and compare the response body
to the payload the service produced, so a dropped or renamed field fails.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from maop.core.evolution.ab_test import SPRTDecision, SPRTState, SPSRTExperimentResult, VariantStats
from maop.core.evolution.auto_deployer import DeploymentRecord, PromoteResult
from maop.core.evolution.evolution_loop_types import LoopReport
from maop.core.evolution.evolution_perf_loop import EvolutionCycleReport
from maop.core.evolution.skill_version import SkillMeta
from maop.dashboard.routers import evolution_experiment, evolve_insights
from maop.dashboard.services import evolution_service

# (method, request URL, OpenAPI template)
CONTRACT_PATHS: list[tuple[str, str, str]] = [
    ("GET", "/api/evolution/cycles", "/api/evolution/cycles"),
    ("GET", "/api/evolution/pending", "/api/evolution/pending"),
    ("POST", "/api/evolution/approve", "/api/evolution/approve"),
    ("GET", "/api/evolution/skills", "/api/evolution/skills"),
    ("POST", "/api/evolution/skills/composite", "/api/evolution/skills/composite"),
    ("GET", "/api/evolution/narrative/c1", "/api/evolution/narrative/{cycle_id}"),
    ("GET", "/api/evolution/ab/list", "/api/evolution/ab/list"),
    ("GET", "/api/evolution/ab/evaluate/evo-a", "/api/evolution/ab/evaluate/{experiment}"),
    ("GET", "/api/evolution/deploy/history", "/api/evolution/deploy/history"),
    ("GET", "/api/evolution/loop/status", "/api/evolution/loop/status"),
    ("POST", "/api/evolution/loop/trigger", "/api/evolution/loop/trigger"),
    ("GET", "/api/evolution/approvals", "/api/evolution/approvals"),
    ("POST", "/api/evolution/approvals/c1:s1/decision", "/api/evolution/approvals/{approval_id}/decision"),
    ("GET", "/api/evolution/ab/c1", "/api/evolution/ab/{cycle_id}"),
    ("POST", "/api/evolution/loop/rollback", "/api/evolution/loop/rollback"),
]


@pytest.fixture
def client(monkeypatch):
    """App with both evolution routers and admin injected."""
    monkeypatch.setattr(evolution_experiment, "require_admin", lambda request: None)
    monkeypatch.setattr(evolve_insights, "require_admin", lambda request: None)

    app = FastAPI()
    app.include_router(evolution_experiment.router)
    app.include_router(evolve_insights.router)
    return TestClient(app)


def _patch(monkeypatch, name: str, value: Any) -> None:
    monkeypatch.setattr(evolution_service, name, value)


def _assert_keeps_every_payload_field(body: dict[str, Any], payload: dict[str, Any]) -> None:
    """The response may add defaulted envelope keys (the error branch needs
    them), but it must not drop, rename or retype anything the service sent."""
    for key, value in payload.items():
        assert key in body, f"dropped by response_model: {key}"
        assert body[key] == value, f"altered by response_model: {key}"


def _cycle() -> dict[str, Any]:
    return EvolutionCycleReport(
        cycle_id="c1",
        experiment="evo-x",
        suggestions_count=2,
        winner="treatment",
        sprt_decision="accept_h1",
        promoted=True,
        detail="2 suggestions applied",
    ).model_dump()


# ── cycles / pending / approve ────────────────────────────────────


def test_cycles_body_is_returned_verbatim(client, monkeypatch):
    payload = {"status": "ok", "cycles": [_cycle()]}
    _patch(monkeypatch, "get_cycle_history", lambda **kw: payload)
    body = client.get("/api/evolution/cycles").json()
    assert body == payload


def test_cycles_fallback_error_value_still_validates(client, monkeypatch):
    # handle_api_errors returns error_value as a plain dict, so the model has
    # to accept it — otherwise a service crash turns into a 500 response error.
    def _boom(**_kw):
        raise RuntimeError("loop down")

    monkeypatch.setattr(evolution_service, "get_cycle_history", _boom)
    resp = client.get("/api/evolution/cycles")
    assert resp.status_code == 200
    assert resp.json() == {"status": "error", "cycles": []}


def test_pending_body_is_returned_verbatim(client, monkeypatch):
    payload = {"status": "ok", "pending": [_cycle()]}
    _patch(monkeypatch, "get_pending_approvals", lambda: payload)
    assert client.get("/api/evolution/pending").json() == payload


def test_approve_wraps_promote_result(client, monkeypatch):
    result = PromoteResult(success=True, winner="treatment", snapshot_id="snap-1", deployment_id="dep-1")
    payload = {"status": "ok", "result": result.model_dump()}
    _patch(monkeypatch, "approve_and_promote", lambda *a, **kw: payload)
    body = client.post("/api/evolution/approve", json={"experiment": "evo-x"}).json()
    assert body == payload


# ── skills ────────────────────────────────────────────────────────


def test_skills_has_no_status_key(client, monkeypatch):
    skill = SkillMeta(name="review", description="d", tags=["code"], pitfalls=["missed test"])
    payload = {"skills": [skill.model_dump()]}
    _patch(monkeypatch, "list_skills", lambda: payload)
    body = client.get("/api/evolution/skills").json()
    assert body == payload
    assert "status" not in body, "list_skills() never carried status — do not invent one"


def test_skill_composite_returns_saved_name_and_version(client, monkeypatch):
    payload = {"status": "ok", "saved": True, "name": "review", "version": "1.0.0"}
    _patch(monkeypatch, "save_composite_skill", lambda **kw: payload)
    body = client.post("/api/evolution/skills/composite", json={"name": "review"}).json()
    assert body == payload


# ── narrative (two disjoint shapes) ──────────────────────────────


def test_narrative_markdown_shape(client, monkeypatch):
    payload = {"status": "ok", "cycle_id": "c1", "format": "markdown", "markdown": "# Cycle c1"}
    _patch(monkeypatch, "get_evolution_narrative", lambda *a, **kw: payload)
    body = client.get("/api/evolution/narrative/c1").json()
    _assert_keeps_every_payload_field(body, payload)
    assert body["narrative"] is None, "markdown branch must not fabricate a json narrative"


def test_narrative_json_shape(client, monkeypatch):
    payload = {
        "status": "ok",
        "cycle_id": "c1",
        "format": "json",
        "narrative": {"summary": {"cycle_id": "c1"}, "phases": [], "suggestions": [{"a": 1}]},
    }
    _patch(monkeypatch, "get_evolution_narrative", lambda *a, **kw: payload)
    body = client.get("/api/evolution/narrative/c1?format=json").json()
    _assert_keeps_every_payload_field(body, payload)
    assert body["markdown"] == ""


def test_narrative_missing_cycle_is_404_jsonresponse(client, monkeypatch):
    def _missing(*_a, **_kw):
        raise KeyError("c1")

    monkeypatch.setattr(evolution_service, "get_evolution_narrative", _missing)
    assert client.get("/api/evolution/narrative/c1").status_code == 404


# ── AB + deploy ───────────────────────────────────────────────────


def test_ab_list_returns_names(client, monkeypatch):
    payload = {"status": "ok", "experiments": ["evo-a", "evo-b"]}
    _patch(monkeypatch, "list_ab_experiments", lambda: payload)
    assert client.get("/api/evolution/ab/list").json() == payload


def test_ab_evaluate_serializes_sprt_decision(client, monkeypatch):
    result = SPSRTExperimentResult(
        experiment="evo-a",
        control=VariantStats(name="control", samples=40, successes=20, success_rate=0.5),
        treatment=VariantStats(name="treatment", samples=45, successes=36, success_rate=0.8),
        sprt=SPRTState(samples=85, successes=56, llr=2.4, decision=SPRTDecision.ACCEPT_H1, winner="treatment", stopped_at=61),
        decision=SPRTDecision.ACCEPT_H1,
        winner="treatment",
        is_significant=True,
    )
    payload = {"status": "ok", "result": result.model_dump()}
    _patch(monkeypatch, "evaluate_ab_experiment", lambda *a, **kw: payload)
    body = client.get("/api/evolution/ab/evaluate/evo-a").json()
    assert body["result"]["experiment"] == "evo-a"
    assert body["result"]["control"]["success_rate"] == 0.5
    # Enum fields leave the wire as their string value, never as a repr.
    assert body["result"]["decision"] == "accept_h1"
    assert body["result"]["sprt"]["decision"] == "accept_h1"


def test_deploy_history_returns_records(client, monkeypatch):
    record = DeploymentRecord(experiment="evo-a", action="promote", winner="treatment", success=True, created_at=1.5)
    payload = {"status": "ok", "history": [record.model_dump()]}
    _patch(monkeypatch, "get_deploy_history", lambda **kw: payload)
    assert client.get("/api/evolution/deploy/history").json() == payload


# ── loop status / trigger / rollback ─────────────────────────────


def _loop_report() -> dict[str, Any]:
    return LoopReport(
        cycle_id="c1",
        started_at=1.0,
        finished_at=2.0,
        total_duration_s=1.0,
        errors_observed=3,
        suggestions_generated=2,
        suggestions_applied=1,
        validation_improved=True,
        pending_approval=["s2"],
        approval_state="pending",
        dry_run=False,
    ).model_dump()


def test_loop_status_keeps_nested_report_fields(client, monkeypatch):
    stats = {
        "total_cycles": 7,
        "improved_cycles": 4,
        "improvement_rate": 0.571,
        "avg_duration_s": 2.5,
        "total_suggestions_applied": 9,
        "total_heal_successes": 3,
    }
    report = _loop_report()
    payload = {
        "status": "ok",
        "state": "pending_approval",
        "evolution_loop_enabled": True,
        "recent_cycles": [report],
        "stats": stats,
        "pending_approval_count": 1,
        "pending_approval_ids": ["s2"],
    }
    _patch(monkeypatch, "get_evolution_loop_status", lambda: payload)
    body = client.get("/api/evolution/loop/status").json()
    _assert_keeps_every_payload_field(body, payload)
    # A dropped LoopReport field would show up right here.
    assert body["recent_cycles"][0]["pending_approval"] == ["s2"]
    assert body["recent_cycles"][0]["approval_state"] == "pending"


def test_loop_trigger_returns_full_report(client, monkeypatch):
    payload = {"status": "ok", "report": _loop_report()}

    async def _trigger(_dry_run: bool) -> dict[str, Any]:
        return payload

    monkeypatch.setattr(evolution_service, "trigger_evolution_loop", _trigger)
    body = client.post("/api/evolution/loop/trigger", json={"dry_run": True}).json()
    _assert_keeps_every_payload_field(body, payload)
    assert body["report"]["cycle_id"] == "c1"


def test_loop_rollback_counts_restored_files(client, monkeypatch):
    payload = {"status": "ok", "restored_files": 2, "cycle_id": "c1"}
    _patch(monkeypatch, "rollback_evolution_loop", lambda *a, **kw: payload)
    body = client.post("/api/evolution/loop/rollback", json={"cycle_id": "c1"}).json()
    assert body == payload
    assert body["restored_files"] == 2


def test_loop_rollback_empty_cycle_id_is_400(client, monkeypatch):
    def _raise(cycle_id: str, _snapshot_id: str) -> dict[str, Any]:
        raise ValueError("cycle_id required") if not cycle_id else {}

    monkeypatch.setattr(evolution_service, "rollback_evolution_loop", _raise)
    assert client.post("/api/evolution/loop/rollback", json={"cycle_id": ""}).status_code == 400


# ── approvals ─────────────────────────────────────────────────────


def test_approvals_returns_hand_built_items(client, monkeypatch):
    payload = {
        "status": "ok",
        "approvals": [{
            "cycle_id": "c1",
            "started_at": 1234567890.0,
            "pending_approval_ids": ["s1", "s2"],
            "approval_state": "pending",
            "errors_observed": 5,
            "suggestions_generated": 3,
            "validation_improved": False,
        }],
        "total": 1,
    }
    _patch(monkeypatch, "list_evolution_approvals", lambda: payload)
    assert client.get("/api/evolution/approvals").json() == payload


def test_approval_decision_echoes_state(client, monkeypatch):
    payload = {
        "status": "ok",
        "decision": "approve",
        "approval_id": "c1:s1",
        "cycle_id": "c1",
        "approval_state": "partial",
    }
    _patch(monkeypatch, "decide_evolution_approval", lambda *a, **kw: payload)
    body = client.post(
        "/api/evolution/approvals/c1:s1/decision",
        json={"decision": "approve", "approved_by": "ops", "reason": "looks right"},
    ).json()
    assert body == payload


def test_approval_decision_bad_format_is_400(client, monkeypatch):
    def _raise(*_a, **_kw):
        raise ValueError("Invalid approval_id format (cycle_id:suggestion_id)")

    monkeypatch.setattr(evolution_service, "decide_evolution_approval", _raise)
    resp = client.post("/api/evolution/approvals/nocolon/decision", json={"decision": "approve"})
    assert resp.status_code == 400
    assert resp.json()["status"] == "error"


def test_ab_results_allows_null_ab_result(client, monkeypatch):
    _patch(monkeypatch, "get_evolution_ab_results", lambda *_a: {"status": "ok", "cycle_id": "c1", "ab_result": None})
    body = client.get("/api/evolution/ab/c1").json()
    assert body["ab_result"] is None


def test_ab_results_returns_variant_summaries(client, monkeypatch):
    payload = {
        "status": "ok",
        "cycle_id": "c1",
        "ab_result": {
            "experiment": "evo-c1",
            "p_value": 0.01,
            "significant": True,
            "control": {"success_rate": 0.5, "samples": 40},
            "treatment": {"success_rate": 0.8, "samples": 45},
        },
    }
    _patch(monkeypatch, "get_evolution_ab_results", lambda *_a: payload)
    assert client.get("/api/evolution/ab/c1").json() == payload


# ── OpenAPI ───────────────────────────────────────────────────────


def test_all_contract_routes_declare_a_response_model(client):
    schema = client.app.openapi()
    problems: list[str] = []
    for method, _url, template in CONTRACT_PATHS:
        op = (schema["paths"].get(template) or {}).get(method.lower())
        if op is None:
            problems.append(f"MISSING ROUTE {method} {template}")
            continue
        resp = (op.get("responses") or {}).get("200", {})
        body = (resp.get("content") or {}).get("application/json", {}).get("schema") or {}
        if "$ref" not in body and "properties" not in body:
            problems.append(f"UNtyped {method} {template}")
    assert problems == []


def test_contract_routes_cover_both_routers() -> None:
    """Guard the batch list itself: every declared contract path exists."""
    routes = {getattr(r, "path", "") for r in [*evolution_experiment.router.routes, *evolve_insights.router.routes]}
    for _method, _url, template in CONTRACT_PATHS:
        assert template in routes, template


def test_contract_list_covers_the_frontend_evolution_calls() -> None:
    """15 endpoints — the batch size the ratchet allowlist carried."""
    assert len(CONTRACT_PATHS) == 15
