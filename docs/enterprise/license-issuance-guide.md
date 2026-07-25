# MAOP Enterprise License 签发指南

> **文档级别**：商业机密 - 仅限 MAOP 商业团队
> **最后更新**：2026-07-25

## 1. 概述

MAOP Enterprise 采用 Ed25519 签名的 license key 机制进行商业授权。本文档描述 license 的签发流程、密钥管理、客户交付流程。

### 1.1 License 机制架构

```
┌─────────────────┐      ┌──────────────────┐      ┌──────────────────┐
│  商业团队(离线)  │      │   客户部署环境    │      │   MAOP 运行时    │
│                 │      │                  │      │                  │
│  ┌───────────┐  │      │  ┌────────────┐  │      │  ┌────────────┐  │
│  │ 私钥 .pem │──┼──签发──▶│ license.key│──┼──验证──▶│ 公钥 .pem  │  │
│  └───────────┘  │      │  └────────────┘  │      │  (随包分发) │  │
│       ↑         │      │       ↑          │      │       ↓        │
│  Ed25519 签名   │      │  MAOP_LICENSE_KEY│      │  LicenseValidator│
└─────────────────┘      └──────────────────┘      └──────────────────┘
                                                        │
                                                        ↓
                                            ┌───────────────────────┐
                                            │  三态降级策略          │
                                            │  • 无 key  → honor    │
                                            │  • 有效    → ENTERPRISE│
                                            │  • 无效    → PERSONAL  │
                                            └───────────────────────┘
```

**核心流程**：
1. 商业团队在离线机器上用 Ed25519 私钥对 license payload 签名
2. 签名后的 license key 通过安全渠道交付给客户
3. 客户配置 `MAOP_LICENSE_KEY` 环境变量或 `data/license.key` 文件
4. MAOP 运行时用随包分发的公钥验证签名，决定 edition

### 1.2 三态降级策略

| 场景 | 行为 |
|------|------|
| 无 license key | honor-system 模式 + 警告日志（向后兼容） |
| key 有效 | ENTERPRISE edition + info 日志 |
| key 无效（签名/格式/过期超宽限期） | 降级 PERSONAL + error 日志 + record_degradation |

降级逻辑实现见 `py/maop/config/edition.py` 中的 `_detect_with_license_check()`。

### 1.3 7 天宽限期

license 过期后仍有 7 天宽限期，期间仅 warning 不抛异常，避免服务突然中断。

- 常量：`_GRACE_PERIOD_DAYS = 7`（`py/maop/enterprise/license.py`）
- 宽限期内：`LicenseInfo.is_in_grace_period` 返回 `True`，仅记录 warning 日志
- 宽限期后：抛出 `LicenseExpiredError`，edition 降级为 PERSONAL

## 2. 密钥管理

### 2.1 密钥对生成

**生产环境密钥**（由商业团队安全保管）：

```powershell
# 在安全离线机器上执行
python -c "
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

# 生成生产密钥对
private_key = Ed25519PrivateKey.generate()
public_key = private_key.public_key()

# 保存私钥（绝对不能提交到 git）
private_pem = private_key.private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.PKCS8,
    encryption_algorithm=serialization.NoEncryption()
)
with open('maop_enterprise_private_key.pem', 'wb') as f:
    f.write(b'PRODUCTION PRIVATE KEY - DO NOT COMMIT TO REPOSITORY\n')
    f.write(private_pem)

# 保存公钥（替换包内的开发公钥）
public_pem = public_key.public_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PublicFormat.SubjectPublicKeyInfo
)
with open('public_key.pem', 'wb') as f:
    f.write(public_pem)

print('生产密钥对已生成')
print('私钥：maop_enterprise_private_key.pem（离线保管）')
print('公钥：public_key.pem（替换 py/maop/enterprise/keys/public_key.pem）')
"
```

### 2.2 密钥保管要求

- **私钥**：存储在离线机器或 HSM 中，绝对不提交到代码仓库
- **公钥**：打包在 `maop.enterprise.keys/` 中随企业版分发
- **备份**：私钥至少 2 份离线备份（不同物理位置）
- **轮换**：建议每年轮换一次密钥对（需要为所有客户重新签发 license）

> ⚠️ **警告**：仓库内 `scripts/dev_private_key.pem` 仅为开发测试用，**绝对不能**用于生产签发。该私钥已公开在代码仓库中，任何人都可获取。

### 2.3 密钥轮换流程

