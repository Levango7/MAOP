"""HTTP contracts for ``/api/quota/*`` and the billing view's row vocabulary.

The defects this file exists for — every one of them proven by running the
real producers, not by reading the mocks:

1. ``GET /api/quota/{agent}`` answered with the flat ``QuotaEntry`` columns
   (``free_quota`` / ``free_used`` / ...) while the view reads the nested
   ``quota.free.total`` / ``.used`` / ``.remaining``. Every quota card
   rendered "unlimited / 0" no matter what the database held.
2. ``PUT /api/quota/{agent}`` took a ``QuotaEntry`` body while the view sends
   ``{bucket, total}``. Pydantic v2 drops unknown fields silently, so both
   keys were discarded and the agent's quota was written as all-zero — the
   UI showed a success toast while the quota had been wiped.
3. ``POST /api/quota/{agent}/consume`` ignored the ``bucket`` field: the
   bucket dropdown was a no-op.
4. The consume failure (``success=false`` with HTTP 200) was reported to the
   user as "consume succeeded".
5. ``GET /api/billing/summary/{agent}`` sends ``total_cost_usd`` while the
   view read ``total_cost`` — the cost column was permanently $0.0000, and
   the bucket bars read a ``s.buckets`` key the producer never sends.
6. ``GET /api/billing/records`` sends ``tokens_consumed`` / ``calls_made`` /
   ``cost_usd`` / ``quota_source`` while the view read ``tokens`` / ``calls``
   / ``cost`` / ``source`` — the records table body rendered "—" for every
   numeric cell.

The shapes are asserted against real ``QuotaBucket`` / ``BillingEngine``
instances over temporary SQLite files, never against a hand-written mock: a
mock only restates the assumption under test. The pre-existing frontend test
mocked a quota shape the backend never produced, so the quota cards stayed
broken while the suite stayed green.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from maop.core.agent.billing.billing_abstraction import BillingEngine
from maop.core.agent.billing.quota_bucket import QuotaBucket
from maop.core.agent.registry.agent_catalog import (
    AgentCatalog,
    AgentDescriptor,
    BillingModel,
)
from maop.dashboard.routers import billing_abstraction as billing_route
from maop.dashboard.routers import quota as quota_route
from maop.dashboard.routers.billing_schemas import (
    BillingRecordsEnvelope,
    BillingSummaryEnvelope,
)
from maop.dashboard.routers.quota_schemas import (
    QuotaConsumeEnvelope,
    QuotaGetEnvelope,
    QuotaRefundEnvelope,
    QuotaRemainingEnvelope,
    QuotaResetEnvelope,
    QuotaSetEnvelope,
)
from maop.dashboard.services import billing_service

#: Every route this batch models.
CONTRACT_ROUTES: list[tuple[str, str]] = [
    ("GET", "/api/quota/{agent_name}"),
    ("PUT", "/api/quota/{agent_name}"),
    ("POST", "/api/quota/{agent_name}/consume"),
    ("POST", "/api/quota/{agent_name}/refund"),
    ("POST", "/api/quota/{agent_name}/reset"),
    ("GET", "/api/quota/{agent_name}/remaining"),
    ("GET", "/api/billing/summary/{agent_name}"),
    ("GET", "/api/billing/records"),
]

#: The three buckets the quota view renders.
BUCKET_NAMES = ("free", "prepaid", "postpaid")

#: The keys one bucket view carries.
BUCKET_KEYS = {"total", "used", "remaining"}

VIEW_FILE = (
    Path(__file__).resolve().parents[2]
    / "dashboard-enterprise"
    / "src"
    / "views"
    / "AgentBilling.vue"
)

#: ``api.put(`/api/quota/${id}`, { ... })`` — the body keys the view sends.
_CALL_BODY_RE = re.compile(
    r"api\.(put|post)\(\s*`([^`]*)`\s*,\s*\{([^}]*)\}\s*\)", re.DOTALL,
)


def _body_keys(body: str) -> set[str]:
    """The keys of a flat object literal — ``{bucket: x, amount}``."""
    keys: set[str] = set()
    for part in body.split(","):
        part = part.strip()
        if not part:
            continue
        if ":" in part:
            keys.add(part.split(":", 1)[0].strip())
        elif re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", part):
            keys.add(part)
    return keys


def _view_request_bodies(source: str) -> dict[tuple[str, str], set[str]]:
    """Map ``(method, url-template)`` → the body keys the view sends.

    The URL's ``${...}`` interpolation is normalized to ``{}`` so the view
    and the OpenAPI path template can be compared.
    """
    bodies: dict[tuple[str, str], set[str]] = {}
    for method, url, body in _CALL_BODY_RE.findall(source):
        template = re.sub(r"\$\{[^}]+\}", "{}", url)
        bodies[(method, template)] = _body_keys(body)
    return bodies


def _resolve(schema: dict, components: dict) -> dict:
    """Follow a ``$ref`` into the OpenAPI components."""
    ref = schema.get("$ref")
    if not ref:
        return schema
    return components[ref.rsplit("/", 1)[-1]]


def _records_block(source: str) -> str:
    """The records-table region of the template.

    The ``r.`` scan must not see the ``r.value`` / ``r.status`` of the
    summary loader — those are a different object with the same variable
    name.
    """
    start_marker = "<!-- Records table -->"
    end_marker = "<!-- Pagination -->"
    assert start_marker in source and end_marker in source, "the view moved?"
    return source[source.index(start_marker):source.index(end_marker)]


# ── Fixtures ─────────────────────────────────────────────────────


@pytest.fixture
def bucket(tmp_path: Path) -> QuotaBucket:
    """Quota bucket on an isolated database."""
    return QuotaBucket(db_path=tmp_path / "quota.db")


@pytest.fixture
def engine(tmp_path: Path, bucket: QuotaBucket) -> BillingEngine:
    """Billing engine wired to the isolated bucket and its own catalog."""
    catalog = AgentCatalog(db_path=tmp_path / "catalog.db")
    engine = BillingEngine(
        catalog=catalog, quota_bucket=bucket, db_path=tmp_path / "billing.db",
    )
    engine._catalog.register(
        AgentDescriptor(
            name="coder",
            billing_model=BillingModel.PER_TOKEN,
            billing_config={"price_per_token": 0.002},
        )
    )
    # A PER_TOKEN agent with no quota fails every charge ("quota consume
    # failed") and therefore records nothing — the fixtures below need a
    # funded bucket.
    bucket.set_bucket_quota("coder", "free", 1_000_000)
    return engine


@pytest.fixture
def client(
    bucket: QuotaBucket, engine: BillingEngine, monkeypatch: pytest.MonkeyPatch,
) -> TestClient:
    """Dashboard client with the isolated bucket / engine installed."""
    monkeypatch.setattr(billing_service, "_quota_bucket", bucket)
    monkeypatch.setattr(billing_service, "_billing_engine", engine)
    app = FastAPI()

    @app.middleware("http")
    async def _inject_admin(request, call_next):
        request.state.auth_roles = ["admin"]
        request.state.auth_identity = "admin"
        return await call_next(request)

    app.include_router(quota_route.router)
    app.include_router(billing_route.router)
    return TestClient(app)


# ── the quota endpoint speaks the nested view vocabulary ───────────


def test_quota_get_returns_the_nested_bucket_view(client, bucket) -> None:
    """The view reads ``quota.free.total`` — so the endpoint must nest it."""
    bucket.set_bucket_quota("coder", "free", 1000)
    bucket.set_bucket_quota("coder", "prepaid", 500)
    bucket.consume("coder", 300, "free")

    body = client.get("/api/quota/coder").json()
    assert body["status"] == "ok"
    assert body["agent_name"] == "coder"
    assert body["quota"]["free"] == {"total": 1000, "used": 300, "remaining": 700}
    assert body["quota"]["prepaid"] == {"total": 500, "used": 0, "remaining": 500}
    assert body["quota"]["postpaid"] == {"total": 0, "used": 0, "remaining": 0}


def test_quota_get_does_not_emit_the_flat_entry_columns(client, bucket) -> None:
    """The old shape answered with ``free_quota`` / ``free_used``."""
    body = client.get("/api/quota/coder").json()
    flat = {"free_quota", "free_used", "prepaid_quota", "prepaid_used"}
    assert flat.isdisjoint(body["quota"]), body["quota"]


def test_every_bucket_carries_the_three_keys_the_view_reads(client, bucket) -> None:
    """``total`` / ``used`` / ``remaining`` — no more, no less."""
    body = client.get("/api/quota/coder").json()
    for name in BUCKET_NAMES:
        assert set(body["quota"][name]) == BUCKET_KEYS, body["quota"][name]


def test_the_view_reads_no_bucket_key_the_producer_does_not_emit(client, bucket) -> None:
    """Static guard against the live response, both directions.

    The view reads ``b.total`` / ``b.used`` / ``b.remaining``; if the
    endpoint ever stops sending one of them the cards go blank again — and
    if the view invents a fourth key it silently reads ``undefined``.
    """
    body = client.get("/api/quota/coder").json()
    source = VIEW_FILE.read_text(encoding="utf-8")
    read_keys = set(re.findall(r"\bb\.([A-Za-z_][A-Za-z0-9_]*)", source))
    assert read_keys, "the scan found no bucket property access — the view moved?"
    assert read_keys <= BUCKET_KEYS, f"view reads keys no bucket carries: {read_keys - BUCKET_KEYS}"
    for name in BUCKET_NAMES:
        assert set(body["quota"][name]) == read_keys, name


def test_the_view_does_not_read_the_dead_flat_vocabulary() -> None:
    """``free_quota`` / ``free_used`` were the pre-batch producer's shape."""
    source = VIEW_FILE.read_text(encoding="utf-8")
    for dead in ("free_quota", "free_used", "prepaid_quota", "prepaid_used"):
        assert dead not in source, f"the view still reads {dead!r}"


