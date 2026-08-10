# MAOP 运维手册（Runbook）

> 适用版本：v5.0.0+ ｜ 文档维护：MAOP 团队 ｜ 适用：企业版生产部署

## 第 1 章 概述

本手册覆盖 MAOP 生产环境常见运维场景与故障处理流程。所有操作假设已按 [docker-compose.prod.yml](../docker-compose.prod.yml) 部署，并启用 Patroni PG 高可用（`--profile patroni`）。

### 1.1 关键组件

| 组件 | 作用 | 健康检查 |
|------|------|----------|
| Patroni 集群（patroni1/2/3） | PG 高可用 | `curl http://patroni1:8008/health` |
| HAProxy（pg-haproxy） | PG 读写分离 | `curl http://localhost:7000` |
| etcd | Patroni DCS | `etcdctl endpoint health` |
| Redis | 缓存/队列/HA | `redis-cli ping` |
| Vault | 密钥管理 | `vault status` |
| MAOP Dashboard | 应用服务 | `curl http://localhost:9079/api/health` |
| Nginx | 反向代理 | `curl http://localhost/` |

### 1.2 告警分级

| 级别 | 颜色 | 响应 | 示例 |
|------|------|------|------|
| Critical | 红 | 立即（≤ 15 分钟） | PG primary 不可用、Vault sealed |
| Warning | 黄 | 工作时间（≤ 4 小时） | replica lag > 100MB、磁盘 > 80% |
| Info | 蓝 | 通知即可 | 配置重载、部署完成 |

## 第 2 章 PostgreSQL 故障切换

### 2.1 自动故障切换（primary 宕判）

**触发条件**：Patroni 检测到 primary 不可达（默认 30s TTL）。

**自动流程**：

1. etcd 中的 leader key 过期（30s）。
2. replica 节点发起选举。
3. 选出 lag 最小的 replica 提升为新 primary。
4. HAProxy 健康检查更新，写流量路由到新 primary。
5. Patroni 回调 `on_role_change.sh` 通知 MAOP 应用层刷新连接池。
6. 旧 primary 恢复后以 replica 身份重新加入（pg_rewind 处理脑裂）。

**RTO/RPO**：

- RTO（恢复时间目标）：≤ 30 秒
- RPO（恢复点目标）：0（同步复制模式）

**验证**：

```bash
# 1. 查看集群状态
docker compose exec patroni1 patronictl list

# 2. 确认新 primary
curl -sf http://pg-haproxy:8008/read-write | jq .

# 3. 确认 MAOP 应用已刷新连接
docker compose logs dashboard --since 1m | grep "pg:role-change"

# 4. 写入测试
docker compose exec pg-haproxy psql -h localhost -p 5432 -U maop -d maop \
  -c "INSERT INTO maop_health_check(ts) VALUES (now())"
```

### 2.2 手动 switchover（计划内切换）

**适用场景**：PG 主版本升级、硬件维护、性能调优。

**操作步骤**：

```bash
# 1. 确认集群健康
docker compose exec patroni1 patronictl list
# 全部节点 State=running，replica lag=0

# 2. 执行 switchover（patroni1 → patroni2）
docker compose exec patroni1 patronictl switchover \
  --master patroni1 --candidate patroni2

# 3. 等待切换完成（≤ 10s）
sleep 10

# 4. 验证新 primary
docker compose exec patroni2 patronictl list
# patroni2 应为 Leader

# 5. 验证 MAOP 应用连接正常
curl -sf http://localhost:9079/api/health
```

**回滚**：

```bash
# 切回 patroni1
docker compose exec patroni2 patronictl switchover \
  --master patroni2 --candidate patroni1
```

### 2.3 脑裂处理

**症状**：两个节点同时声称自己是 primary。

**诊断**：

```bash
# 检查 etcd 是否正常
docker compose exec etcd etcdctl endpoint health

# 检查各节点角色
for i in 1 2 3; do
  echo "=== patroni$i ==="
  curl -sf http://patroni$i:8008/cluster | jq '.members[] | {name, role, state}'
done
```

**处理**：

1. 确认 etcd 健康（脑裂通常因 etcd 分区引起）。
2. 确定数据较新的节点（比较 timeline + LSN）：
   ```bash
   docker compose exec patroni1 psql -U maop -d maop -c "SELECT pg_current_wal_lsn(), pg_control_checkpoint()"
   ```
3. 将数据较旧的节点降级为 replica：
   ```bash
   docker compose exec patroni2 patronictl reinit maop-cluster patroni2
   ```
