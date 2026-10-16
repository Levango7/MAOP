#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
API鉴权中间件
提供require_auth装饰器，实现接口级权限校验
"""

from functools import wraps
from flask import request, jsonify
from py.maop.core.auth import AuthManager
from py.maop.core.rbac import Permission, RBACManager

# 全局AuthManager实例，实际项目通过配置初始化
auth_manager = AuthManager(RBACManager())


def require_auth(permission: Permission):
    """鉴权装饰器，要求请求携带API Key并拥有指定权限"""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            # 从Header获取API Key
            api_key = request.headers.get("X-API-Key")
            if not api_key:
                return jsonify({"code": 401, "message": "缺少API Key"}), 401
            # 校验权限
            if not auth_manager.check_api_permission(api_key, permission):
                return jsonify({"code": 403, "message": "无访问权限"}), 403
            return f(*args, **kwargs)
        return decorated_function
    return decorator
