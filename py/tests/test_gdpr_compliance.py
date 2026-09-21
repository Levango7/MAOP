"""GDPR 法定流程端点与管理器（2026-09-21 接线）。

背景：``GDPRComplianceManager`` 自 ``e2e0f2a`` 模块化拆分后**从未被实例化、
无任何测试覆盖**；在用的 ``ComplianceManager`` 只有 delete/export 两个方法，
因此数据主体请求追踪、DPA 登记（Art. 28）、处理活动记录（Art. 30）实为缺失。
本文件补齐这两块：管理器本身的行为，以及路由接线的**租户隔离**属性。
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from maop.core.tenant.gdpr_manager import (
    GDPRComplianceManager,
    ProcessingAgreement,
    ProcessingRecord,
)


@pytest.fixture
def gdpr(tmp_path: Path) -> GDPRComplianceManager:
    return GDPRComplianceManager(tmp_path)


# ── 管理器行为（此前 0 覆盖）────────────────────────────────────────


def test_access_request_creates_tracked_request(gdpr):
    """访问权请求会留下可追踪记录（Art. 15 且有响应期限）。"""
    req, report = gdpr.access_request("u1", tenant_id="acme")
    assert req.user_id == "u1"
    assert req.tenant_id == "acme"
    assert req.request_type == "access"
    assert req.due_at, "应有法定响应期限"
    assert report is not None

    found = gdpr.get_request(req.request_id)
    assert found is not None and found.request_id == req.request_id


def test_erasure_request_creates_tracked_request(gdpr):
    req, report = gdpr.right_to_erasure("u1", tenant_id="acme")
    assert req.request_type == "erasure"
    assert report is not None


def test_portability_request_creates_tracked_request(gdpr):
    req, report = gdpr.data_portability("u1", tenant_id="acme", fmt="json")
    assert req.request_type == "portability"
    assert report is not None


def test_requests_are_tenant_scoped(gdpr):
    """不同租户的请求互不可见 —— 这是隔离的核心断言。"""
    gdpr.access_request("u1", tenant_id="acme")
    gdpr.access_request("u2", tenant_id="globex")

    assert [r.user_id for r in gdpr.list_requests(tenant_id="acme")] == ["u1"]
    assert [r.user_id for r in gdpr.list_requests(tenant_id="globex")] == ["u2"]


def test_register_and_get_dpa(gdpr):
    dpa = ProcessingAgreement(
        dpa_id="dpa-1", controller_name="Acme", processor_name="MAOP", tenant_id="acme",
    )
    saved = gdpr.register_dpa(dpa)
    assert saved.dpa_id == "dpa-1"
    assert gdpr.get_dpa("dpa-1") is not None
    assert gdpr.get_dpa("missing") is None


def test_dpas_are_tenant_scoped(gdpr):
    gdpr.register_dpa(ProcessingAgreement(dpa_id="d1", controller_name="A",
                                          processor_name="P", tenant_id="acme"))
    gdpr.register_dpa(ProcessingAgreement(dpa_id="d2", controller_name="A",
                                          processor_name="P", tenant_id="globex"))
    assert [d.dpa_id for d in gdpr.list_dpas(tenant_id="acme")] == ["d1"]
    assert [d.dpa_id for d in gdpr.list_dpas(tenant_id="globex")] == ["d2"]


def test_record_and_list_processing_activity(gdpr):
    """Art. 30 处理活动记录。"""
    rec = ProcessingRecord(
        record_id="p1", tenant_id="acme", activity_name="会话存储",
        purpose="提供对话服务", legal_basis="合同必要",
    )
    saved = gdpr.record_processing_activity(rec)
    assert saved.record_id == "p1"
    assert [r.record_id for r in gdpr.list_processing_records(tenant_id="acme")] == ["p1"]
    assert gdpr.get_processing_record("missing") is None


def test_processing_records_are_tenant_scoped(gdpr):
    gdpr.record_processing_activity(ProcessingRecord(record_id="p1", tenant_id="acme",
                                                     activity_name="A", purpose="a"))
    gdpr.record_processing_activity(ProcessingRecord(record_id="p2", tenant_id="globex",
                                                     activity_name="B", purpose="b"))
    assert [r.record_id for r in gdpr.list_processing_records(tenant_id="acme")] == ["p1"]
    assert [r.record_id for r in gdpr.list_processing_records(tenant_id="globex")] == ["p2"]


# ── 路由接线：租户强制来自 JWT ──────────────────────────────────────


def _mock_request(tmp_path: Path, tenant_id: str) -> MagicMock:
    request = MagicMock()
    request.state.tenant_id = tenant_id
    request.state.auth_roles = ["admin"]
    request.app.state.root_dir = str(tmp_path)
    return request


@pytest.fixture
def router_env(gdpr):
    """把 has_feature / require_admin / 管理器都换成测试态。"""
    import maop.dashboard.routers.compliance as C

    with patch.object(C, "has_feature", return_value=True), \
         patch.object(C, "require_admin", lambda request: None), \
         patch.object(C.rbac_service, "get_gdpr_manager", return_value=gdpr):
        yield C


async def test_dpa_tenant_forced_from_jwt(tmp_path, gdpr, router_env):
    """DPA 的租户必须来自 JWT，请求体无法指定。"""
    from maop.dashboard.routers.compliance import DpaCreate, gdpr_register_dpa

    body = DpaCreate(controller_name="Acme", processor_name="MAOP")
    # 注意：请求体里没有 tenant_id 字段，路由用 JWT 的租户强制填充
    result = await gdpr_register_dpa(body, _mock_request(tmp_path, "acme"))
    assert result["dpa"]["tenant_id"] == "acme"


async def test_processing_record_tenant_forced_from_jwt(tmp_path, gdpr, router_env):
    from maop.dashboard.routers.compliance import (
        ProcessingRecordCreate,
        gdpr_record_processing,
    )

    body = ProcessingRecordCreate(activity_name="会话存储", purpose="对话服务")
    result = await gdpr_record_processing(body, _mock_request(tmp_path, "globex"))
    assert result["record"]["tenant_id"] == "globex"


async def test_cross_tenant_dpa_read_is_404(tmp_path, gdpr, router_env):
    """跨租户读取返回 404（不泄露资源是否存在）。

    注意：``@handle_api_errors`` 会把 HTTPException 渲染成 JSONResponse，
    所以直接调用路由函数时断言的是响应对象而非抛出的异常。
    """
    from maop.dashboard.routers.compliance import gdpr_get_dpa

    gdpr.register_dpa(ProcessingAgreement(dpa_id="d1", controller_name="A",
                                          processor_name="P", tenant_id="acme"))
    resp = await gdpr_get_dpa("d1", _mock_request(tmp_path, "globex"))
    assert resp.status_code == 404


async def test_cross_tenant_request_read_is_404(tmp_path, gdpr, router_env):
    from maop.dashboard.routers.compliance import gdpr_get_request

    req, _ = gdpr.access_request("u1", tenant_id="acme")
    resp = await gdpr_get_request(req.request_id, _mock_request(tmp_path, "globex"))
    assert resp.status_code == 404


async def test_list_endpoints_only_return_own_tenant(tmp_path, gdpr, router_env):
    from maop.dashboard.routers.compliance import gdpr_list_dpas, gdpr_list_requests

    gdpr.access_request("u1", tenant_id="acme")
    gdpr.access_request("u2", tenant_id="globex")
    gdpr.register_dpa(ProcessingAgreement(dpa_id="d1", controller_name="A",
                                          processor_name="P", tenant_id="acme"))

    mine = await gdpr_list_requests(_mock_request(tmp_path, "acme"), user_id="",
                                    status_filter="", request_type="")
    assert [r["user_id"] for r in mine["requests"]] == ["u1"]

    dpa_mine = await gdpr_list_dpas(_mock_request(tmp_path, "acme"), dp_status="")
    assert [d["dpa_id"] for d in dpa_mine["dpas"]] == ["d1"]


async def test_missing_tenant_is_403(tmp_path, gdpr, router_env):
    """无租户的会话（未分配）不得操作合规数据。"""
    from maop.dashboard.routers.compliance import DpaCreate, gdpr_register_dpa

    request = _mock_request(tmp_path, "")   # 未分配租户
    resp = await gdpr_register_dpa(
        DpaCreate(controller_name="A", processor_name="P"), request
    )
    assert resp.status_code == 403
