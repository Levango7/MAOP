# LDAP 集成指南

> 适用版本：v5.0.0+ ｜ 企业版功能 ｜ 实现模块：`py/maop/core/security/ldap_provider.py`

## 第 1 章 概述

MAOP 企业版支持通过 LDAP/Active Directory 进行用户认证与同步。本文档指导如何将 MAOP 与现有 LDAP 目录服务（OpenLDAP、389 Directory Server、Microsoft AD 等）集成。

### 1.1 功能

- **用户认证**：通过 LDAP bind 操作验证用户凭据。
- **用户同步**：从 LDAP 拉取用户列表写入 MAOP 用户表，支持增量同步。
- **组映射**：将 LDAP group DN 映射到 MAOP role（admin / viewer / editor 等）。
- **自动停用**：全量同步时，不在 LDAP 中的用户自动停用。

### 1.2 依赖

| 库 | 说明 | 安装 |
|----|------|------|
| `ldap3` | 纯 Python LDAP 客户端（推荐，跨平台） | `pip install ldap3` |
| `python-ldap` | C 扩展 LDAP 客户端（Unix only，性能更好） | `pip install python-ldap` |

> MAOP 优先使用 `ldap3`，若未安装回退到 `python-ldap`，若两者都未安装，`LDAPProvider` 实例化时抛出 `LDAPConfigError`。

## 第 2 章 配置

### 2.1 OpenLDAP 配置

```python
from maop.core.security.ldap_provider import LDAPConfig, LDAPProvider, GroupRoleMapping

config = LDAPConfig(
    server_url="ldap://openldap.example.com:389",
    bind_dn="cn=maop-svc,ou=services,dc=example,dc=com",
    bind_password="<service-account-password>",
    user_base_dn="ou=users,dc=example,dc=com",
    user_filter="(&(objectClass=inetOrgPerson)(uid={username}))",
    group_base_dn="ou=groups,dc=example,dc=com",
    group_filter="(objectClass=groupOfNames)",
    use_tls=True,           # STARTTLS
    page_size=1000,
    is_active_directory=False,
    modify_timestamp_attr="modifyTimestamp",
)

group_mappings = [
    GroupRoleMapping(
        group_dn_pattern="cn=maop-admins,ou=groups,dc=example,dc=com",
        role="admin",
    ),
    GroupRoleMapping(
        group_dn_pattern="cn=maop-editors,ou=groups,dc=example,dc=com",
        role="editor",
    ),
    GroupRoleMapping(
        group_dn_pattern=r"cn=maop-.*,ou=groups,dc=example,dc=com",
        use_regex=True,
        role="viewer",
        is_default=True,
    ),
]

provider = LDAPProvider(config, group_mappings=group_mappings)
```

### 2.2 Active Directory 配置

```python
config = LDAPConfig(
    server_url="ldaps://dc.example.com:636",
    bind_dn="cn=maop-svc,ou=Service Accounts,dc=example,dc=com",
    bind_password="<service-account-password>",
    user_base_dn="ou=Users,dc=example,dc=com",
    user_filter="(&(objectClass=user)(sAMAccountName={username}))",
    group_base_dn="ou=Groups,dc=example,dc=com",
    group_filter="(objectClass=group)",
    use_ssl=True,            # LDAPS
    page_size=1000,
    is_active_directory=True,
    modify_timestamp_attr="whenChanged",
)
```

### 2.3 环境变量配置

MAOP 支持通过环境变量配置 LDAP（用于 docker-compose / K8s 部署）：

```bash
# .env
MAOP_LDAP_SERVER_URL=ldap://openldap.example.com:389
MAOP_LDAP_BIND_DN=cn=maop-svc,ou=services,dc=example,dc=com
MAOP_LDAP_BIND_PASSWORD=<from-vault>
MAOP_LDAP_USER_BASE_DN=ou=users,dc=example,dc=com
MAOP_LDAP_USER_FILTER=(&(objectClass=inetOrgPerson)(uid={username}))
MAOP_LDAP_GROUP_BASE_DN=ou=groups,dc=example,dc=com
MAOP_LDAP_USE_TLS=1
MAOP_LDAP_PAGE_SIZE=1000
```

