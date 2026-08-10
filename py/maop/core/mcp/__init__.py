"""MCP (Model Context Protocol) subpackage.

MCP 协议相关：hub、传输、适配、缓存、并发、发现、市场、权限、审计、
工具签名（Ed25519）、工具发现（本地+远程 registry）。

Modules:
    mcp_hub, mcp_hub_types, mcp_hub_transport, mcp_adapter, mcp_cache,
    mcp_concurrency, mcp_discovery, mcp_marketplace, mcp_permission, mcp_audit,
    tool_signing, tool_discovery
"""
from __future__ import annotations

import importlib

__all__ = [
    "logger",
    "MCPHub",
    "TransportType",
    "ServerStatus",
    "MCPServerConfig",
    "MCPPermissionDeniedError",
    "MCPRateLimitedError",
    "MCPTool",
    "MCPResource",
    "ToolResult",
    "ResourceContent",
    "ServerInfo",
    "logger",
    "logger",
    "MCPAdapter",
    "logger",
    "MCPCacheKey",
    "MCPCacheEntry",
    "MCPCacheStats",
    "MCPCache",
    "logger",
    "MCPServerConcurrency",
    "MCPServerRateLimiter",
    "logger",
    "DiscoveryReport",
    "MCPDiscovery",
    "logger",
    "MarketplaceServer",
    "MarketplaceRegistry",
    "MarketplaceConfig",
    "MCPMarketplace",
    "logger",
    "MCPPermissionDecision",
    "MCPPermissionChecker",
    "logger",
    "MCPAuditRecord",
    "hash_arguments",
    "MCPAuditLogger",
    "ToolSigner",
    "ToolSignatureError",
    "generate_keypair",
    "sign_bytes",
    "verify_bytes",
    "canonical_bytes",
    "DiscoverySource",
    "DiscoveredTool",

    "ToolDiscovery",
]

# 符号 → 子模块名映射（惰性加载用，含私有符号）
_SYMBOL_TO_MODULE: dict[str, str] = {
    # 注: 多个子模块均导出同名符号（如 logger），
    # 按字典构造语义仅最后一个映射生效，与重构前运行时行为一致。
    "_MCP_DDL": "mcp_hub",
    "MCPHub": "mcp_hub",
    "TransportType": "mcp_hub_types",
    "ServerStatus": "mcp_hub_types",
    "MCPServerConfig": "mcp_hub_types",
    "MCPPermissionDeniedError": "mcp_hub_types",
    "MCPRateLimitedError": "mcp_hub_types",
    "MCPTool": "mcp_hub_types",
    "MCPResource": "mcp_hub_types",
    "ToolResult": "mcp_hub_types",
    "ResourceContent": "mcp_hub_types",
    "ServerInfo": "mcp_hub_types",
    "_StdioTransport": "mcp_hub_transport",
    "_SSETransport": "mcp_hub_transport",
    "_WebSocketTransport": "mcp_hub_transport",
    "_StreamableHttpTransport": "mcp_hub_transport",
    "_BackgroundLoop": "mcp_adapter",
    "MCPAdapter": "mcp_adapter",
    "MCPCacheKey": "mcp_cache",
    "MCPCacheEntry": "mcp_cache",
    "MCPCacheStats": "mcp_cache",
    "MCPCache": "mcp_cache",
    "MCPServerConcurrency": "mcp_concurrency",
    "MCPServerRateLimiter": "mcp_concurrency",
    "DiscoveryReport": "mcp_discovery",
    "_claude_desktop_config_path": "mcp_discovery",
    "MCPDiscovery": "mcp_discovery",
    "MarketplaceServer": "mcp_marketplace",
    "MarketplaceRegistry": "mcp_marketplace",
    "MarketplaceConfig": "mcp_marketplace",
    "MCPMarketplace": "mcp_marketplace",
    "MCPPermissionDecision": "mcp_permission",
    "_MCPPermissionDefaults": "mcp_permission",
    "_RULE": "mcp_permission",
    "MCPPermissionChecker": "mcp_permission",
    "_glob_match": "mcp_permission",
    "logger": "mcp_audit",
    "MCPAuditRecord": "mcp_audit",
    "_MCP_AUDIT_DDL": "mcp_audit",
    "hash_arguments": "mcp_audit",
    "MCPAuditLogger": "mcp_audit",
    "ToolSigner": "tool_signing",
    "ToolSignatureError": "tool_signing",
    "generate_keypair": "tool_signing",
    "sign_bytes": "tool_signing",
    "verify_bytes": "tool_signing",
    "canonical_bytes": "tool_signing",
    "DiscoverySource": "tool_discovery",
    "DiscoveredTool": "tool_discovery",

    "ToolDiscovery": "tool_discovery",
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
