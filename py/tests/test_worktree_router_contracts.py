"""HTTP contracts for ``/api/worktree/*`` plus the worktree manager's own promises.

Two directions, because either one alone has already been wrong in this repo:

* the envelope must not drop a key the producer emits — asserted against the
  real ``BranchInfo`` / ``MergeResult`` models and against live responses;
* the producer must not lie — ``abandon()`` returned ``True`` for a node that
  did not exist, and ``merge()`` looked branches up by id while the dashboard
  sends names, so a merge driven from the UI could never find its branch.

Both producer claims are asserted against the real ``WorktreeManager`` over a
temporary SQLite file rather than a mock: a mock would only re-state the
assumption under test. (The pre-existing coverage test mocked ``abandon`` to
return ``False`` for a missing node, which the real manager never did — so its
404 assertion passed against a behaviour the product did not have.)
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from maop.dashboard.routers import worktree as worktree_router
from maop.dashboard.routers.worktree_schemas import (
    AbandonEnvelope,
    BranchEnvelope,
    CheckpointEnvelope,
    CreateRootEnvelope,
    GetEnvelope,
    ListEnvelope,
    MergeEnvelope,
    RollbackEnvelope,
)

CONTRACT_ROUTES: list[tuple[str, str]] = [
    ("POST", "/api/worktree/create-root"),
    ("POST", "/api/worktree/branch"),
    ("POST", "/api/worktree/abandon"),
    ("POST", "/api/worktree/merge"),
    ("POST", "/api/worktree/checkpoint"),
    ("POST", "/api/worktree/rollback"),
    ("GET", "/api/worktree/get"),
    ("GET", "/api/worktree/list"),
]


@pytest.fixture
def mgr(tmp_path):
    from maop.core.agent.memory_ctx.worktree import WorktreeManager

    return WorktreeManager(root_dir=str(tmp_path))


@pytest.fixture
def client(mgr, monkeypatch):
    monkeypatch.setattr(worktree_router, "_get_worktree_mgr", lambda: mgr)
    app = FastAPI()

    @app.middleware("http")
    async def _inject_admin(request, call_next):
        request.state.auth_roles = ["admin"]
        request.state.auth_identity = "admin"
        return await call_next(request)

    app.include_router(worktree_router.router)
    return TestClient(app)


def _seed(mgr) -> tuple[str, str]:
    """One root plus one branch named ``feature/login``."""
    root = mgr.create_root(task="root task", description="root desc")
    branch = mgr.branch(parent_id=root, name="feature/login", description="branch desc")
    return root, branch


def _assert_keeps_every_payload_field(body: dict[str, Any], payload: dict[str, Any]) -> None:
    """The envelopes declare an ``error`` slot for the fallback branch, so the
    response may be a superset — but it must never drop, rename or retype a
    field the service produced."""
    for key, value in payload.items():
        assert key in body, f"dropped by response_model: {key}"
        assert body[key] == value, f"altered by response_model: {key}"


# ── envelopes vs the real producers ──────────────────────────────


def test_branch_envelope_matches_the_real_branch_info() -> None:
    from typing import get_args

    from maop.core.agent.memory_ctx.worktree import BranchInfo

    get_model = get_args(GetEnvelope.model_fields["branch"].annotation)[0]
    list_model = get_args(ListEnvelope.model_fields["branches"].annotation)[0]
    assert get_model is BranchInfo
    assert list_model is BranchInfo
    assert set(BranchInfo.model_fields) == {
        "id", "parent_id", "root_id", "name", "description", "status",
        "result", "metadata", "created_at", "updated_at",
    }


def test_merge_envelope_matches_the_real_merge_result() -> None:
    from typing import get_args

    from maop.core.agent.memory_ctx.worktree import MergeResult

    assert get_args(MergeEnvelope.model_fields["merge"].annotation)[0] is MergeResult
    assert set(MergeResult.model_fields) == {
        "success", "source_branch", "target_branch", "strategy", "conflicts", "merged_result",
    }


def test_list_envelope_keeps_every_branch_field(client, mgr) -> None:
    _seed(mgr)
    body = client.get("/api/worktree/list").json()
    assert body["status"] == "ok"
    assert body["count"] == len(body["branches"]) == 2
    row = body["branches"][0]
    for key in ("id", "parent_id", "root_id", "name", "description", "status",
                "result", "metadata", "created_at", "updated_at"):
        assert key in row, f"dropped by response_model: {key}"
    assert row["status"] == "active"


def test_get_envelope_keeps_the_branch_payload(client, mgr) -> None:
    _root, branch = _seed(mgr)
    body = client.get(f"/api/worktree/get?node_id={branch}").json()
    assert body["status"] == "ok"
    assert body["branch"]["id"] == branch
    assert body["branch"]["name"] == "feature/login"


def test_create_branch_checkpoint_and_rollback_envelopes(client, mgr) -> None:
    body = client.post("/api/worktree/create-root", json={"task": "t"}).json()
    assert body["status"] == "ok" and body["node_id"]
    _assert_keeps_every_payload_field(body, {"status": "ok", "node_id": body["node_id"]})
    root_id = body["node_id"]

    body = client.post(
        "/api/worktree/branch",
        json={"parent_id": root_id, "name": "f1"},
    ).json()
    assert body["status"] == "ok" and body["node_id"]
    branch_id = body["node_id"]

    body = client.post(
        "/api/worktree/checkpoint", json={"node_id": branch_id, "label": "v1"},
    ).json()
    assert body["status"] == "ok" and body["checkpoint_id"]
    checkpoint_id = body["checkpoint_id"]

    body = client.post(
        "/api/worktree/rollback",
        json={"node_id": branch_id, "checkpoint_id": checkpoint_id},
    ).json()
    assert body["status"] == "ok"


# ── the producer must not lie ────────────────────────────────────


def test_abandon_of_a_missing_node_is_404(client, mgr) -> None:
    """``abandon()`` used to return True for a node that was never there, so
    this 404 was unreachable in production."""
    resp = client.post("/api/worktree/abandon", json={"id": "does-not-exist"})
    assert resp.status_code == 404
    assert resp.json()["status"] == "error"


def test_abandon_of_a_real_node_succeeds_and_is_persisted(client, mgr) -> None:
    _root, branch = _seed(mgr)
    body = client.post("/api/worktree/abandon", json={"id": branch}).json()
    assert body["status"] == "ok"
    assert body["id"] == branch
    assert mgr.get_branch(branch).status == "abandoned"


def test_merge_accepts_a_branch_name_as_the_dashboard_sends_it(client, mgr) -> None:
    """The dashboard sends the branch *name*; the manager used to look ids up."""
    _root, branch = _seed(mgr)
    resp = client.post("/api/worktree/merge", json={"source_branch": "feature/login"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["merge"]["success"] is True
    assert body["merge"]["source_branch"] == branch
    assert mgr.get_branch(branch).status == "merged"


def test_merge_accepts_a_branch_id_too(client, mgr) -> None:
    _root, branch = _seed(mgr)
    resp = client.post("/api/worktree/merge", json={"source_branch": branch})
    assert resp.status_code == 200
    assert resp.json()["merge"]["source_branch"] == branch


def test_merge_of_a_missing_branch_is_404_not_ok(client, mgr) -> None:
    """A merge that touched no row used to answer 200 ``{"status": "ok"}``."""
    _seed(mgr)
    resp = client.post("/api/worktree/merge", json={"source_branch": "no-such-branch"})
    assert resp.status_code == 404
    assert resp.json()["status"] == "error"


def test_merge_reports_conflicts_instead_of_hiding_them(client, mgr) -> None:
    root = mgr.create_root(task="root")
    src = mgr.branch(parent_id=root, name="src")
    tgt = mgr.branch(parent_id=root, name="tgt")
    mgr.update_result(src, result="result A")
    mgr.update_result(tgt, result="result B")

    body = client.post(
        "/api/worktree/merge", json={"source_branch": "src", "target_branch": "tgt"},
    ).json()
    assert body["status"] == "ok"
    assert body["merge"]["success"] is True
    assert body["merge"]["conflicts"], "a conflicting merge must not look clean"
    assert body["merge"]["strategy"] == "manual"


# ── fallbacks must not be labelled success ───────────────────────


def test_error_fallback_is_labelled_error_not_ok(client, monkeypatch) -> None:
    """The ``/api/worktree/list`` fallback omits ``status`` entirely."""
    from maop.dashboard.services import execution_service

    def _boom(*_a: Any, **_kw: Any) -> list[Any]:
        raise RuntimeError("db down")

    monkeypatch.setattr(execution_service, "worktree_list", _boom)
    body = client.get("/api/worktree/list").json()
    assert body["status"] == "error"
    assert body["error"] == "List failed"
    assert body["branches"] == []
    assert body["count"] == 0


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
        CreateRootEnvelope, BranchEnvelope, AbandonEnvelope, GetEnvelope,
        ListEnvelope, MergeEnvelope, CheckpointEnvelope, RollbackEnvelope,
    ):
        assert model.model_fields["status"].default == "error", model.__name__
        assert model.model_fields["error"].default == "", model.__name__
