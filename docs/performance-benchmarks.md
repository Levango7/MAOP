# MAOP Performance Benchmarks

本文档提供 MAOP（Multi-Agent Orchestration Platform）v4.3.0 的性能基准数据、核心子系统默认参数、API 延迟分级、资源使用基线以及调优指南。所有数据均来源于代码默认值（标注 `文件:行号`）、单元测试日志（`py/final_regression.log`）与压力测试断言（`py/tests/test_stress.py`），无真实压测数据处标注"预估"。文档目的：为性能敏感场景的部署容量规划、SLO 设定与回归检测提供可追溯的参考基线。

---

## 1. Overview

### 1.1 Test Environment

| Item | Value | Source |
|------|-------|--------|
| MAOP Version | 4.3.0 | 项目根目录版本声明 |
| Python | 3.11+ | `py/requirements.txt` |
| Framework | FastAPI + uvicorn | `docker-compose.yml` |
| OS (reference) | Linux x86_64 / Windows | 开发与 CI 环境 |
| Hardware (reference) | 4 vCPU / 8GB RAM | CI runner 常规规格 |

### 1.2 Data Sources

- **Unit Test Suite**: `py/final_regression.log`（回归基线日志）
- **Stress Test**: `py/tests/test_stress.py`（并发与吞吐断言）
- **Code Defaults**: `py/maop/core/*.py`、`py/maop/loop_models.py`
- **Monitoring Config**: `monitoring/prometheus.yml`、`monitoring/slo-alerts.yml`
- **Resource Limits**: `docker-compose.yml`

### 1.3 Test Methods

```bash
# 单元测试套件（含最慢 10 项）
cd py
pytest --durations=10 -v

# 压力测试套件
pytest tests/test_stress.py -v

# Prometheus 指标采样
curl http://127.0.0.1:9079/api/prometheus
```

性能基线版本：**v4.3.0**。所有后续回归检测以此为对比锚点。

---

## 2. Test Suite Performance

### 2.1 Regression Baseline

基于 `py/final_regression.log:71` 实测数据：

| Metric | Value |
|--------|-------|
| Total tests | 3993 (3989 passed + 4 skipped) |
| Passed | 3989 |
| Skipped | 4 |
| Failed | 0 |
| Warnings | 3 |
| Total duration | 256.09s (0:04:16) |
| Avg per test | ~64.2ms |

```text
============================== warnings summary ===============================
...
3989 passed, 4 skipped, 3 warnings in 256.09s (0:04:16)
```

### 2.2 Reproduction

```bash
cd py
pytest --durations=10 -v 2>&1 | tee final_regression.log
```

`--durations=10` 会在日志末尾输出最慢的 10 个测试用例，用于回归趋势对比。当前基线总耗时 **256.09s / 3989 用例**，作为 §11 性能回归检测的对比锚点。

---

## 3. Core Subsystem Benchmarks

以下为各核心子系统的默认配置参数，均来自代码默认值，部署时可通过环境变量或配置文件覆盖。

