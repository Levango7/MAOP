# MAOP 容量规划

> 适用版本：v5.0.0+ ｜ 文档维护：MAOP 团队 ｜ 配套：[SLA](sla.md)、[性能基准](performance-benchmarks.md)

## 第 1 章 概述

本文档提供 MAOP 平台的容量规划指导，包括：

- 各组件的资源需求基准（CPU、内存、磁盘、网络）。
- 不同规模部署的推荐配置（小/中/大/超大）。
- 性能基准数据与 SLO 对齐。
- 扩缩容策略与瓶颈识别。

### 1.1 容量规划原则

1. **先测量再扩容**：基于实际监控数据（参见 [runbook.md](runbook.md) §8）决策，非凭感觉。
2. **预留 30% 余量**：峰值负载不超过容量的 70%，留出突发空间。
3. **垂直优先水平其次**：单实例扩到性能拐点后再加实例，避免过早复杂化。
4. **瓶颈导向**：先扩最紧缺的资源（CPU/内存/IO/网络），而非全部等比扩。

## 第 2 章 组件资源需求

### 2.1 MAOP Dashboard（应用层）

| 规模 | 并发用户 | API QPS | CPU | 内存 | 实例数 |
|------|----------|---------|-----|------|--------|
| 小 | ≤ 50 | ≤ 100 | 1 核 | 1 GB | 1 |
| 中 | ≤ 500 | ≤ 1000 | 2 核 | 2 GB | 2 |
| 大 | ≤ 2000 | ≤ 5000 | 4 核 | 4 GB | 3–5 |
| 超大 | ≤ 10000 | ≤ 20000 | 8 核 | 8 GB | 5–10 |

> **扩容信号**：API P95 延迟 > SLO 的 80%（控制面 > 160ms），CPU 利用率 > 70%。
>
> **扩容方式**：增加实例数（水平扩容），配合负载均衡。单实例超过 4 核后边际效益递减。

### 2.2 PostgreSQL（数据层）

#### 单机模式（无 HA）

| 规模 | 数据量 | 编排 QPS | CPU | 内存 | 磁盘 | IOPS |
|------|--------|----------|-----|------|------|-------|
| 小 | ≤ 10 GB | ≤ 50 | 2 核 | 2 GB | 50 GB SSD | 1000 |
| 中 | ≤ 100 GB | ≤ 200 | 4 核 | 8 GB | 200 GB SSD | 5000 |
| 大 | ≤ 500 GB | ≤ 1000 | 8 核 | 16 GB | 1 TB NVMe | 20000 |

#### Patroni 集群模式（HA，参见 [deploy/patroni/](../deploy/patroni/)）

| 规模 | 数据量 | 编排 QPS | 节点数 | 单节点 CPU | 单节点内存 | 磁盘 |
|------|--------|----------|--------|-----------|-----------|------|
| 中 | ≤ 100 GB | ≤ 200 | 3 | 4 核 | 8 GB | 200 GB SSD |
| 大 | ≤ 500 GB | ≤ 1000 | 3 | 8 核 | 16 GB | 1 TB NVMe |
| 超大 | ≤ 2 TB | ≤ 5000 | 3–5 | 16 核 | 32 GB | 2 TB NVMe |

> **关键参数**：
> - `shared_buffers`：内存的 25%（如 16GB 内存 → 4GB shared_buffers）。
> - `max_connections`：实例数 × 每实例连接池大小 + 50 余量。
> - `wal_keep_size`：≥ 1GB，确保 replica 追赶窗口。
>
> **扩容信号**：PG CPU > 70%，连接数 > 80% max_connections，复制 lag > 100MB。

### 2.3 Redis（缓存/队列层）

| 规模 | 缓存大小 | 队列 QPS | CPU | 内存 | 持久化 |
|------|----------|----------|-----|------|--------|
| 小 | ≤ 1 GB | ≤ 500 | 1 核 | 2 GB | AOF |
| 中 | ≤ 5 GB | ≤ 2000 | 2 核 | 8 GB | AOF |
| 大 | ≤ 20 GB | ≤ 10000 | 4 核 | 32 GB | AOF + RDB |

