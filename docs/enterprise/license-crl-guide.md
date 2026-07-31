# License CRL 在线撤销指南

> 企业版功能 — 需 `maop-enterprise` 包

## 概述

License CRL（Certificate Revocation List）允许在线撤销已颁发的 license，无需重新部署系统。

## 工作原理

1. **CRL 服务**：HTTP 端点返回 JSON 格式的撤销列表
2. **本地缓存**：CRL 数据缓存到本地文件，减少网络请求
3. **检查时机**：每次 license 验证时检查 CRL

## CRL 服务格式

CRL 服务应返回以下 JSON 格式：

```json
{
  "revoked": [
    {
      "customer": "customer-id",
      "reason": "license_violation",
      "revoked_at": "2026-01-01T00:00:00Z"
    }
  ],
  "issued_at": "2026-01-01T00:00:00Z"
}
```

## 配置

### 环境变量

```bash
MAOP_CRL_URL=https://license.example.com/crl
MAOP_CRL_CACHE_TTL=3600
MAOP_CRL_STRICT=0
```

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `MAOP_CRL_URL` | CRL 服务 URL | 空（禁用 CRL） |
| `MAOP_CRL_CACHE_TTL` | 缓存有效期（秒） | 3600 |
| `MAOP_CRL_STRICT` | 严格模式（无法获取 CRL 时拒绝 license） | 0 |

## 模式

### 宽松模式（默认）

- CRL 服务不可用时，允许 license 通过
- 适用于网络不稳定的环境

### 严格模式

- CRL 服务不可用时，拒绝 license
- 适用于高安全要求的环境

```bash
MAOP_CRL_STRICT=1
```

## 缓存

- 缓存文件位置：`data/crl_cache.json`
- 缓存有效期内不会重新请求 CRL 服务
- 缓存失败时自动回退到网络请求

## 故障排查

| 问题 | 原因 | 解决方案 |
|------|------|----------|
| License 被拒绝 | 客户在 CRL 中 | 检查 CRL 服务数据 |
| 严格模式下 license 被拒绝 | CRL 服务不可用 | 检查网络连接或切换到宽松模式 |
| 撤销不生效 | 缓存未过期 | 等待缓存过期或删除 `data/crl_cache.json` |

## 测试

运行 CRL 集成测试：

```bash
cd py
python -m pytest tests/test_enterprise_integration.py::TestCRLIntegration -v
```
