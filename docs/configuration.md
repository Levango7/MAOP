# MAOP 环境变量配置说明

> 本文档是 MAOP 所有环境变量的权威参考。涵盖 `.env.example` 中已定义的变量以及代码中使用但未列入示例的变量。
>
> **配置加载优先级**：环境变量 > `.env` 文件 > `config/settings.yaml` > Pydantic 默认值
>
> **最后更新**：2026-09-14 ｜ **对应版本**：v5.2.0

---

## 目录

- [1. 核心配置](#1-核心配置)
- [2. 安全与认证](#2-安全与认证)
- [3. TLS/HTTPS](#3-tlshttps)
- [4. 日志与监控](#4-日志与监控)
- [5. 数据库与存储](#5-数据库与存储)
- [6. 记忆系统](#6-记忆系统)
- [7. 缓存与队列后端](#7-缓存与队列后端)
- [8. Redis 配置](#8-redis-配置)
- [9. PostgreSQL 配置](#9-postgresql-配置)
- [10. Vault 配置](#10-vault-配置)
- [11. etcd 配置（企业版）](#11-etcd-配置企业版)
- [12. OpenTelemetry 配置](#12-opentelemetry-配置)
- [13. 限流与 CORS](#13-限流与-cors)
- [14. Dashboard 配置](#14-dashboard-配置)
- [15. Worker 与调度](#15-worker-与调度)
- [16. 预算与成本](#16-预算与成本)
- [17. 熔断器](#17-熔断器)
- [18. MCP 安全](#18-mcp-安全)
- [19. CSP 安全](#19-csp-安全)
- [20. SSO/SAML 配置（企业版）](#20-ssosaml-配置企业版)
- [21. License 与 CRL](#21-license-与-crl)
- [22. n8n 集成（企业版）](#22-n8n-集成企业版)
- [23. OmniRoute 网关](#23-omniroute-网关)
- [24. 版本与运行时](#24-版本与运行时)
- [25. Agent 与 LLM 默认配置](#25-agent-与-llm-默认配置)
- [26. 演化与自愈](#26-演化与自愈)
- [27. 沙箱与工具策略](#27-沙箱与工具策略)
- [28. 路由与模型选择](#28-路由与模型选择)
- [29. 超时配置](#29-超时配置)
- [30. 备份配置](#30-备份配置)
- [31. 告警通知](#31-告警通知)
- [32. 未文档化变量补充说明](#32-未文档化变量补充说明)
- [附录：生产环境检查清单](#附录生产环境检查清单)

---

## 1. 核心配置

| 变量名 | 默认值 | 必填 | 说明 |
|--------|--------|------|------|
| `MAOP_ENV` | `production` | 否 | 运行环境标识。影响认证默认策略、JWT 密钥校验、数据库 URL 校验等。设为 `dev`/`development`/`local`/`test` 时认证默认禁用；其他值（含未设置）默认启用认证 |
| `MAOP_PROJECT_NAME` | `MAOP` | 否 | 项目名称，用于日志和 UI 显示 |
| `MAOP_PORT` | `9079` | 否 | **已废弃**（v5.0.0）：使用 `MAOP_DASH_PORT`。仅作文档别名，v6.0.0 移除 |
| `MAOP_DASH_PORT` | `9079` | 否 | Dashboard 监听端口（`server.py` 和 `Dockerfile` 读取） |
| `MAOP_ROOT` | `/app` | 否 | 项目根目录路径 |
| `MAOP_ROOT_DIR` | （空） | 否 | 项目根目录（优先于 `MAOP_ROOT`，空则自动检测） |
| `MAOP_DEBUG` | `0` | 否 | 调试模式开关。设为 `1`/`true`/`yes` 启用 |
| `MAOP_LOG_LEVEL` | `INFO` | 否 | 日志级别：`DEBUG`/`INFO`/`WARNING`/`ERROR`/`CRITICAL` |
| `MAOP_EDITION` | `auto` | 否 | 版本选择：`personal`（个人版）/`enterprise`（企业版）/`auto`（按 License 自动检测） |

## 2. 安全与认证

| 变量名 | 默认值 | 必填 | 说明 |
|--------|--------|------|------|
| `MAOP_AUTH` | `1` | 否 | **已废弃**（v5.0.0）：使用 `MAOP_AUTH_ENABLED`。仍可读但触发 `DeprecationWarning`，v6.0.0 移除 |
| `MAOP_AUTH_ENABLED` | （见说明） | 否 | 认证启用开关。默认值由 `_default_auth_enabled()` 决定：仅 `dev`/`development`/`local`/`test` 环境默认禁用，其他一律启用 |
| `MAOP_AUTH_DISABLED_ADMIN` | `0` | 否 | 认证禁用时匿名用户角色。`0`=read 角色，`1`=admin 角色（本地开发工具需要写权限时设置） |
| `MAOP_AUTH_DB_PATH` | （空） | 否 | 认证数据库路径（空则默认 `data/auth.db`） |
| `MAOP_JWT_SECRET` | （空） | **生产必填** | JWT 签名密钥。生产环境必须设置 ≥32 字符随机字符串。生成：`python -c "import secrets; print(secrets.token_hex(32))"`。留空时非生产环境自动生成并持久化到 `data/jwt_secret` |
| `MAOP_JWT_TTL_S` | `7200` | 否 | JWT Token 有效期（秒），默认 2 小时。代码位置：`auth.py:76` |
| `MAOP_JWT_ALLOW_EPHEMERAL` | `1` | 否 | 允许临时 JWT 密钥（非生产环境自动生成时使用） |
| `MAOP_ADMIN_PASSWORD` | （空） | **生产必填** | 初始管理员密码。留空则自动生成随机密码并保存到 `data/.admin-password`。生产环境必须设置 ≥16 位随机强密码 |
| `MAOP_ADMIN_PASSWORD_FILE` | （空） | 否 | Docker secrets 标准支持。设置后从文件读取管理员密码，优先级高于 `MAOP_ADMIN_PASSWORD`。示例：`/run/secrets/maop_admin_password` |
| `MAOP_KEY` | （空） | 否 | API Key Vault 主密钥（Fernet 格式）。生产环境必填，用于 API Key 加解密 |
| `MAOP_KEY_FILE` | （空） | 否 | API Key Vault 密钥文件路径（替代 `MAOP_KEY`） |
| `MAOP_API_KEY` | （空） | 否 | 外部服务 API Key（部分适配器使用） |
| `MAOP_SECRET_KEY` | （空） | 否 | 通用密钥（部分安全模块使用） |
| `MAOP_CREDENTIAL_KEY` | （空） | 否 | 凭证加密密钥环境变量名引用（`credential_vault.py`） |
| `MAOP_TRUST_PROXY` | `0` | 否 | 信任 `X-Forwarded-For` 头。反代后端设为 `1`，默认 `0` 防止 IP 伪造 |
| `MAOP_HOOK_FAIL_MODE` | `raise` | 否 | Hook 失败模式：`raise`（抛异常）或 `log`（仅记录） |
| `MAOP_ALLOW_DESTRUCTIVE_DOWNGRADE` | `0` | 否 | 允许破坏性降级（数据迁移回退时使用） |

## 3. TLS/HTTPS

| 变量名 | 默认值 | 必填 | 说明 |
|--------|--------|------|------|
| `MAOP_TLS` | `0` | 否 | **已废弃**（v5.0.0）：使用 `MAOP_TLS_ENABLED`，v6.0.0 移除 |
| `MAOP_TLS_ENABLED` | `0` | 否 | 启用 TLS 直连（uvicorn 单 worker 模式）。生产环境推荐使用 nginx 终结 TLS |
| `MAOP_TLS_CERT` | （空） | 否 | TLS 证书路径（短名，与 `MAOP_TLS_CERT_FILE` 等效） |
| `MAOP_TLS_KEY` | （空） | 否 | TLS 私钥路径（短名，与 `MAOP_TLS_KEY_FILE` 等效） |
| `MAOP_TLS_CERT_FILE` | （空） | 否 | TLS 证书路径（长名） |
| `MAOP_TLS_KEY_FILE` | （空） | 否 | TLS 私钥路径（长名） |
| `MAOP_TLS_MIN_VERSION` | `TLSv1_2` | 否 | 最低 TLS 版本：`TLSv1_2` 或 `TLSv1_3`（TLSv1/TLSv1_1 已拒绝） |
| `MAOP_TLS_ALLOW_DEPRECATED` | `0` | 否 | 允许不安全 TLS 版本（TLSv1/TLSv1_1）。仅用于遗留集成调试 |
| `MAOP_TLS_CERT_DIR` | `./certs/nginx` | 否 | nginx TLS 证书目录（bind mount，宿主机目录须存在） |
| `MAOP_TLS_CERT_FILENAME` | `fullchain.pem` | 否 | 证书文件名（位于 `MAOP_TLS_CERT_DIR` 内） |
| `MAOP_TLS_KEY_FILENAME` | `privkey.pem` | 否 | 私钥文件名（位于 `MAOP_TLS_CERT_DIR` 内） |

## 4. 日志与监控

| 变量名 | 默认值 | 必填 | 说明 |
|--------|--------|------|------|
| `MAOP_JSON_LOG` | `1` | 否 | JSON 结构化日志输出（适配 ELK/Loki/CloudWatch）。容器环境建议设为 `1` |
| `MAOP_JSON_LOG_FILE` | （空） | 否 | JSON 日志文件路径（空则输出到 stdout） |
| `MAOP_OTEL_ENABLED` | `0` | 否 | 启用 OpenTelemetry 分布式追踪 |
| `MAOP_METRICS_ENABLED` | `1` | 否 | 启用 Prometheus 指标采集 |
| `MAOP_METRICS_PATH` | `/api/metrics` | 否 | Prometheus 指标端点路径 |
| `MAOP_METRIC_MAX_CARDINALITY` | `1000` | 否 | 指标标签基数上限（防止高基数标签导致内存溢出） |

## 5. 数据库与存储

| 变量名 | 默认值 | 必填 | 说明 |
|--------|--------|------|------|
| `MAOP_DATA_DIR` | （空） | 否 | 数据目录（空则默认 `root_dir/data`） |
| `MAOP_DB_PATH` | （空） | 否 | 主数据库路径（空则默认 `data_dir/maop.db`） |
| `MAOP_DB_PER_MODULE` | `0` | 否 | 按模块分库（每模块独立 SQLite 文件） |
| `MAOP_SQLITE_BUSY_TIMEOUT_MS` | `10000` | 否 | SQLite BUSY 超时（毫秒） |
| `MAOP_DB_BACKEND` | `sqlite` | 否 | 数据库后端选择（`db_utils.py` 读取）：`sqlite`/`postgres` |
| `MAOP_DB_URL` | （空） | 否 | SQLAlchemy URL（Alembic 迁移用，空则默认 SQLite） |
| `MAOP_DATABASE_URL` | （空） | 否 | 数据库连接 URL（覆盖默认值。生产环境必须设置带认证的连接串） |
| `MAOP_MIGRATION_BACKEND` | `sqlite` | 否 | 迁移后端：`sqlite` 或 `sql` |
| `MAOP_SQLITE_URL` | （空） | 否 | SQLite 数据库 URL（`sqlite_to_pg.py` 迁移工具用） |
| `MAOP_STORAGE_BACKEND` | `sqlite` | 否 | 存储后端（企业版）：`sqlite`/`postgres` |
| `MAOP_STORAGE_ALLOW_FALLBACK` | `0` | 否 | 允许存储后端降级到 SQLite |
| `MAOP_DB_PASSWORD` | （空） | 否 | 数据库密码（部分后端初始化使用） |

## 6. 记忆系统

| 变量名 | 默认值 | 必填 | 说明 |
|--------|--------|------|------|
| `MAOP_MEMORY_DB_PATH` | （空） | 否 | 记忆数据库路径（空则默认 `data_dir/memory.db`） |
| `MAOP_MEMORY_PRUNE_TTL_DAYS` | `90` | 否 | 记忆条目 TTL（天），超期自动清理 |
| `MAOP_MEMORY_PRUNE_ON_STARTUP` | `0` | 否 | 启动时执行记忆清理 |
| `MAOP_WORKING_CACHE_MAX_SIZE` | `1000` | 否 | 工作记忆缓存最大条目数（`memory/manager.py:129`） |

## 7. 缓存与队列后端

| 变量名 | 默认值 | 必填 | 说明 |
|--------|--------|------|------|
| `MAOP_CACHE_BACKEND` | `memory` | 否 | 缓存后端：`memory`/`redis` |
| `MAOP_QUEUE_BACKEND` | `memory` | 否 | 队列后端：`memory`/`redis`/`rabbitmq` |
| `MAOP_KV_BACKEND` | `memory` | 否 | KV 存储后端：`memory`/`redis`/`etcd` |
| `MAOP_SECRET_BACKEND` | `local` | 否 | 密钥存储后端：`local`/`vault` |
| `MAOP_HA_BACKEND` | `memory` | 否 | 高可用后端（企业版专属）：`memory`/`redis`/`etcd` |
| `MAOP_CACHE_ALLOW_FALLBACK` | `0` | 否 | 允许缓存后端降级到内存 |
| `MAOP_QUEUE_ALLOW_FALLBACK` | `0` | 否 | 允许队列后端降级到内存 |
| `MAOP_KV_ALLOW_FALLBACK` | `0` | 否 | 允许 KV 后端降级到内存 |
| `MAOP_SECRET_ALLOW_FALLBACK` | `0` | 否 | 允许密钥后端降级到本地 |
| `MAOP_VECTOR_BACKEND` | （空） | 否 | 向量存储后端（`pg_backend.py` 使用） |
| `MAOP_REDIS_URL` | （空） | 否 | Redis 连接 URL（覆盖 host/port/password 分项配置） |
| `MAOP_RABBITMQ_URL` | （空） | 否 | RabbitMQ 连接 URL |
| `MAOP_PG_DSN` | （空） | 否 | PostgreSQL DSN 连接串 |

## 8. Redis 配置

> 启用方式：`docker compose --profile redis up -d`

| 变量名 | 默认值 | 必填 | 说明 |
|--------|--------|------|------|
| `MAOP_REDIS_HOST` | `redis` | 否 | Redis 主机地址 |
| `MAOP_REDIS_PORT` | `6379` | 否 | Redis 端口 |
| `MAOP_REDIS_PASSWORD` | （空） | **启用 Redis 时必填** | Redis 密码。生产环境必须设置 ≥16 位随机强密码 |
| `MAOP_REDIS_DB` | `0` | 否 | Redis 数据库编号 |

## 9. PostgreSQL 配置

> 启用方式：`docker compose --profile postgres up -d`

| 变量名 | 默认值 | 必填 | 说明 |
|--------|--------|------|------|
| `MAOP_PG_HOST` | `postgres` | 否 | PostgreSQL 主机地址 |
| `MAOP_PG_PORT` | `5432` | 否 | PostgreSQL 端口 |
| `MAOP_PG_DATABASE` | `maop` | 否 | 数据库名称 |
| `MAOP_PG_USER` | `maop` | 否 | 数据库用户名 |
| `MAOP_PG_PASSWORD` | `maop_dev` | **启用 PG 时必填** | 数据库密码。`maop_dev` 仅限本地开发，生产环境必须修改为 ≥16 位随机密码 |

## 10. Vault 配置

> 启用方式：`MAOP_SECRET_BACKEND=vault` + `docker compose --profile vault up -d`

| 变量名 | 默认值 | 必填 | 说明 |
|--------|--------|------|------|
| `MAOP_VAULT_ADDR` | `http://vault:8200` | 否 | Vault 服务地址 |
| `MAOP_VAULT_TOKEN` | `dev-token` | 否 | Vault 访问令牌。`dev-token` 仅限本地开发 |
| `MAOP_VAULT_MOUNT` | `secret` | 否 | Vault KV 挂载路径 |
| `MAOP_VAULT_PATH` | `maop` | 否 | Vault 密钥路径前缀 |

## 11. etcd 配置（企业版）

| 变量名 | 默认值 | 必填 | 说明 |
|--------|--------|------|------|
| `MAOP_ETCD_HOST` | （空） | 否 | etcd 主机地址 |
| `MAOP_ETCD_PORT` | `2379` | 否 | etcd 端口 |
| `MAOP_ETCD_NAMESPACE` | `maop` | 否 | etcd 命名空间前缀 |
| `MAOP_ETCD_USERNAME` | （空） | 否 | etcd 认证用户名。生产环境必须启用认证 |
| `MAOP_ETCD_PASSWORD` | （空） | 否 | etcd 认证密码。须与用户名成对提供 |
| `MAOP_ETCD_CA_CERT` | （空） | 否 | etcd CA 证书路径（TLS） |
| `MAOP_ETCD_CERT_KEY` | （空） | 否 | etcd 客户端 mTLS 私钥 |
| `MAOP_ETCD_CERT_CERT` | （空） | 否 | etcd 客户端 mTLS 证书 |

## 12. OpenTelemetry 配置

| 变量名 | 默认值 | 必填 | 说明 |
|--------|--------|------|------|
| `MAOP_OTEL_ENDPOINT` | `http://otel-collector:4317` | 否 | OTLP Collector 端点 |
| `MAOP_OTEL_SERVICE_NAME` | `maop-dashboard` | 否 | 服务名称（用于追踪 span 标识） |
| `MAOP_OTEL_EXPORTER` | `otlp` | 否 | 导出器类型：`otlp`/`none` |

## 13. 限流与 CORS

| 变量名 | 默认值 | 必填 | 说明 |
|--------|--------|------|------|
| `MAOP_RATE_LIMIT_ENABLED` | `1` | 否 | 启用限流 |
| `MAOP_RATE_LIMIT_RPS` | `30` | 否 | 每秒请求上限 |
| `MAOP_RATE_LIMIT_BURST` | `60` | 否 | 突发请求上限 |
| `MAOP_RATE_LIMIT` | （空） | 否 | 限流覆盖开关（测试用，设为 `0` 禁用） |
| `MAOP_CORS_ORIGINS` | `http://localhost:9079,...` | 否 | 允许的 CORS 源（逗号分隔） |
| `MAOP_APIKEY_RATE_WINDOW_S` | （空） | 否 | API Key 限流时间窗口（秒） |

## 14. Dashboard 配置

| 变量名 | 默认值 | 必填 | 说明 |
|--------|--------|------|------|
| `MAOP_DASH_HOST` | `127.0.0.1` | 否 | 监听地址。容器部署须设为 `0.0.0.0`。安全默认仅本机访问 |
| `MAOP_DASH_WORKERS` | `1` | 否 | Uvicorn worker 数量。>1 时自动禁用后台任务 |
| `MAOP_WORKERS` | `4` | 否 | **已废弃**（v5.0.0）：使用 `MAOP_DASH_WORKERS`，v6.0.0 移除 |
| `MAOP_BACKGROUND_TASKS` | `1` | 否 | 启用每 worker 后台任务（备份、日志轮转、WS 推送）。多 worker 时自动禁用 |
| `MAOP_EXPOSE_DOCS` | （空） | 否 | 强制暴露 API 文档端点（`/api/docs`）。生产环境默认禁用 |
| `MAOP_AUTO_SCHED` | `0` | 否 | 自动调度开关 |

## 15. Worker 与调度

| 变量名 | 默认值 | 必填 | 说明 |
|--------|--------|------|------|
| `MAOP_WORKER_COUNT` | `4` | 否 | Worker 池大小（1–64） |
| `MAOP_DISPATCH_CONCURRENCY` | `10` | 否 | 最大并发调度数（1–100） |
| `MAOP_DISPATCH_RETRY_MAX` | `3` | 否 | 调度最大重试次数（0–10） |
| `MAOP_DISPATCH_RETRY_BASE_MS` | `500` | 否 | 调度重试基础延迟（毫秒，≥100） |

## 16. 预算与成本

| 变量名 | 默认值 | 必填 | 说明 |
|--------|--------|------|------|
| `MAOP_BUDGET_DAILY_LIMIT_USD` | `0` | 否 | 日预算上限（USD）。0=无限额。也可在 Dashboard 运行时配置 |
| `MAOP_BUDGET_MONTHLY_LIMIT_USD` | `0` | 否 | 月预算上限（USD）。0=无限额 |
| `MAOP_BUDGET_ALERT_THRESHOLD` | `0.8` | 否 | 预算告警阈值（占限额比例，0–1） |
| `MAOP_PERSONAL_COST_CAP` | `0` | 否 | 个人版全局累计成本上限（USD）。达到阈值触发熔断。0=不限 |
| `MAOP_PERSONAL_COST_HARD` | `0` | 否 | 硬熔断开关。`0`=软熔断（拒绝新调用，运行中任务跑完），`1`=硬熔断（中断运行中任务） |

## 17. 熔断器

| 变量名 | 默认值 | 必填 | 说明 |
|--------|--------|------|------|
| `MAOP_CB_FAILURE_THRESHOLD` | `5` | 否 | 触发熔断的失败次数阈值 |
| `MAOP_CB_RECOVERY_TIMEOUT_S` | `30` | 否 | 熔断恢复超时（秒） |

## 18. MCP 安全

| 变量名 | 默认值 | 必填 | 说明 |
|--------|--------|------|------|
| `MAOP_MCP_STRICT_COMMAND_WHITELIST` | `1` | 否 | MCP stdio 传输严格命令白名单。生产环境必须为 `1`（禁用则关闭 RCE 保护） |
| `MAOP_PLUGIN_STRICT_CHECKSUM` | `1` | 否 | 插件校验和严格验证。`1`=fail-closed（不匹配则拒绝），`0`=仅本地开发 |

## 19. CSP 安全

| 变量名 | 默认值 | 必填 | 说明 |
|--------|--------|------|------|
| `MAOP_CSP` | （空） | 否 | Content-Security-Policy 头内容（空则使用默认策略） |
| `MAOP_CSP_REPORT_ONLY` | `0` | 否 | CSP 仅报告模式（不阻止违规） |
| `MAOP_CSP_REPORT_URI` | （空） | 否 | CSP 违规报告端点 |
| `MAOP_CSP_CONNECT_SRC` | （空） | 否 | CSP `connect-src` 指令额外源 |

## 20. SSO/SAML 配置（企业版）

| 变量名 | 默认值 | 必填 | 说明 |
|--------|--------|------|------|
| `MAOP_SSO_PROVIDER` | （空） | 否 | SSO 提供商类型：`oidc`/`saml` |
| `MAOP_SSO_CLIENT_ID` | （空） | 否 | SSO 客户端 ID |
| `MAOP_SSO_CLIENT_SECRET` | （空） | **启用 SSO 时必填** | SSO 客户端密钥。须 ≥32 位随机字符串 |
| `MAOP_SSO_AUTHORIZE_URL` | （空） | 否 | OIDC 授权端点 URL |
| `MAOP_SSO_TOKEN_URL` | （空） | 否 | OIDC Token 端点 URL |
| `MAOP_SSO_USERINFO_URL` | （空） | 否 | OIDC UserInfo 端点 URL |
| `MAOP_SSO_REDIRECT_URI` | （空） | 否 | SSO 回调 URI |
| `MAOP_SSO_SCOPES` | `openid profile email` | 否 | OIDC scopes（逗号分隔） |

## 21. License 与 CRL

| 变量名 | 默认值 | 必填 | 说明 |
|--------|--------|------|------|
| `MAOP_LICENSE_KEY` | （空） | 否 | 企业版 License Key。也可保存到 `data/license.key` 文件 |
| `MAOP_LICENSE_MGR_PRIVATE_KEY` | （空） | 否 | License 管理器私钥路径（`licenses.py:68`，签发 License 用） |
| `MAOP_CRL_URL` | （空） | 否 | CRL（证书撤销列表）URL |
| `MAOP_CRL_CACHE_TTL_S` | `86400` | 否 | CRL 缓存 TTL（秒，默认 24 小时） |
| `MAOP_CRL_STRICT` | `0` | 否 | CRL 严格模式（撤销列表不可达时拒绝启动） |
| `MAOP_CRL_MAX_CACHE_AGE_S` | `604800` | 否 | CRL 最大缓存年龄（秒，默认 7 天） |

## 22. n8n 集成（企业版）

> 启用方式：`docker compose --profile n8n up -d`

| 变量名 | 默认值 | 必填 | 说明 |
|--------|--------|------|------|
| `N8N_BASE_URL` | `http://localhost:5678` | 否 | n8n 服务地址 |
| `N8N_USER` | `admin` | 否 | n8n 管理员用户名 |
| `N8N_PASSWORD` | `change-me-in-production` | **生产必填** | n8n 管理员密码。生产环境必须修改为 ≥16 位随机强密码 |
| `N8N_API_KEY` | （空） | **生产必填** | n8n API Key。须 ≥32 位随机密钥 |
| `N8N_WEBHOOK_SECRET` | （空） | 否 | n8n Webhook 签名密钥 |

## 23. OmniRoute 网关

| 变量名 | 默认值 | 必填 | 说明 |
|--------|--------|------|------|
| `OMNIROUTE_API_KEY` | `dummy` | 否 | OmniRoute API Key。本地网关（`http://localhost:20128`）免鉴权，设为 `dummy` 即可。远程接入须替换为真实密钥 |

## 24. 版本与运行时

| 变量名 | 默认值 | 必填 | 说明 |
|--------|--------|------|------|
| `MAOP_VERSION` | （代码定义） | 否 | 版本号（通常从 `maop.__version__` 读取，一般无需手动设置） |
| `MAOP_RUNTIME` | （空） | 否 | 运行时标识（部分监控指标使用） |
| `DOC_PIPELINE_ROOT` | （空） | 否 | doc-pipeline 项目路径 |

## 25. Agent 与 LLM 默认配置

| 变量名 | 默认值 | 必填 | 说明 |
|--------|--------|------|------|
| `MAOP_DEFAULT_AGENT` | `codex` | 否 | 默认 Agent 名称（`maop_plan.py:30`，Plan 阶段使用） |
| `MAOP_LLM_DEFAULT_AGENT` | `MAOP` | 否 | Chat 界面默认 Agent 名称（`chat.py:46`） |
| `MAOP_LLM_DEFAULT_MODEL` | （空） | 否 | Chat 界面默认模型名称（`chat.py:47`） |
| `MAOP_ACTIVE_AGENTS` | （空） | 否 | 活跃 Agent 数量指标（监控用） |

## 26. 演化与自愈

| 变量名 | 默认值 | 必填 | 说明 |
|--------|--------|------|------|
| `MAOP_EVOLUTION_LOOP_ENABLED` | （空） | 否 | 演化循环开关。设为 `1`/`true`/`yes`/`on` 启用（`evolve_insights.py:332`） |
| `MAOP_EVOLVE_LLM_SUGGEST` | （空） | 否 | 演化 LLM 建议开关 |

## 27. 沙箱与工具策略

| 变量名 | 默认值 | 必填 | 说明 |
|--------|--------|------|------|
| `MAOP_SANDBOX_DIR` | （空） | 否 | 沙箱隔离目录路径 |
| `MAOP_SANDBOX_ENV_FILE` | （空） | 否 | 沙箱环境变量文件路径 |
| `MAOP_TOOL_POLICY_CONFIG` | （空） | 否 | 工具策略配置文件路径 |
| `MAOP_TOOL_POLICY_MODE` | （空） | 否 | 工具策略模式 |
| `MAOP_QUOTA_MIDDLEWARE` | `1` | 否 | 企业版配额中间件开关。**默认 `1`（启用）**；设为 `0` 会完全关闭配额强制（配额检查不再生效）。生产环境请勿设为 `0`。注意：仅在 `maop.enterprise` 可导入且 license 激活时该中间件才真正挂载，否则静默跳过。 |

## 28. 路由与模型选择

| 变量名 | 默认值 | 必填 | 说明 |
|--------|--------|------|------|
| `MAOP_ROUTE_DECISION_MODE` | （空） | 否 | 路由决策模式 |
| `MAOP_MODEL_SELECTION_LOAD_AWARE` | （空） | 否 | 模型选择负载感知开关 |
| `MAOP_TASK_DEADLINE_SECONDS` | （空） | 否 | 任务截止时间（秒） |

## 29. 超时配置

> P3-B-04: 集中式超时配置（`settings.py` 中定义）

| 变量名 | 默认值 | 必填 | 说明 |
|--------|--------|------|------|
| `MAOP_SUBPROCESS_TIMEOUT_S` | `10` | 否 | 子进程调用超时（秒） |
| `MAOP_HTTP_TIMEOUT_S` | `30` | 否 | HTTP 请求超时（秒） |
| `MAOP_UPGRADE_TIMEOUT_S` | `120` | 否 | 升级操作超时（秒） |
| `MAOP_TASK_WAIT_TIMEOUT_S` | `300` | 否 | 任务等待超时（秒） |

## 30. 备份配置

| 变量名 | 默认值 | 必填 | 说明 |
|--------|--------|------|------|
| `MAOP_BACKUP_INTERVAL` | `3600` | 否 | 备份间隔（秒） |
| `MAOP_BACKUP_DIR` | （空） | 否 | 备份目录路径（`db_backup.py:97`，空则使用默认 `data/backups`） |
| `MAOP_BACKUP_S3_BUCKET` | （空） | 否 | S3 备份桶名。设置后启用 S3 上传（off-box 备份） |
| `MAOP_BACKUP_S3_PREFIX` | `backups/` | 否 | S3 备份路径前缀 |
| `MAOP_BACKUP_S3_REGION` | （空） | 否 | S3 区域 |
| `MAOP_BACKUP_S3_ENDPOINT` | （空） | 否 | S3 端点（兼容 MinIO 等） |
| `MAOP_BACKUP_KEEP_LOCAL` | `0` | 否 | S3 上传后是否保留本地副本 |
| `MAOP_LOGROTATE_INTERVAL` | `3600` | 否 | 日志轮转间隔（秒） |

## 31. 告警通知

> 启用方式：`docker compose -f docker-compose.yml -f docker-compose.prod.yml --profile monitoring up -d`

| 变量名 | 默认值 | 必填 | 说明 |
|--------|--------|------|------|
| `WEBHOOK_URL` | `http://maop-app:9079/api/alerts/webhook` | 否 | AlertManager Webhook 接收端点 |
| `ALERTS_WEBHOOK_SECRET` | （空） | 否 | 告警 Webhook 签名密钥（`alerts.py:61`，用于验证 AlertManager 请求） |
| `ALERT_EMAIL_TO` | `admin@example.com` | 否 | 告警邮件收件人 |
| `ALERT_EMAIL_FROM` | `alertmanager@example.com` | 否 | 告警邮件发件人 |
| `SMTP_HOST` | `localhost:587` | 否 | SMTP 服务器地址 |
| `SMTP_USER` | （空） | 否 | SMTP 认证用户名 |
| `SMTP_PASSWORD` | （空） | 否 | SMTP 认证密码 |
| `SMTP_AUTH_SECRET` | （空） | 否 | SMTP AUTH SECRET |
| `SMTP_AUTH_IDENTITY` | （空） | 否 | SMTP AUTH IDENTITY |
| `SLACK_WEBHOOK_URL` | （空） | 否 | Slack Webhook URL（空则禁用 Slack 告警） |
| `SLACK_CHANNEL` | `#alerts` | 否 | Slack 告警频道 |
| `GRAFANA_PASSWORD` | `change-me-in-production` | **生产必填** | Grafana 管理员密码。生产环境必须修改 |

---

## 32. 未文档化变量补充说明

以下变量在代码中使用但未列入 `.env.example`，属于内部或高级配置：

### 32.1 运行时指标变量（只读，勿手动设置）

以下变量名是 Prometheus 指标标签，由代码自动生成，**不应手动设置**：

- `MAOP_DELEGATIONS_TOTAL` / `MAOP_DELEGATIONS_SUCCESS` / `MAOP_DELEGATIONS_FAILED` / `MAOP_DELEGATION_DURATION`
- `MAOP_MCP_CALLS_TOTAL` / `MAOP_MCP_CALL_DURATION_SECONDS` / `MAOP_MCP_CALL_ERRORS_TOTAL` 等 MCP 指标
- `MAOP_MCP_CACHE_HIT_TOTAL` / `MAOP_MCP_CACHE_MISS_TOTAL` / `MAOP_MCP_CACHE_EVICTION_TOTAL`
- `MAOP_MCP_CONCURRENT_ACTIVE` / `MAOP_MCP_SERVERS_CONNECTED` / `MAOP_MCP_RATE_LIMITED_TOTAL`
- `MAOP_MEMORY_ENTRIES` / `MAOP_PRIORITY_QUEUE_SIZE` / `MAOP_QUEUE_PENDING`
- `MAOP_TASK_PREEMPTION_TOTAL` / `MAOP_TASK_SLA_VIOLATION_TOTAL` / `MAOP_TASK_PRIORITY_DISTRIBUTION`
- `MAOP_ROUTING_DECISION_TOTAL` / `MAOP_ROUTING_DECISION_DURATION_MS`
- `MAOP_STICKY_SESSION_HIT` / `MAOP_STICKY_SESSION_MISS` / `MAOP_STICKY_SESSION_ACTIVE`
- `MAOP_CIRCUIT_BREAKER_STATE` / `MAOP_PRIORITY_WAIT_HISTOGRAMS`
- `MAOP_MODEL_SELECTION_QUOTA_REJECTED` / `MAOP_TASK_SLA_TIER_DISTRIBUTION`

### 32.2 内部测试/兼容变量

- `MAOP_ROOT_DIR_VAR` / `MAOP_ROOT_LEGACY_VAR` — 根目录环境变量兼容性测试用
- `MAOP_TLS_ENABLED_VAR` / `MAOP_TLS_LEGACY_VAR` — TLS 环境变量兼容性测试用
- `MAOP_KEY_` — API Key 前缀匹配测试用
- `MAOP_BUDGET_` — 预算前缀匹配测试用
- `MAOP_SANDBOX_` — 沙箱前缀匹配测试用
- `MAOP_TASK_PLACEHOLDER` — 任务占位符测试用
- `MAOP_BACKEND_QUEUE` — 后端队列测试用

---

## 附录：生产环境检查清单

部署生产环境前，逐项确认以下变量已正确设置：

### 必须修改的变量

- [ ] `MAOP_ENV=production`
- [ ] `MAOP_JWT_SECRET` — 设置 ≥32 字符随机密钥
- [ ] `MAOP_ADMIN_PASSWORD` 或 `MAOP_ADMIN_PASSWORD_FILE` — 设置 ≥16 位随机强密码
- [ ] `MAOP_DASH_HOST=0.0.0.0` — 容器部署须改为 `0.0.0.0`
- [ ] `MAOP_CORS_ORIGINS` — 设置为实际前端域名

### 条件必填（按启用的后端）

- [ ] `MAOP_PG_PASSWORD` — 启用 PostgreSQL 时必须修改（禁止 `maop_dev`）
- [ ] `MAOP_REDIS_PASSWORD` — 启用 Redis 时必须设置 ≥16 位随机密码
- [ ] `MAOP_KEY` 或 `MAOP_KEY_FILE` — API Key Vault 主密钥
- [ ] `MAOP_DATABASE_URL` — 生产环境必须设置带认证的连接串
- [ ] `GRAFANA_PASSWORD` — 启用监控时必须修改
- [ ] `N8N_PASSWORD` / `N8N_API_KEY` — 启用 n8n 时必须设置
- [ ] `MAOP_SSO_CLIENT_SECRET` — 启用 SSO 时必须设置 ≥32 位随机密钥
- [ ] `MAOP_VAULT_TOKEN` — 启用 Vault 时必须替换 `dev-token`

### 安全加固

- [ ] `MAOP_MCP_STRICT_COMMAND_WHITELIST=1` — MCP 命令白名单（默认已启用）
- [ ] `MAOP_PLUGIN_STRICT_CHECKSUM=1` — 插件校验和验证（默认已启用）
- [ ] `MAOP_TRUST_PROXY=1` — 反代后端须设置
- [ ] `MAOP_TLS_ALLOW_DEPRECATED=0` — 禁止不安全 TLS（默认已启用）
- [ ] `MAOP_AUTH_DISABLED_ADMIN=0` — 禁止匿名 admin（默认已启用）

### 推荐配置

- [ ] `MAOP_JSON_LOG=1` — JSON 结构化日志
- [ ] `MAOP_OTEL_ENABLED=1` — 启用分布式追踪
- [ ] `MAOP_BACKUP_S3_BUCKET` — 配置 off-box 备份
- [ ] `MAOP_PERSONAL_COST_CAP` — 设置成本上限护栏

---

> **注意**：`.env.example` 是环境变量示例的单一事实来源（single source of truth）。本文档是对其的补充和扩展说明。如发现变量不一致，请以 `.env.example` 和 `py/maop/config/settings.py` 为准并提交 issue。
>
> **相关文档**：[部署指南](./deployment.md) ｜ [用户指南](./user-guide.md) ｜ [故障排查](./troubleshooting.md)