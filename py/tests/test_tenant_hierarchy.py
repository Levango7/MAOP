"""Tests for maop.core.tenant.hierarchy — 多级组织树 + 权限继承。

覆盖：
* 组织 CRUD（创建、读取、列出、删除）
* 父子关系与闭包表维护（祖先 / 后代查询）
* 移动组织（含循环检测）
* 权限继承（默认继承、block_inherit、deny）
* 树形渲染
* 多租户隔离
"""

from __future__ import annotations

import pytest

from maop.core.tenant.hierarchy import (
    HierarchyError,
    OrganizationHierarchy,
)

# ── fixtures ─────────────────────────────────────────────────


@pytest.fixture
def hierarchy(tmp_path):
    """每个测试一个独立的 OrganizationHierarchy。"""
    db_path = tmp_path / "data" / "maop.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return OrganizationHierarchy(db_path)


# ── 创建组织 ─────────────────────────────────────────────────


class TestCreateOrganization:
    def test_create_root(self, hierarchy):
        org = hierarchy.create_organization("root", name="Root Org")
        assert org.org_id == "root"
        assert org.name == "Root Org"
        assert org.parent_id == ""
        assert org.created_at != ""

    def test_create_child(self, hierarchy):
        hierarchy.create_organization("root", name="Root")
        child = hierarchy.create_organization(
            "child", name="Child", parent_id="root",
        )
        assert child.parent_id == "root"

    def test_create_grandchild(self, hierarchy):
        hierarchy.create_organization("root")
        hierarchy.create_organization("child", parent_id="root")
        grandchild = hierarchy.create_organization(
            "gc", parent_id="child",
        )
        assert grandchild.parent_id == "child"

    def test_duplicate_raises(self, hierarchy):
        hierarchy.create_organization("root")
        with pytest.raises(HierarchyError, match="already exists"):
            hierarchy.create_organization("root")

    def test_nonexistent_parent_raises(self, hierarchy):
        with pytest.raises(HierarchyError, match="does not exist"):
            hierarchy.create_organization("c", parent_id="nope")

    def test_self_parent_raises(self, hierarchy):
        with pytest.raises(HierarchyError, match="its own parent"):
            hierarchy.create_organization("x", parent_id="x")

    def test_empty_id_raises(self, hierarchy):
        with pytest.raises(HierarchyError, match="must not be empty"):
            hierarchy.create_organization("")

    def test_tenant_inherited_from_parent(self, hierarchy):
        hierarchy.create_organization("root", tenant_id="t1")
        child = hierarchy.create_organization("c", parent_id="root")
        assert child.tenant_id == "t1"

    def test_tenant_mismatch_raises(self, hierarchy):
        hierarchy.create_organization("root", tenant_id="t1")
        with pytest.raises(HierarchyError, match="tenant_id mismatch"):
            hierarchy.create_organization(
                "c", parent_id="root", tenant_id="t2",
            )


# ── 读取与列出 ───────────────────────────────────────────────


class TestReadList:
    def test_get_organization(self, hierarchy):
        hierarchy.create_organization("root", name="Root")
        org = hierarchy.get_organization("root")
        assert org is not None
        assert org.name == "Root"

    def test_get_nonexistent_returns_none(self, hierarchy):
        assert hierarchy.get_organization("nope") is None

    def test_list_all(self, hierarchy):
        hierarchy.create_organization("a")
        hierarchy.create_organization("b")
        orgs = hierarchy.list_organizations()
        assert len(orgs) == 2

    def test_list_by_tenant(self, hierarchy):
        hierarchy.create_organization("a", tenant_id="t1")
        hierarchy.create_organization("b", tenant_id="t2")
        orgs = hierarchy.list_organizations(tenant_id="t1")
        assert len(orgs) == 1
        assert orgs[0].org_id == "a"

    def test_list_roots(self, hierarchy):
        hierarchy.create_organization("root")
        hierarchy.create_organization("child", parent_id="root")
        roots = hierarchy.list_organizations(parent_id="")
        assert len(roots) == 1
        assert roots[0].org_id == "root"

    def test_list_children(self, hierarchy):
        hierarchy.create_organization("root")
        hierarchy.create_organization("c1", parent_id="root")
        hierarchy.create_organization("c2", parent_id="root")
        children = hierarchy.list_organizations(parent_id="root")
        assert len(children) == 2


