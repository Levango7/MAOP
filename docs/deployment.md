# MAOP 生产部署指南

## 架构概览

```
┌─────────────┐     ┌──────────────┐     ┌──────────────┐
│   Nginx     │────▶│  Dashboard   │────▶│  Agent-Exec  │
│  (TLS终止)  │     │  (FastAPI)   │     │  (Worker)    │
└─────────────┘     └──────┬───────┘     └──────┬───────┘
                           │                      │
                    ┌──────▼───────┐      ┌───────▼──────┐
                    │ Queue-Worker │      │  SQLite Vol  │
                    │ (后台任务)   │      │  (共享存储)  │
                    └──────────────┘      └──────────────┘
```

## 快速启动

### 单机模式（开发/测试）

```bash
# 安装依赖
cd py && pip install -e ".[dev]"

# 启动 Dashboard
maop start --port 9079

# 或直接运行
python -m maop.dashboard.server
```

### Docker Compose（生产推荐）

```bash
# 复制环境配置
cp .env.example .env
# 编辑 .env 按需调整

# 启动所有服务
docker compose up -d

# 查看状态
docker compose ps
docker compose logs -f dashboard
```

### 微服务模式

```bash
# 仅启动 Dashboard + Agent Executor
docker compose up -d dashboard agent-exec

# 启用 OTel 追踪
docker compose --profile otel up -d

# 启用 TLS
docker compose --profile tls up -d
```

## 环境变量参考

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `MAOP_ROOT` | `/app` | 项目根目录 |
| `MAOP_DATA_DIR` | `/app/data` | 数据目录 |
| `MAOP_LOG_LEVEL` | `INFO` | 日志级别 |
| `MAOP_DASH_PORT` | `9079` | Dashboard 端口 |
| `MAOP_DASH_HOST` | `127.0.0.1` | Dashboard 监听地址 |
| `MAOP_WORKERS` | `1` | Uvicorn worker 数（>1 时禁用 TLS） |
| `MAOP_AUTH_ENABLED` | `0` | 启用认证 |
| `MAOP_TLS` | `0` | 启用 TLS |
| `MAOP_TLS_CERT` | — | TLS 证书路径 |
| `MAOP_TLS_KEY` | — | TLS 私钥路径 |
| `MAOP_RATE_LIMIT_RPS` | `30` | 速率限制（请求/秒） |
| `MAOP_RATE_LIMIT_BURST` | `60` | 速率限制突发 |
| `MAOP_CORS_ORIGINS` | `http://localhost:9079` | CORS 允许源 |
| `MAOP_BACKEND_STORAGE` | `sqlite` | 存储后端 |
| `MAOP_BACKEND_CACHE` | `local` | 缓存后端 |
| `MAOP_BACKEND_QUEUE` | `local` | 队列后端 |
| `MAOP_BACKEND_KV` | `local` | KV 存储后端 |
| `MAOP_BACKEND_SECRET` | `local` | 密钥管理后端 |
| `MAOP_OTEL_ENABLED` | `0` | 启用 OTel 追踪 |
| `MAOP_OTEL_EXPORTER` | `otlp` | OTel 导出器（otlp/console/none） |
| `MAOP_OTEL_ENDPOINT` | `http://localhost:4317` | OTLP 端点 |
| `MAOP_OTEL_SERVICE_NAME` | `maop` | OTel 服务名 |
| `MAOP_AUTO_SCHED` | `1` | 自动启动备份/日志轮转调度器 |
| `MAOP_BACKUP_INTERVAL` | `3600` | 备份间隔（秒） |
| `MAOP_LOGROTATE_INTERVAL` | `600` | 日志轮转间隔（秒） |

## 多 Worker 部署

```bash
# 4 worker 模式（注意：多 worker 不支持 TLS 直连，需用 Nginx 代理）
MAOP_WORKERS=4 maop start --host 0.0.0.0 --port 9079
```

> **注意**：多 worker 模式下 SQLite 写入需要 WAL 模式。MAOP 默认启用 WAL。
> 如需更高并发，考虑将 `MAOP_BACKEND_STORAGE` 切换到分布式后端。

## OTel 追踪配置

### 接入 Jaeger

```yaml
# otel-collector-config.yaml
exporters:
  otlp/jaeger:
    endpoint: jaeger:4317
    tls:
      insecure: true
```

```bash
MAOP_OTEL_ENABLED=1 MAOP_OTEL_EXPORTER=otlp MAOP_OTEL_ENDPOINT=http://localhost:4317 \
  maop start
```

### 控制台输出（调试）

```bash
MAOP_OTEL_ENABLED=1 MAOP_OTEL_EXPORTER=console maop start
```

## TLS 配置

### 方式一：Uvicorn 直连 TLS（单 worker）

```bash
MAOP_TLS=1 MAOP_TLS_CERT=/path/to/cert.pem MAOP_TLS_KEY=/path/to/key.pem \
  maop start --host 0.0.0.0
```

### 方式二：Nginx 代理 TLS（推荐生产）

```bash
# 1. 准备证书
mkdir certs && cp your-cert.pem certs/ && cp your-key.pem certs/

# 2. 创建 nginx.conf（参考项目根目录模板）

# 3. 启动
docker compose --profile tls up -d
```

## 故障排除

### Dashboard 无法启动

1. 检查端口占用：`lsof -i :9079` 或 `netstat -an | findstr 9079`
2. 检查依赖：`pip install -e ".[dev]"`
3. 查看日志：`docker compose logs dashboard`

### SQLite 锁定错误

- 确认 WAL 模式：`PRAGMA journal_mode=WAL;`
- 多 worker 场景下避免并发写入同一数据库
- 考虑切换到分布式存储后端

### OTel 追踪不生效

1. 确认 `MAOP_OTEL_ENABLED=1`
2. 确认安装了 `opentelemetry-api` 和 `opentelemetry-sdk`
3. OTLP 模式需安装 `opentelemetry-exporter-otlp-proto-grpc`
4. 检查 Collector 是否运行：`curl http://localhost:4318/v1/traces`

### Docker 服务健康检查失败

```bash
# 手动检查
docker compose exec dashboard python -c "import urllib.request; urllib.request.urlopen('http://localhost:9079/api/health')"

# 查看详细日志
docker compose logs --tail 50 dashboard
```

## 数据库备份

MAOP 自动备份 `data/` 下的 SQLite 数据库（`maop.db`, `memory.db`, `queue.db`, `human_queue.db`）。

- 备份目录：`data/backups/`
- 默认保留：10 份/库
- 手动触发：通过 Dashboard API `POST /api/system/backup`
- 禁用自动备份：`MAOP_AUTO_SCHED=0`

## 安全加固清单

- [x] 非 root 用户运行（Docker）
- [x] 速率限制（默认 30 RPS）
- [x] CORS 限制（非通配符）
- [x] 认证中间件（可选启用）
- [x] TLS 支持
- [x] 密钥不落盘（ApiKeyVault）
- [x] 审计日志
- [ ] 网络隔离（Docker network）
- [ ] 只读文件系统（config/ 挂载为 ro）