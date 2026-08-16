# SSO 集成 PRD — OIDC + SAML 2.0 对接 Keycloak/Azure AD

## 文档信息

| 字段 | 值 |
|------|-----|
| 文档名称 | SSO 集成产品需求文档 |
| 版本 | v1.0.0-draft |
| 发布日期 | 2026-08-13 |
| 作者 | MAOP Coding Engineer |
| 状态 | Draft - Pending Review |
| 适用范围 | MAOP Enterprise Edition |
| FeatureFlag | `FeatureFlag.SSO` |
| 路由前缀 | `/api/sso`（管理）+ `/api/v1/sso`（版本化） |
| 前端路由 | `/sso` |
| 导航分组 | Govern |
| i18n key 前缀 | `view.sso.*` |
| 关联文档 | [project-structure-analysis.md](./archive/audits/project-structure-analysis.md)、[saml-sso-guide.md](./enterprise/saml-sso-guide.md)、[database-schema.md](./database-schema.md) |
| 文档约定 | Markdown heading：H1 文档名 / H2 章 / H3 节 / H4 子节 / H5 子子节；中文撰写，技术术语保留英文 |

---

## 第1章 功能概述

### 1.1 功能目标

为 MAOP Enterprise Edition 提供完整的 SSO（Single Sign-On）集成能力，支持 OIDC（OpenID Connect）和 SAML 2.0 两种协议，对接企业身份提供商（Identity Provider, IdP），如 Keycloak、Azure AD、Okta、ADFS 等。使企业员工能够使用现有企业账号登录 MAOP，无需单独创建和维护本地账号。

### 1.2 背景

MAOP 已具备基础的 SSO 骨架代码：

- `py/maop/enterprise/sso.py` — `SSOManager` 已实现 OIDC（Authorization Code Flow）和 SAML 2.0（SP-initiated SSO + XML 签名验证，委托给 `maop.enterprise.saml_handler.SAMLHandler`）。
- `py/maop/dashboard/routers/sso.py` — 已有基础路由：`/api/sso/authorize`、`/api/sso/callback`、`/api/sso/logout`、`/api/sso/validate`、`/api/sso/config`。
- `py/maop/enterprise/saml_handler.py` — SAML XML 签名验证、Conditions 校验、NameID/Attribute 提取。

**现有局限**：

| 局限 | 说明 |
|------|------|
| 单 IdP 配置 | 仅通过环境变量 `MAOP_SSO_*` 配置一个 IdP，无法支持多组织多 IdP 场景 |
| 无持久化 | IdP 配置存于环境变量，无法动态增删改，重启后丢失运行时状态 |
| 无管理 UI | 前端无 SSO 管理页面，管理员无法在 Dashboard 中配置 IdP |
| 无属性映射配置 | 用户属性映射（IdP claims → 系统字段）硬编码在 `_build_user_from_claims`，无法自定义 |
| 无连接测试 | 无法在保存前验证 IdP 配置是否正确 |
| 无 Metadata 导出 | SAML SP Metadata 无法导出，IdP 端配置需手工拼装 |
| 无 PKCE | OIDC 未实现 PKCE（Proof Key for Code Exchange），安全性不足 |
| 无自动跳转 | 单 IdP 场景下无法自动跳转到 IdP 登录页 |

### 1.3 范围

#### 1.3.1 In Scope（本 PRD 覆盖）

- OIDC IdP 配置管理（CRUD + 连接测试）
- SAML 2.0 IdP 配置管理（CRUD + 连接测试 + SP Metadata 导出）
- 多 IdP 同时配置与启用
- 用户属性映射（IdP claims → 系统用户字段/角色）
- JIT Provisioning（首次 SSO 登录自动创建用户）
- SSO 登录后自动签发 JWT token（与现有 `auth.py` 体系打通）
- IdP 管理 UI（SSO 管理页面 + 添加/编辑对话框）
- 登录页 IdP 按钮展示
- 单 IdP 自动跳转
- PKCE（OIDC Authorization Code Flow + PKCE）
- SAML SP Metadata 端点

#### 1.3.2 Out of Scope（本 PRD 不覆盖）

- SLO（Single Logout）跨协议联动（仅支持本地 session 失效）
- OIDC Discovery 自动配置（`/.well-known/openid-configuration` 自动拉取）— 后续迭代
- IdP-initiated SAML SSO（仅支持 SP-initiated）
- 社交登录（Google/GitHub 等）— 非 enterprise IdP
- MFA 多因子认证（由 IdP 端负责）
- SSO 审计报表（复用现有 `audit_events` 表，本 PRD 不单独设计报表）

---

## 第2章 用户故事

### 2.1 管理员故事

| 编号 | 用户故事 | 优先级 |
|------|----------|--------|
| US-A01 | 作为管理员，我想配置 OIDC IdP（如 Azure AD），以便员工用企业账号登录 | P0 |
| US-A02 | 作为管理员，我想配置 SAML 2.0 IdP（如 Keycloak），以便对接传统身份系统 | P0 |
| US-A03 | 作为管理员，我想配置用户属性映射，以便 IdP 用户自动匹配系统角色 | P0 |
| US-A04 | 作为管理员，我想支持多个 IdP，以便不同组织使用不同身份系统 | P1 |
| US-A05 | 作为管理员，我想启用自动跳转，以便用户直接跳转到 IdP 登录页 | P1 |
| US-A06 | 作为管理员，我想测试 IdP 连接，以便在保存前验证配置是否正确 | P0 |
| US-A07 | 作为管理员，我想导出 SAML SP Metadata，以便在 IdP 端快速配置 SP | P1 |
| US-A08 | 作为管理员，我想启用/禁用 IdP，以便临时停用某个 IdP 而不删除配置 | P0 |
| US-A09 | 作为管理员，我想查看 IdP 配置详情，以便确认当前配置参数 | P0 |

### 2.2 终端用户故事

| 编号 | 用户故事 | 优先级 |
|------|----------|--------|
| US-U01 | 作为用户，我想通过 IdP 登录，以便无需单独创建账号 | P0 |
| US-U02 | 作为用户，我想在登录页看到可用的 SSO 选项，以便选择对应的 IdP 登录 | P0 |
| US-U03 | 作为用户，我想 SSO 登录后自动进入 Dashboard，以便无需二次登录 | P0 |

### 2.3 系统故事

| 编号 | 用户故事 | 优先级 |
|------|----------|--------|
| US-S01 | 作为系统，我想在首次 SSO 登录时自动创建用户（JIT provisioning），以便减少管理员手工 provisioning 成本 | P0 |
| US-S02 | 作为系统，我想在 SSO 登录后签发 JWT token，以便与现有认证体系无缝集成 | P0 |
| US-S03 | 作为系统，我想加密存储 client_secret 和 x509_cert，以便防止敏感凭据泄露 | P0 |
| US-S04 | 作为系统，我想在 Personal 版隐藏所有 SSO 功能，以便保持 edition 隔离 | P0 |

---

## 第3章 数据模型设计

### 3.1 sso_providers 表

IdP 配置持久化表，支持 SQLite（Personal 开发测试）和 PostgreSQL（Enterprise 生产）。

