# Marketplace 包签名 — 威胁模型（M-X1 M0，2026-10-31）

> 范围：MCP Marketplace 目录条目的 Ed25519 签名校验（`core/marketplace/signing.py`
> + `core/mcp/mcp_marketplace.py` 安装路径的接线）。不含：Skill 市场二进制分发、
> 沙箱执行（`core/marketplace/sandbox.py`，另行排期）。

## 资产与信任锚

- **被保护物**：安装进 `mcp_installed.yaml` 的 MCP server 配置（command/args/env
  可携带凭据与代码执行入口）。
- **信任锚**：运维配置的 Ed25519 公钥（`MAOP_MARKETPLACE_VERIFY_KEY` 指向 PEM）。
  私钥只在签发方（marketplace operator / 包作者）一侧，**永不出现在安装侧**。
- **既有基线**（不变）：SHA-256 checksum + `trusted` 注册表标志；`confirm_untrusted`
  显式豁免。

## 信任模型

签名覆盖**注册表发布的原始条目 dict**（去掉 `signature_hex` 后 canonical JSON，
sorted-keys 无空白）。安装时逐条验签；任何失败（畸形 / 缺失 / 不匹配）都以
`ValueError`（`SignatureError` 是其子类）拒绝安装——**fail-closed**。

默认（未注入 verifier）**不改变既有信任模型**：checksum + trusted 照旧。这是
M0 的显式取舍——验签是**可选启用**的增强，不是默认强制（生产启用属部署决策，
与"生产签发流程呈批"同一决策点）。

## 威胁与缓解

| 威胁 | 缓解 |
|---|---|
| 伪造签名 / 冒充 operator | Ed25519 非对称：无私钥不可签名（G-01：替代曾用的 HMAC 对称方案） |
| 条目被篡改（注册表或中间人） | 签名覆盖原始发布 dict 的 canonical JSON；任何字段变动 → 验签失败拒绝安装 |
| checksum 与签名双门绕过 | 两道门独立生效：checksum 校验下载产物，签名校验元数据完整性；`signature_hex` 本身不在被签载荷内 |
| 验签私钥泄露 | 安装侧无私钥；operator 侧泄露 → `KeyManagement` 轮换（宽限期双钥重叠）+ 黑名单吊销（`verify_with_key_management` 拒绝黑名单钥） |
| 重放 | 签名对象是条目内容而非时序凭据；条目更新即需重签（无时间窗可重放） |
| 降级（剥掉 `signature_hex` 再投毒） | `require_signature=True`（CLI `--require-signature`）强制缺失即拒；M0 默认不强制（见上取舍） |
| 未知键丢失导致验签误判 | 验签用注册表**原始 dict**（`_fetch_registry_catalog` 保留 raw entry）， narrowed model 只作展示 |

## 残余风险（如实登记）

1. **未配置 verifier 的部署** = 只有 checksum + trusted 信任（现状）；`trusted`
   标志本身由注册表配置文件决定——配置文件被篡改即绕过（与 M0 前一致，非本批回归）。
2. **`n8n` 等"对方版本决定键集合"的外部条目**：签名覆盖原始键集，但**语义**层
   的注入（如 default_config 里的恶意 command）签名无法识别——签名证明"发布者
   发布了它"，不证明"发布者可信"；发布者准入属 registry 治理。
3. **sandbox 未接线**（`core/marketplace/sandbox.py`）：安装的 server 进程环境
   隔离防护未生效（`check_wiring.py` 在册）。

## 密钥治理（前置决策点）

- **M0**：dev/test key（`generate_keypair`）仅用于测试与演练；不签发生产包。
- **生产启用**（呈批项）：① 公钥分发通道（镜像内随包 / 运维下发）；
  ② operator 私钥保管（与 MAOS 激活链同一治理等级）；③ 轮换与吊销 SLA；
  ④ `require_signature` 的默认值是否翻转（secure-by-default 演进）。
