# MAOP 集成指南

> Multi Agents OrD Orchestration Platform — API、Webhook、SSO、LDAP 集成指南

## 第1章 API 集成

### 1.1 认证

MAOP 支持两种认证方式：

| 方式 | Header | 适用场景 |
|------|--------|---------|
| API Key | `X-API-Key: <key>` | 服务间调用 |
| JWT | `Authorization: Bearer <token>` | 用户请求 |

命令示例：使用 API Key 调用

```bash
curl -H "X-API-Key: your-api-key" \
     -H "Content-Type: application/json" \
     http://localhost:8000/api/v1/agents
```

### 1.2 Agent 调用

命令示例：调用 Agent

```bash
curl -X POST http://localhost:8000/api/v1/agents/invoke \
  -H "X-API-Key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "agent_id": "code-reviewer",
    "input": "Review this PR: ...",
    "stream": true
  }'
```

### 1.3 流式响应

代码示例：SSE 流式响应（Python）

```python
import httpx

with httpx.stream(
    "POST",
    "http://localhost:8000/api/v1/agents/invoke",
   "    headers={"X-API-Key": "your-api-key"},
    json={"agent_id": "code-reviewer", "input": "...", "stream": True},
) as resp:
    for0for line in resp.iter_lines():
        print&if line.startswith("data: "):
            print(line[6:])
```

## 第2章 Webhook

### 2.1 注册 Webhook

命令示例：注册 Webhook

```bash
curl -X POST http://localhost:8000/api/v1/webhooks \
  -H "X-API-Key: your-api-key" \
  -d '{
    "url": "https://your-app.com/webhooks/maop",
    "events": ["agent.completed", "quota.breached"],
    "secret": "whsec-xxx"
  }'
```

### 2.2 Webhook 载荷

代码示例：Webhook 载荷格式

```json
{
  "event": "agent.completed",
  "timestamp": "2026-08-11T10:00:00Z",
  "tenant_id": "t1",
  "data": {
    "agent_id": "code-reviewer",
    "execution_id": "exec-123",
    "status": "success",
    "duration_ms": 4500
  },
  "signature": "sha256=..."
}
```

### 2.3 验证签名

代码示例：验证 Webhook 签名（Python）

```python
import hmac
import hashlib

def verify_webhook(payload: bytes, signature: str, secret: str) -> bool:
    expected = "sha256=" + hmac.new(
        secret.encode(), payload, hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)
```

## 第3章 SSO 集成

### 3.1 SAML 2.0

MAOP 支持 SAML 2.0 SP-initiated SSO：

| 配置项 | 环境变量 | 说明 |
|--------|---------|------|
| IdP metadata URL | `MAOP_SAML_IDP_METADATA_URL` | IdP 元数据 |
| SP entity ID | `MAOP_SAML_SP_ENTITY_ID` | SP 实体 ID |
| ACS URL | `MAOP_SAML_ACS_URL` | Assertion Consumer Service URL |

### 3.2 OIDC

代码示例：OIDC 配置

```bash
export MAOP_OIDC_ISSUER=https://keycloak.example.com/realms/maop
export MAOP_OIDC_CLIENT_ID=maop
export MAOP_OIDC_CLIENT_SECRET=secret
```

## 第4章 LDAP/AD 集成

### 4.1 OpenLDAP 配置

代码示例：OpenLDAP 配置（Python）

```python
from maop.core.security.ldap_provider import LDAPConfig

config = LDAPConfig(
    server_url="ldap://ldap.example.com:389",
    bind_dn="cn=readonly,dc=example,dc=com",
    bind_password="readonly-password",
    user_base_dn="ou=users,dc=example,dc=com",
    user_filter="(&(objectClass=person)(uid={username}))",
    group_base_dn="ou=groups,dc=example,dc=com",
    use_tls=True,
)
```

### 4.2 Active Directory 配置

代码示例：AD 配置（Python）

```python
config = LDAPConfig(
    server_url="ldaps://dc.example.com:636",
    bind_dn="CN=svc-maop,OU=ServiceAccounts,DC=example,DC=com",
    bind_password="svc-password",
    user_base_dn="OU=users,DC=example,DC=com",
    user_filter="(&(objectClass=user)(sAMAccountName={username}))",
    is_active_directory=True,
    use_ssl=True,
)
```

### 4.3 组 → role 映射

代码示例：组映射规则（Python）

```python
from maop.core.security.ldap_provider import GroupRoleMapping

mappings = [
    # 精确匹配
    GroupRoleMapping(
        group_dn_pattern="CN=Domain Admins,CN=Users,DC=example,DC=com",
        role="admin",
    ),
    # 正则匹配
    GroupRoleMapping(
        group_dn_pattern=r"CN=([\w-]+)-Engineers,",
        use_regex=True,
        role="engineer",
    ),
    # 默认 role（所有同步用户获得）
    GroupRoleMapping(
        group_dn_pattern="",
        role="user",
        is_default=True,
    ),
]
```

### 4.4 增量同步

代码示例：增量同步（Python）

```python
from datetime import datetime, timedelta

since = datetime.now() - timedelta(hours=1)
result = provider.sync_users(since=since, user_store=store)
print(f"synced={result.synced}, errors={result.errors}")
```

### 4.5 异步 API

代码示例：异步认证（Python）

```python
import asyncio
from maop.core.security.ldap_provider import authenticate_async

async def main():
    result = await authenticate_async(provider, "alice", "password")
    print(result.authenticated, result.roles)

asyncio.run(main())
```

## 第5章 MCP 工具市场集成

### 5.1 注册 MCP 服务器

命令示例：注册 MCP 服务器

```bash
curl -X POST http://localhost:8000/api/v1/mcp/servers \
  -H "X-API-Key: your-api-key" \
  -d '{
    "name": "github-tools",
    "transport": "stdio",
    "command": "npx",
    "args": ["-y", "@modelcontextprotocol/server-github"]
  }'
```

### 5.2 调用 MCP 工具

命令示例：调用 MCP 工具

```bash
curl -X POST http://localhost:8000/api/v1/mcp/tools/call \
  -H "X-API-Key: your-api-key" \
  -d '{
    "server": "github-tools",
    "tool": "create_issue",
    "arguments": {
      "owner": "your-org",
      "repo": "your-repo",
      "title": "Bug: ...",
      "body": "..."
    }
  }'
```