def test_the_remaining_endpoint_agrees_with_the_bucket_view(client, bucket) -> None:
    """Two endpoints report the same remaining — they must not disagree."""
    bucket.set_bucket_quota("coder", "free", 1000)
    bucket.consume("coder", 250, "free")
    quota = client.get("/api/quota/coder").json()["quota"]
    remaining = client.get("/api/quota/coder/remaining").json()
    assert remaining["remaining"]["free"] == quota["free"]["remaining"] == 750
    assert remaining["total"] == sum(quota[b]["remaining"] for b in BUCKET_NAMES)


# ── the request bodies the view sends are the bodies the API accepts ─


def test_the_legacy_set_body_is_rejected_not_silently_applied(client, bucket) -> None:
    """Pydantic v2 drops unknown fields — the old body would have wiped the quota."""
    bucket.set_bucket_quota("coder", "free", 1000)
    resp = client.put("/api/quota/coder", json={"free_quota": 100})
    assert resp.status_code == 422
    assert bucket.get_quota("coder").free_quota == 1000


def test_the_set_body_sets_the_named_bucket_only(client, bucket) -> None:
    bucket.set_bucket_quota("coder", "free", 1000)
    resp = client.put("/api/quota/coder", json={"bucket": "prepaid", "total": 500})
    assert resp.status_code == 200
    assert resp.json()["quota"]["prepaid"]["total"] == 500
    assert resp.json()["quota"]["free"]["total"] == 1000