# ── 祖先 / 后代 ──────────────────────────────────────────────


class TestAncestorsDescendants:
    def _build_tree(self, h):
        h.create_organization("root")
        h.create_organization("a", parent_id="root")
        h.create_organization("b", parent_id="a")
        h.create_organization("c", parent_id="b")

    def test_get_children(self, hierarchy):
        self._build_tree(hierarchy)
        children = hierarchy.get_children("a")
        assert len(children) == 1
        assert children[0].org_id == "b"

    def test_get_descendants(self, hierarchy):
        self._build_tree(hierarchy)
        desc = hierarchy.get_descendants("root")
        ids = {o.org_id for o in desc}
        assert ids == {"a", "b", "c"}

    def test_get_descendants_include_self(self, hierarchy):
        self._build_tree(hierarchy)
        desc = hierarchy.get_descendants("root", include_self=True)
        ids = {o.org_id for o in desc}
        assert ids == {"root", "a", "b", "c"}

    def test_get_ancestors(self, hierarchy):
        self._build_tree(hierarchy)
        anc = hierarchy.get_ancestors("c")
        ids = [o.org_id for o in anc]
        assert ids == ["b", "a", "root"]

    def test_get_ancestors_include_self(self, hierarchy):
        self._build_tree(hierarchy)
        anc = hierarchy.get_ancestors("c", include_self=True)
        ids = [o.org_id for o in anc]
        assert ids == ["c", "b", "a", "root"]

    def test_descendants_of_leaf(self, hierarchy):
        self._build_tree(hierarchy)
        assert hierarchy.get_descendants("c") == []


# ── 移动组织 ─────────────────────────────────────────────────


class TestMoveOrganization:
    def test_move_to_new_parent(self, hierarchy):
        hierarchy.create_organization("root1")
        hierarchy.create_organization("root2")
        hierarchy.create_organization("child", parent_id="root1")
        moved = hierarchy.move_organization("child", "root2")
        assert moved.parent_id == "root2"
        # 验证祖先
        anc = {o.org_id for o in hierarchy.get_ancestors("child")}
        assert anc == {"root2"}

    def test_move_to_root(self, hierarchy):
        hierarchy.create_organization("root")
        hierarchy.create_organization("child", parent_id="root")
        moved = hierarchy.move_organization("child", "")
        assert moved.parent_id == ""

    def test_move_self_raises(self, hierarchy):
        hierarchy.create_organization("x")
        with pytest.raises(HierarchyError, match="under itself"):
            hierarchy.move_organization("x", "x")

    def test_move_creates_cycle_raises(self, hierarchy):
        hierarchy.create_organization("root")
        hierarchy.create_organization("a", parent_id="root")
        hierarchy.create_organization("b", parent_id="a")
        # 将 root 移到 b 下会形成环
        with pytest.raises(HierarchyError, match="cycle"):
            hierarchy.move_organization("root", "b")

    def test_move_nonexistent_raises(self, hierarchy):
        with pytest.raises(HierarchyError, match="does not exist"):
            hierarchy.move_organization("nope", "")

    def test_move_descendants_updated(self, hierarchy):
        """移动中间节点后，后代祖先链应正确更新。"""
        hierarchy.create_organization("r1")
        hierarchy.create_organization("r2")
        hierarchy.create_organization("a", parent_id="r1")
        hierarchy.create_organization("b", parent_id="a")
        hierarchy.move_organization("a", "r2")
        # b 的祖先应包含 a, r2（不再包含 r1）
        anc = {o.org_id for o in hierarchy.get_ancestors("b")}
        assert "a" in anc
        assert "r2" in anc
        assert "r1" not in anc