> **扩容信号**：内存使用 > 80% maxmemory，淘汰率 > 1%，命令 P95 > 10ms。
>
> **HA 建议**：中/大规模使用 Redis Sentinel（3 节点）或 Cluster（分片）。

### 2.4 Vector Store（向量检索层）

| 规模 | 向量数 | 维度 | 索引类型 | 内存 | 磁盘 | 检索 P95 |
|------|--------|------|----------|------|------|----------|
| 小 | ≤ 100K | 768 | sqlite-vec | 1 GB | 5 GB | < 50ms |
| 中 | ≤ 1M | 768 | HNSW | 4 GB | 20 GB | < 100ms |
| 大 | ≤ 10M | 768 | HNSW + pgvector | 16 GB | 100 GB | < 150ms |
| 超大 | ≤ 100M | 768 | pgvector + IVFFlat | 64 GB | 500 GB | < 200ms |

> **扩容信号**：检索 P95 > SLO 的 80%（> 120ms），召回率下降。
>
> **索引选择**：
> - sqlite-vec：≤ 100K 向量，零配置。
> - HNSW（hnswlib）：100K–10M 向量，内存索引，低延迟。
> - pgvector：> 1M 向量，磁盘索引，与 PG 集成。

### 2.5 LLM 调用（外部依赖）

| 规模 | 编排 QPS | 平均 Token/请求 | 并发连接 | 超时 | 熔断阈值 |
|------|----------|----------------|----------|------|----------|
| 小 | ≤ 10 | 2000 | 10 | 30s | 5 次失败 |
| 中 | ≤ 100 | 2000 | 100 | 60s | 10 次失败 |
| 大 | ≤ 500 | 2000 | 500 | 60s | 20 次失败 |

> **关键考虑**：
> - LLM 调用是**外部依赖**，延迟与可用性不受 MAOP 控制。
> - 必须配置熔断器（`MAOP_CB_FAILURE_THRESHOLD`）与多提供商 fallback。
> - Token 消耗是主要成本，需配置预算守卫（`MAOP_BUDGET_*`）。

## 第 3 章 性能基准

### 3.1 基准环境

- **硬件**：8 vCPU / 16 GB RAM / 200 GB NVMe SSD
- **软件**：MAOP v5.0.0 + PG 16 + Redis 7 + Python 3.13
- **负载**：k6 100 VUs / 5 分钟（参见 [tests/performance/k6_maop_load.js](../py/tests/performance/k6_maop_load.js)）

### 3.2 基准结果

| 端点 | QPS | P50 | P95 | P99 | 错误率 |
|------|-----|-----|-----|-----|--------|
| GET /api/agents | 850 | 35ms | 95ms | 180ms | 0.0% |
| GET /api/models | 920 | 28ms | 75ms | 150ms | 0.0% |
| POST /api/execute | 180 | 120ms | 450ms | 1200ms | 0.1% |
| POST /api/search | 650 | 22ms | 65ms | 130ms | 0.0% |
| GET /api/health | 980 | 5ms | 15ms | 30ms | 0.0% |

> **SLO 对齐**：全部 P95 满足 [SLA](sla.md) §2.2 目标。

### 3.3 瓶颈识别

```
QPS ↑
     │
     │  ─────────────  DB 瓶颈 (连接池耗尽)
     │ /
     │/    ─────────  CPU 瓶颈 (单实例饱和)
     │    /
     │   /  ───────  LLM 瓶颈 (外部 API 限流)
     │  /  /
     │ /  /
     │/  /
     └──────────────────→ 并发用户
```

1. **LLM 瓶颈**（最先触发）：外部 LLM API 限流，触发熔断 + fallback。
2. **CPU 瓶颈**：单实例 CPU 饱和，水平扩容增加 Dashboard 实例。
3. **DB 瓶颈**：PG 连接池耗尽，引入 PgBouncer 或增加 max_connections。

