# MAOP 官方 Marketplace 目录（M-X1 D1/D2 批准落地）

## 文件

| 文件 | 角色 |
|---|---|
| `catalog.json` | 目录**源**（未签名；条目增删改在这里） |
| `catalog.signed.json` | **首签工件**（2026-11-01，`marketplace-prod` 键签发；每条 entry 携带 `signature_hex`） |

## 签名合同（与消费侧严格镜像）

被签载荷 = 条目 dict **去掉 `signature_hex`** 后的 canonical JSON
（`maop.core.marketplace.signing._payload_digest`），Ed25519；
`signer_key_id` 在签名**前**写入（属被签载荷）。消费侧：
`MCPMarketplace._verify_catalog_signature`（`require_signature=True`
时 fail-closed），验签公钥 = `config/marketplace_verify_key.pub`
（D2① 内置通道，wheel 资产链分发）。

## 复签配方（条目变更后）

```bash
python py/scripts/sign_marketplace_catalog.py \
    --catalog deploy/marketplace/catalog.json \
    --key /path/to/issuing_key.pem \
    --out deploy/marketplace/catalog.signed.json \
    --key-id marketplace-prod
```

管线 fail-closed：逐条签名后用内置验签公钥**自验证全部条目**，任一失败
不产出文件。首次签发与消费侧独立验证证据（2026-11-01）：

- 签发：3/3 条目 `sign → verified`（omniroute / filesystem / github）；
- 消费侧：`MCPMarketplace(require_signature=True)` 对 `catalog.signed.json`
  逐条 `_verify_catalog_signature` → 3/3 OK。

## 密钥与轮换（D1/D3 口径）

- 签发钥 = 企业发布签名根（与 `config/marketplace_verify_key.pub` 配对；
  私钥不在仓库，部署方持有）；`--key-id marketplace-prod` 为轮换模式的
  KeyManagement `tool_id`。
- 例行轮换/紧急吊销 SLA 见
  `docs/marketplace-signing-key-governance.md`（D3）；换钥 = 新钥重签目录
  + 发布带新内置公钥的版本（7 天双钥宽限期内的 try 顺序验签属 M1 小扩展）。

## 已知边界（诚实登记）

- **`checksum` 字段为 `null`，这是刻意的**：消费侧 `checksum` 语义是
  `download_url` 载荷的 **SHA-256**（`install(verify_checksum=True)` 时校验
  下载内容）。本目录三条目均为 **stdio + `npx` 拉包**型，没有 MAOP 自己
  下载的载荷——硬填 npm 的 `sha512-…` tarball integrity 会**语义错位**
  （算法不同、对象不同），故保持 `null` 并在此说明。
- **版本已钉死**（2026-11-03）：`server-filesystem@2.1.0`、
  `server-github@2025.4.8`（npm latest 实测值）。钉版本消除了
  "npx 拉到任意新版本" 的漂移风险——这是签名目录能给的确定收益。
  **升级 = 改 `catalog.json` 的 version + args 两处 → 复签 → 复审**（README
  复签配方）。
- 若将来某条目改为 MAOP 自己下载载荷（`download_url` 非空），则必须同时
  填 SHA-256 `checksum`（可用 npm `dist.shasum`=SHA-1 之外的 官方
  `dist.integrity` sha512→换算，或直接对下载物算 SHA-256）。
- 不可信注册表（`trusted: false`）对无 checksum 条目的安装另有门槛
  （需 `confirm_untrusted`），见 `mcp_marketplace` 模块 docstring。