4. 若 etcd 损坏，需重建 etcd 集群并让 Patroni 重新选举。

## 第 3 章 Redis 故障

### 3.1 Redis 主节点故障

**症状**：MAOP 缓存命中率下降，队列积压。

**处理**（单节点模式）：

```bash
# 1. 检查 Redis 状态
docker compose exec redis redis-cli ping
# 若无响应，重启
docker compose restart redis

# 2. 确认数据完整性（AOF）
docker compose exec redis redis-cli info persistence | grep aof
# aof_enabled=1, aof_rewrite_in_progress=0

# 3. 确认 MAOP 应用恢复
curl -sf http://localhost:9079/api/health | jq .cache
```

**建议**：生产环境使用 Redis Sentinel 或 Cluster 模式实现 HA。

### 3.2 Redis 内存溢出

**症状**：`OOM command not allowed` 错误。

**处理**：

```bash
# 1. 查看内存使用
docker compose exec redis redis-cli info memory | grep used_memory_human

# 2. 查看淘汰策略
docker compose exec redis redis-cli config get maxmemory-policy
# 应为 allkeys-lru

# 3. 临时清理（谨慎）
docker compose exec redis redis-cli flushdb  # 仅清当前 DB
# 或调整 maxmemory
docker compose exec redis redis-cli config set maxmemory 2gb
```

## 第 4 章 Vault 故障

### 4.1 Vault sealed

**症状**：MAOP 启动失败，错误 `Vault is sealed`。

**处理**：

```bash
# 1. 检查 Vault 状态
docker compose exec vault vault status
# Sealed=true

# 2. 解封（需要 quorum 个 unseal key）
docker compose exec vault vault operator unseal <unseal-key-1>
docker compose exec vault vault operator unseal <unseal-key-2>
docker compose exec vault vault operator unseal <unseal-key-3>

# 3. 确认解封
docker compose exec vault vault status | grep Sealed
# Sealed=false

# 4. 重启 MAOP 应用
docker compose restart dashboard
```

### 4.2 Vault 数据损坏

**处理**：

1. 从备份恢复 Vault 数据：
   ```bash
   docker compose stop vault
   # 恢复 vault-data 卷
   docker run --rm -v vault-data:/data -v /backups:/backup alpine \
     tar xzf /backup/vault-data.tar.gz -C /data
   docker compose start vault
   ```
2. 重新解封。
3. 验证 MAOP 可正常读取密钥。

## 第 5 章 MAOP 应用故障

### 5.1 Dashboard 无法启动

**诊断**：

```bash
# 1. 查看启动日志
docker compose logs dashboard --tail 100

# 2. 常见原因
#    - PG 不可达：检查 patroni 集群
#    - Redis 不可达：检查 redis 服务
#    - Vault sealed：参见 §4.1
#    - License 过期：检查 MAOP_LICENSE_KEY
#    - 端口冲突：检查 9079 端口占用
```

**处理**：

```bash
# 重启 Dashboard
docker compose restart dashboard

# 若仍失败，检查依赖
docker compose exec dashboard python -c "
from maop.config import load_config
from maop.core.shared_db import SharedDB
cfg = load_config()
db = SharedDB(cfg)
print('DB OK:', db.health_check())
"
```

### 5.2 API 响应缓慢

**诊断**：

```bash
# 1. 查看延迟指标
curl -sf http://localhost:9079/api/metrics | grep maop_request_duration

# 2. 检查 PG 慢查询
docker compose exec pg-haproxy psql -h localhost -p 5432 -U maop -d maop \
  -c "SELECT * FROM pg_stat_activity WHERE state = 'active' AND now() - query_start > '10s'"

# 3. 检查连接池
docker compose exec pg-haproxy psql -h localhost -p 5432 -U maop -d maop \
  -c "SELECT count(*), state FROM pg_stat_activity GROUP BY state"
```

**处理**：

- PG 慢查询：`EXPLAIN ANALYZE` + 添加索引。
- 连接池耗尽：调整 `max_connections` 或引入 PgBouncer。
- LLM 提供商延迟：检查熔断器状态，必要时切换提供商。

## 第 6 章 备份与恢复

### 6.1 PG 备份

**自动备份**（WAL-G）：

- 全量备份：每日 02:00（cron 触发 `wal-g backup-push`）。
- WAL 归档：持续（`archive_command` 自动调用 `wal-g wal-push`）。
- 保留期：全量 30 天，WAL 90 天。

**手动备份**：

