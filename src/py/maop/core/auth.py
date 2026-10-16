#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
API认证模块
实现简单的API Key认证机制，支持多租户场景
"""

from typing import Optional, Tuple
from .rbac import RBACManager, Role, Permission


class AuthManager:
    """认证管理器"""
    def __init__(self, rbac_manager: RBACManager):
        self.rbac = rbac_manager
        # API Key到租户、用户的映射，实际场景存储在数据库
        self.api_key_map = {
            "test_admin_key_123": ("tenant_1", "user_admin"),
            "test_editor_key_456": ("tenant_1", "user_editor"),
            "test_viewer_key_789": ("tenant_1", "user_viewer")
        }
        # 初始化默认用户
        self.rbac.bind_user_role("tenant_1", "user_admin", Role.TENANT_ADMIN, "admin@test.com")
        self.rbac.bind_user_role("tenant_1", "user_editor", Role.EDITOR, "editor@test.com")
        self.rbac.bind_user_role("tenant_1", "user_viewer", Role.VIEWER, "viewer@test.com")

    def authenticate(self, api_key: str) -> Optional[Tuple[str, str]]:
        """校验API Key，成功返回(tenant_id, user_id)，失败返回None"""
        return self.api_key_map.get(api_key)

    def check_api_permission(self, api_key: str, permission: Permission) -> bool:
        """校验API Key是否拥有指定权限"""
        auth_result = self.authenticate(api_key)
        if not auth_result:
            return False
        tenant_id, user_id = auth_result
        return self.rbac.check_permission(tenant_id, user_id, permission)
