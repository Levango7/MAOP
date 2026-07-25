# MAOP 重构后架构审视（2026-07-14）

> 视角：Software Architect。基于 `F:/Nexus/MAOP/py/maop/` 现行代码 + `config/` + `docs/adr/` 的只读审查。
> 结论先行：**架构设计方向正确，但「迁移完成」有水分 —— PowerShell 仍重度参与，声明（ADR-009）与运行时现实存在落差。**

---

## 一、总体判断

| 维度 | 评分 | 说明 |
|------|------|------|
| 架构设计 | 8/10 | 配置驱动 + 闭环 + 弹性基础设施，设计成熟 |
| 工程完成度 | 5/10 | 平行移植、依赖缺口、持久化不一致、文档陈旧 |
| 可维护性 | 6/10 | 模块化好，但 PS/Python 双轨并存造成认知负担 |
| 安全面 | 待重审 | 仅 PS 侧审计过，Python `dispatcher`/guardrail 未审 |

**一句话**：Python 引擎是旧 PS 引擎的近 1:1 直译移植，基础设施层确实新建且更健壮；但「主引擎切换」在工程上只做了一半。

---

## 二、做对了什么（应保留）

1. **闭环 + 弹性基础设施**：`Plan→Execute→Verify→Memory→Evolve` 闭环；熔断（per-agent 状态机）、primary→fallback→tertiary 回退链、缓存三防护（穿透/击穿/雪崩）、事件总线、可观测性 —— 这一层是真正的新价值。
2. **配置驱动**：`agents.yaml` + `rules.yaml` → Pydantic `MaopConfig`；18 个 agent + 路由表 + 工作流全部外置，角色可插拔。
3. **测试体系**：`py/tests/` 29 个 `test_*.py`，GitHub Actions 跨平台矩阵（ubuntu/windows/macos × py3.12/3.13）。
4. **Dashboard 后端纯 Python**：FastAPI + `data_bridge` 直连 SQLite，旧 PS dashboard 已按 REMEDIATION_PLAN 删除。
5. **安全审计已启动**：`security-audit.md` 对 PS 侧 19 项（7 Critical/6 High）全部 RESOLVED。

---

## 三、必须修的硬伤（P0 — 阻断级/正确性）

| # | 问题 | 证据 | 影响 |
|---|------|------|------|
| P0-1 | **`pydantic_settings` 未声明依赖** | `config/settings.py:19` import，但 `pyproject.toml` 依赖无此项（CI 靠临时 `pip install pydantic-settings` 补装） | 干净环境 `pip install -e .` 直接失败，无法重现构建 |
| P0-2 | **`data_bridge` 仍 PowerShell 回退** | `dashboard/data_bridge.py:578` `_invoke_ps_fallback()` 调 `powershell -NoProfile -File` | 「纯 Python 数据桥」名不副实；引入子进程 + 跨语言耦合风险 |
| P0-3 | **DB 持久化不一致** | 熔断：`circuit_breaker.py` 写 `maop.db`(SQLite) vs `maop_loop.py`+`provider.py` 用 `circuit-breaker.json`；队列：`maop_loop` 建 `message_queue.db` vs `data_bridge`/`db_backup` 读 `queue.db` | 状态可能双写不同步，熔断/队列读数失真 |
| P0-4 | **依赖无锁文件 + 浮动版本** | `pyproject.toml` 全 `>=`；无 `poetry.lock`/`uv.lock`/`pip-tools` | 不可重现构建；fastapi/uvicorn/httpx 供应链风险（pip-audit 为 `continue-on-error` 不阻断） |

---

## 四、迁移不完整的证据（P1）

1. **`src/` 仍驻留 60+ 个 `.ps1`** —— 旧引擎主体未删，Python 包是其直译平行实现（每个 `.py` docstring 写 `Port of <X>.ps1`），大量逻辑重复。
2. **`core/migration.py` 空壳** —— `MigrationManager` 已实现，但 `data/migrations/*.sql` 不存在，schema 靠 `core/data.py` 内联 `SCHEMA_DDL` 建表。
3. **`hot_reload` 监听幽灵文件** —— `config/routing.yaml` 不存在，路由实际在 `agents.yaml` 内，热 reload 静默失效。
4. **动态路由 / 知识图谱未移植** —— 旧 `dynamic-router.ps1` 无 Python 版（动态路由能力退化）；旧 `memory-graph.ps1` 被 `memory/store.py` 的 FTS5 吸收（图谱边 `graph-nodes/edges.json` 为静态演示数据）。
5. **版本号三处不一致** —— `pyproject 0.1.0` / `Dockerfile LABEL 3.1.0` / `docker-compose "Dashboard v3"`。
6. **重复配置** —— `src/rules.yaml` 是 `config/rules.yaml` 副本；README 仍描述旧 PS 架构。