```sql
-- SQL：sso_providers 建表语句
CREATE TABLE IF NOT EXISTS sso_providers (
    id                  SERIAL PRIMARY KEY,           -- PG; SQLite 用 INTEGER PRIMARY KEY AUTOINCREMENT
    name                TEXT NOT NULL,                -- IdP 显示名称，如 "Corporate Azure AD"
    protocol            TEXT NOT NULL,                -- 'oidc' | 'saml'
    tenant_id           TEXT DEFAULT '',              -- 关联租户（空表示全局可用）
    enabled             INTEGER NOT NULL DEFAULT 1,   -- 0=禁用, 1=启用
    auto_redirect       INTEGER NOT NULL DEFAULT 0,   -- 0=不自动跳转, 1=自动跳转（单 IdP 场景）
    config              JSONB DEFAULT '{}',           -- PG; SQLite 用 TEXT（存 JSON 字符串）
    attribute_mapping   JSONB DEFAULT '{}',           -- PG; SQLite 用 TEXT（存 JSON 字符串）
    created_at          DOUBLE PRECISION NOT NULL DEFAULT 0,
    updated_at          DOUBLE PRECISION NOT NULL DEFAULT 0,
    UNIQUE(name, tenant_id)                            -- 同租户下名称唯一
);
-- 索引
CREATE INDEX IF NOT EXISTS idx_sso_providers_tenant ON sso_providers(tenant_id);
CREATE INDEX IF NOT EXISTS idx_sso_providers_enabled ON sso_providers(enabled);
CREATE INDEX IF NOT EXISTS idx_sso_providers_protocol ON sso_providers(protocol);
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | SERIAL / INTEGER | 主键，自增 |
| `name` | TEXT | IdP 显示名称，同租户下唯一 |
| `protocol` | TEXT | 协议类型：`oidc` 或 `saml` |
| `tenant_id` | TEXT | 关联租户 ID，空字符串表示全局可用 |
| `enabled` | INTEGER | 启用状态：0=禁用，1=启用 |
| `auto_redirect` | INTEGER | 是否自动跳转：0=否，1=是（仅单 IdP + 启用时生效） |
| `config` | JSONB / TEXT | 协议特定配置（见 3.2 / 3.3），敏感字段加密存储 |
| `attribute_mapping` | JSONB / TEXT | 属性映射配置（见 3.4） |
| `created_at` | DOUBLE PRECISION | 创建时间（Unix timestamp） |
| `updated_at` | DOUBLE PRECISION | 更新时间（Unix timestamp） |

### 3.2 OIDC config 结构

`config` JSON 字段在 `protocol = 'oidc'` 时的结构：

```json
// 代码示例：OIDC config JSON 结构
{
  "client_id": "maop-azure-ad-client",
  "client_secret_enc": "<Fernet加密后的密文>",
  "issuer_url": "https://login.microsoftonline.com/<tenant-id>/v2.0",
  "authorize_url": "https://login.microsoftonline.com/<tenant-id>/oauth2/v2.0/authorize",
  "token_url": "https://login.microsoftonline.com/<tenant-id>/oauth2/v2.0/token",
  "userinfo_url": "https://graph.microsoft.com/oidc/userinfo",
  "redirect_uri": "https://maop.example.com/api/v1/sso/oidc/{provider_id}/callback",
  "scopes": ["openid", "profile", "email"],
  "use_pkce": true
}
```

| 字段 | 必需 | 加密 | 说明 |
|------|------|------|------|
| `client_id` | ✅ | ❌ | OIDC 客户端 ID |
| `client_secret_enc` | ✅ | ✅ | Fernet 加密后的 client_secret |
| `issuer_url` | ❌ | ❌ | Issuer URL（用于 Discovery，可选） |
| `authorize_url` | ✅ | ❌ | Authorization endpoint |
| `token_url` | ✅ | ❌ | Token endpoint |
| `userinfo_url` | ❌ | ❌ | UserInfo endpoint（可选，不从 id_token 解析时使用） |
| `redirect_uri` | ✅ | ❌ | 回调 URI，含 `{provider_id}` 占位符 |
| `scopes` | ❌ | ❌ | OAuth scopes，默认 `["openid", "profile", "email"]` |
| `use_pkce` | ❌ | ❌ | 是否启用 PKCE，默认 `true` |

### 3.3 SAML config 结构

`config` JSON 字段在 `protocol = 'saml'` 时的结构：

```json
// 代码示例：SAML config JSON 结构
{
  "entity_id": "maop-sp",
  "sso_url": "https://keycloak.example.com/realms/corporate/protocol/saml",
  "slo_url": "https://keycloak.example.com/realms/corporate/protocol/saml/logout",
  "x509_cert_enc": "<Fernet加密后的X509证书base64>",
  "name_id_format": "urn:oasis:names:tc:SAML:2.0:nameid-format:emailAddress",
  "acs_url": "https://maop.example.com/api/v1/sso/saml/{provider_id}/acs",
  "sp_entity_id": "maop-sp",
  "want_signed": true
}
```

| 字段 | 必需 | 加密 | 说明 |
|------|------|------|------|
| `entity_id` | ✅ | ❌ | IdP Entity ID |
| `sso_url` | ✅ | ❌ | IdP Single Sign-On Service URL |
| `slo_url` | ❌ | ❌ | IdP Single Logout Service URL（可选） |
| `x509_cert_enc` | ✅ | ✅ | Fernet 加密后的 IdP X.509 证书（base64 DER） |
| `name_id_format` | ❌ | ❌ | NameID Format，默认 `emailAddress` |
| `acs_url` | ✅ | ❌ | SP Assertion Consumer Service URL，含 `{provider_id}` 占位符 |
| `sp_entity_id` | ✅ | ❌ | SP Entity ID（本系统） |
| `want_signed` | ❌ | ❌ | 是否要求签名 Response，默认 `true` |

### 3.4 属性映射结构

`attribute_mapping` JSON 字段定义 IdP claims/attributes 到系统用户字段的映射：

```json
// 代码示例：属性映射 JSON 结构
{
  "external_id": "sub",
  "email": "email",
  "display_name": "name",
  "roles": "groups",
  "tenant_id": "tid"
}
```

| 系统字段 | 默认 IdP claim | 说明 |
|----------|----------------|------|
| `external_id` | `sub`（OIDC）/ NameID（SAML） | 外部用户唯一标识 |
| `email` | `email` | 邮箱 |
| `display_name` | `name` | 显示名称 |
| `roles` | `groups` | 角色列表（映射到系统角色） |
| `tenant_id` | `tid` | 租户 ID |

**角色映射规则**：IdP 返回的 `roles`/`groups` 值通过 `role_mapping`（可选，在 `attribute_mapping` 内）映射到系统角色：

```json
// 代码示例：角色映射规则
{
  "roles": "groups",
  "role_mapping": {
    "admins": "admin",
    "developers": "operator",
    "viewers": "viewer"
  }
}
```

### 3.5 与现有模块的关系

#### 3.5.1 与 `enterprise/sso.py` 的关系

现有 `SSOManager` 接受单个 `SSOConfig`，本 PRD 扩展为多 IdP 模式：

- `SSOManager` 保持不变，每个 IdP 对应一个 `SSOManager` 实例。
- 新增 `SSOProviderRegistry`（`enterprise/sso_registry.py`）管理多个 `SSOManager` 实例，从 `sso_providers` 表加载。
- `SSOConfig` 新增 `use_pkce` 字段和 `attribute_mapping` 字段。

#### 3.5.2 与 `dashboard/routers/auth.py` 的关系

SSO 登录成功后，需要与现有认证体系打通：

1. `SSOManager.handle_callback()` 返回 `SSOSession`（含 `SSOUser`）。
2. 新增 `_provision_or_update_user()` 将 `SSOUser` 同步到 `users` 表（JIT provisioning）。
3. 调用 `AuthManager.jwt_handler.create_token()` 签发 JWT，返回给前端。
4. 前端 `useApiStore().setAuthToken(token, user)` 完成登录态注入。

#### 3.5.3 与 `users` 表的关系

JIT provisioning 在 `users` 表中创建/更新记录：

- **首次登录**：`INSERT INTO users`，`username` = `{provider}:{external_id}`，`password_hash` = 空（SSO 用户无本地密码），`roles` = 映射后的角色。
- **后续登录**：`UPDATE users SET roles=...`（同步 IdP 端角色变更），不更新密码。
- **查询**：通过 `username` 精确匹配，`username` 格式为 `{protocol}:{external_id}`（如 `oidc:azure-ad-user-oid`）。

#### 3.5.4 与 `audit_events` 表的关系

所有 SSO 管理操作和登录事件记录到 `audit_events`：

| action | 触发时机 |
|--------|----------|
| `sso.provider.create` | 创建 IdP |
| `sso.provider.update` | 更新 IdP |
| `sso.provider.delete` | 删除 IdP |
| `sso.provider.test` | 测试连接 |
| `sso.login.success` | SSO 登录成功 |
| `sso.login.failure` | SSO 登录失败 |
| `sso.user.provisioned` | JIT provisioning 创建用户 |

---

## 第4章 API 设计

### 4.1 路由总览

所有端点挂载在 `APIRouter(prefix="/api/v1/sso", tags=["sso"])`，同时通过 `_register_v1_aliases()` 兼容 `/api/sso` 无版本前缀路径。

| 方法 | 路径 | 用途 | 权限 | 守卫 |
|------|------|------|------|------|
| POST | `/api/v1/sso/providers` | 添加 IdP 配置 | `require_admin` | `FeatureFlag.SSO` |
| GET | `/api/v1/sso/providers` | 列出所有 IdP | `require_admin` | `FeatureFlag.SSO` |
| GET | `/api/v1/sso/providers/{id}` | 查看 IdP 详情 | `require_admin` | `FeatureFlag.SSO` |
| PUT | `/api/v1/sso/providers/{id}` | 更新 IdP 配置 | `require_admin` | `FeatureFlag.SSO` |
| DELETE | `/api/v1/sso/providers/{id}` | 删除 IdP | `require_admin` | `FeatureFlag.SSO` |
| POST | `/api/v1/sso/providers/{id}/test` | 测试 IdP 连接 | `require_admin` | `FeatureFlag.SSO` |
| GET | `/api/v1/sso/providers/{id}/metadata` | SAML SP Metadata | 公开 | `FeatureFlag.SSO` |
| GET | `/api/v1/sso/oidc/{provider_id}/login` | OIDC 登录跳转 | 公开 | `FeatureFlag.SSO` |
| GET | `/api/v1/sso/oidc/{provider_id}/callback` | OIDC 回调 | 公开 | `FeatureFlag.SSO` |
| GET | `/api/v1/sso/saml/{provider_id}/login` | SAML 登录跳转 | 公开 | `FeatureFlag.SSO` |
| POST | `/api/v1/sso/saml/{provider_id}/acs` | SAML ACS 端点 | 公开 | `FeatureFlag.SSO` |
| GET | `/api/v1/sso/enabled` | 列出已启用 IdP（登录页用） | 公开 | `FeatureFlag.SSO` |

### 4.2 请求/响应模型

#### 4.2.1 创建 IdP

```python
# 代码示例：创建 IdP 请求模型（Python）
class CreateProviderRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    protocol: str = Field(pattern="^(oidc|saml)$")
    tenant_id: str = Field(default="", max_length=100)
    enabled: bool = True
    auto_redirect: bool = False
    config: dict[str, Any] = Field(default_factory=dict)
    attribute_mapping: dict[str, Any] = Field(default_factory=dict)