| Subsystem | Parameter | Default | Source | Notes |
|-----------|-----------|---------|--------|-------|
| WorkerPool | max_workers | 4 | core/worker_pool.py:93 | IO 并发 worker 数（asyncio.Semaphore 控制） |
| WorkerPool | max_cpu_workers | 0 (= auto) | core/worker_pool.py:94 | 自动计算 `max(1, min(2, cpu-1))`，见 :100 |
| LRUCache | max_size | 256 | core/cache.py:125 | 默认缓存条目上限 |
| LRUCache | default_ttl_s | 0.0 | core/cache.py:126 | 0 = 永不过期 |
| LRUCache | ttl_jitter | 0.1 | core/cache.py:127 | ±10% 抖动防雪崩 |
| get_cache("config") | max_size | 50 | core/cache.py:482 | 配置缓存，TTL 300s |
| get_cache("memory") | max_size | 1000 | core/cache.py:483 | 记忆缓存，TTL 60s |
| EventBus | max_history | 200 | core/event_bus.py:24 | 事件历史保留数 |
| EventBus | max_dead_letters | 1000 | core/event_bus.py:25 | 死信队列上限 |
| SingleFlight | wait_timeout | 30.0s | core/cache.py:46 | 单飞等待超时 |
| CircuitBreaker | failure_threshold | 3 | core/circuit_breaker.py:53 | 连续失败熔断阈值 |
| CircuitBreaker | cooldown_s | 60 | core/circuit_breaker.py:55 | OPEN→HALF_OPEN 冷却 |
| CircuitBreaker | sqlite_timeout | 10s | core/circuit_breaker.py:228 | SQLite 锁等待 |
| TokenBucket | rate | 10.0 req/s | core/rate_limiter.py:51 | 稳态令牌速率 |
| TokenBucket | burst | 20 | core/rate_limiter.py:51 | 突发上限 |
| SlidingWindow | max_requests | 600 | core/rate_limiter.py:112 | 窗口内最大请求数 |
| SlidingWindow | window_s | 60.0s | core/rate_limiter.py:112 | 滑动窗口大小 |
| Histogram | DEFAULT_BUCKETS | (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, +Inf) | core/monitoring.py:365 | Prometheus 桶边界（秒） |
| LoopConfig | enable_parallel | True | loop_models.py:90 | 启用并行执行 |
| LoopConfig | max_workers | 4 | loop_models.py:91 | 循环内 worker 数 |
| LoopConfig | default_timeout_s | 120 | loop_models.py:67 | 单步默认超时 |
| LoopConfig | max_retries | 1 | loop_models.py:65 | 重试次数 |
| LoopConfig | iterative_max_attempts | 3 | loop_models.py:68 | 迭代最大尝试 |

> 说明：`WorkerPool` 通过 `asyncio.Semaphore(max_workers)` 控制并发（`core/worker_pool.py:105`），未设置显式 `queue_max_size`；任务积压受事件循环调度与下游反压约束。生产环境如需有界队列，应在调用方实现背压。

---

## 4. API Endpoint Latency

基于代码路径分析与压力测试断言的延迟分级。无真实压测数据处标注"预估"。

| Latency Tier | Endpoints | Expected p50 | Expected p99 | Source / Notes |
|--------------|-----------|--------------|--------------|----------------|
| Ultra-fast (<10ms) | `/api/health`, `/api/v1/version`, `/api/info/*` | <5ms | <10ms | 纯内存；test_stress.py:553 断言 avg < 100ms |
| Fast (10-50ms) | `/api/agents`, `/api/metrics`, `/api/system/resources` | <20ms | <50ms | SQLite 读查询（预估） |
| Medium (50-200ms) | `/api/control/run`（无 LLM）, `/api/evolve/analyze` | <100ms | <200ms | 含 DB 写（预估） |
| Slow (200ms-1s) | `/api/auth/login`, `/api/agent/upgrade` | <500ms | <1s | PBKDF2 600k 迭代 / subprocess（预估） |
| Async (>1s) | `/api/control/run`（含 LLM）, `/api/chat`, `/api/stream` | 不定 | 不定 | 取决于 LLM provider |

### 4.1 Stress Test Assertions

来自 `py/tests/test_stress.py` 的硬性断言阈值：

```python
# /api/health 压测：100 次请求平均延迟（test_stress.py:553）
assert avg_latency < 100, f"Average latency {avg_latency:.1f}ms exceeds 100ms"

# 向量库批量写入：1000 条（test_stress.py:594）
assert write_ms < 120_000, f"Bulk write too slow: {write_ms:.0f}ms"

# 向量库查询（test_stress.py:604）
assert query_ms < 5_000, f"Query too slow: {query_ms:.0f}ms"

# 内存泄漏检测（test_stress.py:645）
assert growth < 500_000, f"Possible memory leak: block count grew by {growth:,}"
```

---

## 5. Concurrency & Throughput

### 5.1 Concurrency Limits