1. 生成新密钥对
2. 用新私钥为所有活跃客户重新签发 license
3. 在下一个版本中更新包内的 public_key.pem
4. 通知客户升级到新版本并更换 license key
5. 旧密钥作废

```
轮换时间线：
Day 1   : 生成新密钥对（新私钥 + 新公钥）
Day 2-7 : 用新私钥为所有活跃客户重新签发 license
Day 8   : 发布新版本 maop-enterprise（包含新 public_key.pem）
Day 9+  : 通知客户升级版本 + 更换 license key
Day 90  : 旧密钥正式作废（给予客户 3 个月迁移窗口）
```

## 3. License 签发流程

### 3.1 使用签发 CLI 工具

```powershell
# 基本用法
python scripts/generate_license.py `
    --customer "ACME Corporation" `
    --expires 2027-07-25 `
    --private-key /secure/path/maop_enterprise_private_key.pem

# 完整参数
python scripts/generate_license.py `
    --customer "ACME Corporation" `
    --expires 2027-12-31 `
    --private-key /secure/path/maop_enterprise_private_key.pem `
    --max-users 100 `
    --fingerprint "machine-binding-hash" `
    --output /tmp/acme_license.key
```

> **注意**：在 PowerShell 中使用反引号 `` ` `` 作为行续接符；在 bash 中使用反斜杠 `\`。

### 3.2 参数说明

| 参数 | 必填 | 说明 |
|------|------|------|
| `--customer` | 是 | 客户/组织名称（写入 license payload） |
| `--expires` | 是 | 过期日期（YYYY-MM-DD 格式，自动设为当日 23:59:59 UTC） |
| `--private-key` | 否 | 私钥路径，默认 `scripts/dev_private_key.pem`（仅开发用） |
| `--max-users` | 否 | 最大并发用户数（不填 = 无限） |
| `--fingerprint` | 否 | 机器指纹绑定（不填 = 不绑定） |
| `--output` | 否 | 输出文件路径（不填 = stdout） |

### 3.3 License Key 格式

```
MAOP-ENT-{base64url(payload_json)}.{base64url(signature)}
```

**格式解析**：
- 前缀：`MAOP-ENT-`（固定标识）
- payload：URL-safe base64 编码的 JSON
- 分隔符：`.`（最后一个点分隔 payload 和 signature）
- signature：URL-safe base64 编码的 Ed25519 签名（64 字节）

Payload 字段：
```json
{
  "customer": "ACME Corporation",
  "edition": "enterprise",
  "issued_at": "2026-07-25T10:00:00+00:00",
  "expires_at": "2027-07-25T23:59:59+00:00",
  "max_users": 100,           // 可选
  "fingerprint": "abc123",    // 可选
  "features": ["rbac", "audit_log"]  // 可选
}
```

**必填字段**：`customer`、`edition`、`issued_at`、`expires_at`
**可选字段**：`max_users`、`fingerprint`、`features`

### 3.4 签发记录登记

每次签发必须记录以下信息（保存到商业团队内部的签发台账）：

| 字段 | 示例 |
|------|------|
| 客户名称 | ACME Corporation |
| 签发日期 | 2026-07-25 |
| 过期日期 | 2027-07-25 |
| max_users | 100（或"无限"） |
| license key 前 20 字符 | MAOP-ENT-eyJjdXN0...（用于追踪，**不记录完整 key**） |
| 签发人 | 张三 |
| 签发原因 | 新购 / 续费 / 升级 |

## 4. 客户交付流程

### 4.1 交付内容

向客户交付：
1. `maop-enterprise` pip 包（或 whl 文件）
2. License key（通过安全渠道）
3. 部署文档链接

### 4.2 安全交付渠道

推荐渠道（按安全度排序）：
1. 企业邮件加密附件（PGP/GPG）
2. 安全文件传输平台（如 ownCloud/NextCloud 加密链接）
3. 客户专属 portal 下载

**禁止**：明文邮件、即时通讯工具（微信/钉钉/Slack）、公开链接。

### 4.3 客户配置指南

提供给客户的配置说明：

```bash
# 方式 1：环境变量（推荐）
export MAOP_LICENSE_KEY="MAOP-ENT-xxx.yyy"

# 方式 2：文件
mkdir -p data
echo "MAOP-ENT-xxx.yyy" > data/license.key

