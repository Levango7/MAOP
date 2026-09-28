"""/api/sso/callback 的请求级守卫：state 缺失必须 400，不许 fail-open。

背景：2026-09-28 的修复（见 routers/sso.py 的 `if not state:` 分支注释）关闭了 callback 的
fail-open —— 此前空 state 会跳过 SSOManager.handle_callback 的 CSRF 校验直接换 session，
而该端点公开无鉴权，攻击者可注入自己的 authorization code 完成 login-CSRF。修复后缺 state
直接 400。该分支此前零测试覆盖（`grep "Missing state parameter" py/tests/` 命中为空）。

依赖 `maop.enterprise`（MAOS 私有仓）：CI 无企业包时由 tests/conftest.py 的
collect_ignore 机制跳过（与 test_sso.py / test_sso_providers.py 同惯例），须在本机
（装有 MAOS）跑。断言只看 HTTP 语义，不触网、不碰 SSO manager。
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from maop.config.edition import Edition, reset_edition, set_edition


@pytest.fixture(autouse=True)
def _enterprise_edition(monkeypatch: pytest.MonkeyPatch) -> Any:
    """SSO 只在企业特性集合里（FeatureFlag.SSO），须先切版本再打端点。"""
    monkeypatch.setenv("MAOP_EDITION", "enterprise")
    set_edition(Edition.ENTERPRISE)
    yield
    reset_edition()


def _client() -> TestClient:
    """只挂 sso 路由的最小 app：不引入真实 server.app 的中间件与单例状态。"""
    from maop.dashboard.routers import sso

    app = FastAPI()
    app.include_router(sso.router)
    return TestClient(app)


def test_missing_state_is_400_not_skip() -> None:
    """回归靶心：code 有、state 空 → 400 "Missing state parameter"，不得进入换 session 分支。"""
    resp = _client().get("/api/sso/callback", params={"code": "attacker-supplied-code"})
    assert resp.status_code == 400, resp.text
    assert resp.json()["error"] == "Missing state parameter"


def test_missing_code_is_400() -> None:
    """code 缺失在 state 之前判定：报的是 code，不是 state（避免误导排查方向）。"""
    resp = _client().get("/api/sso/callback")
    assert resp.status_code == 400, resp.text
    assert resp.json()["error"] == "Missing authorization code"


def test_provider_error_is_400() -> None:
    """IdP 侧回传的 error 原样上报 400，同样不进换 session 流程。"""
    resp = _client().get("/api/sso/callback", params={"error": "access_denied"})
    assert resp.status_code == 400, resp.text
    assert resp.json()["error"] == "SSO provider error: access_denied"


def test_personal_edition_returns_404(monkeypatch: pytest.MonkeyPatch) -> None:
    """Personal 版整体 404（FeatureFlag.SSO 守卫）；即使带了 code+state 也不得放行。"""
    reset_edition()
    monkeypatch.setenv("MAOP_EDITION", "personal")
    set_edition(Edition.PERSONAL)
    try:
        resp = _client().get("/api/sso/callback", params={"code": "x", "state": "y"})
        assert resp.status_code == 404, resp.text
        assert resp.json()["error"] == "SSO not available in this edition"
    finally:
        reset_edition()
