"""HTTP contracts for ``/api/agent-catalog/*`` and what AgentRegistry.vue reads.

Three defects this file pins (each reproduced against the real producer
before the fix, see CHANGELOG):

1. ``GET /stats`` returned only ``count`` / ``healthy_count`` while the view's
   four cards read ``total`` / ``enabled`` / ``healthy`` / ``capabilities`` —
   every card rendered 0 forever.
2. The row carries a boolean ``healthy``; the badges read ``a.health ||
   a.health_status``, so every agent showed "unknown".
3. ``PUT /agents/{name}`` accepted a body key ``name`` and dropped it in
   silence (the view sent it on every edit).

Rows are asserted against ``AgentDescriptor`` — the object the service
actually dumps — never against a hand-written list.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from maop.core.agent.registry.agent_catalog import AgentDescriptor
from maop.dashboard.routers import agent_catalog as route_mod
from maop.dashboard.routers.agent_catalog_schemas import (
    AgentDeleteEnvelope,
    AgentEnvelope,
    AgentListEnvelope,
    CatalogStatsPayload,
)
from maop.dashboard.services import agent_service

CONTRACT_ROUTES: list[tuple[str, str]] = [
    ("GET", "/api/agent-catalog/agents"),
    ("POST", "/api/agent-catalog/agents"),
    ("PUT", "/api/agent-catalog/agents/{name}"),
    ("DELETE", "/api/agent-catalog/agents/{name}"),
    ("GET", "/api/agent-catalog/stats"),
]

VIEW_FILE = (
    Path(__file__).resolve().parents[2]
    / "dashboard-enterprise"
    / "src"
    / "views"
    / "AgentRegistry.vue"
)

#: Field names of a register/update body, i.e. the payload the modal builds.
_PAYLOAD_RE = re.compile(r"const payload = \{(.*?)\n    \};", re.DOTALL)


@pytest.fixture(autouse=True)
def _reset_catalog_singleton():
    agent_service._set_catalog(None)
    yield
    agent_service._set_catalog(None)


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    """Real ``AgentCatalog`` over an isolated SQLite file + real admin guard."""
    from maop.core.agent.registry.agent_catalog import AgentCatalog

    agent_service._set_catalog(
        AgentCatalog(db_path=tmp_path / "contract_agent_catalog.db"),
    )

    app = FastAPI()

    @app.middleware("http")
    async def _inject_admin(request, call_next):
        request.state.auth_roles = ["admin"]
        request.state.auth_identity = "root"
        request.state.tenant_id = ""
        return await call_next(request)

    app.include_router(route_mod.router)
    return TestClient(app)


def _register(client: TestClient, name: str, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": name,
        "display_name": name.upper(),
        "vendor": "acme",
        "adapter_type": "cli",
        "capabilities": ["chat"],
        "billing_model": "per_token",
        "auth_method": "none",
        "max_concurrent": 2,
        "rate_limit_per_min": 0,
        "timeout_s": 30,
        "retry_count": 1,
        "fallback_agents": [],
        "enabled": True,
    }
    body.update(overrides)
    resp = client.post("/api/agent-catalog/agents", json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _seed_three(client: TestClient) -> None:
    """alpha: enabled+healthy(chat) / beta: disabled(code_generation+chat) /
    gamma: enabled but flipped unhealthy(tool_use) via the health endpoint."""
    _register(client, "alpha", capabilities=["chat"])
    _register(client, "beta", capabilities=["code_generation", "chat"], enabled=False)
    _register(client, "gamma", capabilities=["tool_use"])
    resp = client.put("/api/agent-catalog/agents/gamma/health", json={"healthy": False})
    assert resp.status_code == 200, resp.text


# ── the row speaks the vocabulary the view reads ──────────────────


def _strip_strings(source: str) -> str:
    """Drop string literals so i18n keys (``stats.total``) stop matching."""
    return re.sub(r"'[^']*'", "''", source)


def test_the_row_carries_every_key_the_view_reads(client) -> None:
    _seed_three(client)
    row = client.get("/api/agent-catalog/agents").json()["agents"][0]
    source = _strip_strings(VIEW_FILE.read_text(encoding="utf-8"))
    read_keys = set(re.findall(r"\ba\.([A-Za-z_][A-Za-z0-9_]*)", source))
    assert read_keys, "the scan found no row property access — the view moved?"
    assert read_keys <= set(row), f"view reads keys the API never sends: {read_keys - set(row)}"


def test_the_row_keeps_every_producer_field(client) -> None:
    """No field of the dumped ``AgentDescriptor`` may be lost on the way out.

    The expected set comes from the domain model itself — a hand-written list
    would only restate the envelope and hide a future field addition.
    """
    _register(client, "alpha")
    row = client.get("/api/agent-catalog/agents").json()["agents"][0]
    expected = set(AgentDescriptor(name="alpha").model_dump(mode="json"))
    assert set(row) == expected, set(row) ^ expected


def test_health_is_a_boolean_the_badge_can_read(client) -> None:
    """Defect 2: the badges read ``a.health``/``a.health_status``, which the
    producer never sent — every agent rendered "unknown"."""
    _register(client, "alpha")
    row = client.get("/api/agent-catalog/agents").json()["agents"][0]
    assert row["healthy"] is True
    assert "health" not in row
    assert "health_status" not in row

    source = VIEW_FILE.read_text(encoding="utf-8")
    assert "function isHealthy(a) {" in source
    assert "a.healthy !== false" in source
    assert not re.search(r"a\.health\b(?!_check)", _strip_strings(source)), (
        "the view still reads a string health key the API never sends"
    )
    # "unknown" was the third state the dead key produced; the API only ever
    # sends true/false, so the vocabulary must not offer it.
    assert "status.unknown" not in source


def test_the_single_agent_envelope_carries_the_same_row(client) -> None:
    _register(client, "alpha")
    listed = client.get("/api/agent-catalog/agents").json()["agents"][0]
    body = client.post(
        "/api/agent-catalog/agents",
        json={"name": "alpha", "display_name": "renamed"},
    ).json()
    assert body["status"] == "ok"
    assert body["agent"]["name"] == "alpha"
    assert body["agent"]["display_name"] == "renamed"
    assert set(body["agent"]) == set(listed)


# ── the four stat cards ───────────────────────────────────────────


def test_stats_returns_exactly_the_four_counts_the_cards_read(client) -> None:
    """Defect 1: only ``count``/``healthy_count`` existed, so all four cards
    (total/enabled/healthy/capabilities) rendered 0."""
    _seed_three(client)
    body = client.get("/api/agent-catalog/stats").json()
    assert body == {
        "status": "ok",
        "count": 3,
        "healthy_count": 2,
        "enabled_count": 2,
        "capability_count": 3,  # chat, code_generation, tool_use — deduped
        "error": "",
    }


def test_stats_capability_count_is_deduped_across_agents(client) -> None:
    _register(client, "alpha", capabilities=["chat"])
    _register(client, "beta", capabilities=["chat", "tool_use"])
    body = client.get("/api/agent-catalog/stats").json()
    assert body["count"] == 2
    assert body["capability_count"] == 2


def test_stats_agrees_with_the_list_it_summarizes(client) -> None:
    _seed_three(client)
    rows = client.get("/api/agent-catalog/agents").json()["agents"]
    body = client.get("/api/agent-catalog/stats").json()
    assert body["count"] == len(rows)
    assert body["enabled_count"] == sum(1 for r in rows if r["enabled"])
    assert body["healthy_count"] == sum(1 for r in rows if r["healthy"])
    assert body["capability_count"] == len({c for r in rows for c in r["capabilities"]})


def test_the_view_stat_cards_read_the_stats_payload_keys() -> None:
    source = _strip_strings(VIEW_FILE.read_text(encoding="utf-8"))
    read = set(re.findall(r"\bstats\.([A-Za-z_][A-Za-z0-9_]*)", source)) - {"value"}
    assert read == {"count", "enabled_count", "healthy_count", "capability_count"}, read
    assert set(CatalogStatsPayload.model_fields) >= read


# ── the request shapes the modal builds ───────────────────────────


def test_the_register_body_is_name_plus_the_payload(client) -> None:
    """``payload`` carries the 10 editable fields; the create branch spreads
    ``name`` in front of it, the update branch sends ``payload`` alone
    (defect 3: it used to send ``name`` too, which the API dropped)."""
    source = VIEW_FILE.read_text(encoding="utf-8")
    match = _PAYLOAD_RE.search(source)
    assert match, "the payload literal moved — update this scan"
    keys = {
        line.split(":", 1)[0].strip()
        for line in match.group(1).splitlines()
        if ":" in line
    }
    assert keys == {
        "display_name", "vendor", "adapter_type", "capabilities",
        "billing_model", "auth_method", "max_concurrent", "timeout_s",
        "retry_count", "fallback_agents",
    }, keys
    assert "name" not in keys
    assert "await api.post('/api/agent-catalog/agents', { name, ...payload });" in source


def test_update_rejects_a_name_in_the_body(client) -> None:
    """Defect 3, pinned: ``name`` is the path identity. Accepting it in the
    body means a caller can send a second identity that is silently ignored
    (200 OK, nothing renamed) — or, worse, honoured."""
    _register(client, "alpha")
    resp = client.put(
        "/api/agent-catalog/agents/alpha",
        json={"name": "hacked", "display_name": "X"},
    )
    assert resp.status_code == 422, resp.text
    row = client.get("/api/agent-catalog/agents/alpha").json()["agent"]
    assert row["name"] == "alpha"
    assert row["display_name"] == "ALPHA", "the rejected body must not half-apply"


def test_register_rejects_an_unknown_field(client) -> None:
    """A typo'd capability key used to vanish: the agent came up with the
    defaults instead of failing loudly."""
    resp = client.post(
        "/api/agent-catalog/agents",
        json={"name": "alpha", "capabilitie": ["chat"]},
    )
    assert resp.status_code == 422, resp.text
    assert client.get("/api/agent-catalog/agents").json()["count"] == 0


def test_the_partial_update_keeps_the_fields_it_was_not_given(client) -> None:
    _register(client, "alpha", vendor="acme", timeout_s=30, capabilities=["chat"])
    body = client.put(
        "/api/agent-catalog/agents/alpha", json={"display_name": "renamed"},
    ).json()
    assert body["status"] == "ok"
    row = body["agent"]
    assert row["display_name"] == "renamed"
    assert row["vendor"] == "acme"
    assert row["timeout_s"] == 30
    assert row["capabilities"] == ["chat"]
    assert row["enabled"] is True


def test_the_delete_envelope_names_what_it_removed(client) -> None:
    _register(client, "alpha")
    body = client.delete("/api/agent-catalog/agents/alpha").json()
    assert body == {"status": "ok", "deleted": "alpha", "error": ""}
    assert client.get("/api/agent-catalog/agents").json()["count"] == 0


def test_the_enabled_toggle_round_trips_through_the_listed_row(client) -> None:
    """The power button PUTs ``{enabled}`` and then patches the local row; the
    next load must agree with what the toggle claimed."""
    _register(client, "alpha", enabled=True)
    body = client.put(
        "/api/agent-catalog/agents/alpha", json={"enabled": False},
    ).json()
    assert body["agent"]["enabled"] is False
    row = client.get("/api/agent-catalog/agents").json()["agents"][0]
    assert row["enabled"] is False
    assert client.get("/api/agent-catalog/stats").json()["enabled_count"] == 0


# ── declared schemas ──────────────────────────────────────────────


def test_the_request_models_declare_everything_the_view_sends(client) -> None:
    """OpenAPI request schema vs. what the modal builds.

    The models may declare more than the modal exposes (``version`` /
    ``enabled`` / ``rate_limit_per_min`` are API-only knobs), but every key the
    view sends must be declared — an undeclared one is dropped in silence,
    which is exactly defect 3.
    """
    schema = client.app.openapi()
    components = schema["components"]["schemas"]

    def _declared(method: str, template: str) -> dict:
        op = schema["paths"][template][method]
        body = op["requestBody"]["content"]["application/json"]["schema"]
        ref = body.get("$ref")
        return components[ref.rsplit("/", 1)[-1]] if ref else body

    source = VIEW_FILE.read_text(encoding="utf-8")
    payload_keys = {
        line.split(":", 1)[0].strip()
        for line in _PAYLOAD_RE.search(source).group(1).splitlines()
        if ":" in line
    }

    create = _declared("post", "/api/agent-catalog/agents")
    assert set(create["properties"]) == {
        "name", "display_name", "vendor", "version", "adapter_type",
        "capabilities", "billing_model", "auth_method", "max_concurrent",
        "rate_limit_per_min", "timeout_s", "retry_count", "fallback_agents",
        "enabled",
    }
    assert payload_keys | {"name"} <= set(create["properties"]), (
        payload_keys - set(create["properties"])
    )
    assert create.get("additionalProperties") is False

    update = _declared("put", "/api/agent-catalog/agents/{name}")
    assert set(update["properties"]) == set(create["properties"]) - {"name"}
    assert payload_keys <= set(update["properties"])
    assert "name" not in update["properties"]
    assert update.get("additionalProperties") is False


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
    for model in (AgentListEnvelope, AgentEnvelope, AgentDeleteEnvelope, CatalogStatsPayload):
        assert model.model_fields["status"].default == "error", model.__name__
        assert model.model_fields["error"].default == "", model.__name__
    assert AgentEnvelope.model_fields["agent"].default is None


def test_the_error_fallback_passes_the_response_model(client, monkeypatch) -> None:
    """``error_value`` dicts ARE validated by ``response_model`` — an
    undeclared key there would turn a caught crash into a 500."""
    def _boom(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("catalog exploded")

    monkeypatch.setattr(agent_service, "list_agents", _boom)
    resp = client.get("/api/agent-catalog/agents")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "status": "error", "agents": [], "count": 0, "error": "List failed",
    }

    monkeypatch.setattr(agent_service, "catalog_stats", _boom)
    resp = client.get("/api/agent-catalog/stats")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "status": "error",
        "count": 0,
        "healthy_count": 0,
        "enabled_count": 0,
        "capability_count": 0,
        "error": "Stats failed",
    }


def test_http_errors_bypass_the_envelope_and_keep_their_status(client) -> None:
    """A ``HTTPException`` renders via ``error_response()`` — the response
    model never sees it, so the status code must still reach the client."""
    resp = client.put("/api/agent-catalog/agents/ghost", json={"display_name": "x"})
    assert resp.status_code == 404
    assert resp.json()["status"] == "error"

    resp = client.delete("/api/agent-catalog/agents/ghost")
    assert resp.status_code == 404
    assert resp.json()["status"] == "error"


def test_an_unknown_capability_is_a_400_not_a_silent_default(client) -> None:
    resp = client.post(
        "/api/agent-catalog/agents",
        json={"name": "alpha", "capabilities": ["teleport"]},
    )
    assert resp.status_code == 400, resp.text
    assert client.get("/api/agent-catalog/agents").json()["count"] == 0


def test_non_admin_callers_are_refused(tmp_path: Path) -> None:
    """The admin guard is real (not stubbed out): a read-only role gets 403,
    and the refusal body keeps its status through ``error_response()``."""
    from maop.core.agent.registry.agent_catalog import AgentCatalog

    agent_service._set_catalog(AgentCatalog(db_path=tmp_path / "guard.db"))
    app = FastAPI()

    @app.middleware("http")
    async def _inject_reader(request, call_next):
        request.state.auth_roles = ["read"]
        request.state.auth_identity = "guest"
        request.state.tenant_id = ""
        return await call_next(request)

    app.include_router(route_mod.router)
    client = TestClient(app)
    for call in (
        lambda: client.get("/api/agent-catalog/agents"),
        lambda: client.get("/api/agent-catalog/stats"),
        lambda: client.post("/api/agent-catalog/agents", json={"name": "a"}),
        lambda: client.put("/api/agent-catalog/agents/a", json={"enabled": False}),
        lambda: client.delete("/api/agent-catalog/agents/a"),
    ):
        resp = call()
        assert resp.status_code == 403, resp.text
        assert resp.json()["status"] == "error"