def test_the_consume_body_honours_the_bucket_it_names(client, bucket) -> None:
    """The dropdown must actually pick the bucket."""
    bucket.set_bucket_quota("coder", "free", 1000)
    bucket.set_bucket_quota("coder", "prepaid", 500)
    resp = client.post(
        "/api/quota/coder/consume", json={"bucket": "prepaid", "amount": 200},
    )
    assert resp.status_code == 200
    result = resp.json()["result"]
    assert result["success"] is True
    assert result["from_prepaid"] == 200
    assert result["from_free"] == 0
    quota = client.get("/api/quota/coder").json()["quota"]
    assert quota["free"]["used"] == 0
    assert quota["prepaid"]["used"] == 200


def test_the_consume_failure_is_reported_as_failure(client, bucket) -> None:
    """``success=false`` with HTTP 200 — the view must not call it a success."""
    bucket.set_bucket_quota("coder", "free", 10)
    resp = client.post("/api/quota/coder/consume", json={"bucket": "auto", "amount": 50})
    assert resp.status_code == 200
    result = resp.json()["result"]
    assert result["success"] is False
    assert result["reason"]


def test_the_view_sends_the_bucket_it_selected(client) -> None:
    """Static guard: the request bodies carry the fields the API declares."""
    source = VIEW_FILE.read_text(encoding="utf-8")
    assert "bucket: quotaForm.bucket" in source
    bodies = _view_request_bodies(source)
    assert bodies[("put", "/api/quota/{}")] == {"bucket", "total"}
    assert bodies[("post", "/api/quota/{}/consume")] == {"bucket", "amount"}
    assert bodies[("post", "/api/quota/{}/refund")] == {"bucket", "amount"}
    assert bodies[("post", "/api/quota/{}/reset")] == {"bucket"}


