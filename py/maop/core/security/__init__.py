"""Security subpackage.

认证、授权、沙箱、守卫、TLS、密钥管理、租户、会话、中间件。

Modules:
    auth, sandbox, guardrail, permission, tls, api_key_vault, byok,
    session, middleware

注：``TenantManager`` / ``TenantConfig`` 的规范位置是 :mod:`maop.core.tenant`。
本包仅保留历史导入路径（``security/tenant.py`` 已于 2026-09-25 删除 ——
它是 ``core/tenant/manager.py`` 的严格子集）。
"""
from __future__ import annotations

import importlib

__all__ = [
    "DEFAULT_RULES",
    "APIKey",
    "APIKeyStore",
    "ApiKeyVault",
    "AuthConfig",
    "AuthManager",
    "AuthMiddleware",
    "AuthResult",
    "BYOKGateway",
    "CSPMiddleware",
    "CheckResult",
    "GuardConfig",
    "GuardRule",
    "Guardrail",
    "JWTConfig",
    "JWTHandler",
    "KeyRoute",
    "KeySource",
    "PermissionCheck",
    "PermissionManager",
    "PermissionRule",
    "RateLimitMiddleware",
    "ResolvedKey",
    "RuleAction",
    "RuleType",
    "SandboxInfo",
    "SandboxManager",
    "SandboxResult",
    "Session",
    "SessionManager",
    "SessionStatus",
    "TLSSettings",
    "TenantConfig",
    "TenantManager",
    "Violation",
    "create_ssl_context",
    "fnmatch_simple",
    "generate_self_signed",
    "load_jwt_secret",
    "logger",
    "require_admin",
    "setup_middleware",
]

# 符号 → 子模块名映射（惰性加载用，含私有符号）
_SYMBOL_TO_MODULE: dict[str, str] = {
    # 注: 多个子模块均导出同名符号（如 logger），
    # 按字典构造语义仅最后一个映射生效，与重构前运行时行为一致。
    "ApiKeyVault": "api_key_vault",
    "AuthResult": "auth",
    "APIKey": "auth",
    "JWTConfig": "auth",
    "AuthConfig": "auth",
    "APIKeyStore": "auth",
    "JWTHandler": "auth",
    "load_jwt_secret": "auth",
    "AuthManager": "auth",
    "KeySource": "byok",
    "KeyRoute": "byok",
    "ResolvedKey": "byok",
    "BYOKGateway": "byok",
    "RuleAction": "guardrail",
    "RuleType": "guardrail",
    "GuardRule": "guardrail",
    "GuardConfig": "guardrail",
    "Violation": "guardrail",
    "CheckResult": "guardrail",
    "DEFAULT_RULES": "guardrail",
    "_default_config": "guardrail",
    "Guardrail": "guardrail",
    "fnmatch_simple": "guardrail",
    "AuthMiddleware": "middleware",
    "RateLimitMiddleware": "middleware",
    "CSPMiddleware": "middleware",
    "setup_middleware": "middleware",
    "require_admin": "middleware",
    "PermissionRule": "permission",
    "PermissionCheck": "permission",
    "PermissionManager": "permission",
    "SandboxInfo": "sandbox",
    "SandboxResult": "sandbox",
    "SandboxManager": "sandbox",
    "SessionStatus": "session",
    "Session": "session",
    "SessionManager": "session",
    # 2026-09-25: security/tenant.py 已删（它是 core/tenant/manager.py 的严格
    # 子集，此前仅被测试使用）。这里指向**规范位置**，保留本条历史导入路径可用。
    "TenantConfig": "maop.core.tenant.manager",
    "TenantManager": "maop.core.tenant.manager",
    "logger": "tls",
    "TLSSettings": "tls",
    "create_ssl_context": "tls",
    "generate_self_signed": "tls",
}


def __getattr__(name: str):
    """惰性加载子模块符号，避免循环导入。

    映射值以 ``maop.`` 开头时视为**绝对模块路径**（可指向本包之外的模块），
    否则按相对本包的子模块名解析。支持绝对路径是为了让 ``TenantManager``
    等符号指向其**规范位置** —— 2026-09-25 删除了 ``security/tenant.py``
    （它是 ``core/tenant/manager.py`` 的严格子集，仅测试在用），
    但保留 ``from maop.core.security import TenantManager`` 这条历史路径可用。
    """
    if name in _SYMBOL_TO_MODULE:
        target = _SYMBOL_TO_MODULE[name]
        mod = (
            importlib.import_module(target)
            if target.startswith("maop.")
            else importlib.import_module(f".{target}", __name__)
        )
        value = getattr(mod, name)
        globals()[name] = value  # 缓存，下次直接访问
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