> **安全建议**：`MAOP_LDAP_BIND_PASSWORD` 应通过 Vault 管理，不要明文写入 `.env`。参见 `MAOP_SECRET_BACKEND=vault`。

## 第 3 章 用户同步

### 3.1 全量同步

```python
from maop.core.security.ldap_provider import LDAPProvider

provider = LDAPProvider(config, group_mappings=mappings)
result = provider.sync_users(user_store=my_user_store)

print(f"同步: {result.synced} (新建 {result.created}, 更新 {result.updated})")
print(f"停用: {result.deactivated}")
print(f"错误: {result.errors}")
for detail in result.error_details:
    print(f"  - {detail}")
```

### 3.2 增量同步

```python
from datetime import datetime, timedelta

# 只同步过去 1 小时内修改的用户
since = datetime.now() - timedelta(hours=1)
result = provider.sync_users(since=since, user_store=my_user_store)
```

### 3.3 定时同步（Cron / K8s CronJob）

```bash
# 每小时增量同步
maop admin ldap-sync --incremental --interval 3600

# 每天凌晨全量同步
maop admin ldap-sync --full
```

K8s CronJob 示例：

```yaml
apiVersion: batch/v1
kind: CronJob
metadata:
  name: maop-ldap-sync
spec:
  schedule: "0 * * * *"    # 每小时
  jobTemplate:
    spec:
      template:
        spec:
          containers:
            - name: ldap-sync
              image: maop:5.0.0
              command: ["maop", "admin", "ldap-sync", "--incremental"]
              envFrom:
                - secretRef:
                    name: maop-ldap-credentials
          restartPolicy: OnFailure
```

## 第 4 章 用户认证

### 4.1 认证流程

```
用户提交 (username, password)
  → MAOP 用 service account 搜索用户 DN
  → MAOP 用用户 DN + password 执行 LDAP bind
  → bind 成功 → 获取用户组 → 映射为 MAOP roles
  → 返回 AuthResult(authenticated=True, user=..., roles=...)
```

### 4.2 代码示例

```python
result = provider.authenticate("jdoe", "user-password")

if result.authenticated:
    print(f"认证成功: {result.user.display_name}")
    print(f"角色: {result.roles}")
    # 创建 JWT 会话
    token = create_jwt(user=result.user, roles=result.roles)
else:
    print(f"认证失败: {result.error}")
```

### 4.3 异步认证

```python
from maop.core.security.ldap_provider import authenticate_async

result = await authenticate_async(provider, "jdoe", "user-password")
```

## 第 5 章 组映射

### 5.1 精确匹配

```python
GroupRoleMapping(
    group_dn_pattern="cn=maop-admins,ou=groups,dc=example,dc=com",
    role="admin",
)
```

### 5.2 正则匹配

```python
GroupRoleMapping(
    group_dn_pattern=r"cn=maop-(\w+)-admins,ou=groups,dc=example,dc=com",
    use_regex=True,
    role="admin",
)
```

### 5.3 默认角色

```python
GroupRoleMapping(
    group_dn_pattern="cn=maop-users,ou=groups,dc=example,dc=com",
    role="viewer",
    is_default=True,    # 所有同步用户都获得此角色
)
```

### 5.4 映射优先级

1. 精确匹配优先于正则匹配。
2. 多个匹配合并角色（取并集）。
3. `is_default=True` 的映射对所有用户生效。

## 第 6 章 OpenLDAP 测试环境搭建

### 6.1 Docker 快速启动

```bash
# 启动 OpenLDAP 容器（含种子数据）
docker run -d --name maop-openldap \
  -p 389:389 \
  -e LDAP_ORGANISATION="MAOP Test" \
  -e LDAP_DOMAIN=example.org \
  -e LDAP_ADMIN_PASSWORD=admin \
  osixia/openldap:1.5.0

# 验证
docker exec maop-openldap ldapsearch -x -H ldap://localhost \
  -b dc=example,dc=org -D cn=admin,dc=example,dc=org -w admin
```

### 6.2 添加测试用户

