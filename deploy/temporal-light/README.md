# Temporal 轻生产（本机/单机形态，E1-a 两段式第二段）

与 M0 验收用的 `temporal server start-dev`（sqlite 单进程）的三个本质
差异：**MySQL 持久化**（重启存活）、**auto-setup 完整栈**（内部
frontend/history/matching/worker 同容器组合）、**独立 UI**。云上形态
（腾讯云 Lighthouse 单机）即本 compose 原样上云——配方零差异。

## 启动 / 停止

```bash
docker compose -f deploy/temporal-light/docker-compose.yml up -d     # 常驻
docker compose -f deploy/temporal-light/docker-compose.yml down      # 停止（数据保留在卷）
docker compose -f deploy/temporal-light/docker-compose.yml down -v   # 连数据清除
```

- Temporal 前端：`localhost:7233`
- Web UI：`http://localhost:8088`

## 接 MAOP workflow 执行（M-X2 E2-a）

```bash
MAOP_ORCHESTRATION_BACKEND=temporal python -m maop workflow run m0-demo
# 或显式指向：
python scripts/temporal_m0_demo.py --address localhost:7233 --task-queue maop-light
```

（`TemporalAdapter` 构造参数 `server_address` 默认即 `localhost:7233`；
dev server 与轻生产对 MAOP 侧零差异——后端透明，这正是 E1-a 两段式的
设计点。）

## 验收证据（2026-11-01 本机实跑）

1. `docker compose up -d` → 三服务 healthy（mysql healthcheck → auto-setup
   schema 建库 → temporal namespace describe 通过）；
2. 真实 workflow 跑通：`scripts/temporal_m0_demo.py --address localhost:7233`
   → 3 节点 DAG（规划 → 分支 A∥B）全部 success；
3. **重启存活**：`docker compose down && up -d` 后同一 workflow 再次跑通，
   workflow 历史在 UI 可查（MySQL 卷持久化生效）——这是与 dev server 的
   分界线证据。

## 边界（诚实登记）

- 单容器内多服务（auto-setup 内置组合）：吞吐与高可用不等价于 Temporal
  官方多主机拓扑；单机轻生产定位 = 团队内部/演示级供给。
- 密码为本地默认值（`temporal-light`）——上云时改 secrets 并收紧端口暴露。
- 不内置 Prometheus/Grafana 观测栈（Temporal 指标口已暴露，按需接）。
