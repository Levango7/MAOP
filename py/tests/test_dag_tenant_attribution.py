"""DAG 执行路径的**租户归属**契约。

这块要钉的是 ``docs/configuration.md``「多租户（企业版）现状与边界」里那条曾经
成立的事实：dashboard 的 DAG 执行路径上**根本没有租户身份**，所以任何想加的
派发门都做出来是空开关。本文件把"身份现在确实流到了编排链"变成可执行的事实，
并钉住三件容易悄悄退化的事：

1. 租户身份**只能**来自认证态（``request.state``）。写进请求体的租户声明必须被
   忽略 —— 那是 G-07 修过的越权口子（调用方能随便宣称自己属于哪个租户）。
2. 作用域要真的把下游（步骤执行器 → ``Dispatcher.dispatch``）覆盖到，而不只是
   在函数签名上多一个参数。
3. 生命周期门**不许对准空集合**。注册表为空 / 企业包没装 / 本来就没有租户身份，
   三种"无从检查"都必须放行；只有"有登记表且这个租户被停用或查无"才拒。

第 3 条为什么重要：一个把"查不到"读成"不许"的门，会把所有没接租户库的部署
一律打死 —— 那比没有门更糟，因为它看起来在工作。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, ClassVar

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from maop.core.tenant.context import current_tenant

# ── 替身：记录调用参数的假 Dispatcher ─────────────────────────────

class _RecordingDispatcher:
    """假 Dispatcher：记下每次 dispatch 时的 current_tenant()。

    刻意记录**调用当下**的 ContextVar 值，而不是调用方传了什么 —— 本用例要证的
    就是"下游读得到身份"，替身只当观察探针。
    """

    seen: ClassVar[list[dict[str, Any]]] = []

    async def dispatch(self, **kwargs: Any) -> Any:
        _RecordingDispatcher.seen.append(
            {"tenant": current_tenant(), "kwargs": kwargs}
        )
        result = SimpleNamespace(
            stdout="ok", exit_code=0, error="", stderr="", ok=True
        )
        return SimpleNamespace(result=result)


def _dag_body() -> dict[str, Any]:
    return {
        "nodes": [
            {"id": "n1", "type": "agent", "label": "跑一步", "config": {"agent": "echo"}}
        ],
        "edges": [],
    }


def _make_app(*, tenant_id: str | None, roles: list[str] | None = None) -> FastAPI:
    """最小 app：中间件按参数写 request.state（模拟认证中间件的产物）。

    ``tenant_id=None`` 表示"认证中间件没写过这个字段"，与"写了但是空串"是两种
    状态，必须都能造出来 —— 下游语义上它们也不同。
    """
    from maop.dashboard.routers import dag as dag_router

    app = FastAPI()

    @app.middleware("http")
    async def _inject_identity(request, call_next):
        request.state.auth_roles = roles if roles is not None else ["admin"]
        if tenant_id is not None:
            request.state.tenant_id = tenant_id
        return await call_next(request)

    app.include_router(dag_router.router)
    return app


async def _post(app: FastAPI, payload: dict[str, Any]) -> Any:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post("/api/dag/execute", json=payload)


@pytest.fixture
def _clean_recorder() -> Any:
    _RecordingDispatcher.seen = []
    yield _RecordingDispatcher
    _RecordingDispatcher.seen = []


@pytest.fixture
def _fake_dispatcher(monkeypatch: pytest.MonkeyPatch, _clean_recorder: Any) -> Any:
    """把 _default_step_executor 里那个 import 位置的 Dispatcher 换成替身。"""
    from maop.delegate import dispatch_core

    monkeypatch.setattr(dispatch_core, "Dispatcher", _clean_recorder)
    return _clean_recorder


# ── 1. 身份从认证态来，并覆盖到编排链下游 ─────────────────────────


class TestAttributionReachesTheOrchestrationChain:
    async def test_tenant_from_auth_state_is_visible_to_the_step_executor(
        self, _fake_dispatcher: Any
    ) -> None:
        app = _make_app(tenant_id="acme")
        resp = await _post(app, _dag_body())

        assert resp.status_code == 200, resp.text
        assert resp.json()["tenant_id"] == "acme"
        # 真接线证明：执行器被调用时读得到租户，而不是只有签名上有参数。
        assert _fake_dispatcher.seen, "step executor never ran"
        assert [call["tenant"] for call in _fake_dispatcher.seen] == ["acme"]

    async def test_current_tenant_is_restored_after_the_run(self) -> None:
        from maop.core.tenant.context import tenant_context

        with tenant_context("outer"):
            assert current_tenant() == "outer"
        assert current_tenant() == ""

    async def test_empty_tenant_is_set_explicitly_not_left_unset(
        self, _fake_dispatcher: Any
    ) -> None:
        """个人版：作用域里要确实被设成空串（"明确无租户" ≠ "没人设过"）。"""
        app = _make_app(tenant_id=None)
        resp = await _post(app, _dag_body())

        assert resp.status_code == 200, resp.text
        assert resp.json()["tenant_id"] == ""
        assert [call["tenant"] for call in _fake_dispatcher.seen] == [""]


# ── 2. 请求体里的租户声明一律无效（G-07 回归守卫）─────────────────


class TestBodyCannotDeclareTheTenant:
    async def test_tenant_id_in_body_is_ignored(self, _fake_dispatcher: Any) -> None:
        app = _make_app(tenant_id="real-tenant")
        payload = {**_dag_body(), "tenant_id": "someone-elses-tenant"}
        resp = await _post(app, payload)

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["tenant_id"] == "real-tenant"
        assert [call["tenant"] for call in _fake_dispatcher.seen] == ["real-tenant"]

    async def test_tenant_id_in_body_with_no_auth_tenant_stays_empty(
        self, _fake_dispatcher: Any
    ) -> None:
        """没身份时也不能靠 body 字段伪造出身份 —— 否则空串放行会变成任意放行。"""
        app = _make_app(tenant_id=None)
        payload = {**_dag_body(), "tenant_id": "acme"}
        resp = await _post(app, payload)

        assert resp.status_code == 200, resp.text
        assert resp.json()["tenant_id"] == ""
        assert [call["tenant"] for call in _fake_dispatcher.seen] == [""]

    def test_request_model_structurally_cannot_carry_a_tenant(self) -> None:
        """上面两条现在是靠"模型里没有这个字段"通过的，所以要把前提本身钉住。

        变异验证发现：只断言"body 里的 tenant_id 不生效"是**零射程**的 —— 把路由
        改成 ``getattr(body, 'tenant_id', ...)`` 也照样全绿，因为 pydantic 默认丢弃
        extras，那个 mutation 根本是惰性的。真正会被未来改动打开的口子有两个：
        给模型加一个租户字段，或把 ``extra`` 改成 ``allow``。这条断言盯的就是这两个。
        """
        from maop.dashboard.routers.dag import ExecuteDagRequest

        tenantish = {f for f in ExecuteDagRequest.model_fields if "tenant" in f.lower()}
        assert tenantish == set(), f"请求模型竟能携带租户声明: {tenantish}"

        instance = ExecuteDagRequest(**{**_dag_body(), "tenant_id": "acme"})
        assert not hasattr(instance, "tenant_id"), (
            "extras 被保留了 —— 请求体里的租户声明变成了可读属性"
        )


# ── 3. 生命周期门的判定面：只有"有登记表且这个租户不许"才拒 ────────


class _StubManager:
    def __init__(self, tenants: dict[str, str]) -> None:
        self._tenants = tenants

    def list_tenants(self, status: Any = None) -> list[Any]:
        return [SimpleNamespace(tenant_id=t) for t in self._tenants]

    def get_tenant(self, tenant_id: str) -> Any:
        if tenant_id not in self._tenants:
            return None
        return SimpleNamespace(
            tenant_id=tenant_id,
            status=SimpleNamespace(value=self._tenants[tenant_id]),
        )


class TestExecutionBlockReason:
    """直接测判定函数，四种情形都要有正向和反向样本。"""

    def _patch(self, monkeypatch: pytest.MonkeyPatch, mgr: Any) -> None:
        from maop.dashboard.services import tenant_service

        monkeypatch.setattr(tenant_service, "try_get_manager", lambda: mgr)

    def test_no_identity_is_never_blocked(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from maop.dashboard.services import tenant_service

        self._patch(monkeypatch, _StubManager({"acme": "active"}))
        assert tenant_service.execution_block_reason("") is None

    def test_missing_enterprise_package_is_never_blocked(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from maop.dashboard.services import tenant_service

        monkeypatch.setattr(tenant_service, "try_get_manager", lambda: None)
        assert tenant_service.execution_block_reason("acme") is None

    def test_empty_registry_does_not_deny_all(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """对准空集合的门会打死所有没接租户库的部署 —— 必须显式放行。"""
        from maop.dashboard.services import tenant_service

        self._patch(monkeypatch, _StubManager({}))
        assert tenant_service.execution_block_reason("acme") is None

    def test_suspended_and_terminated_are_denied(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from maop.dashboard.services import tenant_service

        self._patch(
            monkeypatch,
            _StubManager({"s": "suspended", "t": "terminated", "a": "active", "r": "trial"}),
        )
        assert tenant_service.execution_block_reason("s") == "tenant 's' is suspended"
        assert tenant_service.execution_block_reason("t") == "tenant 't' is terminated"
        assert tenant_service.execution_block_reason("a") is None
        assert tenant_service.execution_block_reason("r") is None

    def test_unregistered_tenant_denied_only_when_registry_has_rows(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from maop.dashboard.services import tenant_service

        self._patch(monkeypatch, _StubManager({"known": "active"}))
        assert tenant_service.execution_block_reason("ghost") == (
            "tenant 'ghost' is not registered"
        )


class TestEndpointEnforcesTheGate:
    async def test_suspended_tenant_gets_403_and_never_dispatches(
        self,
        monkeypatch: pytest.MonkeyPatch,
        _fake_dispatcher: Any,
    ) -> None:
        from maop.dashboard.services import tenant_service

        self._patch_manager(monkeypatch, tenant_service, {"acme": "suspended"})
        app = _make_app(tenant_id="acme")
        resp = await _post(app, _dag_body())

        assert resp.status_code == 403, resp.text
        # 拒绝原因必须真的到客户端：handle_api_errors 把 4xx 文案放在 error 字段
        # （detail 恒为空串），静默的 403 对租户管理员等于没有门。
        assert "suspended" in resp.json()["error"], resp.json()
        assert _fake_dispatcher.seen == [], "被拒绝的执行仍然派发了步骤"

    async def test_unknown_tenant_with_populated_registry_gets_403(
        self,
        monkeypatch: pytest.MonkeyPatch,
        _fake_dispatcher: Any,
    ) -> None:
        from maop.dashboard.services import tenant_service

        self._patch_manager(monkeypatch, tenant_service, {"other": "active"})
        app = _make_app(tenant_id="ghost")
        resp = await _post(app, _dag_body())

        assert resp.status_code == 403, resp.text
        assert _fake_dispatcher.seen == []

    async def test_active_tenant_passes_the_gate(
        self, monkeypatch: pytest.MonkeyPatch, _fake_dispatcher: Any
    ) -> None:
        from maop.dashboard.services import tenant_service

        self._patch_manager(monkeypatch, tenant_service, {"acme": "active"})
        app = _make_app(tenant_id="acme")
        resp = await _post(app, _dag_body())

        assert resp.status_code == 200, resp.text
        assert resp.json()["tenant_id"] == "acme"

    def _patch_manager(
        self, monkeypatch: pytest.MonkeyPatch, tenant_service: Any, tenants: dict[str, str]
    ) -> None:
        monkeypatch.setattr(
            tenant_service, "try_get_manager", lambda: _StubManager(tenants)
        )


# ── 4. 身份缺失时不许退化成"看到/允许全部"之外的第二种失效 ────────


class TestTryGetManagerCoversBothKindsOfMissing:
    """钉住实测踩出来的那个 500。

    只挡 ``ImportError`` 的 ``try_get_manager`` 在"MAOS 在 sys.path 里 + 跑个人版"
    的机器上照样会炸：``TenantManager()`` 构造时 ``require_feature(TENANT_ISOLATION)``
    抛的是 ``FeatureNotAvailable``，于是每次 DAG 执行都 500。本机就是这个形态，
    所以第一版接线被自家测试当场判红 —— 这里的两条断言分别盯住两种"没有"。
    """

    def test_feature_off_short_circuits_before_constructing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from maop.config import edition as edition_mod
        from maop.dashboard.services import tenant_service

        constructed: list[str] = []

        def _fake_get_manager() -> Any:
            constructed.append("called")
            return object()

        monkeypatch.setattr(edition_mod, "has_feature", lambda flag: False)
        monkeypatch.setattr(tenant_service, "_get_manager", _fake_get_manager)

        assert tenant_service.try_get_manager() is None
        assert constructed == [], (
            "租户能力关闭时仍去构造 manager —— 个人版会在这一步抛 "
            "FeatureNotAvailable，把 DAG 执行变成 500"
        )

    def test_package_missing_is_also_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from maop.config import edition as edition_mod
        from maop.dashboard.services import tenant_service

        def _boom() -> Any:
            raise ImportError("No module named 'maop.enterprise'")

        monkeypatch.setattr(edition_mod, "has_feature", lambda flag: True)
        monkeypatch.setattr(tenant_service, "_get_manager", _boom)

        assert tenant_service.try_get_manager() is None


class TestFailClosedOnTransport:
    async def test_require_admin_still_gates_before_any_tenant_logic(
        self, _fake_dispatcher: Any
    ) -> None:
        """非 admin 必须在租户逻辑之前就被拦下，且不得派发。"""
        app = _make_app(tenant_id="acme", roles=["read"])
        resp = await _post(app, _dag_body())

        assert resp.status_code >= 400, resp.text
        assert _fake_dispatcher.seen == []


class TestServiceSignatureIsKeywordOnly:
    async def test_tenant_id_is_keyword_only(self) -> None:
        """关键字-only + 空串默认：既有调用点零改动，也不许有人再按位置传身份。"""
        import inspect

        from maop.dashboard.services.execution_service import execute_dag

        params = inspect.signature(execute_dag).parameters
        assert params["tenant_id"].kind is inspect.Parameter.KEYWORD_ONLY
        assert params["tenant_id"].default == ""