| Layer | Limit | Source |
|-------|-------|--------|
| 单进程并发请求 | 受 uvicorn workers 约束 | `MAOP_WORKERS` 环境变量 |
| WebSocket 连接 | 无显式上限 | 受内存约束（预估） |
| WorkerPool IO 并发 | 4（默认） | core/worker_pool.py:93 |
| WorkerPool CPU 并发 | auto (max 2) | core/worker_pool.py:100 |
| TokenBucket 突发 | 20 | core/rate_limiter.py:51 |
| SlidingWindow 限流 | 600 req/60s | core/rate_limiter.py:112 |

### 5.2 Recommended Production Config

```bash
# 单机（默认，避免 SQLite 写锁竞争）
export MAOP_WORKERS=1

# 多机（需切换 PostgreSQL 后端）
export MAOP_DB_BACKEND=postgres
export MAOP_PG_DSN=postgresql://user:pass@host:5432/maop
export MAOP_WORKERS=4
# 配合 Nginx 负载均衡多实例

# K8s
# 推荐 replicas=2-3，HPA 基于 CPU 70% 扩缩容
```

> SQLite 单写入者约束：高并发写场景必须切换 PostgreSQL，详见 §12。

---

## 6. Resource Usage

### 6.1 Container Resource Limits

基于 `docker-compose.yml` 实际 `deploy.resources` 配置：

| Container | CPU Limit | CPU Reserve | Memory Limit | Memory Reserve | Source |
|-----------|-----------|-------------|--------------|----------------|--------|
| maop-dashboard | 1.0 | 0.25 | 512M | 128M | docker-compose.yml:99-106 |
| maop-agent-exec | 2.0 | 0.5 | 1G | 256M | docker-compose.yml:139-146 |
| maop-queue-worker | 0.5 | 0.1 | 256M | 64M | docker-compose.yml:176-183 |

### 6.2 Disk Usage Estimate

| Component | Estimate | Notes |
|-----------|----------|-------|
| SQLite database | ~10-100MB | 取决于使用量（预估） |
| Logs | 轮转 | `log_rotation_max_kb` × `log_rotation_retain` |
| Vector store | ~1MB / 1000 records | numpy 实现（预估） |
| Backups | 自动 | `db_backup` 调度器周期性备份 |

---

## 7. Security Performance Impact

| Mechanism | Overhead | Mitigation | Source |
|-----------|----------|------------|--------|
| PBKDF2 600k iterations (login) | ~300-500ms/登录 | 仅登录时计算，后续用 JWT | dashboard/routers/auth.py:33 |
| TLS 1.2+ handshake | <1ms/handshake | Session resumption | core/tls.py:27 |
| JWT 验证 (HS256) | <1ms/请求 | 对称签名 | （预估） |
| require_admin 检查 | <1ms/请求 | 内存角色查询 | （预估） |
| CSP Middleware | <1ms/响应 | 静态 header | （预估） |
| RateLimit Middleware | <1ms/请求 | 内存计数器 | core/rate_limiter.py |
| Path traversal check | <0.1ms/请求 | realpath 缓存 | （预估） |
| Sandbox (plugin) | 一次性 ~10ms | 启动时初始化 | （预估） |
| Log redaction | <0.5ms/日志条目 | 正则预编译 | （预估） |
| TraceID validation | <0.1ms/请求 | 正则预编译 | （预估） |

> PBKDF2 迭代数 `_AUTH_PBKDF2_ITERATIONS = 600_000`（`dashboard/routers/auth.py:33`）为 OWASP 2023 推荐值，不可降低。登录开销通过 JWT 后续校验摊销。

---

## 8. Monitoring & SLO

### 8.1 Prometheus Config

基于 `monitoring/prometheus.yml` 实际配置：

| Parameter | Value | Source |
|-----------|-------|--------|
| scrape_interval | 15s | prometheus.yml:4 |
| evaluation_interval | 15s | prometheus.yml:5 |
| metrics_path | /api/prometheus | prometheus.yml:13 |
| target | dashboard:9079 | prometheus.yml:15 |
| rule_files | alerts.yml, slo-alerts.yml | prometheus.yml:24-25 |