## 第 4 章 扩缩容策略

### 4.1 自动扩缩容（K8s）

```yaml
# deploy/k8s/operator/templates/deployment.yaml 中的 HPA 配置
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: maop-dashboard-hpa
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: maop-dashboard
  minReplicas: 2
  maxReplicas: 10
  metrics:
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          averageUtilization: 70
    - type: Resource
      resource:
        name: memory
        target:
          type: Utilization
          averageUtilization: 80
```

### 4.2 手动扩缩容（docker-compose）

```bash
# 增加 Dashboard 实例（需配合负载均衡）
docker compose up -d --scale dashboard=3

# PG 扩容：垂直扩（增加 CPU/内存）
# 修改 docker-compose.prod.yml 中 deploy.resources.limits
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d

# Redis 扩容：迁移到 Sentinel/Cluster
# 参见 docs/runbook.md §3
```

### 4.3 扩缩容检查清单

扩容后验证：

- [ ] API P95 延迟满足 SLO（参见 [SLA](sla.md) §2.2）。
- [ ] 错误率 < 0.1%。
- [ ] PG 复制 lag < 10MB（HA 模式）。
- [ ] Redis 内存使用 < 80% maxmemory。
- [ ] 熔断器未触发（LLM 调用正常）。
- [ ] 审计日志正常记录（无丢失）。

## 第 5 章 成本估算

### 5.1 自托管成本（月度）

| 规模 | 云实例 | 数量 | 单价 (USD) | 月度合计 |
|------|--------|------|-----------|----------|
| 小 | 2C4G | 3 (app+pg+redis) | $30 | $90 |
| 中 | 4C8G | 5 (2app+pg+redis+lb) | $80 | $400 |
| 大 | 8C16G | 7 (3app+3pg+redis) | $160 | $1120 |
| 超大 | 16C32G | 12 (5app+5pg+2redis) | $320 | $3840 |

> 不含 LLM API 调用费用（按用量计费，与 MAOP 部署成本独立）。

### 5.2 LLM 调用成本

| 模型 | 输入价格 | 输出价格 | 估算月度（1000 编排/天） |
|------|----------|----------|--------------------------|
| gpt-4o | $2.5/1M | $10/1M | ~$300 |
| gpt-4o-mini | $0.15/1M | $0.6/1M | ~$20 |
| claude-3.5-sonnet | $3/1M | $15/1M | ~$450 |

> **成本控制**：
> - 配置预算守卫（`MAOP_BUDGET_DAILY_LIMIT`）。
> - 简单任务用 mini/haiku 模型，复杂任务用 pro 模型。
> - 启用语义缓存（`MAOP_SEMANTIC_CACHE=1`）减少重复调用。

## 第 6 章 监控与告警

### 6.1 关键容量指标

| 指标 | PromQL | 告警阈值 |
|------|--------|----------|
| API QPS | `rate(maop_requests_total[5m])` | — |
| API P95 延迟 | `histogram_quantile(0.95, maop_request_duration_seconds_bucket)` | > SLO 80% |
| PG 连接数 | `pg_stat_database_numbackends` | > 80% max_connections |
| PG 复制 lag | `pg_replication_lag_bytes` | > 100MB |
| Redis 内存 | `redis_memory_used / redis_memory_max` | > 80% |
| Redis 淘汰率 | `rate(redis_evicted_keys_total[5m])` | > 1/s |
| LLM 熔断次数 | `rate(maop_circuit_breaker_open_total[5m])` | > 0 |
| 磁盘使用 | `1 - disk_free / disk_total` | > 80% |

### 6.2 容量预测

基于历史趋势预测资源耗尽时间：

```promql
# 预测 PG 磁盘 7 天后使用量
predict_linear(pg_database_size_bytes[7d], 7 * 24 * 3600)
```

当预测值 > 磁盘容量 × 80% 时触发容量告警，提前扩容。

---

> 本文档以简体中文为准。配合 [SLA](sla.md)、[runbook.md](runbook.md)、[性能基准](performance-benchmarks.md) 使用。