"""Response-model contracts for the agent-versions and long-running-task routers.

The load-bearing check here is against the **producer**, not against a copy of the
field list:

* version / canary rows are built by ``_version_row_to_dict`` / ``_canary_row_to_dict``
  over ``_AGENT_VERSION_COLUMNS`` / ``_CANARY_COLUMNS`` (explicit column lists added
  after a ``SELECT *`` contract break). If someone adds a column and forgets the model,
  FastAPI would silently filter that field off the wire -- these tests go red instead.
* task items reuse the domain model ``LongRunningTask``; the check is that a live
  response still carries all of its fields.

One endpoint is deliberately NOT modelled: ``GET /api/long-running-tasks/{task_id}/progress``
returns ``StreamingResponse`` (SSE). A route that returns a ``Response`` instance makes
FastAPI skip ``response_model`` entirely, so declaring a model there would be a guard
that can never fire -- and it would satisfy the ratchet while telling nobody. The last
two tests pin both halves of that: no model declared, and the exemption entry exists.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from maop.core.agent.router.long_running_task import LongRunningTask
from maop.dashboard.routers import agent_versions as av_router
from maop.dashboard.routers import long_running_task as lrt_router
from maop.dashboard.routers.agent_versions_helpers import (
    _AGENT_VERSION_COLUMNS,
    _CANARY_COLUMNS,
)
from maop.dashboard.routers.agent_versions_schemas import (
    CanaryConfigEnvelope,
    CanaryItem,
    PageMeta,
    RollbackMeta,
    VersionActivateEnvelope,
    VersionItem,
    VersionListEnvelope,
    VersionRollbackEnvelope,
)
from maop.dashboard.routers.long_running_task_schemas import (
    LongRunningTaskCancelEnvelope,
    LongRunningTaskListEnvelope,
)


def _columns(columns: str | tuple[str, ...]) -> set[str]:
    """列清单写成多段相邻字符串字面量，Python 会自动拼成一个 str。"""
    text = columns if isinstance(columns, str) else "".join(columns)
    return {name.strip() for name in text.split(",") if name.strip()}


# ── Harness ───────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_schema():
    av_router._reset_schema_for_tests()
    yield
    av_router._reset_schema_for_tests()


def _av_client(roles: list[str] | None = None) -> TestClient:
    app = FastAPI()

    @app.middleware("http")
    async def _inject_auth(request, call_next):
        request.state.auth_roles = roles or ["admin"]
        request.state.auth_identity = "test-user"
        request.state.tenant_id = ""
        return await call_next(request)

    app.include_router(av_router.router)
    return TestClient(app)


def _make_version(client: TestClient, agent_id: str = "ag-1", number: str = "1.0.0") -> str:
    resp = client.post(
        "/api/agent-versions",
        json={
            "agent_id": agent_id,
            "version_number": number,
            "config_snapshot": {"model": "gpt-4o"},
            "changelog": "contract batch",
        },
    ).json()
    assert resp["status"] == "ok", resp
    return str(resp["data"]["version_id"])


# ── Model vs producer ─────────────────────────────────────────────


def test_version_item_matches_the_select_column_list() -> None:
    """``_AGENT_VERSION_COLUMNS`` 是这份响应形状的源头。"""
    assert set(VersionItem.model_fields) == _columns(_AGENT_VERSION_COLUMNS), (
        "版本模型的字段与查询列清单漂移：新增列会被 response_model 静默过滤"
    )


def test_canary_item_matches_the_select_column_list() -> None:
    assert set(CanaryItem.model_fields) == _columns(_CANARY_COLUMNS), (
        "灰度模型的字段与查询列清单漂移"
    )


def test_task_items_are_the_domain_model_not_a_mirror() -> None:
    annotation = LongRunningTaskListEnvelope.model_fields["tasks"].annotation
    args = getattr(annotation, "__args__", None) or []
    assert (args[0] if args else annotation) is LongRunningTask


# ── Live HTTP: agent versions ─────────────────────────────────────


def test_list_versions_envelope_and_pagination_keys() -> None:
    client = _av_client()
    _make_version(client)
    body = client.get("/api/agent-versions").json()
    assert set(body) == set(VersionListEnvelope.model_fields) == {"status", "data", "meta"}
    assert set(body["meta"]) == set(PageMeta.model_fields)
    assert body["meta"]["total"] == 1
    assert set(body["data"][0]) == set(VersionItem.model_fields)


def test_activate_returns_the_full_version_row() -> None:
    client = _av_client()
    version_id = _make_version(client)
    body = client.post(f"/api/agent-versions/{version_id}/activate").json()
    assert set(body) == set(VersionActivateEnvelope.model_fields) == {"status", "data"}
    assert body["data"]["status"] == "active"
    assert body["data"]["activated_at"] is not None, "激活时间戳是 active 状态的证据，不能被过滤"


def test_rollback_envelope_carries_its_own_meta() -> None:
    client = _av_client()
    first = _make_version(client, number="1.0.0")
    second = _make_version(client, number="2.0.0")
    client.post(f"/api/agent-versions/{first}/activate")
    body = client.post(
        f"/api/agent-versions/{second}/rollback",
        json={"target_version_id": first, "reason": "contract check"},
    ).json()
    assert set(body) == set(VersionRollbackEnvelope.model_fields) == {"status", "data", "meta"}
    assert set(body["meta"]) == set(RollbackMeta.model_fields)
    assert body["meta"]["reason"] == "contract check"
    assert body["meta"]["rolled_back_from"] == second
    assert body["meta"]["rolled_back_to"] == first


def test_canary_config_envelope_carries_canary_fields() -> None:
    client = _av_client()
    version_id = _make_version(client)
    body = client.post(
        f"/api/agent-versions/{version_id}/canary",
        json={"percentage": 25, "strategy": "percentage"},
    ).json()
    assert set(body) == set(CanaryConfigEnvelope.model_fields) == {"status", "data"}
    assert set(body["data"]) == set(CanaryItem.model_fields)
    assert body["data"]["percentage"] == 25
    assert body["data"]["target_user_groups"] == [], "JSON 列要解析成 list 而不是原始字符串"


def test_null_timestamps_stay_null_not_zero() -> None:
    """activated_at/retired_at 可空：模型把它们做成 ``float | None``，草稿行必须回 None。"""
    client = _av_client()
    version_id = _make_version(client)
    body = client.get("/api/agent-versions").json()
    draft = next(v for v in body["data"] if v["version_id"] == version_id)
    assert draft["activated_at"] is None and draft["retired_at"] is None


# ── Live HTTP: long running tasks ─────────────────────────────────


def _task(task_id: str = "lt-1") -> dict[str, Any]:
    return LongRunningTask(
        task_id=task_id,
        agent_name="openhands",
        task="migrate db",
        status="running",
        progress=0.4,
        progress_message="applying migration",
        submitted_at="2026-10-30T00:00:00",
    ).model_dump()


def _lrt_client(monkeypatch: pytest.MonkeyPatch, tasks: list[dict[str, Any]]) -> TestClient:
    from maop.dashboard.services import session_service

    monkeypatch.setattr(session_service, "list_active_long_running_tasks", lambda: tasks)
    monkeypatch.setattr(session_service, "cancel_long_running_task", lambda task_id: True)
    app = FastAPI()
    app.include_router(lrt_router.router)
    return TestClient(app)


def test_list_long_running_tasks_preserves_every_task_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _lrt_client(monkeypatch, [_task()])
    body = client.get("/api/long-running-tasks").json()
    assert set(body) == set(LongRunningTaskListEnvelope.model_fields) == {"status", "count", "tasks"}
    assert body["count"] == 1
    assert set(body["tasks"][0]) == set(LongRunningTask.model_fields), (
        "领域模型加了字段而响应模型接不住，字段会在网络上消失"
    )


def test_cancel_returns_status_and_task_id(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _lrt_client(monkeypatch, [])
    body = client.post("/api/long-running-tasks/lt-1/cancel").json()
    assert set(body) == set(LongRunningTaskCancelEnvelope.model_fields) == {"status", "task_id"}
    assert body["task_id"] == "lt-1"


# ── SSE 端点：不许挂假守卫 ────────────────────────────────────────


def test_progress_route_has_no_effective_response_model() -> None:
    """SSE 路由不许挂模型：挂了也不生效，却会让棘轮以为这条已经被建模。

    FastAPI 会把返回注解推成 ``response_model``，这里看到的是默认的 ``Any``；
    真正要钉的是"没有一个 Pydantic 模型在描述它"，以及 OpenAPI 里这条 200
    响应确实没有 JSON schema —— 后者才是棘轮据以判断"未建模"的观测量。
    """
    from pydantic import BaseModel

    routes = [
        route
        for route in lrt_router.router.routes
        if getattr(route, "path", "").endswith("/{task_id}/progress")
    ]
    assert len(routes) == 1, [r.path for r in routes]
    declared = routes[0].response_model
    assert not (isinstance(declared, type) and issubclass(declared, BaseModel)), (
        f"SSE 端点挂了 {declared} —— response_model 对返回 Response 的路由从不执行，"
        "这条守卫永远不会触发；要记账就写进 non-JSON 豁免清单"
    )

    app = FastAPI()
    app.include_router(lrt_router.router)
    path = next(
        key
        for key in app.openapi()["paths"]
        if key.endswith("/long-running-tasks/{task_id}/progress")
    )
    responses = app.openapi()["paths"][path]["get"]["responses"]
    schema = (responses.get("200", {}).get("content") or {}).get("application/json", {}).get("schema") or {}
    assert not schema.get("properties") and "$ref" not in schema, (
        "OpenAPI 出现了 JSON 形状，说明这条已不再是 SSE —— 豁免账要跟着撤"
    )


def test_progress_route_is_registered_as_non_json() -> None:
    from tests.test_frontend_response_models import NON_JSON, load_entries

    assert ("GET", "/api/long-running-tasks/{}/progress") in load_entries(NON_JSON), (
        "SSE 端点必须显式记在 non-JSON 豁免账里，而不是靠门禁看不见它"
    )
