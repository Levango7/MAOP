# SAML 2.0 SSO 配置指南

> 企业版功能 — 需 `maop-enterprise` 包

## 概述

MAOP 企业版支持 SAML 2.0 SP-initiated SSO，使用纯 Python 实现（lxml + cryptography），无需 pysaml2 依赖。

## 支持的 IdP

- Azure AD
- Okta
- ADFS
- 任何支持 SAML 2.0 的 IdP

## 配置

### 1. 环境变量

```bash
MAOP_SSO_PROVIDER=saml
MAOP_SSO_SAML_IDP_METADATA_URL=https://idp.example.com/metadata
MAOP_SSO_SAML_SP_ENTITY_ID=maop-sp
MAOP_SSO_SAML_ACS_URL=https://maop.example.com/api/auth/saml/callback
```

### 2. 或直接配置 IdP 证书

```bash
MAOP_SSO_SAML_IDP_CERT=<base64-encoded-X509-certificate>
MAOP_SSO_AUTHORIZE_URL=https://idp.example.com/sso
```

## 工作流程

1. **AuthnRequest 构造**
   - 生成 `<samlp:AuthnRequest>` XML
   - DEFLATE 压缩 + Base64 编码 + URL 编码
   - 重定向到 IdP SSO URL

2. **IdP 认证**
   - 用户在 IdP 完成认证
   - IdP 返回签名的 SAML Response

3. **Response 验证**
   - 解析 XML 签名（enveloped signature, exclusive c14n）
   - 验证 X.509 证书
   - 校验 Conditions（Audience、NotBefore/NotOnOrAfter）
   - 提取 NameID 和 AttributeStatement

4. **Session 创建**
   - 创建 SSOSession（8 小时有效期）
   - 返回 JWT token

## 安全特性

- **Fail-closed 设计**：任何验证失败均抛 SSOError
- **时钟偏移容差**：±60 秒
- **签名验证**：RSA-SHA256
- **证书验证**：X.509 DER 格式

## 故障排查

| 错误 | 原因 | 解决方案 |
|------|------|----------|
| `SSOError: SAML IdP missing SingleSignOnService URL` | 未配置 IdP metadata | 设置 `MAOP_SSO_SAML_IDP_METADATA_URL` |
| `SSOError: Signature verification failed` | 证书不匹配 | 检查 IdP 证书配置 |
| `SSOError: Audience mismatch` | SP Entity ID 不匹配 | 检查 `MAOP_SSO_SAML_SP_ENTITY_ID` |
| `SSOError: Assertion expired` | 时钟不同步 | 同步服务器时间 |

## 测试

运行 SAML 集成测试：

```bash
cd py
python -m pytest tests/test_enterprise_integration.py::TestSAMLIntegration -v
```