```

```json
// 代码示例：创建 OIDC IdP 请求体
{
  "name": "Corporate Azure AD",
  "protocol": "oidc",
  "tenant_id": "",
  "enabled": true,
  "auto_redirect": false,
  "config": {
    "client_id": "maop-azure-ad-client",
    "client_secret": "<明文secret，服务端加密后存储>",
    "issuer_url": "https://login.microsoftonline.com/<tenant>/v2.0",
    "authorize_url": "https://login.microsoftonline.com/<tenant>/oauth2/v2.0/authorize",
    "token_url": "https://login.microsoftonline.com/<tenant>/oauth2/v2.0/token",
    "userinfo_url": "https://graph.microsoft.com/oidc/userinfo",
    "redirect_uri": "https://maop.example.com/api/v1/sso/oidc/{provider_id}/callback",
    "scopes": ["openid", "profile", "email"],
    "use_pkce": true
  },
  "attribute_mapping": {
    "external_id": "sub",
    "email": "email",
    "display_name": "name",
    "roles": "groups"
  }
}
```

**响应**：

```json
// 代码示例：创建 IdP 响应体
{
  "status": "ok",
  "provider": {
    "id": 1,
    "name": "Corporate Azure AD",
    "protocol": "oidc",
    "tenant_id": "",
    "enabled": true,
    "auto_redirect": false,
    "config": {
      "client_id": "maop-azure-ad-client",
      "client_secret": "<已脱敏，返回***>",
      "issuer_url": "https://login.microsoftonline.com/<tenant>/v2.0",
      "authorize_url": "...",
      "token_url": "...",
      "userinfo_url": "...",
      "redirect_uri": "...",
      "scopes": ["openid", "profile", "email"],
      "use_pkce": true
    },
    "attribute_mapping": { "...": "..." },
    "created_at": 1723536000.0,
    "updated_at": 1723536000.0
  }
}
```

> **脱敏规则**：所有响应中的 `client_secret` / `x509_cert` 字段返回 `"***"`，绝不回传明文。

#### 4.2.2 列出 IdP

```python
# 代码示例：列出 IdP 端点签名（Python）
@router.get("/providers")
@handle_api_errors
async def list_providers(
    request: Request,
    protocol: str = "",      # 可选过滤：oidc / saml
    enabled: bool | None = None,  # 可选过滤
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    ...
```

```json
// 代码示例：列出 IdP 响应体
{
  "status": "ok",
  "providers": [ { "...": "..." } ],
  "count": 3,
  "total": 3
}
```

#### 4.2.3 测试 IdP 连接

```json
// 代码示例：测试连接响应体（成功）
{
  "status": "ok",
  "reachable": true,
  "protocol": "oidc",
  "details": {
    "authorize_url_resolved": true,
    "token_url_resolved": true,
    "userinfo_url_resolved": true,
    "discovery_fetched": false,
    "latency_ms": 127
  }
}
```

```json
// 代码示例：测试连接响应体（失败）
{
  "status": "ok",
  "reachable": false,
  "protocol": "oidc",
  "error": "token_url unreachable: HTTP 401",
  "details": {
    "authorize_url_resolved": true,
    "token_url_resolved": false,
    "latency_ms": 5023
  }
}
```

#### 4.2.4 SAML SP Metadata

```xml
<!-- 代码示例：SAML SP Metadata 响应（XML） -->
<EntityDescriptor xmlns="urn:oasis:names:tc:SAML:2.0:metadata"
                  entityID="maop-sp">
  <SPSSODescriptor protocolSupportEnumeration="urn:oasis:names:tc:SAML:2.0:protocol">
    <AssertionConsumerService
      index="0"
      Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST"
      Location="https://maop.example.com/api/v1/sso/saml/1/acs" />
  </SPSSODescriptor>
</EntityDescriptor>
```

#### 4.2.5 列出已启用 IdP（登录页用）

公开端点，供登录页渲染 IdP 按钮，**不返回敏感配置**：

```json
// 代码示例：已启用 IdP 列表响应体
{
  "status": "ok",
  "providers": [
    {
      "id": 1,
      "name": "Corporate Azure AD",
      "protocol": "oidc",
      "auto_redirect": false
    },
    {
      "id": 2,
      "name": "Keycloak",
      "protocol": "saml",
      "auto_redirect": false
    }
  ],
  "count": 2,
  "auto_redirect_provider_id": null
}
```

> `auto_redirect_provider_id`：当仅有一个启用的 IdP 且其 `auto_redirect=true` 时返回该 IdP ID，前端据此自动跳转。

### 4.3 OIDC 登录流程

```text
图：OIDC Authorization Code Flow + PKCE 流程图

前端                MAOP SP              IdP (Azure AD)
 │                    │                      │
 │── 点击IdP按钮 ──→│                      │
 │                    │                      │
 │                    │── 生成 code_verifier │
 │                    │   code_challenge     │
 │                    │   = SHA256(verifier) │
 │                    │   state = random()   │
 │                    │                      │
 │←── 302 Redirect ──│                      │
 │   to IdP authorize │                      │
 │   +code_challenge  │                      │
 │   +state           │                      │
 │                    │                      │
 │── 用户认证 ─────────────────────────────→│
 │                    │                      │
 │←── 302 Redirect ────────────────────────│
 │   to SP callback   │                      │
 │   +code            │                      │
 │   +state           │                      │
 │                    │                      │
 │── GET callback ──→│                      │
 │   +code+state      │                      │
 │                    │                      │
 │                    │── POST token ──────→│
 │                    │   grant_type=        │
 │                    │   authorization_code │
 │                    │   +code_verifier     │
 │                    │   +code              │
 │                    │                      │
 │                    │←── access_token ────│
 │                    │   id_token           │
 │                    │                      │
 │                    │── JIT provisioning   │
 │                    │   签发 JWT           │
 │                    │                      │
 │←── 302 Redirect ──│                      │
 │   to Dashboard     │                      │
 │   +maop_token      │                      │
 │   cookie           │                      │
```

### 4.4 SAML 登录流程

```text
图：SAML SP-Initiated SSO 流程图

前端                MAOP SP              IdP (Keycloak)
 │                    │                      │
 │── 点击IdP按钮 ──→│                      │
 │                    │                      │
 │                    │── 构造 AuthnRequest  │
 │                    │   DEFLATE+Base64     │
 │                    │   RelayState=state   │
 │                    │                      │
 │←── 302 Redirect ──│                      │
 │   to IdP SSO URL   │                      │
 │   +SAMLRequest     │                      │
 │   +RelayState      │                      │
 │                    │                      │
 │── 用户认证 ─────────────────────────────→│
 │                    │                      │
 │←── HTTP-POST ────────────────────────────│
 │   SAMLResponse     │                      │
 │   RelayState       │                      │
 │                    │                      │
 │── POST ACS ──────→│                      │
 │   +SAMLResponse    │                      │
 │   +RelayState      │                      │
 │                    │                      │
 │                    │── 验证 XML 签名      │
 │                    │   验证 Conditions    │
 │                    │   提取 NameID        │
 │                    │   提取 Attributes    │
 │                    │                      │
 │                    │── JIT provisioning   │
 │                    │   签发 JWT           │
 │                    │                      │
 │←── 302 Redirect ──│                      │
 │   to Dashboard     │                      │
 │   +maop_token      │                      │
 │   cookie           │                      │
```

### 4.5 错误响应

统一使用 `ErrorSchema`：

```json
// 代码示例：SSO 错误响应体
{
  "status": "error",
  "error": "SSO provider not found",
  "code": "SSO_PROVIDER_NOT_FOUND",
  "detail": "No SSO provider with id=99",
  "request_id": "req_abc123"
}
```

| HTTP 状态 | code | 触发场景 |
|-----------|------|----------|
| 404 | `SSO_NOT_AVAILABLE` | Personal 版访问 SSO 端点 |
| 404 | `SSO_PROVIDER_NOT_FOUND` | IdP ID 不存在 |
| 400 | `SSO_CONFIG_INVALID` | 配置字段缺失或格式错误 |
| 400 | `SSO_CALLBACK_ERROR` | IdP 回调返回 error |
| 401 | `SSO_TOKEN_EXCHANGE_FAILED` | Token endpoint 拒绝 |
| 403 | `SSO_SIGNATURE_INVALID` | SAML 签名验证失败 |
| 403 | `SSO_CONDITIONS_INVALID` | SAML Conditions 校验失败（Audience/时间） |
| 409 | `SSO_PROVIDER_NAME_CONFLICT` | 同租户下名称重复 |

---

## 第5章 UI 设计

### 5.1 SSO 管理页面（/sso）

使用 `ListPageLayout` + `DataTable` 组件，遵循项目列表页骨架规范。

```vue
<!-- 代码示例：SSO 管理页面结构（Vue） -->
<template>
  <ListPageLayout
    :loading="loading"
    :error="error"
    :empty="!providers.length"
    :filter-schema="filterSchema"
    search-key="query"
    :search-placeholder="t('common.search')"
    :results-label="`${providers.length} ${t('view.sso.providers')}`"
    :error-title="t('view.sso.loadError')"
    :empty-title="t('view.sso.noProviders')">
    <template #badges>
      <Badge tone="brand">{{ t('view.sso.enterprise') }}</Badge>
    </template>
    <template #actions>
      <button class="btn btn--primary" @click="openCreate">
        <AppIcon name="plus" :size="15" /> {{ t('view.sso.addProvider') }}
      </button>
    </template>
    <template #content="{ filters }">
      <DataTable
        :columns="columns"
        :rows="filteredProviders(filters)"
        :loading="loading"
        row-key="id"
        sortable
        compact>
        <template #row-actions="{ row }">
          <button @click="testProvider(row.id)">{{ t('view.sso.test') }}</button>
          <button @click="toggleProvider(row)">{{ row.enabled ? t('common.disable') : t('common.enable') }}</button>
          <button @click="editProvider(row)">{{ t('common.edit') }}</button>
          <button v-if="row.protocol === 'saml'" @click="downloadMetadata(row.id)">
            {{ t('view.sso.metadata') }}
          </button>
          <button @click="deleteProvider(row)">{{ t('common.delete') }}</button>
        </template>
      </DataTable>
    </template>
  </ListPageLayout>

  <!-- 添加/编辑对话框 -->
  <ProviderDialog
    v-if="showDialog"
    :provider="editingProvider"
    @close="showDialog = false"
    @saved="onSaved" />
</template>
```

**DataTable 列定义**：

| 列 key | label | type | 说明 |
|--------|-------|------|------|
| `name` | 名称 | text | IdP 显示名称 |
| `protocol` | 协议 | badge | `oidc` / `saml`，badge 颜色区分 |
| `enabled` | 状态 | bool-icon | 启用/禁用 |
| `auto_redirect` | 自动跳转 | bool-icon | 是/否 |
| `created_at` | 创建时间 | time | 相对时间 |
| `updated_at` | 更新时间 | time | 相对时间 |

**FilterBar 过滤器**：

```javascript
// 代码示例：SSO 页面过滤器定义（JavaScript）
const filterSchema = [
  { key: 'protocol', label: t('view.sso.protocol'),
    options: [{ value: 'oidc', label: 'OIDC' }, { value: 'saml', label: 'SAML 2.0' }] },
  { key: 'enabled', label: t('common.status'),
    options: [{ value: true, label: t('common.enabled') }, { value: false, label: t('common.disabled') }] },
];
```

### 5.2 添加/编辑 IdP 对话框

使用 Modal 组件（`v-modal-a11y`），表单根据协议动态切换。

#### 5.2.1 通用字段

| 字段 | 控件 | 校验 |
|------|------|------|
| 名称 | text input | 必填，1-100 字符 |
| 协议 | Segmented（OIDC / SAML 2.0） | 必选，创建后不可改 |
| 启用 | toggle switch | 默认启用 |
| 自动跳转 | toggle switch | 默认关闭 |

#### 5.2.2 OIDC 表单

| 字段 | 控件 | 校验 |
|------|------|------|
| Issuer URL | text input | 可选，HTTPS |
| Authorize URL | text input | 必填，HTTPS |
| Token URL | text input | 必填，HTTPS |
| UserInfo URL | text input | 可选，HTTPS |
| Client ID | text input | 必填 |
| Client Secret | password input | 必填（编辑时留空表示不修改） |
| Redirect URI | text input（预填模板） | 必填，含 `{provider_id}` |
| Scopes | tag input | 默认 `openid profile email` |
| 启用 PKCE | toggle switch | 默认启用 |

#### 5.2.3 SAML 表单

| 字段 | 控件 | 校验 |
|------|------|------|
| SP Entity ID | text input | 必填 |
| IdP Entity ID | text input | 必填 |
| IdP SSO URL | text input | 必填，HTTPS |
| IdP SLO URL | text input | 可选，HTTPS |
| ACS URL | text input（预填模板） | 必填，含 `{provider_id}` |
| X509 证书 | textarea（base64） | 必填，PEM 或 base64 DER |
| NameID Format | select | 默认 `emailAddress` |
| 要求签名 | toggle switch | 默认启用 |

#### 5.2.4 属性映射配置

| 字段 | 控件 | 说明 |
|------|------|------|
| external_id 映射 | text input | IdP claim 名称 |
| email 映射 | text input | IdP claim 名称 |
| display_name 映射 | text input | IdP claim 名称 |
| roles 映射 | text input | IdP claim 名称 |
| 角色映射表 | key-value 编辑器 | IdP 组名 → 系统角色 |

### 5.3 登录页 IdP 按钮

在现有登录页（`Login.vue` 或 `App.vue` 登录态）底部追加 SSO 选项区域：

```vue
<!-- 代码示例：登录页 SSO 按钮区域（Vue） -->
<template>
  <div v-if="ssoProviders.length" class="sso-section">
    <div class="sso-divider">{{ t('auth.orContinueWith') }}</div>
    <div class="sso-buttons">
      <button
        v-for="provider in ssoProviders"
        :key="provider.id"
        class="sso-btn"
        @click="loginWithProvider(provider)">
        <AppIcon :name="provider.protocol === 'oidc' ? 'oidc' : 'saml'" :size="18" />
        <span>{{ t('auth.signInWith', { provider: provider.name }) }}</span>
      </button>
    </div>
  </div>
</template>
```

**自动跳转逻辑**（前端）：

```javascript
// 代码示例：自动跳转逻辑（JavaScript）
onMounted(async () => {
  const d = await api.get('/api/v1/sso/enabled');
  ssoProviders.value = d.providers || [];
  // 单 IdP + auto_redirect 时自动跳转
  if (d.auto_redirect_provider_id) {
    const provider = ssoProviders.value.find(p => p.id === d.auto_redirect_provider_id);
    if (provider) {
      redirect_to_provider_login(provider);
    }
  }
});
```

### 5.4 图标选择

遵循项目 AppIcon 简约小众风格约定：

| 用途 | AppIcon name | 说明 |
|------|--------------|------|
| SSO 管理导航 | `sso` 或 `key` | 钥匙/盾牌意象 |
| OIDC 协议标识 | `oidc` | 协议徽章 |
| SAML 协议标识 | `saml` | 协议徽章 |
| 添加 IdP | `plus` | 通用新增 |
| 测试连接 | `pulse` | 连通性测试 |
| Metadata 导出 | `download` | 下载 |

### 5.5 布局规范

遵循用户偏好：左右宽度一致、顶栏单行、浅色/白色主题、简约布局。SSO 管理页面与 Tenants/RBAC/Audit 等企业版页面保持一致的 ListPageLayout 骨架。

---

## 第6章 验收标准

### 6.1 功能验收标准

| 编号 | 验收标准 | 验证方法 |
|------|----------|----------|
| AC-01 | 支持 OIDC Authorization Code Flow + PKCE 协议 | 配置 Azure AD OIDC IdP，完成端到端登录 |
| AC-02 | 支持 SAML 2.0 SP-Initiated SSO 协议 | 配置 Keycloak SAML IdP，完成端到端登录 |
| AC-03 | 支持多个 IdP 同时配置与启用 | 配置 2+ 个 IdP，登录页均显示按钮，各自可独立登录 |
| AC-04 | IdP 用户属性自动映射到系统用户 | 配置属性映射，SSO 登录后检查 users 表字段 |
| AC-05 | 首次 SSO 登录自动创建用户（JIT provisioning） | 新 IdP 用户首次登录后，users 表出现新记录 |
| AC-06 | SSO 登录后自动生成 JWT token | 登录后检查响应含有效 JWT，前端进入 Dashboard |
| AC-07 | 管理员可测试 IdP 连接 | 点击"测试连接"按钮，返回 reachable + 详情 |
| AC-08 | SAML SP Metadata 可导出 | 访问 metadata 端点，返回有效 XML |
| AC-09 | 管理员可启用/禁用 IdP | 禁用后登录页不显示该 IdP 按钮 |
| AC-10 | 单 IdP + auto_redirect 时自动跳转 | 配置单 IdP + auto_redirect，访问登录页自动跳转 IdP |
| AC-11 | OIDC state 参数防 CSRF | 回调时校验 state，不匹配时拒绝 |
| AC-12 | SAML XML 签名验证 | 篡改 SAMLResponse 签名，登录被拒（403） |
| AC-13 | SAML Conditions 校验（Audience/时间） | 构造过期/错误 Audience 的 Response，登录被拒 |
| AC-14 | client_secret / x509_cert 加密存储 | 查数据库，敏感字段为密文；API 响应脱敏 |
| AC-15 | Personal 版 SSO 功能不可用 | Personal 版访问 `/api/v1/sso/*` 返回 404 |
| AC-16 | SSO 管理操作记录审计日志 | 操作后查 audit_events 表有对应记录 |

### 6.2 Gherkin 验收场景

```gherkin
# 代码示例：OIDC 登录验收场景（Gherkin）
Feature: OIDC SSO 登录
  作为企业用户
  我希望通过 OIDC IdP 登录 MAOP
  以便使用我的企业账号

  Scenario: 首次 OIDC 登录成功（JIT provisioning）
    Given 管理员已配置 OIDC IdP "Azure AD" 且已启用
    And 用户 "alice@corp.com" 在 IdP 存在但 MAOP users 表不存在
    When 用户在登录页点击 "Azure AD" 按钮
    And 用户在 IdP 完成认证
    Then MAOP users 表创建记录 username="oidc:alice-oid"
    And 用户获得有效 JWT token
    And 用户进入 Dashboard
    And audit_events 记录 action="sso.login.success"

  Scenario: OIDC 回调 state 不匹配
    Given 用户发起 OIDC 登录，state="abc123"
    When IdP 回调 state="xyz789"
    Then 返回 HTTP 400
    And 错误 code="SSO_CALLBACK_ERROR"

  Scenario: 禁用的 IdP 不在登录页显示
    Given 管理员配置了 IdP "Keycloak" 但 enabled=false
    When 用户访问登录页
    Then 登录页不显示 "Keycloak" 按钮
```

```gherkin
# 代码示例：SAML 登录验收场景（Gherkin）
Feature: SAML 2.0 SSO 登录
  作为企业用户
  我希望通过 SAML IdP 登录 MAOP

  Scenario: SAML 登录签名验证失败
    Given 管理员已配置 SAML IdP "Keycloak" 且 want_signed=true
    When IdP 返回签名被篡改的 SAMLResponse
    Then 返回 HTTP 403
    And 错误 code="SSO_SIGNATURE_INVALID"
    And audit_events 记录 action="sso.login.failure"

  Scenario: 导出 SAML SP Metadata
    Given 管理员已配置 SAML IdP id=1
    When 访问 GET /api/v1/sso/providers/1/metadata
    Then 返回 Content-Type: application/xml
    And XML 包含有效 EntityDescriptor
    And AssertionConsumerService Location 指向正确 ACS URL
```

---

## 第7章 非功能需求

### 7.1 安全需求

| 编号 | 需求 | 实现方式 |
|------|------|----------|
| NFR-S01 | client_secret 加密存储 | Fernet 对称加密（复用 `MAOP_KEY` / `MAOP_KEY_FILE` Vault 主密钥） |
| NFR-S02 | x509_cert 加密存储 | 同上，Fernet 加密后存入 `config` JSON |
| NFR-S03 | PKCE 防截码攻击 | OIDC Authorization Code Flow + S256 code_challenge |
| NFR-S04 | state 参数防 CSRF | 每次登录生成随机 state，回调时校验，存于服务端 session/Redis |
| NFR-S05 | SAML 签名验证 | RSA-SHA256 enveloped signature + exclusive c14n（现有 `SAMLHandler` 已实现） |
| NFR-S06 | SAML Conditions 校验 | Audience、NotBefore/NotOnOrAfter，±60s 时钟容差（现有已实现） |
| NFR-S07 | API 响应脱敏 | `client_secret` / `x509_cert` 永不回传明文，返回 `"***"` |
| NFR-S08 | 管理操作鉴权 | 所有 CRUD 端点 `require_admin`，登录/回调端点公开 |
| NFR-S09 | 审计日志 | 所有管理操作和登录事件写入 `audit_events` |
| NFR-S10 | fail-closed 设计 | 任何验证失败均抛异常拒绝登录，绝不返回 stub session |

### 7.2 兼容性需求

| 编号 | 需求 | 说明 |
|------|------|------|
| NFR-C01 | Personal 版不启用 SSO | `FeatureFlag.SSO` 守卫，Personal 版所有 SSO 端点返回 404 |
| NFR-C02 | Enterprise 版可选 | SSO 默认不启用，管理员主动配置 IdP 后生效 |
| NFR-C03 | 向后兼容现有环境变量配置 | 保留 `MAOP_SSO_*` 环境变量作为 fallback，启动时自动导入为 `sso_providers` 记录 |
| NFR-C04 | SQLite / PostgreSQL 双后端 | `sso_providers` 表在两种后端均可建表 |
| NFR-C05 | 现有 `SSOManager` API 不破坏 | 保持 `SSOConfig` / `SSOUser` / `SSOSession` 接口不变，新增字段为可选 |

### 7.3 依赖需求

| 编号 | 依赖 | 用途 | Edition |
|------|------|------|---------|
| NFR-D01 | `authlib`（OIDC） | OIDC 客户端、PKCE、token 验证 | Enterprise |
| NFR-D02 | `lxml` + `cryptography`（SAML） | SAML XML 签名验证、证书解析 | Enterprise |
| NFR-D03 | `cryptography.fernet`（加密） | 敏感字段加密存储 | Enterprise（复用现有 Vault） |

> **懒加载**：`lxml` 仅在 SAML provider 实际使用时导入（现有 `_get_saml_handler()` 已实现），避免 Personal 版因缺少依赖而 import 失败。

### 7.4 性能需求

| 编号 | 需求 | 指标 |
|------|------|------|
| NFR-P01 | IdP 配置列表查询 | P95 < 100ms（单租户 < 50 个 IdP） |
| NFR-P02 | 测试连接超时 | 10s 超时，避免前端长时间等待 |
| NFR-P03 | OIDC 回调处理 | P95 < 2s（含 token exchange + userinfo + JIT） |
| NFR-P04 | SAML ACS 处理 | P95 < 1s（含 XML 解析 + 签名验证 + JIT） |

---

## 第8章 实现计划

### 8.1 文件清单

#### 8.1.1 后端新建文件

| 文件路径 | 用途 |
|----------|------|
| `py/maop/enterprise/sso_registry.py` | `SSOProviderRegistry` — 多 IdP 注册中心，从 DB 加载/缓存 `SSOManager` 实例 |
| `py/maop/enterprise/sso_store.py` | `SSOProviderStore` — `sso_providers` 表 CRUD（SQLite + PG 双后端） |
| `py/maop/migrations/alembic/versions/<rev>_sso_providers.py` | Alembic 迁移：建 `sso_providers` 表 |

#### 8.1.2 后端修改文件

| 文件路径 | 修改内容 |
|----------|----------|
| `py/maop/dashboard/routers/sso.py` | 重构为多 IdP 模式，新增 providers CRUD + test + metadata + oidc/saml login/callback 端点 |
| `py/maop/enterprise/sso.py` | `SSOConfig` 新增 `use_pkce` / `attribute_mapping` 字段；`handle_callback` 支持 PKCE；属性映射外部化 |
| `py/maop/enterprise/saml_handler.py` | 支持从 `sso_providers` 配置初始化；新增 `generate_sp_metadata()` 生成 SP Metadata XML |
| `py/maop/enterprise/pg_persist.py` | 追加 `PgSSOProviderStore` — PostgreSQL 持久化 |
| `py/maop/config/edition.py` | 确认 `FeatureFlag.SSO` 已存在（无需新增） |
| `py/maop/dashboard/server.py` | SSO 路由注册已存在（无需修改）；公开 SSO 登录/回调路径到 `public_paths` |
| `py/maop/config/settings.py` | 新增 `sso_encrypt_key` 设置字段（或复用 `MAOP_KEY`） |
| `.env.example` | 追加 SSO 多 IdP 相关环境变量说明 |

#### 8.1.3 前端新建文件

| 文件路径 | 用途 |
|----------|------|
| `dashboard-enterprise/src/views/SSO.vue` | SSO 管理页面 |
| `dashboard-enterprise/src/components/SSOProviderDialog.vue` | 添加/编辑 IdP 对话框组件 |
| `dashboard-enterprise/src/i18n/view-sso.js` | SSO 页面 i18n 字典 |

#### 8.1.4 前端修改文件

| 文件路径 | 修改内容 |
|----------|----------|
| `dashboard-enterprise/src/router/index.js` | 追加 `/sso` 路由，`meta: { requiresEnterprise: true }` |
| `dashboard-enterprise/src/nav.js` | Govern 分组追加 SSO 导航项 |
| `dashboard-enterprise/src/i18n/index.js` | coreMessages 追加 `nav.sso` / `nav.sso.subtitle` |
| `dashboard-enterprise/src/views/Login.vue`（或 App.vue 登录态） | 追加 SSO IdP 按钮区域 + 自动跳转逻辑 |

#### 8.1.5 数据库变更

| 变更 | 说明 |
|------|------|
| 新建 `sso_providers` 表 | 见第3章 schema |
| Alembic 迁移 | `py/maop/migrations/alembic/versions/<rev>_sso_providers.py` |

### 8.2 实现阶段

| 阶段 | 内容 | 交付物 |
|------|------|--------|
| Phase 1 | 数据层 + 后端核心 | `sso_providers` 表 + `SSOProviderStore` + `SSOProviderRegistry` + Alembic 迁移 |
| Phase 2 | 后端 API | providers CRUD + test + metadata + OIDC/SAML login/callback 端点 + PKCE + JIT provisioning |
| Phase 3 | 前端管理 UI | `SSO.vue` + `SSOProviderDialog.vue` + i18n + 路由/导航注册 |
| Phase 4 | 登录页集成 | 登录页 IdP 按钮 + 自动跳转 + SSO 登录后 JWT 签发 |
| Phase 5 | 测试 + 文档 | 单元测试 + 集成测试 + 更新 `saml-sso-guide.md` |

### 8.3 测试计划

| 测试类型 | 范围 | 工具 |
|----------|------|------|
| 单元测试 | `SSOProviderStore` CRUD、`SSOProviderRegistry` 加载/缓存、PKCE 生成、属性映射、脱敏 | pytest |
| 单元测试 | `SSOProviderDialog` 表单校验、协议切换 | Vitest |
| 集成测试 | OIDC 端到端（mock IdP）、SAML 端到端（mock IdP）、JIT provisioning | pytest + httpx |
| 安全测试 | state CSRF、SAML 签名篡改、敏感字段脱敏、Personal 版 404 | pytest |
| E2E 测试 | SSO 管理页面 CRUD 流程、登录页 IdP 按钮 | Playwright |

### 8.4 环境变量变更

```bash
# .env.example 追加（命令示例：SSO 多 IdP 配置）

# SSO 加密密钥（若不复用 MAOP_KEY）
MAOP_SSO_ENCRYPT_KEY=

# 向后兼容：单 IdP 环境变量（启动时自动导入为 sso_providers 记录）
MAOP_SSO_PROVIDER=oidc
MAOP_SSO_CLIENT_ID=
MAOP_SSO_CLIENT_SECRET=
MAOP_SSO_AUTHORIZE_URL=
MAOP_SSO_TOKEN_URL=
MAOP_SSO_USERINFO_URL=
MAOP_SSO_REDIRECT_URI=
MAOP_SSO_SCOPES=openid profile email

# SAML（向后兼容）
MAOP_SSO_SAML_IDP_METADATA_URL=
MAOP_SSO_SAML_SP_ENTITY_ID=maop-sp
MAOP_SSO_SAML_ACS_URL=
MAOP_SSO_SAML_IDP_CERT=
```

### 8.5 PRD 编写检查清单

依据 `docs/archive/audits/project-structure-analysis.md` 第 10.10 节检查清单：

- [x] 功能名称（SSO 集成）与 FeatureFlag 名称（`SSO`）
- [x] 路由前缀（`/api/v1/sso`）
- [x] 前端路由路径（`/sso`）与导航分组（Govern）
- [x] 数据库表 schema（`sso_providers`，SQLite + PostgreSQL）
- [x] 请求/响应 Pydantic 模型（`CreateProviderRequest` 等）
- [x] 权限要求（`require_admin` 管理端点 / 公开登录回调端点）
- [x] 多租户隔离方式（行级 `tenant_id`）
- [x] 环境变量配置（`MAOP_SSO_*` + `MAOP_SSO_ENCRYPT_KEY`）
- [x] i18n key 命名（`view.sso.*`）
- [x] 图标选择（`sso` / `key` / `oidc` / `saml`）
- [x] 是否需要 PG 持久化（是，`PgSSOProviderStore`）
- [x] 是否需要 WebSocket 推送（否）
- [x] 是否需要审计日志记录（是，`audit_events`）
- [x] Personal 版降级行为（404，`FeatureFlag.SSO` 守卫）

---

## 附录 A：现有 SSO 代码资产

### A.1 可复用资产

| 资产 | 路径 | 复用方式 |
|------|------|----------|
| `SSOManager` | `py/maop/enterprise/sso.py` | 每个 IdP 实例化一个，保持接口不变 |
| `SSOConfig` / `SSOUser` / `SSOSession` | `py/maop/enterprise/sso.py` | 扩展字段，不破坏现有接口 |
| `SAMLHandler` | `py/maop/enterprise/saml_handler.py` | 复用签名验证、Conditions 校验，新增 Metadata 生成 |
| `SSOError` | `py/maop/enterprise/sso.py` | 复用错误类型 |
| OIDC token exchange | `SSOManager._exchange_code()` | 扩展支持 PKCE（追加 `code_verifier` 参数） |
| OIDC userinfo fetch | `SSOManager._fetch_userinfo()` | 直接复用 |
| 用户构建 | `SSOManager._build_user_from_claims()` | 外部化属性映射，支持自定义 mapping |
| Fernet 加密 | `py/maop/core/security/`（Vault） | 复用主密钥加密 client_secret / x509_cert |
| JWT 签发 | `AuthManager.jwt_handler.create_token()` | SSO 登录后签发 JWT |
| `require_admin` | `maop.core.security.middleware` | 管理端点鉴权 |
| `handle_api_errors` | `maop.dashboard.error_handler` | 统一异常处理 |
| `FeatureFlag.SSO` | `maop.config.edition` | edition 守卫 |

### A.2 需重构资产

| 资产 | 当前问题 | 重构方向 |
|------|----------|----------|
| `routers/sso.py` `_get_manager()` | 单例，从环境变量加载单 IdP | 改为 `SSOProviderRegistry` 按 provider_id 获取对应 `SSOManager` |
| `SSOConfig` 属性映射 | 硬编码在 `_build_user_from_claims` | 接受外部 `attribute_mapping` dict |
| `SSOConfig` PKCE | 无 `use_pkce` 字段 | 新增字段，`get_authorize_url` 支持 PKCE |

---

## 附录 B：i18n 字典

```javascript
// 代码示例：SSO 页面 i18n 字典（JavaScript）
// src/i18n/view-sso.js
export const messages = {
  en: {
    'view.sso.subtitle': 'Enterprise identity provider integration',
    'view.sso.enterprise': 'Enterprise',
    'view.sso.providers': 'providers',
    'view.sso.noProviders': 'No identity providers configured',
    'view.sso.loadError': 'Failed to load identity providers',
    'view.sso.addProvider': 'Add Provider',
    'view.sso.editProvider': 'Edit Provider',
    'view.sso.protocol': 'Protocol',
    'view.sso.test': 'Test Connection',
    'view.sso.metadata': 'Metadata',
    'view.sso.testSuccess': 'Connection successful',
    'view.sso.testFailed': 'Connection failed',
    'view.sso.confirmDelete': 'Delete this identity provider?',
    'view.sso.name': 'Provider Name',
    'view.sso.enabled': 'Enabled',
    'view.sso.autoRedirect': 'Auto Redirect',
    // OIDC form
    'view.sso.oidc.issuerUrl': 'Issuer URL',
    'view.sso.oidc.authorizeUrl': 'Authorize URL',
    'view.sso.oidc.tokenUrl': 'Token URL',
    'view.sso.oidc.userinfoUrl': 'UserInfo URL',
    'view.sso.oidc.clientId': 'Client ID',
    'view.sso.oidc.clientSecret': 'Client Secret',
    'view.sso.oidc.redirectUri': 'Redirect URI',
    'view.sso.oidc.scopes': 'Scopes',
    'view.sso.oidc.usePkce': 'Use PKCE',
    // SAML form
    'view.sso.saml.spEntityId': 'SP Entity ID',
    'view.sso.saml.idpEntityId': 'IdP Entity ID',
    'view.sso.saml.ssoUrl': 'IdP SSO URL',
    'view.sso.saml.sloUrl': 'IdP SLO URL',
    'view.sso.saml.acsUrl': 'ACS URL',
    'view.sso.saml.x509Cert': 'X509 Certificate',
    'view.sso.saml.nameIdFormat': 'NameID Format',
    'view.sso.saml.wantSigned': 'Require Signed Response',
    // Attribute mapping
    'view.sso.mapping.externalId': 'External ID mapping',
    'view.sso.mapping.email': 'Email mapping',
    'view.sso.mapping.displayName': 'Display Name mapping',
    'view.sso.mapping.roles': 'Roles mapping',
    'view.sso.mapping.roleMapping': 'Role Mapping Table',
  },
  zh: {
    'view.sso.subtitle': '企业身份提供商集成',
    'view.sso.enterprise': '企业版',
    'view.sso.providers': '个身份提供商',
    'view.sso.noProviders': '尚未配置身份提供商',
    'view.sso.loadError': '加载身份提供商失败',
    'view.sso.addProvider': '添加提供商',
    'view.sso.editProvider': '编辑提供商',
    'view.sso.protocol': '协议',
    'view.sso.test': '测试连接',
    'view.sso.metadata': 'Metadata',
    'view.sso.testSuccess': '连接成功',
    'view.sso.testFailed': '连接失败',
    'view.sso.confirmDelete': '确定删除此身份提供商？',
    'view.sso.name': '提供商名称',
    'view.sso.enabled': '启用',
    'view.sso.autoRedirect': '自动跳转',
    'view.sso.oidc.issuerUrl': 'Issuer URL',
    'view.sso.oidc.authorizeUrl': 'Authorize URL',
    'view.sso.oidc.tokenUrl': 'Token URL',
    'view.sso.oidc.userinfoUrl': 'UserInfo URL',
    'view.sso.oidc.clientId': 'Client ID',
    'view.sso.oidc.clientSecret': 'Client Secret',
    'view.sso.oidc.redirectUri': 'Redirect URI',
    'view.sso.oidc.scopes': 'Scopes',
    'view.sso.oidc.usePkce': '启用 PKCE',
    'view.sso.saml.spEntityId': 'SP Entity ID',
    'view.sso.saml.idpEntityId': 'IdP Entity ID',
    'view.sso.saml.ssoUrl': 'IdP SSO URL',
    'view.sso.saml.sloUrl': 'IdP SLO URL',
    'view.sso.saml.acsUrl': 'ACS URL',
    'view.sso.saml.x509Cert': 'X509 证书',
    'view.sso.saml.nameIdFormat': 'NameID Format',
    'view.sso.saml.wantSigned': '要求签名 Response',
    'view.sso.mapping.externalId': 'External ID 映射',
    'view.sso.mapping.email': '邮箱映射',
    'view.sso.mapping.displayName': '显示名称映射',
    'view.sso.mapping.roles': '角色映射',
    'view.sso.mapping.roleMapping': '角色映射表',
  },
};
```

```javascript
// 代码示例：导航 i18n 追加（JavaScript）
// src/i18n/index.js — coreMessages 追加
en: {
  'nav.sso': 'SSO',
  'nav.sso.subtitle': 'Identity providers',
  'auth.orContinueWith': 'or continue with',
  'auth.signInWith': 'Sign in with {provider}',
},
zh: {
  'nav.sso': 'SSO',
  'nav.sso.subtitle': '身份提供商',
  'auth.orContinueWith': '或使用以下方式登录',
  'auth.signInWith': '使用 {provider} 登录',
},
```

---

## 附录 C：路由与导航注册

```javascript
// 代码示例：路由注册（JavaScript）
// src/router/index.js — 追加
{
  path: '/sso',
  name: 'sso',
  component: () => import('../views/SSO.vue'),
  meta: { requiresEnterprise: true },
},
```

```javascript
// 代码示例：导航注册（JavaScript）
// src/nav.js — Govern 分组追加
{
  to: '/sso',
  label: 'nav.sso',
  icon: 'sso',
  subtitle: 'nav.sso.subtitle',
  enterprise: true,
},
```

---

## 附录 D：支持的 IdP 矩阵

| IdP | OIDC | SAML 2.0 | 备注 |
|-----|------|----------|------|
| Azure AD | ✅ | ✅ | 推荐 OIDC（v2.0 endpoint） |
| Keycloak | ✅ | ✅ | 两种均完整支持 |
| Okta | ✅ | ✅ | 两种均支持 |
| ADFS | ✅ | ✅ | SAML 更常见 |
| Google Workspace | ✅ | ❌ | 仅 OIDC |
| GitLab | ✅ | ❌ | 仅 OIDC |

---

## 变更记录

| 版本 | 日期 | 变更 | 作者 |
|------|------|------|------|
| v1.0.0-draft | 2026-08-13 | 初稿 | MAOP Coding Engineer |