# ── 删除 ─────────────────────────────────────────────────────


class TestDeleteOrganization:
    def test_delete_leaf(self, hierarchy):
        hierarchy.create_organization("root")
        assert hierarchy.delete_organization("root") is True
        assert hierarchy.get_organization("root") is None

    def test_delete_with_children_raises(self, hierarchy):
        hierarchy.create_organization("root")
        hierarchy.create_organization("child", parent_id="root")
        with pytest.raises(HierarchyError, match="has children"):
            hierarchy.delete_organization("root")

    def test_delete_recursive(self, hierarchy):
        hierarchy.create_organization("root")
        hierarchy.create_organization("a", parent_id="root")
        hierarchy.create_organization("b", parent_id="a")
        hierarchy.delete_organization("root", recursive=True)
        assert hierarchy.get_organization("root") is None
        assert hierarchy.get_organization("a") is None
        assert hierarchy.get_organization("b") is None

    def test_delete_cleans_permissions(self, hierarchy):
        hierarchy.create_organization("root")
        hierarchy.set_permissions("root", ["read"])
        hierarchy.delete_organization("root")
        # 重新创建同名组织，权限应为空
        hierarchy.create_organization("root")
        entry = hierarchy.get_local_permissions("root")
        assert entry.permissions == []


# ── 权限继承 ─────────────────────────────────────────────────


class TestPermissionInheritance:
    def test_set_and_get_local_permissions(self, hierarchy):
        hierarchy.create_organization("root")
        hierarchy.set_permissions("root", ["read", "write"])
        entry = hierarchy.get_local_permissions("root")
        assert entry.permissions == ["read", "write"]

    def test_effective_no_inheritance(self, hierarchy):
        hierarchy.create_organization("root")
        hierarchy.set_permissions("root", ["read"])
        eff = hierarchy.get_effective_permissions("root")
        assert eff.permissions == ["read"]
        assert eff.inherited_from == []

    def test_inherit_from_parent(self, hierarchy):
        hierarchy.create_organization("root")
        hierarchy.create_organization("child", parent_id="root")
        hierarchy.set_permissions("root", ["read", "admin"])
        hierarchy.set_permissions("child", ["write"])
        eff = hierarchy.get_effective_permissions("child")
        # child 应继承 root 的权限
        assert set(eff.permissions) == {"read", "admin", "write"}
        assert "root" in eff.inherited_from

    def test_inherit_grandparent(self, hierarchy):
        hierarchy.create_organization("root")
        hierarchy.create_organization("mid", parent_id="root")
        hierarchy.create_organization("leaf", parent_id="mid")
        hierarchy.set_permissions("root", ["admin"])
        hierarchy.set_permissions("mid", ["read"])
        hierarchy.set_permissions("leaf", ["write"])
        eff = hierarchy.get_effective_permissions("leaf")
        assert set(eff.permissions) == {"admin", "read", "write"}

    def test_block_inherit(self, hierarchy):
        hierarchy.create_organization("root")
        hierarchy.create_organization("mid", parent_id="root")
        hierarchy.create_organization("leaf", parent_id="mid")
        hierarchy.set_permissions("root", ["admin"])
        hierarchy.set_permissions("mid", ["read"])
        hierarchy.set_block_inherit("mid", True)
        hierarchy.set_permissions("leaf", ["write"])
        eff = hierarchy.get_effective_permissions("leaf")
        # leaf 继承 mid 的 read，但不继承 root 的 admin
        assert set(eff.permissions) == {"read", "write"}
        assert "admin" not in eff.permissions
        assert eff.blocked is True

    def test_deny_permission(self, hierarchy):
        hierarchy.create_organization("root")
        hierarchy.create_organization("child", parent_id="root")
        hierarchy.set_permissions("root", ["read", "write"])
        hierarchy.set_permissions("child", [], denied=["write"])
        eff = hierarchy.get_effective_permissions("child")
        # child 继承 read，但 deny write
        assert "read" in eff.permissions
        assert "write" not in eff.permissions

    def test_check_permission(self, hierarchy):
        hierarchy.create_organization("root")
        hierarchy.create_organization("child", parent_id="root")
        hierarchy.set_permissions("root", ["admin"])
        assert hierarchy.check_permission("child", "admin") is True
        assert hierarchy.check_permission("child", "superadmin") is False

    def test_grant_revoke(self, hierarchy):
        hierarchy.create_organization("root")
        hierarchy.grant_permission("root", "read")
        assert hierarchy.check_permission("root", "read") is True
        hierarchy.revoke_permission("root", "read")
        assert hierarchy.check_permission("root", "read") is False

    def test_deny_permission_method(self, hierarchy):
        hierarchy.create_organization("root")
        hierarchy.create_organization("child", parent_id="root")
        hierarchy.set_permissions("root", ["read", "write"])
        hierarchy.deny_permission("child", "write")
        eff = hierarchy.get_effective_permissions("child")
        assert "write" not in eff.permissions

    def test_effective_nonexistent_raises(self, hierarchy):
        with pytest.raises(HierarchyError, match="does not exist"):
            hierarchy.get_effective_permissions("nope")

    def test_set_permissions_nonexistent_raises(self, hierarchy):
        with pytest.raises(HierarchyError, match="does not exist"):
            hierarchy.set_permissions("nope", ["read"])


