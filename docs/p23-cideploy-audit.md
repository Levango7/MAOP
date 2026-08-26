# MAOP CI/部署审核报告（Task 490）

> 扫描时间：2026-08-26
> 扫描范围：CI 配置、Docker 配置、监控/告警、nginx、Patroni、部署脚本、.env.example、Makefile
> 严重度定义：P2 = 影响生产稳定性/安全的中等问题；P3 = 微小的改进建议

## 目录

- [1. CI 配置（.github/workflows/ci.yml）](#1-ci-配置githubworkflowsciyml)
- [2. Docker 配置（Dockerfile / docker-compose / .dockerignore）](#2-docker-配置dockerfile--docker-compose--dockerignore)
- [3. 监控/告警配置（monitoring/ + deploy/ + alertmanager）](#3-监控告警配置monitoring--deploy--alertmanager)
- [4. nginx 配置（nginx.conf / nginx.prod.conf）](#4-nginx-配置nginxconf--nginxprodconf)
- [5. Patroni / HAProxy 配置](#5-patroni--haproxy-配置)
- [6. 其他部署文件（.env.example / Makefile / start.sh / maop.ps1）](#6-其他部署文件envexample--makefile--startsh--maopps1)
- [7. 汇总统计](#7-汇总统计)

---

## 1. CI 配置（.github/workflows/ci.yml）

### P2-C-01 — performance job 不阻塞主矩阵合并，未构成 CI 门禁

| 项 | 值 |
| --- | --- |
| 严重度 | P2 |
| 问题类型 | 性能/稳定性测试未入 CI 门禁 |
| 文件:行号 | `.github/workflows/ci.yml:220-248` |
| 问题描述 | `performance` job 仅 `needs: lint`，不被 `docker`/`publish`/`compose-smoke` 依赖。性能/可靠性/稳定性测试失败不会阻断 main/master 合并，相当于不是 CI 门禁。注释明确说明"不阻塞主矩阵、可单独重跑"，但任务要求性能测试入 CI 门禁。 |
| 修复建议 | 将 `docker`/`publish` job 的 `needs` 增加 `performance`，或将 performance job 改为必依赖（如 `needs: [lint, test]` 并让下游 job 依赖它）。 |

### P3-C-01 — test matrix 在 macOS 上运行 4 个 Python 版本，价值有限

| 项 | 值 |
| --- | --- |
| 严重度 | P3 |
| 问题类型 | matrix 策略冗余 |
| 文件:行号 | `.github/workflows/ci.yml:86-90` |
| 问题描述 | matrix 为 3 OS × 4 Python = 12 个矩阵。项目是 Python 后端 + Vue 前端，macOS 矩阵主要验证平台兼容性，但 4 个 Python 版本全跑 macOS 价值有限，浪费 CI 资源。 |
| 修复建议 | macOS 仅跑最新 Python（3.13），或排除 macOS 矩阵（`exclude: [{os: macos-latest, python-version: "3.10"}, ...]`）。 |

### P3-C-02 — migrations job 使用 Python 3.12，与生产版本 3.13 不一致

| 项 | 值 |
| --- | --- |
| 严重度 | P3 |
| 问题类型 | Python 版本不一致 |
| 文件:行号 | `.github/workflows/ci.yml:399` |
| 问题描述 | `migrations` job 使用 `python-version: "3.12"`，而 `env.PYTHON_VERSION` 是 3.13，test matrix 包含 3.10-3.13。migrations 应与生产版本一致（3.13）以验证真实环境的迁移。 |
| 修复建议 | 改为 `python-version: ${{ env.PYTHON_VERSION }}`。 |

### P3-C-03 — container-scan 的 trivy 扫描 exit-code: '0'，CRITICAL/HIGH 漏洞不阻断 CI

| 项 | 值 |
| --- | --- |
| 严重度 | P3 |
| 问题类型 | 安全扫描非阻塞 |
| 文件:行号 | `.github/workflows/ci.yml:578` |
| 问题描述 | trivy 扫描 `exit-code: '0'`（非阻塞模式），CRITICAL/HIGH 漏洞仅生成报告不阻断 CI。注释说明"生成报告但不让 CI 失败"，但供应链漏洞应至少对 CRITICAL 阻断。 |
| 修复建议 | 改为 `exit-code: '1'` 并设置 `severity: CRITICAL`（仅 CRITICAL 阻断，HIGH 仍报告）。 |

### P3-C-04 — bandit SAST 报告生成用 `|| true` 掩盖失败

| 项 | 值 |
| --- | --- |
| 严重度 | P3 |
| 问题类型 | `|| true` 掩盖失败 |
| 文件:行号 | `.github/workflows/ci.yml:513` |
| 问题描述 | `bandit -r maop/ -f json -o bandit-report.json || true` 用 `|| true` 掩盖报告生成失败。如果 bandit 因配置错误/导入错误退出非零，报告文件可能不存在或损坏，后续 upload-artifact 上传空文件。 |
| 修复建议 | 拆分为两步：先 `bandit -r maop/ -f json -o bandit-report.json`（允许失败），再 `if: always()` 上传。或用 `continue-on-error: true`。 |

---

## 2. Docker 配置（Dockerfile / docker-compose / .dockerignore）

### P2-D-01 — docker-compose.yml: otel-collector 服务无 healthcheck

| 项 | 值 |
| --- | --- |
| 严重度 | P2 |
| 问题类型 | 健康检查缺失 |
| 文件:行号 | `docker-compose.yml:225-246` |
| 问题描述 | `otel-collector` 服务无 healthcheck，compose 无法检测 collector 是否健康，依赖它的 prometheus/dashboard 无法用 `condition: service_healthy` 等待就绪。 |
| 修复建议 | 添加 `healthcheck: test: ["CMD", "wget", "-q", "--spider", "http://localhost:13133/"]`（OTel Collector health_check extension）。 |

### P2-D-02 — docker-compose.yml: nginx 服务无 healthcheck

| 项 | 值 |
| --- | --- |
| 严重度 | P2 |
| 问题类型 | 健康检查缺失 |
| 文件:行号 | `docker-compose.yml:249-267` |
| 问题描述 | `nginx` 服务无 healthcheck，TLS 终止层故障无法被 compose 检测。 |
| 修复建议 | 添加 `healthcheck: test: ["CMD", "wget", "-q", "--spider", "http://localhost/"]`。 |

### P2-D-03 — docker-compose.yml: prometheus 服务无 healthcheck

| 项 | 值 |
| --- | --- |
| 严重度 | P2 |
| 问题类型 | 健康检查缺失 |
| 文件:行号 | `docker-compose.yml:270-300` |
| 问题描述 | `prometheus` 服务无 healthcheck，监控层故障无法被 compose 检测。 |
| 修复建议 | 添加 `healthcheck: test: ["CMD", "wget", "-q", "--spider", "http://localhost:9090/-/healthy"]`。 |

### P2-D-04 — docker-compose.yml: alertmanager 服务无 healthcheck

| 项 | 值 |
| --- | --- |
| 严重度 | P2 |
| 问题类型 | 健康检查缺失 |
| 文件:行号 | `docker-compose.yml:302-315` |
| 问题描述 | `alertmanager` 服务无 healthcheck，告警层故障无法被 compose 检测。 |
| 修复建议 | 添加 `healthcheck: test: ["CMD", "wget", "-q", "--spider", "http://localhost:9093/-/healthy"]`。 |

### P2-D-05 — docker-compose.yml: vault 服务无 healthcheck

| 项 | 值 |
| --- | --- |
| 严重度 | P2 |
| 问题类型 | 健康检查缺失 |
| 文件:行号 | `docker-compose.yml:320-333` |
| 问题描述 | `vault` 服务（dev 模式）无 healthcheck，secrets backend 故障无法被 compose 检测。 |
| 修复建议 | 添加 `healthcheck: test: ["CMD", "vault", "status"]`。 |

### P2-D-06 — docker-compose.yml: n8n 服务无 healthcheck

| 项 | 值 |
| --- | --- |
| 严重度 | P2 |
| 问题类型 | 健康检查缺失 |
| 文件:行号 | `docker-compose.yml:358-380` |
| 问题描述 | `n8n` 服务无 healthcheck，workflow 自动化层故障无法被 compose 检测。 |
| 修复建议 | 添加 `healthcheck: test: ["CMD", "wget", "-q", "--spider", "http://localhost:5678/healthz"]`。 |

### P2-D-07 — docker-compose.yml: otel-collector 无资源限制

| 项 | 值 |
| --- | --- |
| 严重度 | P2 |
| 问题类型 | 资源限制缺失 |
| 文件:行号 | `docker-compose.yml:225-246` |
| 问题描述 | `otel-collector` 无 `deploy.resources.limits`，collector 在 burst load 下可能 OOM-kill 宿主机。 |
| 修复建议 | 添加 `deploy: resources: limits: cpus: "1.0", memory: 512M`。 |

### P2-D-08 — docker-compose.yml: nginx 无资源限制

| 项 | 值 |
| --- | --- |
| 严重度 | P2 |
| 问题类型 | 资源限制缺失 |
| 文件:行号 | `docker-compose.yml:249-267` |
| 问题描述 | `nginx` 无 `deploy.resources.limits`，TLS 终止层无内存上限。 |
| 修复建议 | 添加 `deploy: resources: limits: cpus: "1.0", memory: 512M`。 |

### P2-D-09 — docker-compose.yml: prometheus 无资源限制

| 项 | 值 |
| --- | --- |
| 严重度 | P2 |
| 问题类型 | 资源限制缺失 |
| 文件:行号 | `docker-compose.yml:270-300` |
| 问题描述 | `prometheus` 无 `deploy.resources.limits`，TSDB 可增长填满磁盘。 |
| 修复建议 | 添加 `deploy: resources: limits: cpus: "1.0", memory: 1G`。 |

### P2-D-10 — docker-compose.yml: alertmanager 无资源限制

| 项 | 值 |
| --- | --- |
| 严重度 | P2 |
| 问题类型 | 资源限制缺失 |
| 文件:行号 | `docker-compose.yml:302-315` |
| 问题描述 | `alertmanager` 无 `deploy.resources.limits`。 |
| 修复建议 | 添加 `deploy: resources: limits: cpus: "0.5", memory: 256M`。 |

### P2-D-11 — docker-compose.yml: vault 无资源限制

| 项 | 值 |
| --- | --- |
| 严重度 | P2 |
| 问题类型 | 资源限制缺失 |
| 文件:行号 | `docker-compose.yml:320-333` |
| 问题描述 | `vault` 无 `deploy.resources.limits`，secrets backend 无内存上限。 |
| 修复建议 | 添加 `deploy: resources: limits: cpus: "1.0", memory: 1G`。 |

### P2-D-12 — docker-compose.yml: n8n 无资源限制

| 项 | 值 |
| --- | --- |
| 严重度 | P2 |
| 问题类型 | 资源限制缺失 |
| 文件:行号 | `docker-compose.yml:358-380` |
| 问题描述 | `n8n` 无 `deploy.resources.limits`，workflow 引擎无内存上限。 |
| 修复建议 | 添加 `deploy: resources: limits: cpus: "1.0", memory: 1G`。 |

### P2-D-13 — docker-compose.prod.yml: nginx 无 healthcheck

| 项 | 值 |
| --- | --- |
| 严重度 | P2 |
| 问题类型 | 健康检查缺失 |
| 文件:行号 | `docker-compose.prod.yml:461-497` |
| 问题描述 | 生产 nginx 无 healthcheck，TLS 终止层故障无法被 compose 检测，dashboard `depends_on: nginx` 无法用 `condition: service_healthy`。 |
| 修复建议 | 添加 `healthcheck: test: ["CMD", "wget", "-q", "--spider", "http://localhost/"]`。 |

### P2-D-14 — docker-compose.prod.yml: prometheus 无 healthcheck

| 项 | 值 |
| --- | --- |
| 严重度 | P2 |
| 问题类型 | 健康检查缺失 |
| 文件:行号 | `docker-compose.prod.yml:499-532` |
| 问题描述 | 生产 prometheus 无 healthcheck，监控层故障无法被 compose 检测。 |
| 修复建议 | 添加 `healthcheck: test: ["CMD", "wget", "-q", "--spider", "http://localhost:9090/-/healthy"]`。 |

### P2-D-15 — docker-compose.prod.yml: grafana 无 healthcheck

| 项 | 值 |
| --- | --- |
| 严重度 | P2 |
| 问题类型 | 健康检查缺失 |
| 文件:行号 | `docker-compose.prod.yml:534-566` |
| 问题描述 | 生产 grafana 无 healthcheck，可视化层故障无法被 compose 检测。 |
| 修复建议 | 添加 `healthcheck: test: ["CMD", "wget", "-q", "--spider", "http://localhost:3000/api/health"]`。 |

### P3-D-01 — Dockerfile 使用 requirements.txt 而非 requirements.lock

| 项 | 值 |
| --- | --- |
| 严重度 | P3 |
| 问题类型 | 依赖文件不一致 |
| 文件:行号 | `py/Dockerfile:25` |
| 问题描述 | Dockerfile 使用 `COPY py/requirements.txt .` 安装依赖，而 CI 使用 `requirements.lock`（pip-audit/SBOM）。两者可能不一致，镜像依赖与审计依赖脱节。 |
| 修复建议 | 改为 `COPY py/requirements.lock .` 并 `pip install --no-cache-dir --prefix=/install -r requirements.lock`。 |

### P3-D-02 — Dockerfile ARG PYTHON_IMAGE 默认值未固定 digest

| 项 | 值 |
| --- | --- |
| 严重度 | P3 |
| 问题类型 | 供应链风险 |
| 文件:行号 | `py/Dockerfile:19` |
| 问题描述 | `ARG PYTHON_IMAGE=python:3.13-slim` 未固定 digest，上游 tag 可被重推。注释说明 CI 可通过 `--build-arg PYTHON_IMAGE=...@sha256:xxx` 固定，但默认值仍有风险。 |
| 修复建议 | 默认值改为 `python:3.13-slim@sha256:<具体 digest>`，或在 CI 中强制传入 digest。 |

### P3-D-03 — Prometheus 镜像版本在 base 与 prod 之间不一致

| 项 | 值 |
| --- | --- |
| 严重度 | P3 |
| 问题类型 | 镜像版本不一致 |
| 文件:行号 | `docker-compose.yml:271` / `docker-compose.prod.yml:500` |
| 问题描述 | base compose 用 `prom/prometheus:v2.51.0`，prod override 用 `prom/prometheus:v2.53.0`。prod override 会覆盖 base，但版本不一致易导致本地与生产行为差异。 |
| 修复建议 | 统一为同一版本（建议 v2.53.0）。 |

### P3-D-04 — docker-compose.yml 多个服务无 logging 配置（日志无大小限制）

| 项 | 值 |
| --- | --- |
| 严重度 | P3 |
| 问题类型 | 日志轮转缺失 |
| 文件:行号 | `docker-compose.yml:225-380`（otel-collector/nginx/prometheus/alertmanager/vault/n8n） |
| 问题描述 | 这 6 个服务无 `logging` 配置，Docker 默认 json-file 无大小限制，可能填满宿主机磁盘。`docker-compose.prod.yml` 通过 `x-logging` anchor 统一处理，但 base compose 未覆盖这些服务。 |
| 修复建议 | 为每个服务添加 `logging: { driver: json-file, options: { max-size: "10m", max-file: "3" } }`。 |

### P3-D-05 — docker-compose.yml: vault 服务无 restart 策略

| 项 | 值 |
| --- | --- |
| 严重度 | P3 |
| 问题类型 | restart 策略缺失 |
| 文件:行号 | `docker-compose.yml:320-333` |
| 问题描述 | `vault` 服务无 `restart` 策略，容器退出后不自动重启。 |
| 修复建议 | 添加 `restart: unless-stopped`。 |

### P3-D-06 — docker-compose.yml: agent-exec 和 queue-worker 无 healthcheck

| 项 | 值 |
| --- | --- |
| 严重度 | P3 |
| 问题类型 | 健康检查缺失 |
| 文件:行号 | `docker-compose.yml:140-222` |
| 问题描述 | `agent-exec` 和 `queue-worker` 无 healthcheck，compose 无法检测 worker 健康状态。虽有 `depends_on: dashboard: condition: service_healthy`，但 worker 自身故障不可见。 |
| 修复建议 | 添加 healthcheck（如检查进程存在或 worker 内部 health endpoint）。 |

### P3-D-07 — .dockerignore 未忽略多个非运行时文件/目录

| 项 | 值 |
| --- | --- |
| 严重度 | P3 |
| 问题类型 | 构建上下文冗余 |
| 文件:行号 | `.dockerignore` |
| 问题描述 | 未忽略 `.github/`、`scripts/`、`tools/`、`deliverables/`、`Makefile`、`start.sh`、`maop.ps1`、`py/tests/`、`py/scripts/`、`py/requirements.lock`、`py/.cov_baseline.json`、`alembic.ini.template`、`CHANGELOG.md`、`LICENSE`、`SECURITY.md`、`CONTRIBUTING.md`、`ROADMAP.md` 等。这些文件进入构建上下文，增大 docker build 传输量与层缓存失效概率。 |
| 修复建议 | 在 .dockerignore 中追加上述路径。 |

---

## 3. 监控/告警配置（monitoring/ + deploy/ + alertmanager）

### P2-M-01 — otel-collector-config.yaml 使用已废弃的 logging exporter，prometheus/jaeger exporter 被注释

| 项 | 值 |
| --- | --- |
| 严重度 | P2 |
| 问题类型 | OTel collector 配置错误 |
| 文件:行号 | `otel-collector-config.yaml:20-30` |
| 问题描述 | 根目录 `otel-collector-config.yaml`（被 docker-compose.yml 挂载）使用已废弃的 `logging` exporter（OTel Collector 0.86+ 改为 `debug`）。`prometheus` 和 `otlp/jaeger` exporter 被注释，traces/metrics 只输出到日志，无法被 Prometheus 抓取或 Jaeger/Tempo 可视化。 |
| 修复建议 | 将 `logging` 改为 `debug`；启用 `prometheus` exporter（endpoint: 0.0.0.0:8889）；按需启用 `otlp/jaeger`。或直接挂载 `deploy/otel-collector.yaml`（已含完整配置）。 |

### P2-M-02 — deploy/otel-collector.yaml 是完整配置但未被 docker-compose 挂载

| 项 | 值 |
| --- | --- |
| 严重度 | P2 |
| 问题类型 | 配置文件未使用 |
| 文件:行号 | `docker-compose.yml:233` / `deploy/otel-collector.yaml` |
| 问题描述 | `deploy/otel-collector.yaml` 是更完整的 OTel collector 配置（含 jaeger/prometheus/loki exporter、tail_sampling、hostmetrics、resource processor），但 docker-compose.yml 第 233 行挂载的是根目录的 `./otel-collector-config.yaml`（简化版），`deploy/otel-collector.yaml` 实际未被使用。 |
| 修复建议 | 将 docker-compose.yml 挂载路径改为 `./deploy/otel-collector.yaml`，或合并两个配置文件。 |

### P2-M-03 — otel-collector 暴露 8889 端口但 prometheus exporter 被注释，端口无服务

| 项 | 值 |
| --- | --- |
| 严重度 | P2 |
| 问题类型 | 端口暴露与配置不一致 |
| 文件:行号 | `docker-compose.yml:237` / `otel-collector-config.yaml:28-30` |
| 问题描述 | docker-compose.yml 暴露 `127.0.0.1:8889:8889`（注释为 Prometheus metrics），但 `otel-collector-config.yaml` 中 prometheus exporter 被注释，8889 端口无实际服务监听。Prometheus 若配置抓取该端口将全部失败。 |
| 修复建议 | 启用 prometheus exporter（见 P2-M-01），或移除 8889 端口映射。 |

### P3-M-01 — prometheus-alerts.yml 与 alerts.yml 存在语义重复的告警规则

| 项 | 值 |
| --- | --- |
| 严重度 | P3 |
| 问题类型 | 告警规则重复 |
| 文件:行号 | `monitoring/prometheus-alerts.yml:32,45,57,66` |
| 问题描述 | `prometheus-alerts.yml` 中 `AgentCircuitBreakerOpen`/`QueueBacklogGrowing`/`NoActiveAgents`/`MemoryStoreGrowing` 与 `alerts.yml` 中 `MAOPCircuitBreakerOpen`/`MAOPQueueBacklogGrowing`/`MAOPNoActiveAgents`/`MAOPMemoryStoreGrowing` 表达式完全相同，仅 alert name 不同。注释称"alert names are distinct, so there is no collision"，但实际效果是同一问题触发两个告警，造成噪音。 |
| 修复建议 | 删除 `prometheus-alerts.yml` 中重复规则，或合并为单一规则集。 |

### P3-M-02 — monitoring/grafana/maop-slo.json 是空文件

| 项 | 值 |
| --- | --- |
| 严重度 | P3 |
| 问题类型 | dashboard 占位符未实现 |
| 文件:行号 | `monitoring/grafana/maop-slo.json` |
| 问题描述 | `maop-slo.json` 是 0 字节空文件，被 `provisioning/dashboards/maop.yaml` 显式排除加载（`include: ["maop-overview.json"]`）。SLO dashboard 未实现。 |
| 修复建议 | 实现 SLO dashboard（基于 `slo-alerts.yml` 的 SLO-1~5 指标），或删除空文件与相关引用。 |

### P3-M-03 — deploy/grafana/dashboards/maop-overview.json 与 monitoring/grafana/maop-overview.json 重复

| 项 | 值 |
| --- | --- |
| 严重度 | P3 |
| 问题类型 | dashboard 重复 |
| 文件:行号 | `deploy/grafana/dashboards/maop-overview.json` / `monitoring/grafana/maop-overview.json` |
| 问题描述 | 存在两个 `maop-overview.json`：`deploy/grafana/dashboards/`（uid `maop-observability`，标题 "MAOP Observability Overview"）与 `monitoring/grafana/`（uid `maop-overview`，标题 "MAOP Overview"）。docker-compose.prod.yml 挂载 `./monitoring/grafana`，`deploy/grafana/` 未被使用。 |
| 修复建议 | 删除 `deploy/grafana/` 下的重复 dashboard，或合并为单一来源。 |

---

## 4. nginx 配置（nginx.conf / nginx.prod.conf）

### P2-N-01 — nginx.conf 和 nginx.prod.conf 均无 gzip/brotli 压缩配置

| 项 | 值 |
| --- | --- |
| 严重度 | P2 |
| 问题类型 | 压缩配置缺失 |
| 文件:行号 | `nginx.conf` / `nginx.prod.conf` |
| 问题描述 | 两个 nginx 配置均无 `gzip` 或 `brotli` 压缩配置。API JSON 响应和静态资源未压缩，增加带宽消耗和前端加载延迟。 |
| 修复建议 | 添加 `gzip on; gzip_types application/json text/css application/javascript; gzip_min_length 1024;` 或启用 brotli 模块。 |

### P2-N-02 — nginx.conf /assets/ location 安全头丢失

| 项 | 值 |
| --- | --- |
| 严重度 | P2 |
| 问题类型 | 安全头丢失 |
| 文件:行号 | `nginx.conf:50-54` |
| 问题描述 | `nginx.conf` 的 `/assets/` location（第50-54行）使用 `add_header Cache-Control "public, immutable"`，但未重复设置 server 块中的其他安全头（X-Frame-Options、X-Content-Type-Options、Strict-Transport-Security、Referrer-Policy、Permissions-Policy）。nginx 的 `add_header` 在 location 块中会覆盖父级所有 `add_header`，导致 `/assets/` 路径下安全头全部丢失。`nginx.prod.conf` 的 `/assets/` location（第55-66行）已正确重复设置所有安全头，但 `nginx.conf` 未修复。 |
| 修复建议 | 在 `nginx.conf` 的 `/assets/` location 中重复设置所有安全头（参照 `nginx.prod.conf:55-66`）。 |

### P3-N-01 — nginx.prod.conf 未设置 server_tokens off

| 项 | 值 |
| --- | --- |
| 严重度 | P3 |
| 问题类型 | 信息泄露 |
| 文件:行号 | `nginx.prod.conf` |
| 问题描述 | `nginx.conf` 第39行有 `server_tokens off`，但 `nginx.prod.conf` 未设置，生产环境会暴露 nginx 版本号。 |
| 修复建议 | 在 `nginx.prod.conf` 的 server 块中添加 `server_tokens off;`。 |

### P3-N-02 — /api/health location 被 /api/ location 遮蔽，access_log off 永不生效

| 项 | 值 |
| --- | --- |
| 严重度 | P3 |
| 问题类型 | location 匹配优先级 |
| 文件:行号 | `nginx.conf:87` / `nginx.prod.conf:101` |
| 问题描述 | `/api/health` location（前缀匹配）写在 `/api/` location 之后。nginx 前缀匹配选最长路径，`/api/health` 实际会匹配 `/api/health`（更长前缀优先），所以 `access_log off` 生效。但 `/api/` 的 `limit_req` 不会应用于 `/api/health`，健康检查不受速率限制——这是预期行为。此条取消，保留为文档说明。 |
| 修复建议 | 无需修复（确认行为正确）。 |

### P3-N-03 — 未设置 proxy_connect_timeout

| 项 | 值 |
| --- | --- |
| 严重度 | P3 |
| 问题类型 | 超时配置不完整 |
| 文件:行号 | `nginx.conf` / `nginx.prod.conf` |
| 问题描述 | 两个配置均设置 `proxy_read_timeout` 但未设置 `proxy_connect_timeout`（默认 60s）。后端启动慢时 nginx 会等待 60s 才返回 502，影响用户体验。 |
| 修复建议 | 添加 `proxy_connect_timeout 5s;`。 |

---

## 5. Patroni / HAProxy 配置

### P2-P-01 — patroni.yml 使用 $(POD_IP) Kubernetes 风格变量引用，docker-compose 不替换

| 项 | 值 |
| --- | --- |
| 严重度 | P2 |
| 问题类型 | 变量替换不兼容 |
| 文件:行号 | `deploy/patroni/patroni.yml:23,36` |
| 问题描述 | `connect_address: $(POD_IP):8008` 和 `$(POD_IP):5432` 使用 Kubernetes 风格的 `$(POD_IP)` 变量引用，但 docker-compose 部署中 `$(POD_IP)` 不会被替换，Patroni 会尝试连接字面量 `$(POD_IP):8008` 导致 DNS 解析失败。 |
| 修复建议 | docker-compose 部署中改为具体容器名（如 `patroni1:8008`），或通过 envsubst 渲染。K8s 部署时用 K8s downward API 注入 POD_IP。 |

### P2-P-02 — patroni.yml 使用 ${VAR} shell 风格变量引用，Patroni 不替换

| 项 | 值 |
| --- | --- |
| 严重度 | P2 |
| 问题类型 | 变量替换不兼容 |
| 文件:行号 | `deploy/patroni/patroni.yml:26,41,44,47` |
| 问题描述 | `password: ${PATRONI_API_PASSWORD:?Set PATRONI_API_PASSWORD}` 等使用 shell 风格 `${VAR}` 引用，但 Patroni 配置文件本身不进行变量替换（Patroni 读取 YAML 字面量）。docker-compose 中通过环境变量 `PATRONI_RESTAPI_PASSWORD` 传递给 Spilo 镜像，Spilo 内部生成 patroni 配置——此 `patroni.yml` 是文档/参考配置，实际未被 Spilo 使用。 |
| 修复建议 | 明确标注此文件为参考配置（README 说明），或改为 Spilo 环境变量映射方式。 |

### P3-P-01 — patroni.yml pg_hba 允许 0.0.0.0/0

| 项 | 值 |
| --- | --- |
| 严重度 | P3 |
| 问题类型 | 网络访问控制过宽 |
| 文件:行号 | `deploy/patroni/patroni.yml:118` |
| 问题描述 | `host all all 0.0.0.0/0 scram-sha-256` 允许任意 IP 连接 PG。虽然在内网/容器网络中风险受控，但应限制为容器网段（如 `10.0.0.0/8` 或 `172.16.0.0/12`）。 |
| 修复建议 | 改为 `host all all 10.0.0.0/8 scram-sha-256`（或具体容器网段）。 |

### P3-P-02 — haproxy.cfg userlist password 为占位符，未通过 envsubst 注入

| 项 | 值 |
| --- | --- |
| 严重度 | P3 |
| 问题类型 | 凭证注入方式不完善 |
| 文件:行号 | `deploy/patroni/haproxy.cfg:54` |
| 问题描述 | `user admin password <replace-with-sha256-hash>` 是占位符，注释说明需手动编辑或通过 envsubst 注入，但 docker-compose.prod.yml 的 pg-haproxy 服务未配置 envsubst entrypoint，直接挂载 haproxy.cfg。部署时 HAProxy 会因密码 hash 无效而启动失败或无认证保护。 |
| 修复建议 | 为 pg-haproxy 服务添加 envsubst entrypoint（参照 alertmanager 的 render-config.sh 模式），或改用 HAProxy 环境变量注入。 |

---

## 6. 其他部署文件（.env.example / Makefile / start.sh / maop.ps1）

### P3-E-01 — .env.example 中 MAOP_OTEL_EXPORTER 重复定义且值冲突

| 项 | 值 |
| --- | --- |
| 严重度 | P3 |
| 问题类型 | 配置示例冲突 |
| 文件:行号 | `.env.example:91,169` |
| 问题描述 | 第91行 `MAOP_OTEL_EXPORTER=none`，第169行 `MAOP_OTEL_EXPORTER=otlp`，同一变量重复定义且值不同。后者覆盖前者，但读者易混淆。 |
| 修复建议 | 删除第91行的 `MAOP_OTEL_EXPORTER=none`，保留第169行的 `MAOP_OTEL_EXPORTER=otlp`。 |

### P3-E-02 — .env.example 中 MAOP_QUEUE_BACKEND 重复定义且值冲突

| 项 | 值 |
| --- | --- |
| 严重度 | P3 |
| 问题类型 | 配置示例冲突 |
| 文件:行号 | `.env.example:140,271` |
| 问题描述 | 第140行 `MAOP_QUEUE_BACKEND=memory`，第271行 `MAOP_QUEUE_BACKEND=sqlite`，值冲突。 |
| 修复建议 | 统一为 `memory`（个人版默认），删除第271行重复定义。 |

### P3-E-03 — .env.example 中 MAOP_CACHE_BACKEND 重复定义

| 项 | 值 |
| --- | --- |
| 严重度 | P3 |
| 问题类型 | 配置示例重复 |
| 文件:行号 | `.env.example:139,270` |
| 问题描述 | 第139行和第270行均为 `MAOP_CACHE_BACKEND=memory`，重复定义。 |
| 修复建议 | 删除第270行重复定义。 |

### P3-F-01 — Makefile test 目标未包含 e2e/performance/reliability 测试

| 项 | 值 |
| --- | --- |
| 严重度 | P3 |
| 问题类型 | Makefile 与 CI 不一致 |
| 文件:行号 | `Makefile:40-42` |
| 问题描述 | `make test` 仅运行 `tests/` 和 `tests/contract/`，未包含 `tests/e2e/`、`tests/performance/`、`tests/reliability/`、`tests/stability/`。CI 有独立 job 跑这些测试，但本地 `make test` 无法验证。 |
| 修复建议 | 添加 `make test-all` 目标包含全部测试，或扩展 `test` 目标。 |

### P3-F-02 — Makefile lint 目标未检查 tests/ 目录

| 项 | 值 |
| --- | --- |
| 严重度 | P3 |
| 问题类型 | Makefile 与 CI 不一致 |
| 文件:行号 | `Makefile:44-45` |
| 问题描述 | `make lint` 只检查 `$(PY_DIR)/maop/`，未检查 `tests/`。CI 中 ruff 检查 `maop/ tests/`（ci.yml:55）。 |
| 修复建议 | 改为 `$(VENV_PY) -m ruff check $(PY_DIR)/maop/ $(PY_DIR)/tests/`。 |

---

## 7. 汇总统计

### 按严重度

| 严重度 | 数量 |
| --- | --- |
| P2 | 22 |
| P3 | 22 |
| **合计** | **44** |

### 按类别

| 类别 | P2 | P3 | 小计 |
| --- | --- | --- | --- |
| CI 配置 | 1 | 4 | 5 |
| Docker 配置 | 15 | 7 | 22 |
| 监控/告警 | 3 | 3 | 6 |
| nginx | 2 | 3 | 5 |
| Patroni/HAProxy | 2 | 2 | 4 |
| .env.example | 0 | 3 | 3 |
| Makefile | 0 | 2 | 2 |
| **合计** | **22** | **22** | **44** |

### P2 问题清单（按优先级排序）

1. P2-D-01~06 — docker-compose.yml 6 个服务无 healthcheck
2. P2-D-07~12 — docker-compose.yml 6 个服务无资源限制
3. P2-D-13~15 — docker-compose.prod.yml 3 个服务无 healthcheck
4. P2-M-01 — otel-collector 使用废弃 logging exporter，prometheus/jaeger 被注释
5. P2-M-02 — deploy/otel-collector.yaml 完整配置未被挂载
6. P2-M-03 — 8889 端口暴露但 prometheus exporter 被注释
7. P2-N-01 — nginx 无 gzip/brotli 压缩
8. P2-N-02 — nginx.conf /assets/ 安全头丢失
9. P2-P-01 — patroni.yml $(POD_IP) 不被 docker-compose 替换
10. P2-P-02 — patroni.yml ${VAR} 不被 Patroni 替换
11. P2-C-01 — performance job 不阻塞合并

### 已确认无问题的项

- e2e 步骤 `|| true` 已修复（ci.yml:197-208 精确过滤 exit 5）
- 覆盖率门槛一致（pyproject.toml 注释说明 + CI 使用 ratchet 脚本 FLOOR=80）
- CI 无被注释掉的步骤
- cache 配置已优化（pip/npm/Playwright 均有 cache）
- alertmanager 有真实告警出口（webhook/email/slack，非全注释）
- prometheus 规则完整（alerts.yml + slo-alerts.yml + prometheus-alerts.yml）
- grafana dashboard 存在（maop-overview.json 已实现）
- 镜像均使用固定 tag（无 latest，除 Dockerfile ARG 默认值）
- 多阶段构建已优化（frontend-builder → builder → runtime）
- 安全头基本完整（HSTS、X-Frame-Options、X-Content-Type-Options、Referrer-Policy、Permissions-Policy）
- 速率限制已配置（nginx limit_req + 应用层 MAOP_RATE_LIMIT）
- docker-compose.prod.yml 资源限制完整（所有服务均有 deploy.resources）
- docker-compose.prod.yml 日志轮转完整（x-logging anchor 统一处理）