### 8.2 SLO Objectives

基于 `monitoring/slo-alerts.yml:18-23`：

| SLO | Objective | Error Budget | Source |
|-----|-----------|--------------|--------|
| SLO-1 Availability | 99.9% | 0.1% over 30d | slo-alerts.yml:19 |
| SLO-2 Latency P95 | < 2s | delegation duration | slo-alerts.yml:20 |
| SLO-3 Error rate | < 1% | failed delegations | slo-alerts.yml:21 |
| SLO-4 Queue freshness | < 100 pending for 5m | — | slo-alerts.yml:22 |

### 8.3 Multi-Burn-Rate Thresholds (Google SRE)

| Burn Rate | Page Severity | Window | Budget Consumed | Source |
|-----------|---------------|--------|-----------------|--------|
| Fast | critical (page) | 5m + 1h | 2% in 1h | slo-alerts.yml:25 |
| Slow | critical (page) | 30m + 6h | 5% in 6h | slo-alerts.yml:26 |
| Ticket | warning (ticket) | 2h + 1d | 10% in 3d | slo-alerts.yml:27 |

具体阈值：

- **Availability fast burn**: error rate > 1.44% over 5m + 1h (`slo-alerts.yml:41,49`)
- **Latency fast burn**: P95 > 2s over 5m + 1h (`slo-alerts.yml:121,125`)
- **Latency ticket**: P99 > 4s over 1d (`slo-alerts.yml:158`)
- **Error rate fast burn**: failure rate > 14.4% over 5m + 1h (`slo-alerts.yml:178,184`)
- **Queue freshness**: `MAOP_queue_pending > 100` for 5m (`slo-alerts.yml:244`)
- **Queue critical**: `MAOP_queue_pending > 500` (`slo-alerts.yml:254`)

### 8.4 Prometheus Query Examples

```promql
# API 错率（failed / total delegations）
sum(rate(MAOP_delegations_failed[5m]))
  / clamp_min(sum(rate(MAOP_delegations_total[5m])), 1e-9)

# P95 委托延迟
histogram_quantile(0.95, sum by (le) (rate(MAOP_delegation_duration_seconds_bucket[5m])))

# P99 委托延迟
histogram_quantile(0.99, sum by (le) (rate(MAOP_delegation_duration_seconds_bucket[1d])))

# 队列积压深度
MAOP_queue_pending

# 熔断器状态（1=closed, 0.5=half, 0=open）
MAOP_circuit_breaker_state
```

---

## 9. Performance Tuning Guide

### 9.1 Single-Node Tuning

```bash
# 调整 worker 数（如使用 PostgreSQL，可放开 SQLite 写锁约束）
export MAOP_WORKERS=4

# 启用 LRU 结果缓存
export MAOP_RESULT_CACHE_SIZE=1000

# 调整 worker pool（编辑 config/agents.yaml）
# worker_pool:
#   max_workers: 16
#   max_cpu_workers: 4
```

### 9.2 Database Tuning

```bash
# SQLite WAL 模式（默认已启用，显式确认）
sqlite3 data/maop.db "PRAGMA journal_mode=WAL;"
sqlite3 data/maop.db "PRAGMA synchronous=NORMAL;"  # 性能与一致性折中

# 切换 PostgreSQL（高并发写场景必需）
export MAOP_DB_BACKEND=postgres
export MAOP_PG_DSN=postgresql://user:pass@host:5432/maop
```

### 9.3 LLM Call Optimization

- 启用 OmniRoute 智能路由（默认）
- 配置 FailoverChain 防止单点故障（`core/circuit_breaker.py:60`）
- 启用 `result_cache` 减少重复调用
- 使用 `semantic_cache` 语义级去重
- SingleFlight 合并并发重复请求（`core/cache.py:46`，超时 30s）

