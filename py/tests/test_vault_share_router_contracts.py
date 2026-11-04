"""Response-model contracts for the credential-vault and share routers.

Why these specific assertions:

* Both routers pass ``error_value=`` to ``handle_api_errors``. That degraded dict is
  returned as a plain dict, so unlike the HTTPException branch (a JSONResponse, which
  bypasses ``response_model`` entirely) it *does* go through the model. An undeclared
  key there is silently filtered away, and a success-only required key would turn the
  fallback into a 500. So the degraded path is asserted against the live HTTP response.
* Item shapes are compared with their **producers** (``CredentialSummary``,
  ``run_service._summarize``) rather than with a hand copy of the field list -- a model
  that falls behind the producer must fail here instead of quietly dropping the new
  field off the wire.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from maop.core.agent.auth.credential_vault import CredentialSummary, CredentialVault
from maop.dashboard.routers import credential_vault as vault_router
from maop.dashboard.routers import share as share_router
from maop.dashboard.routers.credential_vault_schemas import (
    CredentialCreatedEnvelope,
    CredentialDeletedEnvelope,
    CredentialListEnvelope,
    CredentialRotatedEnvelope,
)
from maop.dashboard.routers.share_schemas import (
    ShareCreatedEnvelope,
    ShareDetailEnvelope,
    ShareSummary,
)
from maop.dashboard.services import credential_service, run_service, share_service
from maop.dashboard.services.run_service import _summarize

# ── Fixtures ──────────────────────────────────────────────────────


@pytest.fixture
def vault(tmp_path: Path) -> CredentialVault:
    return CredentialVault(db_path=tmp_path / "contracts_vault.db")


@pytest.fixture
def vault_client(
    vault: CredentialVault, monkeypatch: pytest.MonkeyPatch
) -> TestClient:
    vault_router._set_vault(vault)
    monkeypatch.setattr(vault_router, "require_admin", lambda request: None)
    app = FastAPI()
    app.include_router(vault_router.router)
    return TestClient(app)


@pytest.fixture
def share_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Real share service on a temp DB; run detail comes from the real summarizer."""
    from maop.core.backends import db_utils

    monkeypatch.setattr(
        db_utils, "get_db_path", lambda *a, **kw: tmp_path / "contracts_shares.db"
    )
    monkeypatch.setattr(
        run_service,
        "get_run_detail",
        lambda trace_id: {
            "summary": _producer_summary(trace_id),
            "events": [{"stdout": "SECRET-STDOUT"}],
        }
        if trace_id == "tr-1"
        else None,
    )
    monkeypatch.setattr(share_router, "require_admin", lambda request: None)
    app = FastAPI()
    app.include_router(share_router.router)
    return TestClient(app)


def _producer_summary(trace_id: str) -> dict[str, Any]:
    """The share summary as ``run_service._summarize`` itself builds it.

    Feeding the producer's real output -- not a hand-written dict -- is what makes the
    "the model must cover every producer key" check independent of the model.
    """
    events = [
        {"kind": "run.started", "payload": {"task": "refactor auth"}, "timestamp": 1.0},
        {"kind": "run.step", "payload": {}, "timestamp": 2.0, "agent": "coder"},
        {
            "kind": "run.finished",
            "payload": {"success": True, "routing_key": "default"},
            "duration_ms": 4200,
            "timestamp": 3.0,
        },
        {
            "kind": "run.verify",
            "payload": {"summary": "all green"},
            "status": "passed",
            "timestamp": 4.0,
        },
        {"kind": "run.cost", "payload": {"total_tokens": 1234}, "timestamp": 5.0},
    ]
    return _summarize(trace_id, events)


def _create_credential(client: TestClient, agent: str = "claude-code") -> str:
    body = client.post(
        "/api/credential-vault/credentials",
        json={
            "credential_type": "api_key",
            "agent_name": agent,
            "api_key": "sk-contract-abcdefgh",
        },
    ).json()
    assert body["status"] == "ok", body
    return str(body["credential_id"])


# ── Model vs producer ─────────────────────────────────────────────


def test_credential_list_reuses_the_domain_model() -> None:
    """列表元素必须是领域模型本身，而不是另抄一份 11 字段的镜像。"""
    annotation = CredentialListEnvelope.model_fields["credentials"].annotation
    args = getattr(annotation, "__args__", None) or []
    assert (args[0] if args else annotation) is CredentialSummary


def test_share_summary_covers_every_producer_key() -> None:
    produced = set(_producer_summary("tr-x"))
    declared = set(ShareSummary.model_fields)
    assert produced == declared, (
        f"producer emits {sorted(produced - declared)} that the model would filter off "
        f"the wire; model declares {sorted(declared - produced)} nothing produces"
    )


# ── Live HTTP: success shape ──────────────────────────────────────


