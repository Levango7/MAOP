"""MAOP LDAP/AD — 用户搜索方法 Mixin。

从 ``ldap_provider.py`` 拆分（Phase 3-2），集中存放 LDAP 用户搜索
相关辅助方法：

* ``search_users`` — 公共搜索入口
* ``_default_user_attributes`` — AD / OpenLDAP 默认属性
* ``_paged_search`` / ``_legacy_paged_search`` — 分页搜索（ldap3 / python-ldap）
* ``_entry_to_user`` / ``_build_user_from_attrs`` — LDAP 条目 → :class:`LDAPUser`
* ``_attr_to_list`` — 属性值归一化

设计为 Mixin：``LDAPProvider(LDAPSearchMixin)`` 通过组合获得这些方法。
Mixin 方法访问宿主属性 ``self.config`` / ``self._connect()`` /
``self._close()``，mypy 无法静态推断 —— 在 ``pyproject.toml`` 中对
本模块豁免 ``attr-defined``（与 T2 / P0-3 同一模式）。

依赖方向：``ldap_provider.py`` → ``ldap_search.py`` → ``ldap_models.py``（单向）。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from maop.core.security.ldap_models import (
    LDAPConnectionError,
    LDAPUser,
)

if TYPE_CHECKING:
    from maop.core.security.ldap_models import LDAPConfig

logger = logging.getLogger(__name__)


class LDAPSearchMixin:
    """LDAP 用户搜索方法 Mixin。

    宿主类需提供：
    * ``self.config : LDAPConfig``
    * ``self._connect() -> Any``
    * ``self._close(conn: Any) -> None``
    """

    if TYPE_CHECKING:
        # 宿主类（LDAPProvider）提供的属性与方法 —— 仅用于类型检查
        config: LDAPConfig
        _connect: Callable[..., Any]
        _close: Callable[..., None]

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


__all__ = ["LDAPSearchMixin"]