# 验证
curl http://localhost:9079/api/info/edition
# 应返回 {"edition": "enterprise", ...}
```

**加载优先级**（见 `LicenseValidator._load_key_from_env_or_file()`）：
1. `MAOP_LICENSE_KEY` 环境变量
2. `{MAOP_ROOT_DIR or MAOP_ROOT or cwd}/data/license.key` 文件

## 5. 验证与排障

### 5.1 验证 license 有效性

```powershell
# 在客户环境验证
python -c "
from maop.enterprise.license import LicenseValidator
import os
validator = LicenseValidator()
key = os.getenv('MAOP_LICENSE_KEY') or open('data/license.key').read().strip()
info = validator.validate(key)
print(f'Customer: {info.customer}')
print(f'Edition: {info.edition}')
print(f'Issued: {info.issued_at}')
print(f'Expires: {info.expires_at}')
print(f'Expired: {info.is_expired}')
print(f'In grace period: {info.is_in_grace_period}')
"
```

### 5.2 常见问题

| 问题 | 原因 | 解决方案 |
|------|------|---------|
| `LicenseFormatError` | key 格式错误 | 检查 key 是否完整复制（无截断/空格） |
| `LicenseSignatureError` | 签名验证失败 | key 被篡改或用了错误的公钥；重新签发 |
| `LicenseExpiredError` | 超过宽限期 | 续费并重新签发 |
| edition 仍是 personal | license 校验失败降级 | 查看 MAOP 日志中的 `[edition]` 条目 |
| honor-system 警告 | 未配置 license key | 设置 `MAOP_LICENSE_KEY` 环境变量 |

### 5.3 日志检查

```bash
# 查看 license 相关日志
grep "\[edition\]" /var/log/maop/*.log
grep "\[license\]" /var/log/maop/*.log

# 健康检查
curl http://localhost:9079/api/info/edition | python -m json.tool
```

**关键日志条目**：
- `[edition] Enterprise license valid for 'ACME' (expires 2027-07-25...)` — license 有效
- `[edition] Enterprise package installed but no license key found. Running in honor-system mode.` — honor-system 模式
- `[edition] License validation failed: ... Degrading to PERSONAL.` — 校验失败降级
- `[license] License for 'ACME' is in grace period` — 宽限期警告

## 6. 续费与回收流程

### 6.1 续费

1. 客户提出续费
2. 商业团队确认付款
3. 用生产私钥签发新 license（新的过期日期）
4. 通过安全渠道交付新 key
5. 客户更新 `MAOP_LICENSE_KEY` 环境变量
6. 重启 MAOP 服务（或等待 hot-reload）

### 6.2 回收（客户流失）

1. 不主动作废 license（无法远程撤销）
2. 到期后 7 天宽限期，之后自动降级 personal
3. 如需立即停止：联系客户删除 `MAOP_LICENSE_KEY`（依赖客户配合）
4. **未来增强**：实现 online revocation list（CRL 机制）

## 7. 安全注意事项

- **私钥绝不离开安全环境**：签发操作必须在离线机器或 HSM 上进行
- **开发私钥不能用于生产**：`scripts/dev_private_key.pem` 仅用于测试
- **license key 不记录日志**：验证日志只记录 customer 和 expires_at，不记录完整 key
- **定期审计**：每季度核对签发记录与活跃客户列表
- **密钥泄露响应**：如私钥泄露，立即生成新密钥对 + 为所有客户重新签发 + 发布安全版本

## 8. 附录

### 8.1 测试 License 生成

开发/测试用 license（使用开发私钥）：

```powershell
cd F:\Nexus\MAOP
python scripts/generate_license.py `
    --customer "MAOP Test Customer" `
    --expires 2027-12-31 `
    --output data/test_license.key
```

> 测试 license 仅用于本地开发和 CI 环境，**不可**用于任何生产或客户环境。

### 8.2 相关文件

| 文件 | 说明 |
|------|------|
| `py/maop/enterprise/license.py` | License 校验核心模块 |
| `py/maop/enterprise/keys/public_key.pem` | 验证公钥（随包分发） |
| `scripts/generate_license.py` | 签发 CLI 工具 |
| `scripts/dev_private_key.pem` | 开发测试私钥（仅开发用） |
| `py/maop/config/edition.py` | Edition 检测与 license 集成 |
| `py/tests/test_enterprise_license.py` | 13 个测试用例 |

### 8.3 相关 ADR

- ADR-016: Dual-Edition Architecture（双线设计）