```bash
docker compose exec patroni1 wal-g backup-push /home/postgres/pgdata/pgroot/data --permanent
```

### 6.2 PG 恢复（PITR）

**恢复到指定时间点**：

```bash
# 1. 停止 MAOP 应用
docker compose stop dashboard

# 2. 停止 Patroni 集群
docker compose stop patroni1 patroni2 patroni3

# 3. 在 patroni1 上恢复
docker compose run --rm patroni1 bash -c '
  rm -rf /home/postgres/pgdata/pgroot/data/*
  wal-g backup-fetch /home/postgres/pgdata/pgroot/data LATEST
  cat > /home/postgres/pgdata/pgroot/data/postgresql.auto.conf <<EOF
restore_command = "wal-g wal-fetch %f %p"
recovery_target_time = "2026-08-11 14:30:00+00"
recovery_target_action = "promote"
EOF
  touch /home/postgres/pgdata/pgroot/data/recovery.signal
'

# 4. 重启集群
docker compose start patroni1
# patroni1 恢复到目标时间点后成为新 primary
docker compose start patroni2 patroni3
# patroni2/3 从 patroni1 重建

# 5. 验证数据
docker compose exec pg-haproxy psql -h localhost -p 5432 -U maop -d maop \
  -c "SELECT max(created_at) FROM maop_executions;"

# 6. 重启 MAOP 应用
docker compose start dashboard
```

### 6.3 Redis 备份

```bash
# RDB 快照
docker compose exec redis redis-cli bgsave

# AOF 重写
docker compose exec redis redis-cli bgrewriteaof

# 拷贝 RDB 文件
docker cp maop-redis:/data/dump.rdb ./backups/redis/dump-$(date +%Y%m%d).rdb
```

## 第 7 章 升级

### 7.1 MAOP 应用升级

```bash
# 1. 备份当前版本数据（参见 §6）
# 2. 拉取新版本镜像
docker compose pull dashboard

# 3. 滚动重启
docker compose up -d dashboard

# 4. 验证
curl -sf http://localhost:9079/api/info | jq .version
# 应为新版本号

# 5. 运行冒烟测试
curl -sf http://localhost:9079/api/health | grep -q '"status"'
```

### 7.2 PG 主版本升级

**前置**：先在 staging 环境验证。

```bash
# 1. 全量备份
docker compose exec patroni1 wal-g backup-push /home/postgres/pgdata/pgroot/data --permanent

# 2. 逐节点滚动升级（使用 patronictl switchover 切换 primary）
for node in patroni2 patroni3 patroni1; do
  # 切走 primary
  docker compose exec patroni1 patronictl switchover --candidate $node
  sleep 10
  # 升级非 primary 节点
  docker compose stop $node
  # 替换镜像版本...
  docker compose start $node
  sleep 30
done

# 3. 验证集群
docker compose exec patroni1 patronictl list
```

## 第 8 章 监控告警

### 8.1 关键告警规则

| 告警 | 条件 | 级别 | 处理 |
|------|------|------|------|
| PGPrimaryDown | 无 primary 节点 | Critical | §2.1 |
| PGReplicaLagHigh | lag > 100MB 持续 5 分钟 | Warning | 检查网络、负载 |
| PGConnectionsHigh | 连接数 > 90% max | Warning | 引入 PgBouncer |
| RedisDown | ping 失败 | Critical | §3.1 |
| VaultSealed | sealed=true | Critical | §4.1 |
| DashboardDown | /api/health 5xx | Critical | §5.1 |
| DiskSpaceLow | 磁盘 > 90% | Warning | 清理日志/扩容 |
| CertExpiring | 证书 < 30 天到期 | Warning | 续期 |

### 8.2 告警通知

配置 Alertmanager（`alertmanager.yml`）：

```yaml
route:
  receiver: oncall
  routes:
    - matchers: ['severity="critical"']
      receiver: oncall-urgent
      group_wait: 0s
    - matchers: ['severity="warning"']
      receiver: oncall-normal
      group_wait: 5m
```

## 第 9 章 联系方式

| 场景 | 联系方式 |
|------|----------|
| P0 紧急 | `urgent@maop.io` + 电话 |
| P1 高 | `support@maop.io` |
| 安全事件 | `security@maop.io`（参见 [SECURITY.md](../SECURITY.md)） |
| 运维咨询 | 参见 [支持政策](support-policy.md) |

---

> 本手册以简体中文为准。配合 [SLA](sla.md)、[支持政策](support-policy.md) 使用。