def test_list_credentials_key_set_and_item_fields(vault_client: TestClient) -> None:
    _create_credential(vault_client)
    body = vault_client.get("/api/credential-vault/credentials").json()
    assert set(body) == {"status", "credentials", "count", "error"}
    assert body["status"] == "ok"
    assert body["count"] == 1 == len(body["credentials"])
    item = body["credentials"][0]
    # CredentialVault.vue 读 id / agent_name / credential_type / preview / last_used_at；
    # 任何一个被模型过滤掉，这里就会红。
    assert set(item) == set(CredentialSummary.model_fields)
    for key in ("id", "agent_name", "credential_type", "preview", "last_used_at"):
        assert key in item, key
    assert item["credential_type"] == "api_key"


def test_create_rotate_delete_key_sets(vault_client: TestClient) -> None:
    credential_id = _create_credential(vault_client, agent="agent-a")

    rotated = vault_client.post(
        f"/api/credential-vault/credentials/{credential_id}/rotate"
    ).json()
    assert set(rotated) == {"status", "credential_id", "error"}
    assert rotated["credential_id"] == credential_id, (
        "rotate 原地换秘密；若它开始返回新 id，实现与模型都要一起改"
    )

    deleted = vault_client.delete(
        f"/api/credential-vault/credentials/{credential_id}"
    ).json()
    assert set(deleted) == {"status", "deleted", "error"}
    assert deleted["deleted"] == credential_id


def test_share_create_and_detail_key_sets(share_client: TestClient) -> None:
    created = share_client.post("/api/share", json={"trace_id": "tr-1"}).json()
    assert set(created) == {
        "status",
        "token",
        "trace_id",
        "path",
        "created_at",
        "expires_at",
        "error",
    }
    assert created["status"] == "ok" and created["token"]

    detail = share_client.get(f"/api/share/{created['token']}").json()
    assert set(detail) == {"status", "share", "error"}
    share = detail["share"]
    assert set(share) == {"token", "trace_id", "created_at", "expires_at", "summary"}
    assert set(share["summary"]) == set(ShareSummary.model_fields)
    assert share["summary"]["success"] is True
    assert share["summary"]["cost"] == {"total_tokens": 1234}
    # 分享只给 summary：事件 payload 绝不能出现在响应里（既有安全断言，建模不得放松）。
    assert "SECRET-STDOUT" not in json.dumps(share, default=str)
    assert "events" not in share


# ── Live HTTP: the degraded branch must still pass the model ──────


@pytest.mark.parametrize(
    ("service_attr", "call", "envelope"),
    [
        (
            "list_credentials",
            lambda c: c.get("/api/credential-vault/credentials"),
            CredentialListEnvelope,
        ),
        (
            "create_credential",
            lambda c: c.post(
                "/api/credential-vault/credentials",
                json={"credential_type": "api_key", "agent_name": "a", "api_key": "k"},
            ),
            CredentialCreatedEnvelope,
        ),
        (
            "rotate_credential",
            lambda c: c.post("/api/credential-vault/credentials/c-1/rotate"),
            CredentialRotatedEnvelope,
        ),
        (
            "delete_credential",
            lambda c: c.delete("/api/credential-vault/credentials/c-1"),
            CredentialDeletedEnvelope,
        ),
    ],
    ids=["list", "create", "rotate", "delete"],
)
def test_degraded_fallback_survives_the_model(
    vault_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    service_attr: str,
    call,
    envelope,
) -> None:
    def _boom(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError(f"forced failure for {service_attr}")

    monkeypatch.setattr(credential_service, service_attr, _boom)
    resp = call(vault_client)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "error", body
    # 声明 error 的意义就在这：降级原因不能被 response_model 过滤掉。
    assert body.get("error"), f"{service_attr}: error_value 的 error 键被模型丢了"
    assert set(body) <= set(envelope.model_fields), sorted(
        set(body) - set(envelope.model_fields)
    )


def test_share_degraded_fallback_keeps_error_and_defaults(
    share_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("forced share failure")

    monkeypatch.setattr(share_service, "create_share", _boom)
    body = share_client.post("/api/share", json={"trace_id": "tr-1"}).json()
    assert body["status"] == "error" and body["error"], body
    # 成功侧的键必须有默认值，否则降级分支自己会变成 500。
    assert body["token"] == "" and body["created_at"] == 0.0


# ── OpenAPI agrees with the model ─────────────────────────────────


@pytest.mark.parametrize(
    ("router", "path", "method", "envelope"),
    [
        (vault_router.router, "/api/credential-vault/credentials", "get", CredentialListEnvelope),
        (vault_router.router, "/api/credential-vault/credentials", "post", CredentialCreatedEnvelope),
        (share_router.router, "/api/share", "post", ShareCreatedEnvelope),
        (share_router.router, "/api/share/{token}", "get", ShareDetailEnvelope),
    ],
    ids=["vault-list", "vault-create", "share-create", "share-detail"],
)
def test_openapi_property_set_matches_the_model(router, path, method, envelope) -> None:
    app = FastAPI()
    app.include_router(router)
    document = app.openapi()
    schema = document["paths"][path][method]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]
    name = schema["$ref"].rsplit("/", 1)[-1]
    props = document["components"]["schemas"][name]["properties"]
    assert set(props) == set(envelope.model_fields), (
        f"{method.upper()} {path}: OpenAPI 与模型字段漂移"
    )
