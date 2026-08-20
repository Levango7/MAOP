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

模块拆分（Phase 3-2）
--------------------
本模块从单文件 669 行拆分为三个文件以改善可维护性：

* :mod:`maop.core.security.ldap_models` — 异常类 + Pydantic 模型
  （``LDAPConfig`` / ``LDAPUser`` / ``LDAPSyncResult`` /
  ``GroupRoleMapping`` / ``AuthResult`` / 三个异常类）。
* :mod:`maop.core.security.ldap_search` — 用户搜索方法 Mixin
  （``LDAPSearchMixin``，含 ``search_users`` / ``_paged_search`` /
  ``_entry_to_user`` 等）。
* 本模块 — ``LDAPProvider`` 核心类（连接管理 / 同步 / 认证 / 组映射）
  + 异步包装 + 全部公共符号 re-export。

兼容性：通过 ``__all__`` re-export，``from maop.core.security.ldap_provider
import LDAPProvider, LDAPConfig, ...`` 等历史导入路径零改动。

依赖方向：``ldap_provider`` → ``ldap_search`` → ``ldap_models``（单向，无循环）。
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any

from maop.core.security.ldap_models import (
    AuthResult,
    GroupRoleMapping,
    LDAPAuthenticationError,
    LDAPConfig,
    LDAPConfigError,
    LDAPConnectionError,
    LDAPSyncResult,
    LDAPUser,
)
from maop.core.security.ldap_search import LDAPSearchMixin

logger = logging.getLogger(__name__)


# ── LDAPProvider ──────────────────────────────────────────────


class LDAPProvider(LDAPSearchMixin):
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


# ── Re-export ─────────────────────────────────────────────────
# 保持 from maop.core.security.ldap_provider import ... 历史导入路径零改动。

__all__ = [
    "AuthResult",
    "GroupRoleMapping",
    "LDAPAuthenticationError",
    "LDAPConfig",
    "LDAPConfigError",
    "LDAPConnectionError",
    "LDAPProvider",
    "LDAPSyncResult",
    "LDAPUser",
    "authenticate_async",
    "sync_users_async",
]
