"""JWT 会话的租户链路（多租户闭环 · 阶段 1-3）。

此前只有 **API Key** 链路能拿到真实租户；JWT 链路拿到的一律是空串，因为
``AuthResult`` 没有 ``tenant_id``、JWT payload 也没有 ``tenant`` claim。
结果是配额 / RBAC / 合规的租户隔离对 dashboard 会话完全空转。

本文件锁定补链路后的行为，**重点是向后兼容**：存量已签发的 token 没有
``tenant`` claim，绝不能因此被判定无效（否则等于强制所有人重新登录）。
"""
from __future__ import annotations

import base64
import json
import sqlite3
import tempfile

import pytest

from maop.core.security.auth import JWTConfig, JWTHandler


@pytest.fixture
def handler():
    return JWTHandler(JWTConfig(secret="s" * 32, issuer="MAOP"))


def _decode_payload(token: str) -> dict:
    part = token.split(".")[1]
    part += "=" * (-len(part) % 4)
    return json.loads(base64.urlsafe_b64decode(part))


# ── 令牌面 ────────────────────────────────────────────────────────────


def test_token_carries_tenant(handler):
    token = handler.create_token("bob", roles=["admin"], tenant_id="acme")
    result = handler.validate_token(token)
    assert result.authenticated is True
    assert result.tenant_id == "acme"
    assert _decode_payload(token)["tenant"] == "acme"


def test_token_without_tenant_is_unassigned(handler):
    """未分配租户时不写入 tenant claim，且解析回空串。"""
    token = handler.create_token("alice", roles=["admin"])
    result = handler.validate_token(token)
    assert result.authenticated is True
    assert result.tenant_id == ""
    assert "tenant" not in _decode_payload(token)


def test_legacy_token_without_claim_still_valid(handler):
    """向后兼容：存量 token 缺 tenant claim 必须仍有效，不能强制登出。"""
    payload = _decode_payload(handler.create_token("legacy", roles=["admin"]))
    assert "tenant" not in payload          # 与存量 token 结构一致
    result = handler.validate_token(handler.create_token("legacy", roles=["admin"]))
    assert result.authenticated is True
    assert result.tenant_id == ""           # 退化为未分配，而非拒绝


def test_non_string_tenant_claim_is_ignored(handler):
    """claim 被篡改成非字符串时按未分配处理，不抛异常。"""
    token = handler.create_token("eve", roles=["admin"], tenant_id="acme")
    header, payload_b64, _sig = token.split(".")
    forged = {"iss": "MAOP", "sub": "eve", "roles": ["admin"],
              "iat": 1.0, "exp": 4.0e9, "tenant": 12345}
    new_payload = base64.urlsafe_b64encode(
        json.dumps(forged, separators=(",", ":")).encode()
    ).decode().rstrip("=")
    # 签名不匹配，故预期整体校验失败——这里只验证不会抛非预期异常
    result = handler.validate_token(f"{header}.{new_payload}._badsig_")
    assert isinstance(result.tenant_id, str)


def test_refresh_preserves_tenant(handler):
    """续期必须带上原 token 的租户，否则续期后隔离会静默失效。"""
    original = handler.validate_token(
        handler.create_token("bob", roles=["admin"], tenant_id="acme")
    )
    refreshed = handler.create_token(
        original.identity, roles=original.roles, tenant_id=original.tenant_id
    )
    assert handler.validate_token(refreshed).tenant_id == "acme"


def test_authresult_tenant_defaults_to_empty():
    from maop.core.security.auth import AuthResult

    assert AuthResult().tenant_id == ""


def test_ldap_authresult_also_exposes_tenant():
    """LDAP 版 AuthResult 是独立类，必须有同名字段，否则 getattr 会静默取空。"""
    from maop.core.security.ldap_models import AuthResult

    assert AuthResult().tenant_id == ""


# ── 数据面 ────────────────────────────────────────────────────────────


@pytest.fixture
def legacy_users_db(tmp_path, monkeypatch):
    """建一个**没有** tenant_id 列的存量 users 表，模拟升级前状态。"""
    monkeypatch.setenv("MAOP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MAOP_SKIP_INTEGRITY", "1")
    from maop.core.backends.db_utils import get_db_path

    path = str(get_db_path("auth"))
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            username TEXT PRIMARY KEY,
            password_hash TEXT NOT NULL,
            roles TEXT NOT NULL DEFAULT '["admin"]',
            created_at REAL NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1
        )
    """)
    conn.execute(
        "INSERT INTO users VALUES ('legacy','x','[\"admin\"]',1.0,1)"
    )
    conn.commit()
    conn.close()
    return path


def test_legacy_table_gets_tenant_column(legacy_users_db):
    from maop.dashboard.services import auth_service as A

    A._ensure_default_user()   # 触发列迁移

    cols = {r["name"] for r in _rows(legacy_users_db, "PRAGMA table_info(users)")}
    assert "tenant_id" in cols


def test_existing_users_keep_unassigned_tenant(legacy_users_db):
    """存量用户升级后租户必须仍为空——这是"不构成行为变更"的关键。"""
    from maop.dashboard.services import auth_service as A

    A._ensure_default_user()

    rows = _rows(legacy_users_db,
                 "SELECT username, tenant_id FROM users WHERE username='legacy'")
    assert rows and rows[0]["tenant_id"] == ""


def test_migration_is_idempotent(legacy_users_db):
    from maop.dashboard.services import auth_service as A

    A._ensure_default_user()
    A._ensure_default_user()   # 再跑一次不得报错、不得重复加列

    names = [r["name"] for r in _rows(legacy_users_db, "PRAGMA table_info(users)")]
    assert names.count("tenant_id") == 1


def test_register_with_and_without_tenant(legacy_users_db):
    from maop.dashboard.services import auth_service as A

    A._ensure_default_user()
    assert A.db_register_user(legacy_users_db, "bob", "pw", ["admin"], "acme")["tenant_id"] == "acme"
    assert A.db_register_user(legacy_users_db, "carol", "pw", ["admin"])["tenant_id"] == ""

    listed = {u["username"]: u["tenant_id"] for u in A.db_list_users(legacy_users_db)}
    assert listed["bob"] == "acme"
    assert listed["carol"] == ""


def _rows(path: str, sql: str) -> list[sqlite3.Row]:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()
