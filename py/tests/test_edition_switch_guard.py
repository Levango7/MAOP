"""P1-2 edition 切换门禁单测。

验证 POST /api/info/edition 的切换守卫逻辑：
  - 无有效 license 切换到 enterprise 被明确拒绝（403）并返回授权指引消息
  - 切换到 personal 正常成功（200）
  - 无效 edition 值返回 400
  - 缺少 edition 字段返回 400
  - 错误消息包含 MAOS / License 关键词，便于前端展示明确提示

设计说明:
  - conftest.py 默认 MAOP_AUTH=0 + MAOP_AUTH_DISABLED_ADMIN=1，中间件授予
    admin 角色，因此无需 JWT token 即可通过 require_admin 守卫。
  - 测试环境未安装 maop.enterprise 包，_detect_with_license_check(ENTERPRISE)
    必然降级到 PERSONAL，因此切换到 enterprise 应触发 403 门禁。
  - 每个测试前后 reset_edition() 避免全局状态污染。
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from maop.config.edition import Edition, get_edition, reset_edition, set_edition
from maop.dashboard.server import app


@pytest.fixture(autouse=True)
def _reset_edition_state():
    """每个测试前后重置 edition 全局状态，避免测试间相互污染。"""
    reset_edition()
    yield
    reset_edition()


@pytest.fixture
async def client():
    """创建无 auth 测试客户端（conftest 已配置 auth=0 + disabled_admin=1）。"""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# ── P1-2: 无 license 切换 enterprise 被拒绝 ──────────────────────────


class TestEditionSwitchEnterpriseGuard:
    """无有效 license 时切换到 enterprise 应被明确拒绝（403）。"""

    async def test_switch_to_enterprise_without_license_rejected_403(self, client):
        """无 license 切换到 enterprise 返回 403，而非静默降级 200。

        P1-2 门禁核心：个人版交付不允许无授权启用企业版功能。
        后端应返回 403 + 授权指引消息，前端据此显示明确提示。
        """
        # 确保起点为 personal
        set_edition(Edition.PERSONAL)

        resp = await client.post("/api/info/edition", json={"edition": "enterprise"})
        assert resp.status_code == 403, (
            f"Switch to enterprise without license should be 403, "
            f"got {resp.status_code}: {resp.text}"
        )

    async def test_403_error_message_contains_authorization_hint(self, client):
        """403 错误消息包含 MAOS / License 授权指引关键词。

        前端依赖错误消息中的关键词向用户展示明确的升级指引，
        消息必须提及 MAOS 商业包和 License。

        注意：handle_api_errors 装饰器将 HTTPException.detail 渲染为
        ErrorSchema.error 字段（非 FastAPI 默认的 detail 字段），前端
        store.switchEdition 读取 data.error || data.detail 兼容两者。
        """
        set_edition(Edition.PERSONAL)

        resp = await client.post("/api/info/edition", json={"edition": "enterprise"})
        assert resp.status_code == 403
        body = resp.json()
        # error_handler 把 HTTPException.detail 放在 "error" 字段
        msg = body.get("error", "") or body.get("detail", "")
        # 消息必须包含 MAOS 和 License 关键词（授权指引）
        assert "MAOS" in msg, (
            f"403 error should mention MAOS commercial package, got: {msg!r}"
        )
        assert "License" in msg or "license" in msg, (
            f"403 error should mention License requirement, got: {msg!r}"
        )
        # 应包含 HTTP_403 错误码字段
        assert body.get("code", "") == "HTTP_403", (
            f"Error code should be HTTP_403, got: {body.get('code')!r}"
        )

    async def test_enterprise_rejection_does_not_change_edition(self, client):
        """被 403 拒绝后，当前 edition 仍为 personal（未误切换）。

        门禁不仅返回 403，还必须确保 edition 全局状态未被改成 enterprise，
        避免拒绝响应但状态已泄漏的安全风险。
        """
        set_edition(Edition.PERSONAL)

        resp = await client.post("/api/info/edition", json={"edition": "enterprise"})
        assert resp.status_code == 403

        # edition 应仍为 personal（降级后的实际状态）
        assert get_edition() is Edition.PERSONAL, (
            "Edition should remain personal after 403 rejection"
        )


# ── 正常切换路径 ─────────────────────────────────────────────────────


class TestEditionSwitchPersonal:
    """切换到 personal 的正常路径验证。"""

    async def test_switch_to_personal_succeeds(self, client):
        """切换到 personal 返回 200 + status=ok。"""
        set_edition(Edition.PERSONAL)

        resp = await client.post("/api/info/edition", json={"edition": "personal"})
        assert resp.status_code == 200, (
            f"Switch to personal should succeed, got {resp.status_code}: {resp.text}"
        )
        data = resp.json()
        assert data["status"] == "ok"
        assert data["edition"] == "personal"

    async def test_switch_to_personal_returns_previous(self, client):
        """切换响应包含 previous 字段记录切换前 edition。"""
        set_edition(Edition.PERSONAL)

        resp = await client.post("/api/info/edition", json={"edition": "personal"})
        assert resp.status_code == 200
        data = resp.json()
        assert "previous" in data
        assert data["requested"] == "personal"
        # 未降级（personal -> personal）
        assert data.get("degraded") is False

    async def test_get_edition_after_switch(self, client):
        """POST 切换后 GET /api/info/edition 返回新 edition。"""
        set_edition(Edition.PERSONAL)

        resp = await client.post("/api/info/edition", json={"edition": "personal"})
        assert resp.status_code == 200

        resp = await client.get("/api/info/edition")
        assert resp.status_code == 200
        data = resp.json()
        assert data["edition"] == "personal"


# ── 输入校验 ─────────────────────────────────────────────────────────


class TestEditionSwitchValidation:
    """无效输入返回 400。"""

    async def test_invalid_edition_rejected_400(self, client):
        """无效的 edition 值返回 400。"""
        resp = await client.post(
            "/api/info/edition", json={"edition": "invalid-edition"}
        )
        assert resp.status_code == 400, (
            f"Invalid edition should be 400, got {resp.status_code}"
        )

    async def test_missing_edition_field_rejected_400(self, client):
        """缺少 edition 字段返回 400。"""
        resp = await client.post("/api/info/edition", json={})
        assert resp.status_code == 400, (
            f"Missing edition field should be 400, got {resp.status_code}"
        )

    async def test_invalid_json_body_rejected_400(self, client):
        """非法 JSON body 返回 400。"""
        resp = await client.post(
            "/api/info/edition",
            content=b"not-json",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 400, (
            f"Invalid JSON body should be 400, got {resp.status_code}"
        )