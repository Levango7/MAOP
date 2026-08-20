"""MAOP LDAP/AD — 异常类与 Pydantic 模型。

从 ``ldap_provider.py`` 拆分（Phase 3-2），集中存放：

* 异常：``LDAPConfigError`` / ``LDAPConnectionError`` / ``LDAPAuthenticationError``
* 模型：``LDAPConfig`` / ``LDAPUser`` / ``LDAPSyncResult`` /
  ``GroupRoleMapping`` / ``AuthResult``

依赖方向：``ldap_provider.py`` → ``ldap_models.py``（单向）。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

# ── 异常 ──────────────────────────────────────────────────────


class LDAPConfigError(Exception):
    """LDAP 配置错误。"""


class LDAPConnectionError(Exception):
    """LDAP 连接 / 操作错误。"""


class LDAPAuthenticationError(Exception):
    """LDAP 认证失败。"""


# ── 配置与模型 ────────────────────────────────────────────────


class LDAPConfig(BaseModel):
    """LDAP/AD 连接配置。

    Parameters
    ----------
    server_url : str
        LDAP 服务器 URL，例如 ``ldap://dc.example.com:389`` 或
        ``ldaps://dc.example.com:636``。
    bind_dn : str
        用于搜索的绑定 DN（service account）。
    bind_password : str
        绑定密码。
    user_base_dn : str
        用户搜索基 DN，例如 ``ou=users,dc=example,dc=com``。
    user_filter : str
        用户搜索过滤器，默认 ``(&(objectClass=person)(uid={username}))``。
        ``{username}`` 占位符在认证时替换。
    group_base_dn : str
        组搜索基 DN。
    group_filter : str
        组搜索过滤器。
    use_ssl : bool
        是否使用 SSL/TLS。
    use_tls : bool
        是否在明文连接上启动 STARTTLS。
    page_size : int
        分页搜索的页大小（默认 1000，AD 推荐值）。
    """

    server_url: str
    bind_dn: str = ""
    bind_password: str = ""
    user_base_dn: str = ""
    user_filter: str = "(&(objectClass=person)(uid={username}))"
    group_base_dn: str = ""
    group_filter: str = "(objectClass=groupOfNames)"
    use_ssl: bool = False
    use_tls: bool = False
    page_size: int = 1000
    #: 是否为 Active Directory（影响 user_filter 默认值与属性名）。
    is_active_directory: bool = False
    #: 增量同步属性（AD: whenChanged, OpenLDAP: modifyTimestamp）。
    modify_timestamp_attr: str = "modifyTimestamp"


class LDAPUser(BaseModel):
    """从 LDAP 同步过来的用户。"""

    dn: str                          # LDAP distinguished name
    username: str                    # MAOP 用户名（从 uid/sAMAccountName 提取）
    email: str = ""
    display_name: str = ""
    first_name: str = ""
    last_name: str = ""
    groups: list[str] = Field(default_factory=list)  # LDAP group DN 列表
    is_active: bool = True
    raw_attributes: dict[str, Any] = Field(default_factory=dict)


class LDAPSyncResult(BaseModel):
    """用户同步结果。"""

    synced: int = 0
    created: int = 0
    updated: int = 0
    deactivated: int = 0
    errors: int = 0
    error_details: list[str] = Field(default_factory=list)
    synced_users: list[LDAPUser] = Field(default_factory=list)


class GroupRoleMapping(BaseModel):
    """LDAP group → MAOP role 映射规则。"""

    #: LDAP group DN 模式（精确匹配或正则）。
    group_dn_pattern: str
    #: 是否使用正则匹配。
    use_regex: bool = False
    #: 映射到的 MAOP role 名称。
    role: str
    #: 是否为默认 role（所有同步用户都获得）。
    is_default: bool = False


class AuthResult(BaseModel):
    """LDAP 认证结果。"""

    authenticated: bool = False
    user: LDAPUser | None = None
    roles: list[str] = Field(default_factory=list)
    error: str = ""


__all__ = [
    "AuthResult",
    "GroupRoleMapping",
    "LDAPAuthenticationError",
    "LDAPConfig",
    "LDAPConfigError",
    "LDAPConnectionError",
    "LDAPSyncResult",
    "LDAPUser",
]