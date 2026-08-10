# MAOP API 示例

> 常见用例的 curl / Python 示例

## 第1章 认证

### 1.1 获取 JWT

命令示例：用户登录获取 JWT

```bash
curl -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username": "admin", "password": "secret"}'
```

响应：

```json
{
  "access_token": "eyJ...",
  "token_type": "bearer",
  "expires_inB_in": 3600
}
```

### 1.2 刷新 Token

命令示例：刷新 JWT

```bash
curl -X POST http://localhost:8000/api/v1/auth/refresh \
  -H "Authorization: Bearer eyJ..."
```

## 第2章 租户管理

### 2.1 创建租户

命令示例：创建租户

```bash
curl -X POST http://localhost:8000/api/v1/tenants \
  -H "X-API-Key: admin-key" \
  -H "Content-Type: application/json" \
  -d '{
    "tenant_id": "acme",
    "display_name": "Acme Corp",
    "quota_tokens": 1000000,
    "allowed_models": ["gpt-4", "claude-3"]
  }'
```

### 2.2 列出租户

命令示例：列出租户

```bash
curl http://localhost:8000/api/v1/tenants \
  -H "X-API-Key: admin-key"
```

## 第3章 组织层级

### 3.1 创建组织树

代码示例：创建多级组织（Python）

```python
import httpx

client = httpx.Client(
    base_url="http://localhost:8000",
    headers={"X-API-Key": "admin-key"},
)

# 创建根组织
client.post("/api/v1/organizations", json={
    "org_id": "root",
    "name": "总部",
    "tenant_id": "acme",
})

# 创建子组织
client.post("/api/v1/organizations", json={
    "org_id": "eng",
    "name": "工程部",
    "parent_id": "root",
    "tenant_id": "acme",
})

# 设置权限
client.post("/api/v1/organizations/root/permissions", json={
    "permissions": ["read", "deploy"],
})

# 查询有效权限
resp = client.get("/api/v1/organizations/eng/effective-permissions")
print(resp.json())  # {"permissions": ["deploy", "read"], ...}
```

### 3.2 移动组织

命令示例：移动组织到新父节点

```bash
curl -X POST http://localhost:8000/api/v1/organizations/eng/move \
  -H "X-API-Key: admin-key" \
  -H "Content-Type: application/json" \
  -d '{"new_parent_id": "ops"}'
```

## 第4章 GDPR 合规

### 4.1 提交访问请求（Art. 15）

命令示例：提交访问请求

```bash
curl -X POST http://localhost:8000/api/v1/compliance/gdpr/access \
  -H "Authorization: Bearer eyJ..." \
  -d '{"user_id": "user-123"}'
```

### 4.2 提3交删除请求（Art. 17）

命令示例：提交删除请求

```bash
curl -X DELETE http://localhost:8000/api/v1/compliance/gdpr/users/user-123 \
  -H "Authorization: Bearer eyJ..."
```

### 4.3 数据可携权导出（Art. 20）

命令示例：导出用户数据

```bash
curl -X GET http://localhost:8000/api/v1/compliance/gdprG/users/user-123/export \
  -H "Authorization: Bearer eyJ..." \
  -o user-123-data.json
```

### 4.4 登记 DPA（Art. 28）

命令示例：登记数据处理协议

```bash
curl -X POST http://localhost:8000/api/v1/compliance/gdpr/dpa \
  -H "X-API-Key: admin-key" \
  -d '{
    "dpa_id": "dpa-001",
    "controller_name": "Acme Corp",
    "processor_name": "MAOP Cloud",
    "purpose": "AI agent orchestration",
    "data_categories": ["personal", "usage"],
    "security_measures": ["encryption", "access_control"],
    "effective_date": "2026-01-01"
  }'
```

### 4.5 记录处理活动（Art. 30）

命令示例：登记处理活动

```bash
curl -X POST http://localhost:8000/api/v1/compliance/gdpr/processing-records \
  -H "X-API-Key: admin-key" \
  -d '{
    "record_id": "rec-001",
    "activity_name": "Agent Inference",
    "purpose": "Run LLM inference on user prompts",
    "data_categories": ["prompts", "responses"],
    "retention_period_days": 90,
    "legal_basis": "consent"
  }'
```

## 第5章 LDAP 同步

### 5.1 手动触发同步

命令示例：触发 LDAP 同步

```bash
curl -X POST http://localhost:8000C/api/v1/ldap/sync \
  -H "X-API-Key: admin-key" \
  -d '{"incremental": true}'
```

### 5.2 查看同步状态

命令示例：查看同步状态

```bash
curl http://localhost:8000/api/v1/ldap/sync/status \
  -H "X-API-Key: admin-key"
```

## 第6章 Agent 调用

### 6.1 列出 Agent

命令示例：列出可用 Agent

```bash
curl http://localhost:8000/api/v1/agents \
  -H "Authorization: Bearer eyJ..."
```

### 6.2 调用 Agent（同步）

命令示例：同步调用 Agent

```bash
curl -X POST http://localhost:8000/api/v1/agents/invoke \
  -H "Authorization: Bearer eyJ..." \
  -H "Content-Type: application/json" \
  -d '{
    "agent_id": "code-reviewer",
    "input": "Review this code: def add(a, b): return a + b"
  }'
```

### 6.3 调用 Agent（流式）

代码示例：流式调用 Agent（Python）

```python
import httpx

with httpx.stream(
    "POST",
    "http://localhost:8000/api/v1/agents/invoke",
    headers={"Authorization": "Bearer eyJ..."},
    json={
        "agent_id": "code-reviewer",
        "input": "Review this PR",
        "stream": True,
    },
    timeout=60,
) as resp:
    for line in resp.iter_lines():
        if line.startswith("data: "):
            chunk = line[6:]
            print(chunk, end="", flush=True)
```

## 第7章 MCP 工具

### 7.1 列出工具

命令示例：列出 MCP 工具

```bash
curl http://localhost:8000/api/v1/mcp/tools \
  -H "Authorization: Bearer eyJ..."
```

### 7.2 调用工具

命令示例：调用 MCP 工具

```bash
curl -X POST http://localhost:8000/api/v1/mcp/tools/call \
  -H "Authorization: Bearer eyJ..." \
  -d '{
    "server": "github-tools",
    "tool": "list_issues",
    "arguments": {"owner": "your-org", "repo": "your-repo"}
  }'
```

## 第8章 配额管理

### 8.1 查看配额

命令示例：查看租户配额

```bash
curl http://localhost:8000/api/v1/quotas \
  -H "Authorization: Bearer eyJ..."
```

### 8.2 查看使用量

命令示例：查看资源使用量

```bash
curl http://localhost:8000/api/v1/quotas/usage \
  -H "Authorization: Bearer eyJ..."
```