---

## 五、旧 13 角色 → 新架构映射（你最关心的「角色变了」）

| 旧角色 | 新实现 | 变化 |
|--------|--------|------|
| Router | `maop_plan.py` + `core/load_balancer.py` + `delegate/dispatcher.py` | **拆分**；动态路由未移植 → 能力退化 |
| Human | `core/human_proxy.py` | 改名 Human Proxy（SQLite 审批队列），保留 |
| Planner | `maop_plan.py` + `core/analyzer.py` | 扩充（新增语义分解 + 依赖 DAG） |
| Orchestrator | `maop_loop.py` + `engine.py` | 旧 `orchestrator.ps1` 已废弃 |
| Worker | `config/agents.yaml`（18 agent）+ `dispatcher` | 外置配置驱动，保留 |
| Evaluator | `maop_verify.py` | 改名 Verify，保留 |
| Guard | `core/guardrail.py` | 保留 |
| Sandbox | `core/sandbox.py` | 保留 |
| Memory | `memory/store.py`（SQLite+FTS5） | 增强（FTS5 替代 O(N) 正则） |
| ToolMgr | `core/tool_manager.py` | 保留 |
| Knowledge | （并入 Memory 的 FTS5） | **移除独立模块**，图谱未移植 |
| Monitor | `core/monitoring.py` + `timeseries.py` | 改名 Monitoring，保留 |
| Evolve | `evolve.py` | 保留 |

**新增基础设施层（旧版隐含或未独立）**：circuit_breaker / event_bus / message_queue / cache(+cache_guard) / kv_store / load_balancer / worker_pool / rate_limiter / auth / tls / middleware / runtime(local/isolated/container) / data / migration / db_backup / filelock / log_rotate / error_schema / bloom_filter / analyzer / concurrency(SSE) / prompt_manager / config 子系统 / deploy。

> 净变化：**Knowledge 被合并进 Memory；Router 被拆分且动态路由缺失；其余 11 个旧角色均有对应模块；同时长出一整套弹性/安全/可观测基础设施。**

---

## 六、文档债

- **README.md 陈旧**：仍写 `src/*.ps1`、`maop.ps1 -Action start`、`server-v2.ps1` —— 与新 Python 现实不符，需重写。
- **security-audit.md 范围仅限 PS**：Python 版 `dispatcher`（仍拼接 CLI 命令串，虽用 subprocess 列表式）与 `guardrail` 需用同标准重审。
- **docs/plugin-migration.md** 描述插件迁移，但 `tools/maop-bridge.ps1` 仍是 PS 路径核心依赖，桥接层未拆。

---

## 七、优先行动清单（建议顺序）

| 序 | 行动 | 解决 | 工作量 |
|----|------|------|--------|
| 1 | `pyproject.toml` 补 `pydantic-settings`；引入 `uv.lock`/`pip-tools` 锁依赖 | P0-1, P0-4 | S |
| 2 | 拆 `data_bridge` 的 PS 回退，或显式降级为「遗留桥」并改注释 | P0-2 | M |
| 3 | 定 SQLite 为单一持久化真源，删 JSON 双写（熔断/队列） | P0-3 | M |
| 4 | 建 `data/migrations/` + 初版 `001_*.sql`，让 `migration.py` 非空壳 | P1-2 | M |
| 5 | 删/归档 `src/*.ps1`；修 `hot_reload` 监听路径；统一版本号 | P1-1,3,5 | M |
| 6 | 重写 README；对 Python `dispatcher`/guardrail 重做安全审计 | 文档债 | M |
| 7 | （可选）补动态路由 Python 版，恢复 Router 退化能力 | P1-4 | L |

**S=小 / M=中 / L=大**

---

## 八、架构师建议

> 你们现在的「Python 主引擎」其实是两个并行系统在跑：新 Python 包 + 旧 PS 脚本，靠 `data_bridge` 的 PS 回退和 `src/` 残留缝在一起。
> 这能跑，但**不是真正的迁移**——是「套了一层 Python 壳」。
> 真正的终点应该是：**`src/*.ps1` 可删、`data_bridge` 无 PS 分支、所有状态走单一 SQLite**。到那一天，ADR-009 才算落地。
> 在此之前，建议把 README/ADR-009 的状态从「Python 主引擎」改成「Python 优先、PS 兼容层」，避免对外承诺与实现脱节。
