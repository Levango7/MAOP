#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RBAC权限模块单元测试
"""
import pytest
from py.maop.core.rbac import RBACManager, Role, Permission


class TestRBACManager:
    def setup_method(self):
        self.rbac = RBACManager()
        self.rbac.bind_user_role("tenant_1", "user_admin", Role.TENANT_ADMIN, "admin@test.com")
        self.rbac.bind_user_role("tenant_1", "user_editor", Role.EDITOR, "editor@test.com")
        self.rbac.bind_user_role("tenant_1", "user_viewer", Role.VIEWER, "viewer@test.com")

    def test_tenant_admin_permissions(self):
        assert self.rbac.check_permission("tenant_1", "user_admin", Permission.WORKFLOW_CREATE) == True
        assert self.rbac.check_permission("tenant_1", "user_admin", Permission.WORKFLOW_DELETE) == True
        assert self.rbac.check_permission("tenant_1", "user_admin", Permission.AUDIT_VIEW) == True

    def test_editor_permissions(self):
        assert self.rbac.check_permission("tenant_1", "user_editor", Permission.WORKFLOW_CREATE) == True
        assert self.rbac.check_permission("tenant_1", "user_editor", Permission.WORKFLOW_DELETE) == False
        assert self.rbac.check_permission("tenant_1", "user_editor", Permission.AUDIT_VIEW) == False

    def test_viewer_permissions(self):
        assert self.rbac.check_permission("tenant_1", "user_viewer", Permission.WORKFLOW_VIEW) == True
        assert self.rbac.check_permission("tenant_1", "user_viewer", Permission.WORKFLOW_CREATE) == False
        assert self.rbac.check_permission("tenant_1", "user_viewer", Permission.ROUTE_MANAGE) == False

    def test_super_admin_all_permissions(self):
        self.rbac.bind_user_role("system", "super_admin", Role.SUPER_ADMIN, "super@test.com")
        assert self.rbac.check_permission("system", "super_admin", Permission.TENANT_MANAGE) == True
        assert self.rbac.check_permission("system", "super_admin", permission.WORKFLOW_DELETE) == True

    def test_unknown_user_no_permission(self):
        assert self.rbac.check_permission("tenant_1", "unknown_user", Permission.WORKFLOW_VIEW) == False
