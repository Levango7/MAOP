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

- `checksum` 字段当前为 `null`：签名保证**条目内容**完整性；`checksum`
  保护的是 `download_url` 载荷本身（omniroute 为本地网关无下载载荷；
  npm 两条目待版本钉死后补 sha256）——不可信注册表安装路径对无 checksum
  条目另有门槛（见 `mcp_marketplace` 模块 docstring）。
