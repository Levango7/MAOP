# MAOP Enterprise — 混淆与防破解加固指南

> 适用对象：MAOP 商业团队 / 企业版的发布管理员
> 生效条件：PyArmor 8.x+（不在 maop 的默认依赖中，发布机上 `pip install pyarmor` 单独安装）

## 1. 防破解的分层模型

MAOP 企业版的防破解是**深度防御**(defence-in-depth)，不存在单一银弹:

| 层 | 措施 | 防护对象 | 状态 |
|---|------|---------|------|
| L1 | Ed25519 license 签名（必要前提） | 未授权使用（无 license)| ✅ 已实现 |
| L2 | license 过期 + 宽限期 + CRL 在线撤销 | 过期 / 撤销 license | ✅ 已实现 |
| L3 | 企业版模块完整性校验（`_integrity_manifest.json`) | 篡改 rbac.py / audit.py 等业务文件 | ✅ 已实现（本轮） |
| L4 | 移除 honor-system(无 license 静默降级） | 删除 license 文件获取企业功能 | ✅ 已实现（本轮） |
| L5 | PyArmor 字节码混淆 | 静态阅读源码定位 L1-L4 检查点以打补丁 | 🛠 本指南配置 |
| L6 | （未来可选）在线激活 + 能力下发 | 内存补丁、Hook 框架 | 路线图中 |

**核心理念**：单独任何一层都可被绕过，但**叠加成本指数上升**。攻击者必须先破 L5 才能读懂代码定位 L3 的 verify 调用，再伪造有效签名才能绕过 L3 — 此时还需面对 L1。

## 2. L3 — 模块完整性校验（已实现）

### 2.1 发布流程

每次发布企业版前，**必须用生产私钥**重新签名模块清单：

```bash
# 在发布机（能访问生产私钥的安全环境）上执行
python py/scripts/sign_enterprise_modules.py \
    --private-key ~/.maop/keys/prod_private_key.pem
```

### 2.2 激活时行为

`detect_edition()` 在 license 校验通过后调用 `verify_module_integrity()`:

| 场景 | 结果 |
|------|------|
| manifest 有效 + 所有 hash 匹配 | ENTERPRISE 激活 ✅ |
| manifest 有效 + 任意文件被改 | 降级 PERSONAL + 记录 degradation |
| manifest 缺失（开发态） | 警告，不阻断 |
| manifest 签名无效 | 降级 PERSONAL |

### 2.3 已知局限（诚实声明）

- **反射/补丁攻击**：攻击者可在运行时 `monkey-patch` `verify_module_integrity` 本身。我们已在调用方（`edition.py`）使用 `strict=False` 并在 import 时立即调用，攻击者需要更早期 hook,**这能被 PyArmor 混淆（L5）显著加大难度**。
- **pub key 替换**：攻击者同时替换 `keys/public_key.pem` 即可用任意私钥重新签名。防御：公钥 hash 硬编码在已混淆的 `edition.py`，这是 **L5 才有的防御强度**。

## 3. L5 — PyArmor 混淆发布

### 3.1 已知混淆雷区（本仓库现状）

| 文件 | 问题 | 缓解 |
|------|------|------|
| `enterprise/crl.py` | 动态 `getattr()` 调用 | 开启 `--mix-str` 但**不要**启用 `--assert-call`，后者会破坏动态分发 |
| `enterprise/license.py` | 使用 `__file__` 定位公钥和 manifest | PyArmor 保持 `__file__` 指向 `.py` 同名文件，但**不要把 keys/ 目录混淆**——签名工具和验证器都依赖 PEM 内容字节一致 |
| `enterprise/__init__.py` | 触发 `get_edition()` 副作用 | 不进混淆范围，保持明文（它只做日志和分发） |
| `conftest.py` / tests | 测试文件混淆无意义 | 排除 |

### 3.2 构建命令

```bash
# 在发布机(安装 pyarmor 后)
bash py/scripts/build_obfuscated_enterprise.sh

# 输出:
#   dist/obf/maop/enterprise/    混淆后的企业版模块
#   dist/obf/maop/enterprise/keys/public_key.pem   明文拷贝(签名验证依赖)
#   dist/obf/maop/enterprise/_integrity_manifest.json  明文(有签名保护)
#   dist/wheels/maop-5.0.0-py3-none-any.whl       含混淆代码的 wheel
```

### 3.3 策略：混淆范围

- ✅ `maop/enterprise/{rbac,tenant,audit,sso,saml_handler,license,crl,ha,container,pg_persist,n8n,tls_auto}.py`
- ❌ `maop/enterprise/__init__.py`(import 副作用已重写为调用 `get_edition`，保持明文便于排障）
- ❌ `maop/enterprise/keys/` (PEM 文件，不是代码）
- ❌ `maop/enterprise/_integrity_manifest.json`（已被 Ed25519 签名保护，明文无意）
- ❌ `maop/core/*`、`maop/dashboard/*`：个人版代码，保持明文遵循 MIT

### 3.4 与 L3 的握手顺序（关键）

**必须先混淆、后签名**——因为混淆会改变文件字节，若用明文 hash 签名，混淆后的字节会在 L3 校验时全部判定为"篡改":

```
[源文件] → [pyarmor 混淆] → [对混淆产物跑 sign_enterprise_modules.py] → [wheel]
```

`build_obfuscated_enterprise.sh` 已经强制这个顺序。

## 4. 验证

构建并安装到测试环境后：

```bash
# 1. 无 license 应得到 personal
unset MAOP_LICENSE_KEY
python -c "from maop.config.edition import get_edition; print(get_edition())"
# 期望: Edition.PERSONAL

# 2. 有 license + 完整字节的混淆包 → enterprise
export MAOP_LICENSE_KEY="MAOP-ENT-..."
python -c "from maop.config.edition import get_edition; print(get_edition())"
# 期望: Edition.ENTERPRISE

# 3. 篡改混淆包中任一 .py → 仍 personal + degradation 记录
echo "# tamper" >> venv/lib/python3.x/site-packages/maop/enterprise/rbac.py
python -c "from maop.config.edition import get_edition; print(get_edition())"
# 期望: Edition.PERSONAL
```

## 5. 撤销与事故响应

- 私钥泄漏：立刻吊销，重新生成密钥对，发布新版（L3 的旧 manifest 失效）
- 客户泄漏 license：通过 CRL 撤销该 customer 的所有 key(`MAOP_CRL_URL` 配置）
- 发现破解样本：追溯到破解点（通常在 license.py 的某一行被打补丁），在下一版混淆时变更结构、迁移关键逻辑

详细 license 颁发流程见 [license-issuance-guide.md](license-issuance-guide.md)。
