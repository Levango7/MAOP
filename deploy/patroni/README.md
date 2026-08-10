# Patroni 集群部署指南

> 适用版本：v5.0.0+ ｜ 配置文件：`deploy/patroni/`

## 概述

MAOP 使用 Patroni 提供 PostgreSQL 高可用，实现：

- **自动故障切换**：primary 故障时自动选举新 primary，RTO ≤ 30s，RPO = 0（同步复制）。
- **读写分离**：HAProxy 在前端路由，写流量打 primary，读流量分散到 replica。
- **数据零丢失**：同步复制模式（`synchronous_mode: true`）。
- **自动修复**：pg_rewind 处理脑裂后旧 primary 重新加入集群。
- **PITR 备份**：WAL-G 周期性全量 + WAL 增量备份至 S3。

## 架构

```
                   ┌────────────────────────────────────────────────┐
                   │              HAProxy (读写分离)                │
                   │   :5432 → primary   :5433 → primary+replica  │
                   └────────────────────────────────────────────────┘
                                      │
              ┌───────────────────────┼───────────────────────┐
              ▼                       ▼                       ▼
        ┌──────────┐            ┌──────────┐            ┌──────────┐
        │ patroni1 │◄─stream────│ patroni2 │◄─stream────│ patroni3 │
        │ (primary)│            │ (replica)│            │ (replica)│
        └──────────┘            └──────────┘            └──────────┘
              │                       │                       │
              └───────────────────────┼───────────────────────┘
                                      ▼
                              ┌──────────────────┐
                              │   etcd (DCS)     │
                              │  leader election │
                              └──────────────────┘
                                      │
                                      ▼
                              ┌──────────────────┐
                              │   WAL-G → S3     │
                              │   PITR 备份      │
                              └──────────────────┘
```

## 部署

### 1. 准备环境变量

在 `.env` 中配置：

```bash
# PG 主密码（与单机模式相同的 MAOP_PG_PASSWORD）
MAOP_PG_PASSWORD=<strong-password>

# Patroni 复制账号密码
PATRONI_REPLICATION_PASSWORD=<strong-password>

# Patroni rewind 账号密码（pg_rewind 用）
PATRONI_REWIND_PASSWORD=<strong-password>

# Patroni REST API 密码
PATRONI_API_PASSWORD=<strong-password>

# Redis（用于通知应用层连接池刷新）
MAOP_REDIS_PASSWORD=<strong-password>

# WAL-G S3 备份（可选）
WALE_S3_PREFIX=s3://maop-pg-backup/cluster1
AWS_ACCESS_KEY_ID=<key>
AWS_SECRET_ACCESS_KEY=<secret>
AWS_REGION=us-east-1
```

### 2. 启动集群

```bash
# 启用 patroni profile 启动 3 节点集群 + HAProxy + etcd
docker compose -f docker-compose.yml -f docker-compose.prod.yml \
  --profile patroni up -d
```

### 3. 验证集群状态

```bash
# 查看集群拓扑
docker compose exec patroni1 patronictl list

# 期望输出：
# + Cluster: maop-cluster (1234567890) ----+----+-----------+
# | Member   | Host       | Role    | State    | TL | Lag in MB |
# +----------+------------+---------+----------+----+-----------+
# | patroni1 | 10.0.0.1   | Leader  | running  |  1 |           |
# | patroni2 | 10.0.0.2   | Replica | streaming|  1 |         0 |
# | patroni3 | 10.0.0.3   | Replica | streaming|  1 |         0 |
# +----------+------------+---------+----------+----+-----------+

# HAProxy 状态面板
open http://localhost:7000  # admin/admin（生产环境修改）
```

### 4. 配置 MAOP 应用

将 MAOP 的 PG 连接指向 HAProxy：

```bash
MAOP_PG_HOST=haproxy
MAOP_PG_PORT=5432          # 写流量
MAOP_PG_READ_HOST=haproxy
MAOP_PG_READ_PORT=5433     # 读流量（可选，未配置则用主连接）
```

## 运维操作

### 手动 switchover（计划内切换）

```bash
# 将 primary 从 patroni1 切到 patroni2
docker compose exec patroni1 patronictl switchover \
  --master patroni1 --candidate patroni2
```

### 故障模拟（测试 failover）

```bash
# 杀掉 primary 容器
docker compose kill patroni1

# 等待 10-30s，观察集群自动选举新 primary
docker compose exec patroni2 patronictl list

# 重新启动 patroni1，它会以 replica 身份加入
docker compose start patroni1
```

### 备份与恢复

```bash
# 创建全量备份
docker compose exec patroni1 wal-g backup-push /var/lib/postgresql/data --permanent

# 查看备份列表
docker compose exec patroni1 wal-g backup-list

# PITR 恢复到指定时间点
# STOPPED_TIME 为目标时间，需停集群后操作
docker compose stop patroni1 patroni2 patroni3
docker compose run --rm patroni1 bash -c '
  wal-g backup-fetch /var/lib/postgresql/data LATEST
  # 创建 recovery.signal
  echo "restore_command = \"wal-g wal-fetch %f %p\"" > /var/lib/postgresql/data/recovery.signal
  echo "recovery_target_time = \"2026-08-11 14:30:00+00\"" >> /var/lib/postgresql/data/postgresql.auto.conf
'
docker compose start patroni1 patroni2 patroni3
```

详细故障切换流程参见 [docs/runbook.md](../../docs/runbook.md)。