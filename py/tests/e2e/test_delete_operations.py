"""E2E 测试：Delete 操作在实体不存在时返回 False（第五轮 P0 修复）。

验证 pg_persist 的 delete_grant / delete_tenant 在实体不存在时
返回 False 而非 True。修复使用 DELETE ... RETURNING 语句，
通过 fetchall 检查实际删除的行数：空列表 → False。

修复背景（P0-1 / P0-2）：
  原实现无论实体是否存在都返回 True，导致调用方误以为删除成功。
  修复后使用 DELETE ... RETURNING，fetchall 返回被删除的行；
  空列表表示实体不存在，返回 False。

注意：
  pg_persist 中不存在 delete_rule 方法（PgRBACStore 仅有 delete_grant，
  PgTenantStore 仅有 delete_tenant）。notifications.py 中的 delete_rule
  是通知规则路由，不属于 pg_persist 持久层，故不在此测试范围。
  见下方 TestDeleteRuleNotApplicable 说明。

测试策略：
  - mock PG backend（execute/fetchall/fetchone）
  - fetchall 返回空列表模拟实体不存在
  - 断言 delete_grant / delete_tenant 返回 False
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

# -- Fixtures -------------------------------------------------------


@pytest.fixture(autouse=True)
def enterprise_mode():
    """启用企业版，使 PostgreSQL 持久层功能可用。"""
    from maop.config.edition import Edition, reset_edition, set_edition
    set_edition(Edition.ENTERPRISE)
    yield
    reset_edition()


@pytest.fixture(autouse=True)
def _no_pg_backend_env(monkeypatch):
    """确保不通过环境变量选中 PG backend（我们用 patch 注入 mock backend）。"""
    monkeypatch.delenv("MAOP_STORAGE_BACKEND", raising=False)


def _mock_backend_with_empty_fetchall():
    """创建 mock PG backend，fetchall 返回空列表（模拟实体不存在）。

    DELETE ... RETURNING 语句执行后，fetchall 返回被删除的行。
    空列表表示没有行被删除（实体不存在）。
    """
    b = MagicMock()
    b.execute = MagicMock()
    b.fetchall = MagicMock(return_value=[])  # 空列表 → 实体不存在
    b.fetchone = MagicMock(return_value=None)
    return b


def _mock_backend_with_rows(rows):
    """创建 mock PG backend，fetchall 返回指定行（模拟实体存在）。"""
    b = MagicMock()
    b.execute = MagicMock()
    b.fetchall = MagicMock(return_value=rows)
    b.fetchone = MagicMock(return_value=None)
    return b


# -- 测试：PgRBACStore.delete_grant 返回 False ----------------------


class TestDeleteGrantReturnsFalse:
    """验证 delete_grant 在实体不存在时返回 False（P0-1 修复）。"""

    def test_delete_grant_returns_false_when_not_found(self):
        """grant 不存在时 delete_grant 返回 False。

        DELETE ... RETURNING 执行后 fetchall 返回空列表，
        表示没有行被删除，应返回 False。
        """
        backend = _mock_backend_with_empty_fetchall()
        with patch("maop.enterprise.pg_persist._get_pg_backend", return_value=backend):
            from maop.enterprise.pg_persist import PgRBACStore

            store = PgRBACStore()
            result = store.delete_grant("nonexistent_user", "admin", "t1")

        assert result is False, (
            f"grant 不存在时 delete_grant 应返回 False，实际返回 {result}"
        )

    def test_delete_grant_returns_true_when_found(self):
        """grant 存在时 delete_grant 返回 True（对照测试）。

        DELETE ... RETURNING 执行后 fetchall 返回被删除的行，
        应返回 True。此测试验证 False/True 区分逻辑正确。
        """
        backend = _mock_backend_with_rows([{"id": 1}])
        with patch("maop.enterprise.pg_persist._get_pg_backend", return_value=backend):
            from maop.enterprise.pg_persist import PgRBACStore

            store = PgRBACStore()
            result = store.delete_grant("existing_user", "admin", "t1")

        assert result is True, (
            f"grant 存在时 delete_grant 应返回 True，实际返回 {result}"
        )

    def test_delete_grant_false_is_boolean_not_falsy(self):
        """delete_grant 返回的 False 是布尔值 False，而非其他 falsy 值。

        确保返回值类型正确，避免 0/None/"" 等被误判为 False。
        """
        backend = _mock_backend_with_empty_fetchall()
        with patch("maop.enterprise.pg_persist._get_pg_backend", return_value=backend):
            from maop.enterprise.pg_persist import PgRBACStore

            store = PgRBACStore()
            result = store.delete_grant("u1", "admin", "t1")

        assert result is False, (
            f"应返回布尔值 False（is False），实际返回 {result!r}（type={type(result)}）"
        )


# -- 测试：PgTenantStore.delete_tenant 返回 False --------------------


class TestDeleteTenantReturnsFalse:
    """验证 delete_tenant 在实体不存在时返回 False（P0-2 修复）。"""

    def test_delete_tenant_returns_false_when_not_found(self):
        """tenant 不存在时 delete_tenant 返回 False。"""
        backend = _mock_backend_with_empty_fetchall()
        with patch("maop.enterprise.pg_persist._get_pg_backend", return_value=backend):
            from maop.enterprise.pg_persist import PgTenantStore

            store = PgTenantStore()
            result = store.delete_tenant("nonexistent_tenant")

        assert result is False, (
            f"tenant 不存在时 delete_tenant 应返回 False，实际返回 {result}"
        )

    def test_delete_tenant_returns_true_when_found(self):
        """tenant 存在时 delete_tenant 返回 True（对照测试）。"""
        backend = _mock_backend_with_rows([{"tenant_id": "t1"}])
        with patch("maop.enterprise.pg_persist._get_pg_backend", return_value=backend):
            from maop.enterprise.pg_persist import PgTenantStore

            store = PgTenantStore()
            result = store.delete_tenant("existing_tenant")

        assert result is True, (
            f"tenant 存在时 delete_tenant 应返回 True，实际返回 {result}"
        )

    def test_delete_tenant_false_is_boolean_not_falsy(self):
        """delete_tenant 返回的 False 是布尔值 False。"""
        backend = _mock_backend_with_empty_fetchall()
        with patch("maop.enterprise.pg_persist._get_pg_backend", return_value=backend):
            from maop.enterprise.pg_persist import PgTenantStore

            store = PgTenantStore()
            result = store.delete_tenant("t1")

        assert result is False, (
            f"应返回布尔值 False（is False），实际返回 {result!r}（type={type(result)}）"
        )


# -- 测试：delete_rule 不适用说明 -----------------------------------


class TestDeleteRuleNotApplicable:
    """说明 delete_rule 不在 pg_persist 测试范围内的原因。

    pg_persist 模块中：
      - PgRBACStore 仅有 delete_grant（删除 RBAC 授权）
      - PgTenantStore 仅有 delete_tenant（删除租户）
      - 不存在 delete_rule 方法

    dashboard/routers/notifications.py 中的 delete_rule 是通知规则
    的 HTTP 路由处理器，属于应用层而非 pg_persist 持久层，
    且其删除逻辑通过 SQLite 而非 PostgreSQL backend 实现。
    因此 delete_rule 不属于本次 P0 修复（pg_persist DELETE 返回值）范围。
    """

    def test_pg_persist_has_no_delete_rule_method(self):
        """验证 pg_persist 的 Store 类没有 delete_rule 方法。"""
        with patch(
            "maop.enterprise.pg_persist._get_pg_backend",
            return_value=_mock_backend_with_empty_fetchall(),
        ):
            from maop.enterprise.pg_persist import PgRBACStore, PgTenantStore

            rbac_methods = [m for m in dir(PgRBACStore) if "delete" in m.lower()]
            tenant_methods = [m for m in dir(PgTenantStore) if "delete" in m.lower()]

        assert "delete_rule" not in rbac_methods, (
            f"PgRBACStore 不应有 delete_rule 方法，实际 delete 方法: {rbac_methods}"
        )
        assert "delete_rule" not in tenant_methods, (
            f"PgTenantStore 不应有 delete_rule 方法，实际 delete 方法: {tenant_methods}"
        )


# -- 测试：无 backend 时 delete 返回 False ---------------------------


class TestDeleteWithoutBackend:
    """无 PG backend 时 delete 操作返回 False（降级场景）。"""

    def test_delete_grant_returns_false_without_backend(self):
        """无 PG backend 时 delete_grant 返回 False。"""
        with patch("maop.enterprise.pg_persist._get_pg_backend", return_value=None):
            from maop.enterprise.pg_persist import PgRBACStore

            store = PgRBACStore()
            result = store.delete_grant("u1", "admin", "t1")

        assert result is False, (
            f"无 backend 时 delete_grant 应返回 False，实际返回 {result}"
        )

    def test_delete_tenant_returns_false_without_backend(self):
        """无 PG backend 时 delete_tenant 返回 False。"""
        with patch("maop.enterprise.pg_persist._get_pg_backend", return_value=None):
            from maop.enterprise.pg_persist import PgTenantStore

            store = PgTenantStore()
            result = store.delete_tenant("t1")

        assert result is False, (
            f"无 backend 时 delete_tenant 应返回 False，实际返回 {result}"
        )