# ── 树形渲染 ─────────────────────────────────────────────────


class TestRenderTree:
    def test_render_simple_tree(self, hierarchy):
        hierarchy.create_organization("root", name="Root")
        hierarchy.create_organization("a", name="A", parent_id="root")
        hierarchy.create_organization("b", name="B", parent_id="root")
        hierarchy.create_organization("c", name="C", parent_id="a")
        tree = hierarchy.render_tree()
        assert "root" in tree
        assert "a" in tree
        assert "b" in tree
        assert "c" in tree
        # 缩进层级
        lines = tree.splitlines()
        assert lines[0].startswith("- root")
        assert "  - a" in tree
        assert "    - c" in tree

    def test_render_with_block_inherit_flag(self, hierarchy):
        hierarchy.create_organization("root", name="Root")
        hierarchy.create_organization("child", name="Child", parent_id="root")
        hierarchy.set_block_inherit("child", True)
        tree = hierarchy.render_tree()
        assert "block_inherit" in tree

    def test_render_from_subtree(self, hierarchy):
        hierarchy.create_organization("root")
        hierarchy.create_organization("a", parent_id="root")
        hierarchy.create_organization("b", parent_id="a")
        tree = hierarchy.render_tree("a")
        assert "a" in tree
        assert "root" not in tree


# ── 闭包表一致性 ─────────────────────────────────────────────


class TestClosureConsistency:
    def test_self_reference_exists(self, hierarchy):
        hierarchy.create_organization("root")
        desc = hierarchy.get_descendants("root", include_self=True)
        assert any(o.org_id == "root" for o in desc)

    def test_deep_tree_ancestors(self, hierarchy):
        """构建 5 层树，验证最底层节点的祖先链完整。"""
        prev = ""
        chain = ["l0", "l1", "l2", "l3", "l4"]
        for i, org_id in enumerate(chain):
            hierarchy.create_organization(org_id, parent_id=prev)
            prev = org_id
        anc = [o.org_id for o in hierarchy.get_ancestors("l4")]
        assert anc == ["l3", "l2", "l1", "l0"]

    def test_move_preserves_descendants(self, hierarchy):
        hierarchy.create_organization("r1")
        hierarchy.create_organization("r2")
        hierarchy.create_organization("a", parent_id="r1")
        hierarchy.create_organization("b", parent_id="a")
        hierarchy.create_organization("c", parent_id="b")
        hierarchy.move_organization("a", "r2")
        # c 的祖先链应正确
        anc = {o.org_id for o in hierarchy.get_ancestors("c")}
        assert anc == {"b", "a", "r2"}
        assert "r1" not in anc