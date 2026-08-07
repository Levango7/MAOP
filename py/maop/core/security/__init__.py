"""Security subpackage.

认证、授权、沙箱、守卫、TLS、密钥管理、租户、会话、中间件。

Modules:
    auth, sandbox, guardrail, permission, tls, api_key_vault, byok,
    tenant, session, middleware
"""
from __future__ import annotations

import importlib

__all__ = [
    "logger",
    "ApiKeyVault",
    "logger",
    "AuthResult",
    "APIKey",
    "JWTConfig",
    "AuthConfig",
    "APIKeyStore",
    "JWTHandler",
    "load_jwt_secret",
    "AuthManager",
    "logger",
    "KeySource",
    "KeyRoute",
    "ResolvedKey",
    "BYOKGateway",
    "logger",
    "RuleAction",
    "RuleType",
    "GuardRule",
    "GuardConfig",
    "Violation",
    "CheckResult",
    "DEFAULT_RULES",
    "Guardrail",
    "fnmatch_simple",
    "logger",
    "AuthMiddleware",
    "RateLimitMiddleware",
    "CSPMiddleware",
    "setup_middleware",
    "require_admin",
    "logger",
    "PermissionRule",
    "PermissionCheck",
    "PermissionManager",
    "logger",
    "SandboxInfo",
    "SandboxResult",
    "SandboxManager",
    "logger",
    "SessionStatus",
    "Session",
    "SessionManager",
    "logger",
    "TenantConfig",
    "TenantManager",
    "logger",
    "TLSSettings",
    "create_ssl_context",
    "generate_self_signed",
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
    "TenantConfig": "tenant",
    "TenantManager": "tenant",
    "logger": "tls",
    "TLSSettings": "tls",
    "create_ssl_context": "tls",
    "generate_self_signed": "tls",
}


def __getattr__(name: str):
    """惰性加载子模块符号，避免循环导入。"""
    if name in _SYMBOL_TO_MODULE:
        mod_name = _SYMBOL_TO_MODULE[name]
        mod = importlib.import_module(f".{mod_name}", __name__)
        value = getattr(mod, name)
        globals()[name] = value  # 缓存，下次直接访问
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
