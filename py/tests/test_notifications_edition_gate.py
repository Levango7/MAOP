"""通知中心的双版边界：个人版不挂载，企业版挂载。

为什么需要这条测试
------------------
`routers/notifications.py` / `notification_schemas.py` 在模块级 import
`maop.enterprise.notification.models`，`services/notification_service.py` 惰性
import `EventBus` / `NotificationManager`。也就是说**个人版一旦挂载就必然
ImportError → 首次调用 500**，而注册层是 ``try/except ImportError`` + warning，
不会让启动失败 —— 历史上前端 `/notifications` 无条件暴露，用户进入即 500。

本测试把这条边界钉死：`FeatureFlag.NOTIFICATION_CENTER` 决定是否 include_router，
个人版 0 条通知路由，企业版 >0 条。前端侧的对应断言在
`dashboard-enterprise/src/__tests__/router.test.js`
（`meta.requiresEnterprise === true`）。
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI

pytest.importorskip("maop.enterprise", reason="企业包未安装，跳过（个人版 CI 由 check_personal_imports 覆盖）")

from maop.config.edition import (
    Edition,
    FeatureFlag,
    has_feature,
    reset_edition,
    set_edition,
    set_feature_override,
)
from maop.dashboard._register_routes._routers import (
    _register_notifications_and_config,
)


def _included_targets(app: FastAPI) -> list[object]:
    """列出 ``include_router`` 实际登记的路由对象。

    较新 FastAPI 把 include 记为惰性包装（``_IncludedRouter``，``path`` 为
    None，真实路由在 ``original_router``），直接遍历 ``app.routes`` 找 path
    会得到"什么都没挂"的假象 —— 故按"登记了哪个 APIRouter"来判断，
    与 FastAPI 版本无关。
    """
    targets: list[object] = []
    for route in app.routes:
        target = getattr(route, "original_router", None)
        if target is None:
            target = getattr(route, "router", None) if getattr(route, "path", None) is None else None
        if target is not None:
            targets.append(target)
    return targets


def _notifications_mounted(app: FastAPI, notifications_router) -> bool:
    return any(target is notifications_router.router for target in _included_targets(app))


@pytest.fixture(autouse=True)
def _clean_edition():
    reset_edition()
    yield
    reset_edition()


def _register(monkeypatch: pytest.MonkeyPatch) -> tuple[FastAPI, object]:
    """挂载通知路由，但屏蔽 broadcaster 接线（避免测试里起后台投递）。"""
    import maop.dashboard.routers.notifications as notifications_router

    monkeypatch.setattr(notifications_router, "wire_broadcaster", lambda: None)
    app = FastAPI()
    _register_notifications_and_config(app)
    return app, notifications_router


def test_personal_edition_does_not_mount_notification_routes(monkeypatch: pytest.MonkeyPatch) -> None:
    """个人版：flag 为假 → 不 include_router（而不是挂上后 500）。"""
    set_edition(Edition.PERSONAL)
    assert not has_feature(FeatureFlag.NOTIFICATION_CENTER)

    app, notifications_router = _register(monkeypatch)
    assert not _notifications_mounted(app, notifications_router), (
        "个人版不应挂载 /api/notifications/*：后端首次调用即 ImportError，"
        "而 try/except 只会留一行 warning，用户看到的是 500"
    )


def test_enterprise_edition_mounts_notification_routes(monkeypatch: pytest.MonkeyPatch) -> None:
    """企业版：flag 为真 → 路由就位，证明 gate 不是"永远不挂载"。"""
    set_edition(Edition.ENTERPRISE)
    set_feature_override(FeatureFlag.NOTIFICATION_CENTER, True)
    assert has_feature(FeatureFlag.NOTIFICATION_CENTER)

    app, notifications_router = _register(monkeypatch)
    assert _notifications_mounted(app, notifications_router), "企业版激活后通知中心路由必须存在"


def test_enterprise_edition_without_flag_does_not_mount(monkeypatch: pytest.MonkeyPatch) -> None:
    """企业版但显式关掉 flag → 同样不挂载（gate 依据是 flag，不是 edition 常量）。"""
    set_edition(Edition.ENTERPRISE)
    set_feature_override(FeatureFlag.NOTIFICATION_CENTER, False)

    app, notifications_router = _register(monkeypatch)
    assert not _notifications_mounted(app, notifications_router)
