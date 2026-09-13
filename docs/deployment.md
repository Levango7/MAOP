# MAOP 部署指南

> 本文档覆盖 MAOP 的全部部署方式：Docker Compose、Kubernetes（Operator/Helm）、裸机部署（含 PM2 进程管理）、TLS 配置、监控部署及升级回滚。
>
> **对应版本**：v5.1.0 ｜ **最后更新**：2026-09-14
>
> **相关文档**：[环境变量配置](./configuration.md) ｜ [运维手册](./runbook.md) ｜ [容量规划](./capacity-planning.md)

---

## 目录

- [1. Docker Compose 部署](#1-docker-compose-部署)
  - [1.1 快速启动（开发环境）](#11-快速启动开发环境)
  - [1.2 生产环境部署](#12-生产环境部署)
  - [1.3 启用 PostgreSQL 后端](#13-启用-postgresql-后端)
  - [1.4 启用 Redis 缓存](#14-启用-redis-缓存)
  - [1.5 启用监控栈](#15-启用监控栈)
  - [1.6 启用 TLS](#16-启用-tls)
  - [1.7 启用 OpenTelemetry](#17-启用-opentelemetry)
  - [1.8 启用 n8n 集成（企业版）](#18-启用-n8n-集成企业版)
  - [1.9 启用 Vault 密钥管理](#19-启用-vault-密钥管理)
- [2. Kubernetes 部署](#2-kubernetes-部署)
  - [2.1 Operator + Helm 部署](#21-operator--helm-部署)
  - [2.2 健康检查配置](#22-健康检查配置)
  - [2.3 资源限制](#23-资源限制)
  - [2.4 自定义 values 覆盖](#24-自定义-values-覆盖)
- [3. 裸机部署](#3-裸机部署)
  - [3.1 Linux/macOS 直接运行](#31-linuxmacos-直接运行)
  - [3.2 PM2 进程管理](#32-pm2-进程管理)
  - [3.3 systemd 服务](#33-systemd-服务)
  - [3.4 Windows 部署](#34-windows-部署)
- [4. TLS/HTTPS 配置](#4-tlshttps-配置)
- [5. Nginx 反向代理](#5-nginx-反向代理)
- [6. 数据库迁移](#6-数据库迁移)
- [7. 升级与回滚](#7-升级与回滚)
- [8. 验证部署](#8-验证部署)

---

## 1. Docker Compose 部署

MAOP 提供两个 Compose 文件：

| 文件 | 用途 |
|------|------|
| `docker-compose.yml` | 基础服务定义（dashboard、agent-exec、queue-worker） |
| `docker-compose.prod.yml` | 生产覆盖（nginx、资源限制、安全加固） |

### 1.1 快速启动（开发环境）

```bash
# 1. 克隆仓库
git clone https://github.com/Levango7/MAOP.git
cd MAOP

# 2. 复制环境配置
cp .env.example .env

# 3. 启动服务（仅 Dashboard，SQLite 存储）
docker compose up -d

# 4. 检查健康状态
curl http://localhost:9079/api/health

# 5. 查看日志
docker compose logs -f dashboard
```

首次启动时会自动：
- 运行数据库迁移
- 生成 JWT 密钥（如未设置 `MAOP_JWT_SECRET`）
- 生成管理员密码（如未设置 `MAOP_ADMIN_PASSWORD`，保存到 `data/.admin-password`）

### 1.2 生产环境部署

```bash
# 1. 复制并编辑环境配置
cp .env.example .env

# 2. 设置生产环境必需变量（参见 docs/configuration.md 附录：生产环境检查清单）
# 至少修改以下项：
#   MAOP_ENV=production
#   MAOP_JWT_SECRET=<python -c "import secrets; print(secrets.token_hex(32))">
#   MAOP_ADMIN_PASSWORD=<python -c "import secrets; print(secrets.token_urlsafe(16))">
#   MAOP_DASH_HOST=0.0.0.0
#   MAOP_CORS_ORIGINS=https://your-domain.com

# 3. 启动生产栈（含 Nginx 反代）
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d

# 4. 验证
curl -f http://localhost:9079/api/health || echo "FAILED"
```

**生产环境注意事项**：
- `MAOP_ENV=production` 时 API 文档端点（`/api/docs`）自动禁用
- `docker-compose.prod.yml` 包含 Nginx TLS 终结、资源限制、安全头
- 建议配合 `--profile monitoring` 启用 Prometheus + Grafana 监控

### 1.3 启用 PostgreSQL 后端

```bash
# 1. 在 .env 中设置 PostgreSQL 密码（禁止使用 maop_dev）
echo 'MAOP_PG_PASSWORD=<强密码>' >> .env

# 2. 启动（自动启用 PG profile）
docker compose -f docker-compose.yml -f docker-compose.prod.yml --profile postgres up -d

# 3. 验证 PostgreSQL 连接
docker compose exec postgres pg_isready -U maop -d maop
```

**适用场景**：多容器部署、高并发写入、企业版多租户。

### 1.4 启用 Redis 缓存

```bash
# 1. 在 .env 中设置 Redis 密码
echo 'MAOP_REDIS_PASSWORD=<强密码>' >> .env
echo 'MAOP_CACHE_BACKEND=redis' >> .env
echo 'MAOP_QUEUE_BACKEND=redis' >> .env

# 2. 启动
docker compose -f docker-compose.yml -f docker-compose.prod.yml --profile redis up -d
```

### 1.5 启用监控栈

```bash
# 启动含 Prometheus + Grafana + AlertManager 的完整监控栈
docker compose -f docker-compose.yml -f docker-compose.prod.yml --profile monitoring up -d

# 访问 Grafana（默认 admin / GRAFANA_PASSWORD）
# http://localhost:3000
```

**AlertManager 配置**：通过环境变量注入接收器配置（参见 `.env.example` 中 AlertManager 部分）。

### 1.6 启用 TLS

**方式一：Nginx 终结 TLS（推荐）**

```bash
# 1. 准备证书文件
mkdir -p ./certs/nginx
cp /path/to/fullchain.pem ./certs/nginx/
cp /path/to/privkey.pem ./certs/nginx/

# 2. 在 .env 中配置
echo 'MAOP_TLS_CERT_DIR=./certs/nginx' >> .env

# 3. 启动（docker-compose.prod.yml 已包含 Nginx TLS 配置）
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

**方式二：uvicorn 直连 TLS（单 worker，仅开发/测试）**

```bash
echo 'MAOP_TLS_ENABLED=1' >> .env
echo 'MAOP_TLS_CERT=/path/to/cert.pem' >> .env
echo 'MAOP_TLS_KEY=/path/to/key.pem' >> .env
docker compose up -d
```

### 1.7 启用 OpenTelemetry

```bash
# 启用 OTel Collector + Jaeger
docker compose --profile otel up -d

# 在 .env 中配置
echo 'MAOP_OTEL_ENABLED=1' >> .env
```

### 1.8 启用 n8n 集成（企业版）

```bash
# 在 .env 中配置 n8n 凭据
echo 'N8N_PASSWORD=<强密码>' >> .env
echo 'N8N_API_KEY=<32位随机密钥>' >> .env

# 启动
docker compose --profile n8n up -d
```

### 1.9 启用 Vault 密钥管理

```bash
# 在 .env 中配置
echo 'MAOP_SECRET_BACKEND=vault' >> .env
echo 'MAOP_VAULT_TOKEN=<token>' >> .env

# 启动
docker compose --profile vault up -d
```

---

## 2. Kubernetes 部署

MAOP 提供 Kubernetes Operator（基于 CRD + Helm Chart），位于 `deploy/k8s/operator/`。

### 2.1 Operator + Helm 部署

```bash
# 1. 添加 Helm 仓库（或使用本地 Chart）
cd deploy/k8s/operator

# 2. 安装 CRD 和 Operator
helm install maop-operator ./helm/ \
  --namespace maop-system \
  --create-namespace

# 3. 创建 MaopAgent 自定义资源
cat <<EOF | kubectl apply -f -
apiVersion: maop.io/v1alpha1
kind: MaopAgent
metadata:
  name: maop-dashboard
  namespace: default
spec:
  image: ghcr.io/maop/dashboard:5.1.0
  replicas: 1
  env:
    - name: MAOP_ENV
      value: production
    - name: MAOP_JWT_SECRET
      valueFrom:
        secretKeyRef:
          name: maop-secrets
          key: jwt-secret
  resources:
    requests:
      cpu: 500m
      memory: 512Mi
    limits:
      cpu: 2000m
      memory: 2Gi
EOF

# 4. 验证
kubectl get maopagent maop-dashboard -o wide
kubectl get pods -l app=maop-dashboard
```

### 2.2 健康检查配置

MAOP 提供两个端点供 K8s 探针使用：

```yaml
livenessProbe:
  httpGet:
    path: /api/health
    port: 9079
  initialDelaySeconds: 15
  periodSeconds: 30
  failureThreshold: 3

readinessProbe:
  httpGet:
    path: /api/health
    port: 9079
  initialDelaySeconds: 5
  periodSeconds: 10
  failureThreshold: 3
```

### 2.3 资源限制

```yaml
resources:
  limits:
    cpu: "2000m"
    memory: "2Gi"
  requests:
    cpu: "500m"
    memory: "512Mi"
```

**资源建议**（按规模）：

| 规模 | CPU 请求 | CPU 上限 | 内存请求 | 内存上限 | 副本数 |
|------|---------|---------|---------|---------|--------|
| 小型（<10 Agent） | 500m | 2000m | 512Mi | 2Gi | 1 |
| 中型（10–50 Agent） | 1000m | 4000m | 1Gi | 4Gi | 2 |
| 大型（50–200 Agent） | 2000m | 8000m | 2Gi | 8Gi | 3+ |

### 2.4 自定义 values 覆盖

```yaml
# my-values.yaml
controller:
  replicas: 2  # 高可用：>1 启用 leader election
  resources:
    requests:
      cpu: 200m
      memory: 256Mi
    limits:
      cpu: 1000m
      memory: 1Gi
  multiTenant:
    enabled: true
    defaultQuotas:
      maxTokensPerDay: 200000
      maxRequestsPerDay: 20000
      maxAgents: 50

metrics:
  enabled: true
  serviceMonitor:
    enabled: true  # 需安装 Prometheus Operator
    interval: 15s

webhook:
  enabled: true
  certManager:
    enabled: true  # 需安装 cert-manager
```

```bash
helm install maop-operator ./helm/ \
  -f my-values.yaml \
  --namespace maop-system
```

---

## 3. 裸机部署

### 3.1 Linux/macOS 直接运行

```bash
# 1. 克隆并安装
git clone https://github.com/Levango7/MAOP.git
cd MAOP

# 2. 创建虚拟环境
python3 -m venv .venv
source .venv/bin/activate

# 3. 安装依赖
pip install -e py/

# 4. 构建前端（如需 Dashboard UI）
cd dashboard-enterprise
npm install && npm run build
cd ..

# 5. 配置环境
cp .env.example .env
# 编辑 .env，至少设置 MAOP_JWT_SECRET 和 MAOP_ADMIN_PASSWORD

# 6. 使用启动脚本
./start.sh
# 或直接运行
python -m maop.dashboard.server --host 0.0.0.0 --port 9079
```

### 3.2 PM2 进程管理

MAOP 未内置 PM2 配置，以下为推荐的 PM2 配置方案：

**安装 PM2**：

```bash
npm install -g pm2
```

**创建 `ecosystem.config.cjs`**（项目根目录）：

```javascript
module.exports = {
  apps: [
    {
      name: 'maop-dashboard',
      script: 'python',
      args: '-m maop.dashboard.server --host 0.0.0.0 --port 9079',
      cwd: __dirname,
      env: {
        MAOP_ROOT: __dirname,
        MAOP_ENV: 'production',
        PYTHONPATH: `${__dirname}/py`,
        MAOP_DASH_HOST: '0.0.0.0',
        MAOP_DASH_PORT: '9079',
        MAOP_DASH_WORKERS: '1',
        // 以下须按实际情况设置
        MAOP_JWT_SECRET: process.env.MAOP_JWT_SECRET,
        MAOP_ADMIN_PASSWORD: process.env.MAOP_ADMIN_PASSWORD,
      },
      instances: 1,           // 单实例（SQLite 不支持多写入者）
      exec_mode: 'fork',
      autorestart: true,
      max_restarts: 10,
      restart_delay: 5000,
      max_memory_restart: '2G',
      error_file: './logs/maop-error.log',
      out_file: './logs/maop-out.log',
      log_date_format: 'YYYY-MM-DD HH:mm:ss',
      merge_logs: true,
      // 健康检查
      health_check: {
        http: 'http://localhost:9079/api/health',
        interval: 30000,
        timeout: 5000,
        max_restarts: 3,
      },
    },
    // 如需多实例（须使用 PostgreSQL 后端）
    // {
    //   name: 'maop-dashboard-2',
    //   ...同上,
    //   env: { ...同上, MAOP_DASH_PORT: '9080' },
    // },
  ],
};
```

**使用 PM2 管理服务**：

```bash
# 启动
pm2 start ecosystem.config.cjs

# 查看状态
pm2 status

# 查看日志
pm2 logs maop-dashboard

# 重启
pm2 restart maop-dashboard

# 停止
pm2 stop maop-dashboard

# 设置开机自启
pm2 save
pm2 startup  # 按提示执行输出的命令
```

> **注意**：SQLite 后端仅支持单实例。多实例部署须使用 PostgreSQL（`MAOP_DB_BACKEND=postgres`）。

### 3.3 systemd 服务

**创建 `/etc/systemd/system/maop.service`**：

```ini
[Unit]
Description=MAOP Multi-Agent Orchestration Platform
After=network.target postgresql.service

[Service]
Type=simple
User=maop
Group=maop
WorkingDirectory=/opt/maop
EnvironmentFile=/opt/maop/.env
Environment=PYTHONPATH=/opt/maop/py
ExecStart=/opt/maop/.venv/bin/python -m maop.dashboard.server --host 0.0.0.0 --port 9079
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

# 安全加固
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/opt/maop/data /opt/maop/logs

[Install]
WantedBy=multi-user.target
```

```bash
# 启用并启动
sudo systemctl daemon-reload
sudo systemctl enable maop
sudo systemctl start maop

# 查看状态
sudo systemctl status maop
journalctl -u maop -f
```

### 3.4 Windows 部署

```powershell
# 1. 克隆
git clone https://github.com/Levango7/MAOP.git
cd MAOP

# 2. 创建虚拟环境
python -m venv .venv
.venv\Scripts\activate

# 3. 安装
pip install -e py\

# 4. 构建前端
cd dashboard-enterprise
npm install
npm run build
cd ..

# 5. 配置
copy .env.example .env
# 编辑 .env

# 6. 启动（使用提供的 PowerShell 脚本）
.\maop.ps1
# 或直接运行
set PYTHONPATH=py
python -m maop.dashboard.server --host 0.0.0.0 --port 9079
```

---

## 4. TLS/HTTPS 配置

### 方式一：Nginx 终结 TLS（生产推荐）

```bash
# 1. 使用 Let's Encrypt 获取证书
sudo certbot certonly --standalone -d your-domain.com

# 2. 复制证书到项目目录
mkdir -p ./certs/nginx
sudo cp /etc/letsencrypt/live/your-domain.com/fullchain.pem ./certs/nginx/
sudo cp /etc/letsencrypt/live/your-domain.com/privkey.pem ./certs/nginx/
chown -R $USER:$USER ./certs/

# 3. 配置 .env
echo 'MAOP_TLS_CERT_DIR=./certs/nginx' >> .env
echo 'MAOP_TLS_CERT_FILENAME=fullchain.pem' >> .env
echo 'MAOP_TLS_KEY_FILENAME=privkey.pem' >> .env

# 4. 启动
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

### 方式二：uvicorn 直连 TLS（仅开发/测试）

```bash
# 生成自签名证书
openssl req -x509 -newkey rsa:4096 -keyout key.pem -out cert.pem \
  -days 365 -nodes -subj "/CN=localhost"

# 配置
export MAOP_TLS_ENABLED=1
export MAOP_TLS_CERT=./cert.pem
export MAOP_TLS_KEY=./key.pem

# 启动（单 worker 模式）
./start.sh
```

---

## 5. Nginx 反向代理

项目提供 `nginx.conf`（开发）和 `nginx.prod.conf`（生产）。

**关键配置项**（`nginx.prod.conf`）：

```nginx
server {
    listen 443 ssl http2;
    server_name your-domain.com;

    ssl_certificate     /etc/nginx/certs/fullchain.pem;
    ssl_certificate_key /etc/nginx/certs/privkey.pem;
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_ciphers         HIGH:!aNULL:!MD5;

    # 反代到 MAOP Dashboard
    location / {
        proxy_pass http://maop-dashboard:9079;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 86400s;  # SSE/WebSocket 长连接
    }
}
```

> 启用 Nginx 反代后，须设置 `MAOP_TRUST_PROXY=1` 以正确解析客户端 IP。

---

## 6. 数据库迁移

### SQLite（默认）

```bash
# 自动迁移（start.sh 已包含）
python -c "from maop.core.migrations import run_migrations; run_migrations('.')"

# 或使用 CLI
python -m maop.cli db migrate
```

### PostgreSQL

```bash
# 1. 设置数据库 URL
export MAOP_DB_URL="postgresql+psycopg2://maop:<password>@localhost:5432/maop"

# 2. 运行迁移
python -m maop.cli db migrate --backend postgres

# 3. SQLite → PostgreSQL 数据迁移（如需）
python -m maop.migrations.sqlite_to_pg
```

### Alembic（企业版）

```bash
# 使用 Alembic 迁移工具
cd py/
alembic upgrade head
```

---

## 7. 升级与回滚

### Docker Compose 升级

```bash
# 1. 拉取新版本
git pull origin main

# 2. 重建镜像
docker compose build

# 3. 滚动重启
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d

# 回滚（如需）
git checkout <previous-version-tag>
docker compose build
docker compose up -d
```

### Kubernetes 升级

```bash
# Helm 升级
helm upgrade maop-operator ./helm/ \
  -f my-values.yaml \
  --namespace maop-system

# 或更新 MaopAgent CR 的 image tag
kubectl patch maopagent maop-dashboard --type='json' \
  -p='[{"op":"replace","path":"/spec/image","value":"ghcr.io/maop/dashboard:5.2.0"}]'

# 回滚
helm rollback maop-operator 0  # 0 = 上一个版本
```

### 裸机升级

```bash
# 1. 备份数据
cp -r data/ data.backup.$(date +%Y%m%d)/

# 2. 拉取新版本
git pull origin main
pip install -e py/ --upgrade

# 3. 运行迁移
python -c "from maop.core.migrations import run_migrations; run_migrations('.')"

# 4. 重启服务
pm2 restart maop-dashboard  # 或 sudo systemctl restart maop
```

---

## 8. 验证部署

部署完成后，执行以下验证步骤：

```bash
# 1. 健康检查
curl -f http://localhost:9079/api/health
# 预期: {"status":"ok","version":"5.1.0",...}

# 2. 认证测试
TOKEN=$(curl -s -X POST http://localhost:9079/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"<your-password>"}' | jq -r '.data.token')

# 3. API 可用性
curl -s http://localhost:9079/api/agents \
  -H "Authorization: Bearer $TOKEN" | jq '.status'
# 预期: "ok"

# 4. WebSocket 连接（如使用）
wscat -c ws://localhost:9079/ws -H "Authorization: Bearer $TOKEN"

# 5. Prometheus 指标（如启用）
curl -s http://localhost:9079/api/metrics | head -5

# 6. 检查日志无错误
docker compose logs dashboard 2>&1 | grep -i error | head -10
# 或
journalctl -u maop --since "5 min ago" | grep -i error
```

---

> **故障排查**：如部署遇到问题，请查阅 [故障排查手册](./troubleshooting.md)。
>
> **容量规划**：生产环境资源需求请参考 [容量规划指南](./capacity-planning.md)。
>
> **运维手册**：日常运维操作请参考 [运维 Runbook](./runbook.md)。
