# MAOS 企业版独立审计报告（2026-08-31）

> 审计人：ZCode 深度审查会话（应用户"剩余的能解决的也解决一下"指令）
> 审计对象：`F:\Nexus\MAOS`（私有仓库，`maop-enterprise` 包源码）
> 审计范围：26 个 Python 文件，共 12,153 行企业版模块
> 审计方式：静态深读 + 自动化哈希验证 + 签名验证 + 危险模式全量扫描

---

## 1. 审计范围

| 模块 | 行数 | 功能 |
|------|------|------|
| license.py / license_manager.py | 655 + 895 | Ed25519 许可证签发/校验/管理 |
| audit.py / audit_enhanced.py | 480 + 720 | 审计日志（含风险分级/分类/标签） |
| pg_persist.py | 753 | PostgreSQL 持久化（RBAC/审计双 store） |
| quota.py / quota_middleware.py | 901 + 236 | 多租户配额强制 |
| rbac.py | 360 | 四级角色 × 17 权限 RBAC |
| sso.py / saml_handler.py / sso_store 系 | 606 + 869 + ~1400 | OIDC + SAML SSO 全链 |
| ha.py | 469 | 分布式 HA（Redis 租约，ADR-015） |
| crl.py | 416 | 许可证在线撤销（CRL 缓存 + 离线降级） |
| n8n.py / container.py / tenant.py / tls_auto.py | 500 + 224 + 246 + 204 | n8n 集成/容器/租户/TLS 自动化 |
| notification/（6 文件） | 2,897 | 通知中心（渠道/事件总线/存储） |

## 2. 验证结果

### 2.1 模块完整性（防篡改）— ✅ 通过
- `_integrity_manifest.json` 覆盖 **26/26 文件，全部 SHA-256 哈希匹配**（missing=0, mismatch=0）
- Manifest 本身的 **Ed25519 签名验证通过**（compact-JSON 序列化格式，与捆绑公钥 `keys/public_key.pem` 匹配）
- 该 manifest 正是运行时 `verify_module_integrity()` 的信任基础——MAOP 主仓库 `config/edition.py` 在 license 校验通过后调用它做二次防篡改（签名/哈希不符即降级 PERSONAL），验证链闭环成立

### 2.2 许可证机制（防破解）— ✅ 设计合理
深读 `license.py`（655 行）核心路径：
- **Ed25519 签名验证**：私钥在商业侧，公钥捆绑于包内；签名验证失败 → `LicenseSignatureError`
- **机器指纹绑定 fail-closed**：`compute_machine_fingerprint()`（MachineGuid/machine-id + OS + arch 的 SHA-256）绑定后，换机即拒（`LicenseFingerprintError`）；无指纹字段的旧许可证向后兼容跳过
- **过期 + 7 天宽限期**：宽限期内警告，超期硬拒（`LicenseExpiredError`）
- **honor-system 已移除**（2026-08-11 加固）：无 key 或无效 key 一律降级 PERSONAL——与 MAOP 侧 `set_edition` 生产门禁（本次会话修复）形成双端防御
- **CRL 在线撤销**：`MAOP_CRL_URL` 配置时签名+过期校验后查吊销列表（缓存 TTL + 离线降级 + `MAOP_CRL_STRICT` 严格模式）
- **限制强制**：`max_users` / `features` 由业务层显式调用 `enforce_max_users` / `feature_allowed`（校验器本身不假设调用方状态——职责划分正确）

### 2.3 危险模式全量扫描 — ✅ 干净
11 类危险模式正则扫描（f-string SQL、shell=True、os.system、eval/exec、pickle 反序列化、硬编码密码、TLS verify=False 等）：
- **2 处命中均为误报**：
  - `pg_persist.py:236` f-string SQL——`col_def` 是代码内硬编码白名单常量（3 个固定列定义），无外部输入
  - `channels.py:185` "hardcoded pwd"——实为配置读取 `cfg.get("password", "")`，非硬编码
- **无** SQL 注入面、命令执行面、反序列化面、TLS 降级面

### 2.4 抽审代码质量
- `rbac.py`：角色层级（superadmin > admin > operator > viewer）× 17 权限枚举，frozenset 不可变权限表；SQLite 持久化兜底与 PG store 接口对齐（`UNIQUE(user_id, role, tenant_id)` + upsert 语义）
- 注释密度与修复标注（P0 #8、P1 #12 等）与主仓库同风格，工程纪律一致

## 3. 审计结论

**MAOS 企业版代码库通过本次独立审计**。许可证防伪链（Ed25519 签名 → 指纹绑定 → 完整性 manifest → CRL 撤销 → honor-system 移除）闭环完整，防篡改验证实测通过，无危险模式命中。结合 MAOP 主仓库本次会话补上的 `set_edition` 生产 license 门禁，个人版/企业版的授权边界在两端都 fail-closed。

**遗留小项**（不阻断，记录在案）：
1. `pg_persist.py:244` 的 `except Exception` 用 `logger.debug('swallowed exception', ...)` 吞 ALTER TABLE 异常——语义上合理（duplicate column 预期），但建议升 `logger.debug` 带上下文（与主仓库同类问题同修法）
2. SSO 系（OIDC/SAML ~2900 行）本次为抽审+模式扫描级覆盖；如需合同级交付，建议对 SAML XML 签名验证（`saml_handler.py` 869 行）做专项渗透测试（XXE/canonicalization 是 SAML 经典攻击面，静态审计无法完全覆盖）

---
*本报告存档于 MAOP 主仓库 `deliverables/`，与个人版审计报告互为独立证据链。*