def test_the_view_request_bodies_match_the_declared_request_models(client) -> None:
    """OpenAPI request schema vs. the keys the view sends.

    A request model that declares fewer fields than the view sends would
    have the extras dropped in silence — the "set quota wiped the agent"
    defect, in a new place.
    """
    schema = client.app.openapi()
    components = schema["components"]["schemas"]
    source = VIEW_FILE.read_text(encoding="utf-8")
    bodies = _view_request_bodies(source)
    routes = {
        ("put", "/api/quota/{}"): "/api/quota/{agent_name}",
        ("post", "/api/quota/{}/consume"): "/api/quota/{agent_name}/consume",
        ("post", "/api/quota/{}/refund"): "/api/quota/{agent_name}/refund",
        ("post", "/api/quota/{}/reset"): "/api/quota/{agent_name}/reset",
    }
    for (method, view_url), template in routes.items():
        op = schema["paths"][template][method]
        declared = _resolve(
            op["requestBody"]["content"]["application/json"]["schema"], components,
        )
        assert set(declared["properties"]) == bodies[(method, view_url)], (
            method, template, declared["properties"], bodies[(method, view_url)]
        )
        assert declared.get("additionalProperties") is False, (
            f"{method} {template} accepts unknown fields — they would be dropped silently"
        )


# ── the billing summary ───────────────────────────────────────────


def test_the_summary_carries_the_producer_cost_key(client, engine) -> None:
    """``total_cost_usd`` — the view read ``total_cost`` and showed $0."""
    engine.charge("coder", tokens=1000, calls=2, session_id="s1", model="m1")
    body = client.get("/api/billing/summary/coder").json()
    assert body["status"] == "ok"
    summary = body["summary"]
    assert summary["total_cost_usd"] == pytest.approx(2.0)
    assert summary["total_tokens"] == 1000
    # The per-agent summary counts *charges* (one row each); ``calls_made``
    # (2) is the agent-call count inside the single charge.
    assert summary["total_calls"] == 1


def test_the_summary_carries_the_bucket_view(client, engine, bucket) -> None:
    """The summary tab renders the same three buckets as the quota tab."""
    bucket.set_bucket_quota("coder", "free", 1000)
    engine.charge("coder", tokens=1000, calls=1, session_id="s1", model="m1")
    summary = client.get("/api/billing/summary/coder").json()["summary"]
    assert summary["quota"]["free"]["total"] == 1000
    assert summary["quota"]["free"]["used"] == 1000
    assert summary["quota"]["free"]["remaining"] == 0


def test_the_view_reads_the_producer_cost_key() -> None:
    """Static guard: ``s.total_cost_usd``, not the ``s.total_cost`` that is
    the *records* aggregate key — two different numbers, two different names."""
    source = VIEW_FILE.read_text(encoding="utf-8")
    read_keys = set(re.findall(r"\bs\.([A-Za-z_][A-Za-z0-9_]*)", source))
    assert read_keys, "the scan found no summary property access — the view moved?"
    assert "total_cost_usd" in read_keys, read_keys
    assert "total_cost" not in read_keys, read_keys