### 9.4 Frontend Optimization

- Vue3 SPA 已构建优化（`dist/`）
- 静态资源由 Nginx 缓存（`docker-compose.yml:210`）
- WebSocket 替代轮询，降低无效请求

---

## 10. Benchmark Reproduction

```bash
# 1. 克隆并安装
git clone https://github.com/Levango7/MAOP.git
cd MAOP
pip install -r py/requirements.txt -e py/

# 2. 运行测试套件（含最慢 10 项）
cd py
pytest --durations=10

# 3. 运行压力测试
pytest tests/test_stress.py -v

# 4. 启动服务并测试 API
maop start --port 9079 &
# 使用 wrk 或 hey 压测 /api/health
wrk -t12 -c400 -d30s http://127.0.0.1:9079/api/health

# 5. 查看 Prometheus 指标
curl http://127.0.0.1:9079/api/prometheus | grep -E "duration|total|queue"
```

---

## 11. Performance Regression Detection

### 11.1 Current Baseline

| Metric | Baseline | Source |
|--------|----------|--------|
| 测试总耗时 | 256.09s | final_regression.log:71 |
| 测试用例数 | 3989 passed + 4 skipped | final_regression.log:71 |
| 平均每用例 | ~64.2ms | 计算值 |
| /api/health avg latency | < 100ms | test_stress.py:553 |
| 向量库批量写入 (1000 条) | < 120s | test_stress.py:594 |
| 向量库查询 | < 5s | test_stress.py:604 |

### 11.2 Detection Strategy

- CI 已有 `pytest --durations=10` 输出，每次构建可比对最慢 10 项趋势
- 建议引入 `pytest-benchmark` 建立基线对比
- 监控关键指标趋势：
  - 测试总耗时（当前基线：**256.09s / 3989 用例**）
  - API P99 延迟（SLO-2：P95 < 2s，P99 < 4s ticket）
  - 容器内存占用（dashboard 512M / agent-exec 1G / queue-worker 256M）

---

## 12. Known Performance Limitations

| Limitation | Impact | Mitigation |
|------------|--------|------------|
| SQLite 单写入者 | 高并发写场景锁竞争 | 切换 PostgreSQL（`MAOP_DB_BACKEND=postgres`） |
| 向量搜索（numpy 实现） | 超过 10 万条性能下降 | 切换 FAISS 或专用向量库 |
| LLM 调用 | 受 provider 限流约束 | FailoverChain + OmniRoute 路由 |
| PBKDF2 登录 | 600k 迭代约 300-500ms | OWASP 2023 推荐，不可降低；JWT 摊销 |
| 单进程事件循环 | CPU 密集任务阻塞 | 放 `ProcessPoolExecutor`（`max_cpu_workers`） |
| WorkerPool 无界队列 | 任务积压无反压 | 调用方实现背压或限流 |
| Histogram 桶固定 | 无法动态调整 | `core/monitoring.py:365` DEFAULT_BUCKETS 需代码修改 |

---

## References

- `py/final_regression.log` — 回归测试基线日志
- `py/tests/test_stress.py` — 压力测试断言
- `py/maop/core/worker_pool.py` — WorkerPool 默认配置
- `py/maop/core/cache.py` — LRU 缓存与 SingleFlight
- `py/maop/core/event_bus.py` — EventBus 历史与死信
- `py/maop/core/circuit_breaker.py` — 熔断器状态机
- `py/maop/core/rate_limiter.py` — 令牌桶与滑动窗口
- `py/maop/core/tls.py` — TLS 最小版本
- `py/maop/core/monitoring.py` — Prometheus Histogram 桶
- `py/maop/dashboard/routers/auth.py` — PBKDF2 迭代
- `py/maop/loop_models.py` — LoopConfig 默认值
- `monitoring/prometheus.yml` — 抓取与评估间隔
- `monitoring/slo-alerts.yml` — SLO 阈值与多燃耗率
- `docker-compose.yml` — 容器资源限制
