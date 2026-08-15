"""MAOP LDAP/Active Directory Provider — 用户同步、认证、组映射。

提供与 LDAP/AD 目录服务的集成，支持：

1. **用户同步**（``sync_users``）：从 LDAP/AD 拉取用户列表并写入
   MAOP 用户表，支持增量同步（基于 ``modifyTimestamp`` 过滤）。
2. **认证**（``authenticate``）：通过 LDAP bind 操作验证用户凭据。
3. **组映射**（``map_groups_to_roles``）：将 LDAP group DN 映射到
   MAOP role，支持正则 / 精确匹配。

设计要点
--------
* **零运行时依赖**：优先使用 ``ldap3``（纯 Python，推荐）；若未安装，
  回退到 ``ldap``（python-ldap，Unix only）；若两者都未安装，模块
  仍可导入，但 ``LDAPProvider`` 实例化时抛出 ``LDAPConfigError``。
* **可测试性**：所有 LDAP 操作通过 ``_connect`` 工厂获取连接，
  测试时可注入 mock connection。
* **安全**：密码从不记录到日志；bind DN 与密码存储在配置中
  （生产环境应使用 vault）。
* **异步**：``sync_users_async`` / ``authenticate_async`` 通过
  ``asyncio.to_thread`` 包装同步调用。
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


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


# ── LDAPProvider ──────────────────────────────────────────────


class LDAPProvider:
    """LDAP/Active Directory 集成提供者。

    Parameters
    ----------
    config : LDAPConfig
        连接配置。
    group_mappings : list[GroupRoleMapping]
        LDAP group → MAOP role 映射规则列表。
    connection_factory : callable, optional
        自定义连接工厂，签名 ``(config) -> connection``。
        用于测试注入 mock。若为 ``None``，使用默认工厂
        （需要 ``ldap3`` 或 ``ldap`` 库）。
    """

    def __init__(
        self,
        config: LDAPConfig,
        *,
        group_mappings: list[GroupRoleMapping] | None = None,
        connection_factory: Any = None,
    ) -> None:
        self.config = config
        self.group_mappings = group_mappings or []
        self._connection_factory = connection_factory
        # 验证映射规则
        for m in self.group_mappings:
            if m.use_regex:
                try:
                    re.compile(m.group_dn_pattern)
                except re.error as exc:
                    raise LDAPConfigError(
                        f"invalid regex in mapping {m.group_dn_pattern!r}: {exc}"
                    ) from exc

    # ── 连接管理 ─────────────────────────────────────────────

    def _connect(self) -> Any:
        """建立 LDAP 连接并执行绑定。

        若 ``connection_factory`` 已注入（测试场景），直接调用；
        否则尝试使用 ``ldap3`` 或 ``ldap`` 库。
        """
        if self._connection_factory is not None:
            conn = self._connection_factory(self.config)
            return conn
        return self._default_connect()

    def _default_connect(self) -> Any:
        """使用 ldap3 或 ldap 库建立连接。"""
        try:
            import ssl as ssl_module

            from ldap3 import ALL, Connection, Server, Tls
        except ImportError:
            try:
                return self._connect_legacy_ldap()
            except ImportError as exc:
                raise LDAPConnectionError(
                    "no LDAP library available; install 'ldap3' (recommended) "
                    "or 'python-ldap'"
                ) from exc

        server_kwargs: dict[str, Any] = {"get_info": ALL}
        if self.config.use_ssl:
            server_kwargs["use_ssl"] = True
            server_kwargs["tls"] = Tls(
                validate=ssl_module.CERT_REQUIRED,
                version=ssl_module.PROTOCOL_TLS_CLIENT,
            )
        server = Server(self.config.server_url, **server_kwargs)
        conn = Connection(
            server,
            user=self.config.bind_dn,
            password=self.config.bind_password,
            auto_bind=True,
            read_only=True,
        )
        if self.config.use_tls and not self.config.use_ssl:
            conn.start_tls()
        return conn

    def _connect_legacy_ldap(self) -> Any:
        """使用 python-ldap 建立连接（Unix only）。"""
        import ldap as legacy_ldap

        conn = legacy_ldap.initialize(self.config.server_url)
        if self.config.use_tls:
            conn.start_tls_s()
        if self.config.bind_dn:
            conn.simple_bind_s(self.config.bind_dn, self.config.bind_password)
        return conn

    # ── 用户搜索 ─────────────────────────────────────────────

    def search_users(
        self,
        filter_str: str | None = None,
        *,
        attributes: list[str] | None = None,
    ) -> list[LDAPUser]:
        """搜索 LDAP 用户。

        Parameters
        ----------
        filter_str : str | None
            LDAP 搜索过滤器。若为 ``None``，使用 ``config.user_filter``
            并移除 ``{username}`` 占位符（搜索所有用户）。
        attributes : list[str] | None
            要取回的属性列表。``None`` 表示全部。
        """
        if filter_str is None:
            # 移除 username 占位符以搜索所有用户
            filter_str = self.config.user_filter.replace(
                "(uid={username})", "(uid=*)",
            ).replace("{username}", "*")

        default_attrs = self._default_user_attributes()
        attrs = attributes or default_attrs

        conn = self._connect()
        try:
            entries = self._paged_search(
                conn,
                self.config.user_base_dn,
                filter_str,
                attrs,
            )
        finally:
            self._close(conn)

        users: list[LDAPUser] = []
        for entry in entries:
            try:
                user = self._entry_to_user(entry)
                users.append(user)
            except Exception as exc:
                logger.warning("[ldap] failed to parse entry: %s", exc)
        return users

    def _default_user_attributes(self) -> list[str]:
        """根据 AD / OpenLDAP 返回默认属性列表。"""
        if self.config.is_active_directory:
            return [
                "sAMAccountName", "userPrincipalName", "mail",
                "displayName", "givenName", "sn",
                "memberOf", "userAccountControl", "whenChanged",
            ]
        return [
            "uid", "mail", "cn", "givenName", "sn",
            "memberOf", "modifyTimestamp",
        ]

    def _paged_search(
        self,
        conn: Any,
        base_dn: str,
        filter_str: str,
        attributes: list[str],
    ) -> list[Any]:
        """执行分页搜索（兼容 ldap3 与 mock）。"""
        # ldap3 风格
        if hasattr(conn, "search"):
            entries: list[Any] = []
            conn.search(
                search_base=base_dn,
                search_filter=filter_str,
                attributes=attributes,
                paged_size=self.config.page_size,
            )
            # ldap3 的响应可能分页，循环读取
            entries = list(conn.entries)
            # 处理分页 cookie
            while hasattr(conn, "result") and conn.result.get("controls"):
                cookie = (
                    conn.result["controls"]
                    .get("1.2.840.113556.1.4.319", [None, None, None])[2]
                )
                if not cookie:
                    break
                conn.search(
                    search_base=base_dn,
                    search_filter=filter_str,
                    attributes=attributes,
                    paged_size=self.config.page_size,
                    paged_cookie=cookie,
                )
                entries.extend(conn.entries)
            return entries
        # python-ldap 风格
        if hasattr(conn, "search_ext"):
            return self._legacy_paged_search(
                conn, base_dn, filter_str, attributes,
            )
        # mock 风格：直接调用
        result = conn.search(base_dn, filter_str, attributes)
        return list(result) if result else []

    def _legacy_paged_search(
        self,
        conn: Any,
        base_dn: str,
        filter_str: str,
        attributes: list[str],
    ) -> list[Any]:
        """python-ldap 分页搜索。"""
        import ldap as legacy_ldap

        results: list[Any] = []
        page_cookie = ""
        while True:
            msg_id = conn.search_ext(
                base_dn, legacy_ldap.SCOPE_SUBTREE, filter_str,
                attrlist=attributes,
                serverctrls=[
                    legacy_ldap.controls.SimplePagedResultsControl(
                        True, self.config.page_size, page_cookie,
                    ),
                ],
            )
            _rtype, rdata, _rmsgid, rctrls = conn.result3(msg_id)
            results.extend(rdata)
            # 解析分页 cookie
            page_ctrls = [
                c for c in rctrls
                if c.controlType == legacy_ldap.LIBLDAP_CONTROL_PAGEDRESULTS
            ]
            if not page_ctrls:
                break
            _, _, page_cookie = page_ctrls[0].controlValue
            if not page_cookie:
                break
        return results

    def _entry_to_user(self, entry: Any) -> LDAPUser:
        """将 LDAP 条目转换为 :class:`LDAPUser`。"""
        # ldap3 Entry 风格
        if hasattr(entry, "entry_dn") and hasattr(entry, "entry_attributes"):
            dn = entry.entry_dn
            raw: dict[str, Any] = {}
            for attr in entry.entry_attributes:
                try:
                    values = entry[attr].values
                    raw[attr] = list(values) if values else []
                except Exception:
                    raw[attr] = []
            return self._build_user_from_attrs(dn, raw)

        # python-ldap / mock 风格：(dn, attrs_dict)
        if isinstance(entry, tuple) and len(entry) == 2:
            dn, attrs = entry
            raw = {
                k: (list(v) if isinstance(v, (list, tuple)) else [v])
                for k, v in (attrs or {}).items()
            }
            return self._build_user_from_attrs(dn, raw)

        # mock 对象风格：有 .dn 与 .attributes
        if hasattr(entry, "dn") and hasattr(entry, "attributes"):
            return self._build_user_from_attrs(
                entry.dn, dict(entry.attributes),
            )

        raise LDAPConnectionError(f"unsupported entry type: {type(entry)}")

    def _build_user_from_attrs(self, dn: str, attrs: dict[str, Any]) -> LDAPUser:
        """从属性字典构造 :class:`LDAPUser`。"""
        def _first(key: str, *aliases: str) -> str:
            for k in (key, *aliases):
                if attrs.get(k):
                    val = attrs[k][0] if isinstance(attrs[k], list) else attrs[k]
                    return val.decode() if isinstance(val, bytes) else str(val)
            return ""

        if self.config.is_active_directory:
            username = _first("sAMAccountName")
            email = _first("userPrincipalName", "mail")
            display_name = _first("displayName", "cn")
            first_name = _first("givenName")
            last_name = _first("sn")
            groups = self._attr_to_list(attrs.get("memberOf", []))
            # AD userAccountControl: bit 1 (ACCOUNTDISABLE) 表示禁用
            uac = _first("userAccountControl")
            is_active = True
            if uac:
                try:
                    is_active = not (int(uac) & 0x2)
                except ValueError:
                    pass
        else:
            username = _first("uid", "cn")
            email = _first("mail")
            display_name = _first("cn")
            first_name = _first("givenName")
            last_name = _first("sn")
            groups = self._attr_to_list(attrs.get("memberOf", []))
            is_active = True

        return LDAPUser(
            dn=dn,
            username=username,
            email=email,
            display_name=display_name,
            first_name=first_name,
            last_name=last_name,
            groups=groups,
            is_active=is_active,
            raw_attributes={
                k: [v.decode() if isinstance(v, bytes) else v for v in val]
                if isinstance(val, list)
                else val
                for k, val in attrs.items()
            },
        )

    @staticmethod
    def _attr_to_list(value: Any) -> list[str]:
        """将 LDAP 属性值转换为字符串列表。"""
        if not value:
            return []
        if isinstance(value, list):
            return [
                v.decode() if isinstance(v, bytes) else str(v) for v in value
            ]
        if isinstance(value, bytes):
            return [value.decode()]
        return [str(value)]

    # ── 用户同步 ─────────────────────────────────────────────

    def sync_users(
        self,
        *,
        since: datetime | None = None,
        user_store: Any = None,
    ) -> LDAPSyncResult:
        """从 LDAP 同步用户到 MAOP。

        Parameters
        ----------
        since : datetime | None
            若提供，仅同步 ``modifyTimestamp`` >= since 的用户（增量同步）。
        user_store : Any, optional
            用户存储后端，需实现 ``upsert_user(user: LDAPUser) -> str``
            （返回 ``"created"`` 或 ``"updated"``）和
            ``deactivate_user(dn: str) -> bool``。
            若为 ``None``，仅返回同步结果不持久化。
        """
        result = LDAPSyncResult()
        try:
            filter_str = self._build_sync_filter(since)
            users = self.search_users(filter_str)
        except LDAPConnectionError as exc:
            result.errors = 1
            result.error_details.append(f"search failed: {exc}")
            logger.error("[ldap] sync search failed: %s", exc)
            return result

        result.synced_users = users
        for user in users:
            try:
                if user_store is not None:
                    action = user_store.upsert_user(user)
                    if action == "created":
                        result.created += 1
                    elif action == "updated":
                        result.updated += 1
                result.synced += 1
            except Exception as exc:
                result.errors += 1
                result.error_details.append(
                    f"upsert {user.dn}: {exc}",
                )
                logger.warning("[ldap] upsert failed for %s: %s", user.dn, exc)

        # 停用不在 LDAP 中的用户（仅全量同步时执行）
        if user_store is not None and since is None:
            try:
                active_dns = {u.dn for u in users}
                if hasattr(user_store, "list_active_dns"):
                    for dn in user_store.list_active_dns():
                        if dn not in active_dns:  # noqa: SIM102
                            if user_store.deactivate_user(dn):
                                result.deactivated += 1
            except Exception as exc:
                logger.warning("[ldap] deactivation sweep failed: %s", exc)

        logger.info(
            "[ldap] sync complete: %d synced, %d created, %d updated, "
            "%d deactivated, %d errors",
            result.synced, result.created, result.updated,
            result.deactivated, result.errors,
        )
        return result

    def _build_sync_filter(self, since: datetime | None) -> str:
        """构建同步搜索过滤器（含增量时间过滤）。"""
        base_filter = self.config.user_filter.replace(
            "(uid={username})", "(uid=*)",
        ).replace("{username}", "*")
        if since is None:
            return base_filter
        # 格式化为 LDAP 通用时间格式 YYYYMMDDHHMMSSZ
        ts = since.strftime("%Y%m%d%H%M%SZ")
        attr = self.config.modify_timestamp_attr
        time_filter = f"({attr}>={ts})"
        # 合并过滤器：(&(base)(time))
        if base_filter.startswith("&"):
            return f"(&{base_filter[2:-1]}{time_filter})"
        return f"(&{base_filter}{time_filter})"

    # ── 认证 ─────────────────────────────────────────────────

    def authenticate(
        self,
        username: str,
        password: str,
        *,
        user_store: Any = None,
    ) -> AuthResult:
        """通过 LDAP bind 验证用户凭据。

        流程：
        1. 用 service account 搜索用户 DN。
        2. 用用户 DN + 密码执行 bind。
        3. 若成功，获取用户组并映射为 MAOP roles。

        Parameters
        ----------
        username, password : str
            用户凭据。密码从不记录到日志。
        user_store : Any, optional
            若提供且认证成功，调用 ``user_store.update_last_login(dn)``
            记录登录时间。
        """
        if not username or not password:
            return AuthResult(error="username and password are required")

        try:
            # 1. 搜索用户 DN
            filter_str = self.config.user_filter.replace("{username}", username)
            users = self.search_users(filter_str)
            if not users:
                return AuthResult(error=f"user {username!r} not found in LDAP")
            if len(users) > 1:
                logger.warning(
                    "[ldap] multiple entries for %s, using first", username,
                )
            user = users[0]
            if not user.is_active:
                return AuthResult(error=f"user {username!r} is disabled in LDAP")

            # 2. 用户 bind 验证
            if not self._bind_as_user(user.dn, password):
                return AuthResult(error="invalid credentials")

            # 3. 组 → role 映射
            roles = self.map_groups_to_roles(user.groups)

            # 4. 记录登录
            if user_store is not None and hasattr(user_store, "update_last_login"):
                try:
                    user_store.update_last_login(user.dn)
                except Exception:
                    logger.debug("[ldap] update_last_login failed", exc_info=True)

            return AuthResult(
                authenticated=True, user=user, roles=roles,
            )
        except LDAPConnectionError as exc:
            logger.error("[ldap] authenticate failed for %s: %s", username, exc)
            return AuthResult(error=str(exc))

    def _bind_as_user(self, user_dn: str, password: str) -> bool:
        """用用户凭据执行 LDAP bind 验证。

        使用独立的连接，避免污染搜索连接。
        """
        # 若注入了 connection_factory，使用 mock 风格验证
        if self._connection_factory is not None:
            try:
                conn = self._connection_factory(self.config)
                if hasattr(conn, "bind_as"):
                    return bool(conn.bind_as(user_dn, password))
                if hasattr(conn, "simple_bind_s"):
                    try:
                        conn.simple_bind_s(user_dn, password)
                        return True
                    except Exception:
                        return False
                # mock 默认成功
                return True
            except Exception:
                return False

        # 真实 LDAP
        try:
            from ldap3 import Connection, Server
        except ImportError:
            try:
                import ldap as legacy_ldap
                conn = legacy_ldap.initialize(self.config.server_url)
                if self.config.use_tls:
                    conn.start_tls_s()
                conn.simple_bind_s(user_dn, password)
                conn.unbind_s()
                return True
            except Exception:
                return False

        try:
            server = Server(self.config.server_url, use_ssl=self.config.use_ssl)
            conn = Connection(
                server, user=user_dn, password=password,
                auto_bind=True, read_only=True,
            )
            conn.unbind()
            return True
        except Exception:
            return False

    # ── 组 → role 映射 ──────────────────────────────────────

    def map_groups_to_roles(self, group_dns: list[str]) -> list[str]:
        """将 LDAP group DN 列表映射为 MAOP role 列表。

        映射规则按 ``group_mappings`` 顺序匹配；一个 group 可匹配多个规则，
        一个 role 只出现一次。
        """
        roles: list[str] = []
        seen: set[str] = set()

        # 默认 role（不依赖 group）
        for mapping in self.group_mappings:
            if mapping.is_default and mapping.role not in seen:
                roles.append(mapping.role)
                seen.add(mapping.role)

        # group-based role
        for group_dn in group_dns:
            for mapping in self.group_mappings:
                if mapping.is_default:
                    continue
                if self._match_group(group_dn, mapping):  # noqa: SIM102
                    if mapping.role not in seen:
                        roles.append(mapping.role)
                        seen.add(mapping.role)

        return roles

    @staticmethod
    def _match_group(group_dn: str, mapping: GroupRoleMapping) -> bool:
        """检查 group_dn 是否匹配映射规则。"""
        if mapping.use_regex:
            return bool(re.search(mapping.group_dn_pattern, group_dn, re.IGNORECASE))
        return group_dn.lower() == mapping.group_dn_pattern.lower()

    def add_group_mapping(self, mapping: GroupRoleMapping) -> None:
        """添加一条 group → role 映射。"""
        if mapping.use_regex:
            try:
                re.compile(mapping.group_dn_pattern)
            except re.error as exc:
                raise LDAPConfigError(
                    f"invalid regex: {mapping.group_dn_pattern!r}: {exc}"
                ) from exc
        self.group_mappings.append(mapping)

    # ── 辅助 ─────────────────────────────────────────────────

    @staticmethod
    def _close(conn: Any) -> None:
        """安全关闭连接。"""
        try:
            if hasattr(conn, "unbind"):
                conn.unbind()
            elif hasattr(conn, "unbind_s"):
                conn.unbind_s()
            elif hasattr(conn, "close"):
                conn.close()
        except Exception:
            logger.debug("[ldap] connection close failed", exc_info=True)

    def test_connection(self) -> bool:
        """测试 LDAP 连接是否可用（bind 成功）。"""
        try:
            conn = self._connect()
            self._close(conn)
            return True
        except Exception as exc:
            logger.warning("[ldap] connection test failed: %s", exc)
            return False


# ── 异步包装 ──────────────────────────────────────────────────


async def sync_users_async(
    provider: LDAPProvider,
    *,
    since: datetime | None = None,
    user_store: Any = None,
) -> LDAPSyncResult:
    """``sync_users`` 的异步包装。"""
    import asyncio
    return await asyncio.to_thread(
        provider.sync_users, since=since, user_store=user_store,
    )


async def authenticate_async(
    provider: LDAPProvider,
    username: str,
    password: str,
    *,
    user_store: Any = None,
) -> AuthResult:
    """``authenticate`` 的异步包装。"""
    import asyncio
    return await asyncio.to_thread(
        provider.authenticate, username, password, user_store=user_store,
    )