def test_the_view_reads_the_bucket_keys_the_summary_sends(client) -> None:
    """``s.quota.free.total`` — the summary must nest its buckets too."""
    body = client.get("/api/billing/summary/coder").json()
    source = VIEW_FILE.read_text(encoding="utf-8")
    assert "s.quota" in source
    for name in BUCKET_NAMES:
        assert set(body["summary"]["quota"][name]) == BUCKET_KEYS, name


# ── the billing records ───────────────────────────────────────────


def test_the_records_carry_the_keys_the_view_reads(client, engine) -> None:
    """``tokens_consumed`` / ``calls_made`` / ``cost_usd`` / ``quota_source``.

    The view read ``tokens`` / ``calls`` / ``cost`` / ``source`` — four keys
    the producer has never sent, so the whole table body rendered "—".
    """
    engine.charge("coder", tokens=1000, calls=2, session_id="s1", model="m1")
    body = client.get("/api/billing/records").json()
    assert body["status"] == "ok"
    assert body["count"] == 1
    row = body["records"][0]
    assert row["agent_name"] == "coder"
    assert row["billing_model"] == "per_token"
    assert row["tokens_consumed"] == 1000
    assert row["calls_made"] == 2
    assert row["cost_usd"] == pytest.approx(2.0)
    assert row["quota_source"].startswith("free:")
    assert row["model"] == "m1"
    assert row["timestamp"]


def test_the_view_reads_no_record_key_the_producer_does_not_emit(client, engine) -> None:
    """Static guard, both directions, on the record rows."""
    engine.charge("coder", tokens=1000, calls=2, session_id="s1", model="m1")
    body = client.get("/api/billing/records").json()
    row = body["records"][0]
    source = VIEW_FILE.read_text(encoding="utf-8")
    read_keys = set(re.findall(r"\br\.([A-Za-z_][A-Za-z0-9_]*)", _records_block(source)))
    assert read_keys, "the scan found no record property access — the view moved?"
    assert read_keys <= set(row), f"view reads keys the API never sends: {read_keys - set(row)}"


def test_the_view_reads_the_record_keys_not_the_old_ones() -> None:
    """The dead vocabulary must be gone from the table."""
    source = VIEW_FILE.read_text(encoding="utf-8")
    for dead in ("r.tokens)", "r.calls)", "r.cost)", "r.source)"):
        assert dead not in source, f"the view still reads {dead!r}"


def test_the_records_summary_uses_its_own_aggregate_keys(client, engine) -> None:
    """The records summary is a *total*, not a per-agent cost — different keys."""
    engine.charge("coder", tokens=1000, calls=2, session_id="s1", model="m1")
    summary = client.get("/api/billing/records").json()["summary"]
    assert summary["total_tokens"] == 1000
    assert summary["total_cost"] == pytest.approx(2.0)
    assert summary["total_calls"] == 2


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
        QuotaGetEnvelope,
        QuotaSetEnvelope,
        QuotaConsumeEnvelope,
        QuotaRefundEnvelope,
        QuotaResetEnvelope,
        QuotaRemainingEnvelope,
        BillingSummaryEnvelope,
        BillingRecordsEnvelope,
    ):
        assert model.model_fields["status"].default == "error", model.__name__
        assert model.model_fields["error"].default == "", model.__name__


def test_the_fallback_branch_answers_the_error_shape(client, monkeypatch) -> None:
    """The ``handle_api_errors`` fallback is validated by the response model
    too — it must survive as ``status="error"``, not be silently dropped."""

    def _boom(*_a: Any, **_kw: Any) -> dict[str, Any]:
        raise RuntimeError("db down")

    monkeypatch.setattr(billing_service, "get_agent_quota", _boom)
    body = client.get("/api/quota/coder").json()
    assert body["status"] == "error"
    assert body["error"] == "Get failed"
    assert body["quota"]["free"] == {"total": 0, "used": 0, "remaining": 0}
