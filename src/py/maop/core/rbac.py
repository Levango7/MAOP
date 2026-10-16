#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
多租户RBAC权限控制模块
实现租户管理、角色权限分配、接口权限校验功能
"""

from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple
from enum import Enum


class Role(Enum):
    """角色枚举"""
    SUPER_ADMIN = "super_admin"      # 超级管理员
    TENANT_ADMIN = "tenant_admin"    # 租户管理员
    EDITOR = "editor"                # 编辑者
    VIEWER = "viewer"                # 查看者


class Permission(Enum):
    """权限枚举"""
    # 工作流权限
    WORKFLOW_CREATE = "workflow:create"
    WORKFLOW_EDIT = "workflow:edit"
    WORKFLOW_DELETE = "workflow:delete"
    WORKFLOW_EXECUTE = "workflow:execute"
    WORKFLOW_VIEW = "workflow:view"
    # 路由权限
    ROUTE_MANAGE = "route:manage"
    ROUTE_VIEW = "route:view"
    # 审计权限
    AUDIT_VIEW = "audit:view"
    # 租户管理权限
    TENANT_MANAGE = "tenant:manage"


# 角色对应权限映射
ROLE_PERMISSIONS: Dict[Role, Set[Permission]] = {
    Role.SUPER_ADMIN: {p for p in Permission},
    Role.TENANT_ADMIN: {
        Permission.WORKFLOW_CREATE,
        Permission.WORKFLOW_EDIT,
        Permission.WORKFLOW_DELETE,
        Permission.WORKFLOW_EXECUTE,
        Permission.WORKFLOW_VIEW,
        Permission.ROUTE_MANAGE,
        Permission.ROUTE_VIEW,
        Permission.AUDIT_VIEW
    },
    Role.EDITOR: {
        Permission.WORKFLOW_CREATE,
        Permission.WORKFLOW_EDIT,
        Permission.WORKFLOW_EXECUTE,
        Permission.WORKFLOW_VIEW,
        Permission.ROUTE_VIEW
    },
    Role.VIEWER: {
        Permission.WORKFLOW_VIEW,
        Permission.ROUTE_VIEW
    }
}


@dataclass
class TenantUser:
    """租户用户"""
    user_id: str
    tenant_id: str
    role: Role
    username: str
    email: Optional[str] = None


class RBACManager:
    """权限管理器"""
    def __init__(self):
        self.tenant_users: Dict[Tuple[str, str], TenantUser] = {} # (tenant_id, user_id) -> user

    def bind_user_role(self, tenant_id: str, user_id: str, role: Role, username: str, email: Optional[str] = None):
        """绑定用户角色"""
        user = TenantUser(
            user_id=user_id,
            tenant_id=tenant_id,
            role=role,
            username=username,
            email=email
        )
        self.tenant_users[(tenant_id, user_id)] = user

    def get_user_role(self, tenant_id: str, user_id: str) -> Optional[Role]:
        """获取用户角色"""
        user = self.tenant_users.get((tenant_id, user_id))
        return user.role if user else None

    def check_permission(self, tenant_id: str, user_id: str, permission: Permission) -> bool:
        """校验用户权限"""
        role = self.get_user_role(tenant_id, user_id)
        if not role:
            return False
        return permission in ROLE_PERMISSIONS.get(role, set())

    def get_user_permissions(self, tenant_id: str, user_id: str) -> List[Permission]:
        """获取用户所有权限"""
        role = self.get_user_role(tenant_id, user_id)
        if not role:
            return []
        return list(ROLE_PERMISSIONS.get(role, set()))