```bash
# 创建 OU
docker exec maop-openldap ldapadd -x -H ldap://localhost \
  -D cn=admin,dc=example,dc=org -w admin <<EOF
dn: ou=users,dc=example,dc=org
objectClass: organizationalUnit
ou: users
EOF

# 添加用户
docker exec maop-openldap ldapadd -x -H ldap://localhost \
  -D cn=admin,dc=example,dc=org -w admin <<EOF
dn: uid=testuser,ou=users,dc=example,dc=org
objectClass: inetOrgPerson
uid: testuser
cn: Test User
sn: User
mail: testuser@example.org
userPassword: testpass
EOF
```

### 6.3 运行联调测试

```bash
# 设置环境变量
export MAOP_LDAP_TEST_HOST=localhost
export MAOP_LDAP_TEST_PORT=389
export MAOP_LDAP_TEST_BIND_DN=cn=admin,dc=example,dc=org
export MAOP_LDAP_TEST_BIND_PASSWORD=admin
export MAOP_LDAP_TEST_USER_BASE=ou=users,dc=example,dc=org
export MAOP_LDAP_TEST_USER_UID=testuser
export MAOP_LDAP_TEST_USER_PASSWORD=testpass

# 运行真实 OpenLDAP 联调测试
cd py
python -m pytest tests/test_ldap_real_env.py -m slow -v
```

### 6.4 Docker 自动化测试

若无法配置固定 LDAP 服务器，测试可自动启动 Docker OpenLDAP 容器：

```bash
# 需要 Docker 但不需要预配置 LDAP
cd py
python -m pytest tests/test_ldap_real_env.py::TestDockerOpenLDAP -m slow -v
```

## 第 7 章 安全最佳实践

### 7.1 连接安全

- **TLS**：生产环境必须使用 `ldaps://`（端口 636）或 `ldap://` + STARTTLS。
- **证书验证**：配置 CA 证书路径，不要禁用证书验证。
- **网络隔离**：LDAP 服务器仅对 MAOP 实例开放，不暴露公网。

### 7.2 凭据安全

- **Service Account**：使用专用 service account，不要用管理员账户。
- **密码存储**：`bind_password` 通过 Vault 管理（`MAOP_SECRET_BACKEND=vault`）。
- **密码轮换**：定期轮换 service account 密码。
- **日志脱敏**：MAOP 不会将密码记录到日志（参见 `ldap_provider.py` 设计要点）。

### 7.3 同步安全

- **最小权限**：Service account 只需 `search` 权限，不需要 `write` 权限。
- **增量同步**：生产环境用增量同步（`--incremental`），减少 LDAP 负载。
- **并发控制**：同步操作串行执行，避免对 LDAP 服务器造成并发压力。
- **错误处理**：同步失败不阻塞登录（认证独立于同步）。

## 第 8 章 故障排查

### 8.1 连接失败

```
错误: LDAPConnectionError: couldn't connect to ldap://openldap:389
```

排查：
1. 确认 LDAP 服务器可达：`telnet openldap 389`。
2. 确认 bind DN 与密码正确：`ldapwhoami -x -H ldap://openldap -D <bind_dn> -w <password>`。
3. 确认防火墙规则允许 MAOP → LDAP 的 389/636 端口。

### 8.2 搜索无结果

```
错误: user 'jdoe' not found in LDAP
```

排查：
1. 确认 `user_base_dn` 正确：`ldapsearch -x -b <user_base_dn> "(uid=jdoe)"`。
2. 确认 `user_filter` 匹配用户 objectClass。
3. 确认用户未被禁用（`is_active=false`）。

### 8.3 认证失败

```
错误: invalid credentials
```

排查：
1. 确认用户密码正确：`ldapwhoami -x -D uid=jdoe,... -w <password>`。
2. 确认用户 DN 正确（MAOP 先搜索 DN 再 bind）。
3. 检查 LDAP 密码策略（账户锁定、密码过期）。

### 8.4 同步错误

```
错误: sync errors: 5
```

排查：
1. 查看 `result.error_details` 获取具体错误。
2. 确认 `user_store.upsert_user` 接口实现正确。
3. 检查 LDAP 条目属性是否完整（缺失 `uid` / `cn` 等）。

---

> 本指南以简体中文为准。LDAP 联调测试参见 `py/tests/test_ldap_real_env.py`。