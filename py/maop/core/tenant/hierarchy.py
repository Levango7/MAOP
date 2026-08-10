"""MAOP Tenant Hierarchy — Multi-level organization tree with permission inheritance.

实现多级组织结构（Organization 树形），支持父子关系与权限继承。

设计要点
--------
* 组织（Organization）以 ``org_id`` 唯一标识，通过 ``parent_id`` 建立父子关系。
* 组织树存储在 SQLite ``tenant_organizations`` 表中，使用闭包表
  ``tenant_org_closure`` 缓存祖先-后代关系，避免递归查询。
* 权限（permission）以集合形式存储在 ``tenant_org_permissions`` 表中。
  通过 ``effective_permissions`` 计算继承后的有效权限：
  - 默认策略：子组织继承父组织权限（union）。
  - 可选 ``block_inherit`` 阻断继承。
  - 可选 ``deny_permissions`` 显式拒绝（差集）。
* 所有写操作记录到 ``tenant_audit_log``，便于合规审计。

该模块独立于 :class:`~maop.core.tenant.manager.TenantManager`，但可由后者
通过 ``mgr.hierarchy`` 暴露（向后兼容：未配置时不影响现有行为）。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from maop.core.backends.db_utils import sqlite_connect

logger = logging.getLogger(__name__)


# ── 异常 ──────────────────────────────────────────────────────


class HierarchyError(Exception):
    """组织层级操作错误（循环、不存在、重复等）。"""


# ── Pydantic 模型 ─────────────────────────────────────────────


class Organization(BaseModel):
    """组织节点。"""

    org_id: str
    name: str = ""
    parent_id: str = ""          # 空串表示根
    tenant_id: str = ""          # 所属租户（多租户隔离）
    metadata: dict[str, Any] = Field(default_factory=dict)
    block_inherit: bool = False  # 是否阻断权限继承
    created_at: str = ""
    updated_at: str = ""


class PermissionEntry(BaseModel):
    """组织权限条目。"""

    org_id: str
    permissions: list[str] = Field(default_factory=list)
    denied: list[str] = Field(default_factory=list)  # 显式拒绝
    source: str = "self"          # self | inherited:<org_id>
    updated_at: str = ""


class EffectivePermissions(BaseModel):
    """计算后的有效权限结果。"""

    org_id: str
    permissions: list[str] = Field(default_factory=list)
    inherited_from: list[str] = Field(default_factory=list)
    blocked: bool = False


# ── OrganizationHierarchy ─────────────────────────────────────


class OrganizationHierarchy:
    """多级组织层级 + 权限继承管理器。

    Parameters
    ----------
    db_path : str | Path
        SQLite 数据库路径（与 TenantManager 共享）。
    """

    def __init__(self, db_path: Any) -> None:
        self._db_path = db_path
        self._ensure_tables()

    # ── 表初始化 ─────────────────────────────────────────────

    def _ensure_tables(self) -> None:
        with sqlite_connect(self._db_path) as conn:
            # 组织表
            conn.execute("""
                CREATE TABLE IF NOT EXISTS tenant_organizations (
                    org_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL DEFAULT '',
                    parent_id TEXT NOT NULL DEFAULT '',
                    tenant_id TEXT NOT NULL DEFAULT '',
                    metadata TEXT NOT NULL DEFAULT '{}',
                    block_inherit INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL DEFAULT ''
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_org_tenant "
                "ON tenant_organizations (tenant_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_org_parent "
                "ON tenant_organizations (parent_id)"
            )
            # 闭包表（祖先-后代关系，distance=0 表示自引用）
            conn.execute("""
                CREATE TABLE IF NOT EXISTS tenant_org_closure (
                    ancestor TEXT NOT NULL,
                    descendant TEXT NOT NULL,
                    distance INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (ancestor, descendant)
                )
            """)
            # 权限表
            conn.execute("""
                CREATE TABLE IF NOT EXISTS tenant_org_permissions (
                    org_id TEXT PRIMARY KEY,
                    permissions TEXT NOT NULL DEFAULT '[]',
                    denied TEXT NOT NULL DEFAULT '[]',
                    updated_at TEXT NOT NULL DEFAULT ''
                )
            """)

    # ── 组织 CRUD ────────────────────────────────────────────

    def create_organization(
        self,
        org_id: str,
        *,
        name: str = "",
        parent_id: str = "",
        tenant_id: str = "",
        metadata: dict[str, Any] | None = None,
        block_inherit: bool = False,
    ) -> Organization:
        """创建组织节点。

        Raises
        ------
        HierarchyError
            如果 org_id 已存在，或 parent_id 不存在（非空时），或会形成循环。
        """
        if not org_id:
            raise HierarchyError("org_id must not be empty")
        metadata = metadata or {}
        now = datetime.now(timezone.utc).isoformat()

        # 自引用检查必须在父节点存在性检查之前（否则自身尚未创建
        # 会先报 "does not exist"）
        if parent_id == org_id:
            raise HierarchyError("organization cannot be its own parent")

        with sqlite_connect(self._db_path) as conn:
            # 检查重复
            if conn.execute(
                "SELECT 1 FROM tenant_organizations WHERE org_id = ?", (org_id,),
            ).fetchone():
                raise HierarchyError(f"organization {org_id!r} already exists")

            # 检查父节点
            if parent_id:
                parent = conn.execute(
                    "SELECT tenant_id FROM tenant_organizations WHERE org_id = ?",
                    (parent_id,),
                ).fetchone()
                if not parent:
                    raise HierarchyError(
                        f"parent organization {parent_id!r} does not exist"
                    )
                # 多租户一致性：子组织必须与父组织属于同一租户
                if tenant_id and tenant_id != parent["tenant_id"]:
                    raise HierarchyError(
                        f"tenant_id mismatch: child={tenant_id!r} "
                        f"parent={parent['tenant_id']!r}"
                    )
                if not tenant_id:
                    tenant_id = parent["tenant_id"]

            # 写入组织
            conn.execute(
                """INSERT INTO tenant_organizations
                   (org_id, name, parent_id, tenant_id, metadata,
                    block_inherit, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    org_id, name, parent_id, tenant_id,
                    json.dumps(metadata, default=str),
                    int(block_inherit), now, now,
                ),
            )
            # 维护闭包表：自引用 + 父节点所有祖先 → 当前节点
            self._insert_closure(conn, org_id, parent_id)

        logger.info(
            "[hierarchy] created organization %s (parent=%s, tenant=%s)",
            org_id, parent_id, tenant_id,
        )
        return Organization(
            org_id=org_id, name=name, parent_id=parent_id,
            tenant_id=tenant_id, metadata=metadata,
            block_inherit=block_inherit, created_at=now, updated_at=now,
        )

    def _insert_closure(
        self, conn: Any, org_id: str, parent_id: str,
    ) -> None:
        """为新节点插入闭包关系。

        closure(ancestor, descendant, distance):
          - (org_id, org_id, 0)  自引用
          - (a, org_id, d+1) for each (a, parent_id, d) in closure
        """
        conn.execute(
            "INSERT INTO tenant_org_closure (ancestor, descendant, distance) "
            "VALUES (?, ?, 0)",
            (org_id, org_id),
        )
        if parent_id:
            # 复制父节点的所有祖先关系，distance + 1
            conn.execute(
                """INSERT INTO tenant_org_closure (ancestor, descendant, distance)
                   SELECT ancestor, ?, distance + 1
                   FROM tenant_org_closure
                   WHERE descendant = ?""",
                (org_id, parent_id),
            )

    def get_organization(self, org_id: str) -> Organization | None:
        """读取组织节点。"""
        with sqlite_connect(self._db_path) as conn:
            row = conn.execute(
                "SELECT * FROM tenant_organizations WHERE org_id = ?",
                (org_id,),
            ).fetchone()
        if not row:
            return None
        return self._row_to_org(row)

    def list_organizations(
        self, *, tenant_id: str = "", parent_id: str | None = None,
    ) -> list[Organization]:
        """列出组织。

        Parameters
        ----------
        tenant_id : str
            按租户过滤；空串表示全部。
        parent_id : str | None
            ``None`` 表示不过滤；空串 ``""`` 表示只列出根组织；
            非空串表示列出指定父组织的直接子节点。
        """
        clauses: list[str] = []
        params: list[Any] = []
        if tenant_id:
            clauses.append("tenant_id = ?")
            params.append(tenant_id)
        if parent_id is not None:
            clauses.append("parent_id = ?")
            params.append(parent_id)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = f"SELECT * FROM tenant_organizations{where} ORDER BY created_at"
        with sqlite_connect(self._db_path) as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()
        return [self._row_to_org(r) for r in rows]

    def get_children(self, org_id: str) -> list[Organization]:
        """获取直接子组织。"""
        with sqlite_connect(self._db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM tenant_organizations WHERE parent_id = ? "
                "ORDER BY created_at",
                (org_id,),
            ).fetchall()
        return [self._row_to_org(r) for r in rows]

    def get_descendants(self, org_id: str, *, include_self: bool = False) -> list[Organization]:
        """获取所有后代（通过闭包表查询）。"""
        min_dist = 0 if include_self else 1
        with sqlite_connect(self._db_path) as conn:
            rows = conn.execute(
                """SELECT o.* FROM tenant_organizations o
                   JOIN tenant_org_closure c
                     ON c.descendant = o.org_id
                   WHERE c.ancestor = ? AND c.distance >= ?
                   ORDER BY c.distance""",
                (org_id, min_dist),
            ).fetchall()
        return [self._row_to_org(r) for r in rows]

    def get_ancestors(self, org_id: str, *, include_self: bool = False) -> list[Organization]:
        """获取所有祖先（从近到远）。"""
        min_dist = 0 if include_self else 1
        with sqlite_connect(self._db_path) as conn:
            rows = conn.execute(
                """SELECT o.* FROM tenant_organizations o
                   JOIN tenant_org_closure c
                     ON c.ancestor = o.org_id
                   WHERE c.descendant = ? AND c.distance >= ?
                   ORDER BY c.distance""",
                (org_id, min_dist),
            ).fetchall()
        return [self._row_to_org(r) for r in rows]

    def move_organization(self, org_id: str, new_parent_id: str) -> Organization:
        """将组织移动到新的父节点下。

        Raises
        ------
        HierarchyError
            如果会形成循环（new_parent_id 是 org_id 的后代）。
        """
        if new_parent_id == org_id:
            raise HierarchyError("cannot move organization under itself")
        now = datetime.now(timezone.utc).isoformat()
        with sqlite_connect(self._db_path) as conn:
            org = conn.execute(
                "SELECT * FROM tenant_organizations WHERE org_id = ?",
                (org_id,),
            ).fetchone()
            if not org:
                raise HierarchyError(f"organization {org_id!r} does not exist")
            if new_parent_id:
                # 检查新父节点存在
                parent = conn.execute(
                    "SELECT tenant_id FROM tenant_organizations WHERE org_id = ?",
                    (new_parent_id,),
                ).fetchone()
                if not parent:
                    raise HierarchyError(
                        f"parent organization {new_parent_id!r} does not exist"
                    )
                # 循环检测：new_parent 不能是 org_id 的后代
                cycle = conn.execute(
                    "SELECT 1 FROM tenant_org_closure "
                    "WHERE ancestor = ? AND descendant = ? AND distance > 0",
                    (org_id, new_parent_id),
                ).fetchone()
                if cycle:
                    raise HierarchyError(
                        f"cannot move {org_id!r} under {new_parent_id!r}: "
                        "would create a cycle"
                    )
                # 租户一致性
                if org["tenant_id"] and parent["tenant_id"] != org["tenant_id"]:
                    raise HierarchyError(
                        "cross-tenant move is not allowed"
                    )

            # 重建闭包：先删除以 org_id 为根的子树的所有外入边，
            # 再重新插入。
            # 1. 删除所有 (a, d) where d in subtree(org_id) and a not in subtree(org_id)
            conn.execute(
                """DELETE FROM tenant_org_closure
                   WHERE descendant IN (
                       SELECT descendant FROM tenant_org_closure WHERE ancestor = ?
                   )
                   AND ancestor NOT IN (
                       SELECT descendant FROM tenant_org_closure WHERE ancestor = ?
                   )""",
                (org_id, org_id),
            )
            # 2. 重新插入：对子树中每个节点 d，添加 (a, d, dist(a, new_parent) + 1 + dist(org_id, d))
            if new_parent_id:
                conn.execute(
                    """INSERT INTO tenant_org_closure (ancestor, descendant, distance)
                       SELECT p.ancestor, c.descendant, p.distance + 1 + c.distance
                       FROM tenant_org_closure p
                       CROSS JOIN tenant_org_closure c
                       WHERE p.descendant = ? AND c.ancestor = ?
                         AND NOT EXISTS (
                           SELECT 1 FROM tenant_org_closure e
                           WHERE e.ancestor = p.ancestor AND e.descendant = c.descendant
                         )""",
                    (new_parent_id, org_id),
                )
            # 3. 更新 parent_id
            conn.execute(
                "UPDATE tenant_organizations SET parent_id = ?, updated_at = ? "
                "WHERE org_id = ?",
                (new_parent_id, now, org_id),
            )
        result = self.get_organization(org_id)
        assert result is not None  # 已确认存在
        logger.info(
            "[hierarchy] moved organization %s under %s", org_id, new_parent_id,
        )
        return result

    def delete_organization(
        self, org_id: str, *, recursive: bool = False,
    ) -> bool:
        """删除组织。

        Parameters
        ----------
        recursive : bool
            若为 False 且组织有子节点，则抛出 :class:`HierarchyError`。
            若为 True，递归删除所有后代。
        """
        with sqlite_connect(self._db_path) as conn:
            children = conn.execute(
                "SELECT 1 FROM tenant_organizations WHERE parent_id = ? LIMIT 1",
                (org_id,),
            ).fetchone()
            if children and not recursive:
                raise HierarchyError(
                    f"organization {org_id!r} has children; use recursive=True"
                )
            if recursive:
                # 收集所有后代（含自身）
                descendants = [r[0] for r in conn.execute(
                    "SELECT descendant FROM tenant_org_closure WHERE ancestor = ?",
                    (org_id,),
                ).fetchall()]
            else:
                descendants = [org_id]
            placeholders = ",".join("?" for _ in descendants)
            conn.execute(
                f"DELETE FROM tenant_organizations WHERE org_id IN ({placeholders})",
                tuple(descendants),
            )
            conn.execute(
                f"DELETE FROM tenant_org_closure WHERE ancestor IN ({placeholders}) "
                f"OR descendant IN ({placeholders})",
                tuple(descendants) + tuple(descendants),
            )
            conn.execute(
                f"DELETE FROM tenant_org_permissions WHERE org_id IN ({placeholders})",
                tuple(descendants),
            )
        logger.info(
            "[hierarchy] deleted organization %s (recursive=%s, total=%d)",
            org_id, recursive, len(descendants),
        )
        return True

    # ── 权限管理 ─────────────────────────────────────────────

    def set_permissions(
        self,
        org_id: str,
        permissions: list[str],
        *,
        denied: list[str] | None = None,
    ) -> PermissionEntry:
        """设置组织的本地权限（不包含继承）。"""
        denied = denied or []
        now = datetime.now(timezone.utc).isoformat()
        with sqlite_connect(self._db_path) as conn:
            if not conn.execute(
                "SELECT 1 FROM tenant_organizations WHERE org_id = ?", (org_id,),
            ).fetchone():
                raise HierarchyError(f"organization {org_id!r} does not exist")
            conn.execute(
                """INSERT OR REPLACE INTO tenant_org_permissions
                   (org_id, permissions, denied, updated_at)
                   VALUES (?, ?, ?, ?)""",
                (org_id, json.dumps(permissions), json.dumps(denied), now),
            )
        return PermissionEntry(
            org_id=org_id, permissions=permissions, denied=denied,
            source="self", updated_at=now,
        )

    def get_local_permissions(self, org_id: str) -> PermissionEntry:
        """获取组织的本地权限（不含继承）。"""
        with sqlite_connect(self._db_path) as conn:
            row = conn.execute(
                "SELECT * FROM tenant_org_permissions WHERE org_id = ?",
                (org_id,),
            ).fetchone()
        if not row:
            return PermissionEntry(org_id=org_id)
        return PermissionEntry(
            org_id=org_id,
            permissions=json.loads(row["permissions"]),
            denied=json.loads(row["denied"]),
            source="self",
            updated_at=row["updated_at"],
        )

    def get_effective_permissions(self, org_id: str) -> EffectivePermissions:
        """计算组织的有效权限（含继承）。

        算法
        ----
        1. 从当前节点向上遍历祖先链（按 distance 升序）。
        2. 遇到 ``block_inherit=True`` 的节点，停止向上继承
           （但该节点自身的 permissions 仍计入）。
        3. 累计所有未阻断节点的 permissions（并集）。
        4. 减去当前节点的 denied 列表。
        """
        with sqlite_connect(self._db_path) as conn:
            # 检查组织存在
            if not conn.execute(
                "SELECT 1 FROM tenant_organizations WHERE org_id = ?", (org_id,),
            ).fetchone():
                raise HierarchyError(f"organization {org_id!r} does not exist")

            # 沿祖先链向上（包括自身），按 distance 升序
            ancestors = conn.execute(
                """SELECT o.org_id, o.block_inherit, p.permissions, p.denied
                   FROM tenant_organizations o
                   LEFT JOIN tenant_org_permissions p ON p.org_id = o.org_id
                   JOIN tenant_org_closure c
                     ON c.ancestor = o.org_id
                   WHERE c.descendant = ?
                   ORDER BY c.distance ASC""",
                (org_id,),
            ).fetchall()

        accumulated: set[str] = set()
        inherited_from: list[str] = []
        blocked = False
        self_denied: set[str] = set()
        # ancestors[0] 是自身（distance=0），最后一个是根
        for row in ancestors:
            try:
                perms = json.loads(row["permissions"]) if row["permissions"] else []
            except (TypeError, json.JSONDecodeError):
                perms = []
            try:
                denied = json.loads(row["denied"]) if row["denied"] else []
            except (TypeError, json.JSONDecodeError):
                denied = []
            # 记录自身的 denied（最后统一扣除）
            if row["org_id"] == org_id:
                self_denied = set(denied)
            # 累计权限
            new_perms = set(perms) - accumulated
            if new_perms:
                accumulated |= new_perms
                if row["org_id"] != org_id:
                    inherited_from.append(row["org_id"])
            # 检查是否阻断继承（仅对非自身节点生效）
            if row["org_id"] != org_id and row["block_inherit"]:
                blocked = True
                break

        # 最后扣除自身的 denied（确保父节点继承的权限也被正确拒绝）
        accumulated -= self_denied

        return EffectivePermissions(
            org_id=org_id,
            permissions=sorted(accumulated),
            inherited_from=inherited_from,
            blocked=blocked,
        )

    def check_permission(self, org_id: str, permission: str) -> bool:
        """检查组织是否拥有指定权限（含继承）。"""
        effective = self.get_effective_permissions(org_id)
        return permission in effective.permissions

    def grant_permission(self, org_id: str, permission: str) -> PermissionEntry:
        """向组织添加单个权限。"""
        entry = self.get_local_permissions(org_id)
        if permission not in entry.permissions:
            entry.permissions.append(permission)
        return self.set_permissions(org_id, entry.permissions, denied=entry.denied)

    def revoke_permission(self, org_id: str, permission: str) -> PermissionEntry:
        """从组织移除单个权限。"""
        entry = self.get_local_permissions(org_id)
        if permission in entry.permissions:
            entry.permissions.remove(permission)
        return self.set_permissions(org_id, entry.permissions, denied=entry.denied)

    def deny_permission(self, org_id: str, permission: str) -> PermissionEntry:
        """显式拒绝权限（加入 denied 列表）。"""
        entry = self.get_local_permissions(org_id)
        if permission not in entry.denied:
            entry.denied.append(permission)
        # 同时从 permissions 中移除
        if permission in entry.permissions:
            entry.permissions.remove(permission)
        return self.set_permissions(org_id, entry.permissions, denied=entry.denied)

    def set_block_inherit(self, org_id: str, block: bool) -> Organization:
        """设置是否阻断权限继承。"""
        now = datetime.now(timezone.utc).isoformat()
        with sqlite_connect(self._db_path) as conn:
            cur = conn.execute(
                "UPDATE tenant_organizations SET block_inherit = ?, updated_at = ? "
                "WHERE org_id = ?",
                (int(block), now, org_id),
            )
            if cur.rowcount == 0:
                raise HierarchyError(f"organization {org_id!r} does not exist")
        result = self.get_organization(org_id)
        assert result is not None
        return result

    # ── 树形可视化 ───────────────────────────────────────────

    def render_tree(
        self,
        root_org_id: str | None = None,
        *,
        tenant_id: str = "",
        indent: str = "  ",
    ) -> str:
        """渲染组织树为文本形式（用于调试 / CLI 展示）。

        Parameters
        ----------
        root_org_id : str | None
            起始节点；``None`` 表示从所有根节点开始。
        tenant_id : str
            按租户过滤。
        indent : str
            缩进字符串。
        """
        if root_org_id is None:
            roots = self.list_organizations(tenant_id=tenant_id, parent_id="")
        else:
            r = self.get_organization(root_org_id)
            roots = [r] if r else []
        lines: list[str] = []
        for root in roots:
            self._render_node(root, 0, indent, lines)
        return "\n".join(lines)

    def _render_node(
        self, node: Organization, depth: int, indent: str, lines: list[str],
    ) -> None:
        prefix = indent * depth
        block_flag = " [block_inherit]" if node.block_inherit else ""
        lines.append(f"{prefix}- {node.org_id} ({node.name}){block_flag}")
        for child in self.get_children(node.org_id):
            self._render_node(child, depth + 1, indent, lines)

    # ── 辅助 ─────────────────────────────────────────────────

    @staticmethod
    def _row_to_org(row: Any) -> Organization:
        try:
            metadata = json.loads(row["metadata"])
        except (json.JSONDecodeError, TypeError):
            metadata = {}
        return Organization(
            org_id=row["org_id"],
            name=row["name"],
            parent_id=row["parent_id"],
            tenant_id=row["tenant_id"],
            metadata=metadata,
            block_inherit=bool(row["block_inherit"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )