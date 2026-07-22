# MAOP v4.0.0 — 企业级多智能体编排平台 全面评估报告

> **评估日期**：2026-07-19  
> **项目路径**：`F:\Nexus\MAOP\`  
> **评估范围**：全部 36,755 行 Python 代码 · 109 测试文件 · 2,640 测试 · 12 份 ADR · 完整配置体系  
> **活跃 Model**：opencode/deepseek-v4-flash-free

> **[t17 数字校正 2026-07-21]** 本报告于 2026-07-19 撰写时使用了低估的规模数字。
> 经实测核对（详见 Section 2 修正表），实际规模为：**328 个 Python 文件 / 82,660 行 LOC**（py/ 全目录，含 tests/），
> 其中 maop/ 主包 **181 文件 / 49,092 行**，**tests/ 144 文件 / 33,511 行**，
> 测试函数 **3,145 个**（非 2,640）。此外，Section 4 标题"七层架构"与下方
> 架构图实际显示的 6 层（CLI / 编排 / 引擎 / 服务 / 基础设施 / 展示）不符，
> 应理解为"6 层 + 1 跨层扩展点（plugin）"或更正为"六层架构"。下方所有具体
> 位置已就地以 **[t17 修正]** 标记。

---

## 📋 目录

1. [执行摘要](#1-执行摘要)
2. [项目全局指标](#2-项目全局指标)
3. [PEV → MAOP 版本跃迁](#3-pev--maop-版本跃迁)
4. [七层架构全景 → 实测六层架构（t17 修正）](#4-七层架构全景)
5. [v4.0.0 新增 16 个核心模块详解](#5-v400-新增-16-个核心模块详解)
6. [基础设施层模块矩阵（46 模块 → 实测 94 模块，t17 修正）](#6-基础设施层模块矩阵)
7. [Dashboard 体系](#7-dashboard-体系)
8. [测试体系](#8-测试体系)
9. [配置体系](#9-配置体系)
10. [依赖与构建体系](#10-依赖与构建体系)
11. [安全审计状态](#11-安全审计状态)
12. [旧报告错误纠正清单](#12-旧报告错误纠正清单)
13. [剩余问题与行动建议](#13-剩余问题与行动建议)
14. [演进路线图](#14-演进路线图)
15. [附录](#15-附录)

---

## 1. 执行摘要

### 1.1 一句话结论

**MAOP v4.0.0 已从"多智能体编排框架"进化为"企业级多智能体平台"，同时增加了插件系统、ReAct 推理、知识图谱、成本管控四大核心能力支柱，36,755 行 Python、2,640 个测试验证了工程成熟度。**

> **[t17 修正 2026-07-21]** 实测规模：**82,660 行 Python**（py/ 全目录）/ **49,092 行 maop/ 主包** / **3,145 个测试**。原数字（36,755 / 2,640）系 v4.0.0 早期评估时统计口径未含新增的 dashboard/enterprise/worker 子包与 tests/contract，以及本轮 t06-t16 期间补齐的 t11-t14 新增测试。

### 1.2 综合评分

| 维度 | 评分 | 关键论据 |
|------|------|---------|
| **架构设计** | **9.0/10** | 七层架构 + 插件化 + ReAct + 知识图谱 + 成本管控，已达企业级标准 |
| **工程完成度** | **8.5/10** | 36,755 行，46 个基础设施模块，26 个 Dashboard 路由，2,640 测试 **[t17 修正]** 实测 49,092 行 maop/ 主包，94 个 core/ 模块，28 个 routers/ 模块，3,145 测试 |
| **可维护性** | **8.0/10** | Pydantic 全模型化、模块分离好；但名称统一（PEV→MAOP）仍有残留 |
| **安全性** | **8.5/10** | 插件沙箱 + SHA-256 + 受限 builtins + 导入守卫；`cryptography` 已为核心依赖 |
| **可扩展性** | **9.0/10** | 插件系统 + MCP + 协议注册 + Agent 自动扫描，扩展点完善 |
| **文档质量** | **7.5/10** | README 重写、12 ADR 完整；但仍有 PEV 旧名残留 |

### 1.3 能力矩阵

| 能力领域 | v3.5.0 状态 | v4.0.0 状态 | 关键模块 |
|---------|------------|------------|---------|
| **基础编排** | ✅ Plan→Execute→Verify | ✅ + ReAct 微循环 | `maop_loop` / `react_loop` |
| **弹性** | ✅ 熔断/缓存/限流 | ✅ + 策略进化 | `circuit_breaker` / `evolution_strategies` |
| **安全** | ✅ TLS/JWT/审计 | ✅ + 插件沙箱/变更审查 | `plugin` / `change_tracker` / `permission` |
| **可观测** | ✅ 监控/WS/Prometheus | ✅ + 实时成本追踪 | `cost_tracker` / `monitoring` |
| **可扩展** | ⚠️ MCP/协议 | ✅ + 插件系统 + Agent 发现 | `plugin` / `agent_scanner` / `agent_registry` |
| **知识管理** | ❌ 静态 JSON | ✅ 动态图谱 + 抽取 + 推理 | `knowledge_graph` / `knowledge_extractor` |
| **LLM 抽象** | ⚠️ model/registry | ✅ + 统一供应商接口 | `llm_provider` / `model/registry` |
| **前端** | ✅ 零构建 SPA | ✅ + Vite + React SPA | `dashboard/` + `dashboard-vite/` |
| **Agent 生态** | ⚠️ 手动配置 | ✅ 自动扫描/注册/匹配 | `agent_scanner` / `capability_matcher` |
| **成本管控** | ❌ 无 | ✅ 实时追踪 + 预算告警 | `cost_tracker` |

---

## 2. 项目全局指标

### 2.1 代码规模

| 类别 | 数量 | 代码行数 | 占比 |
|------|------|---------|------|
| **Python 主包** (`MAOP/`) | ~140 文件 | 36,755 行 | 100% |
| 其中：核心层 (`core/`) | 46 模块 | **~20,640 行** | 56% |
| 其中：Dashboard 路由 (`routers/`) | 26 模块 | **~5,000 行** | 14% |
| 其中：顶层模块（maop_loop 等） | 14 文件 | **~4,205 行** | 11% |
| 其中：存储层 (`memory/`) | 6 模块 | **~1,549 行** | 4% |
| 其中：模型层 (`model/`) | 6 模块 | **~1,500 行** | 4% |
| 其中：委托层 (`delegate/`) | 5 文件 | **~1,200 行** | 3% |
| 其中：其他（config/control 等） | 8 文件 | **~1,170 行** | 3% |
| **测试文件** | 109 文件 | **~10,000 行** | — |

> **[t17 修正 2026-07-21]** 上述表格数字基于 v4.0.0 早期评估口径。实测对照（含 t06-t16 期间补齐模块）：
>
> | 模块 | 原报告 | 实测 | 差异说明 |
> |------|--------|------|----------|
> | Python 主包 (maop/) | ~140 文件 / 36,755 行 | **181 文件 / 49,092 行** | 新增 dashboard/enterprise/worker 子包未计入原表 |
> | core/ | 46 模块 / ~20,640 行 | **94 文件 / 30,196 行** | 增长来自 t07-t13 新增的 change_tracker/plugin/hook_manager/evolution_loop/a2a/three_layer_memory 等模块及企业版扩展 |
> | dashboard/routers/ | 26 模块 / ~5,000 行 | **28 文件 / 4,747 行** | LOC 接近，文件数 +2 |
> | 顶层模块 (maop/*.py) | 14 文件 / ~4,205 行 | **13 文件 / 4,574 行** | 文件 -1（迁移到子包），LOC +369 |
> | memory/ | 6 模块 / ~1,549 行 | **7 文件 / 2,283 行** | +1 模块（three_layer_memory） |
> | model/ | 6 模块 / ~1,500 行 | **7 文件 / 1,252 行** | +1 模块，LOC 略低（部分代码已下沉至 core/） |
> | delegate/ | 5 文件 / ~1,200 行 | **5 文件 / 1,290 行** | ✓ 一致 |
> | 测试文件 | 109 文件 / ~10,000 行 | **144 文件 / 33,511 行** | +35 文件 / +23,511 行，主要来自 t06-t16 新增的契约测试与模块测试 |
| **powerShell（已归档）** | 63 文件 | ~9,000 行（零运行时依赖） | — |
| **前端（Dashboard）** | 2 套 | 零构建 SPA + Vite React SPA | — |

### 2.2 测试规模

| 指标 | 数值 |
|------|------|
| 测试文件总数 | **109 个** **[t17 修正]** 实测 **144 个** |
| 测试函数总数 | **2,640 个** **[t17 修正]** 实测 **3,145 个** |
| 契约测试文件 | 4 个（behavioral/dispatcher/model_api/control_api） |
| CI 矩阵 | 3 平台 × 2 Python（ubuntu/windows/macos × 3.12/3.13） |
| lint 工具 | ruff + mypy（含类型检查） |
| 覆盖率门控 | `--cov-fail-under=40` |

### 2.3 v4.0.0 新增净增量

| 维度 | 新增 |
|------|------|
| 新 Python 代码 | **+8,307 行（29%）** |
| 新核心模块 | **16 个（5,601 行）** |
| 新存储模块 | **2 个（749 行）** |
| 新 Dashboard 路由 | **7 个（1,306 行）** |
| 新测试函数 | **+334（14%）** |
| 新前端 | **Vite + React SPA**（`dashboard-vite/`） |
| 新工具脚本 | **4 个**（`scripts/`） |
| 新依赖 | `numpy>=1.24.0` · `cryptography>=42.0` |

---

## 3. PEV → MAOP 版本跃迁

### 3.1 从 3.5.0 到 4.0.0 的版本跳跃

| 版本 | 日期 | 关键里程碑 |
|------|------|-----------|
| 1.x | 2026-05 | 单文件 `pev.ps1` 原型 |
| 2.x | 2026-06~07 | PowerShell 时代，48 脚本，13 gates |
| 3.0.0 | 2026-07-10 | Python 引擎初版 |
| 3.1.0 | 2026-07-14 | Dashboard v7，42 模块，220 测试 |
| 3.2.0 | 2026-07-16 | PS 引擎归档，711 测试，83 端点 |
| 3.3.0 | 2026-07-18 | 子代理、MCP、工作树 |
| 3.5.0 | 2026-07-18 | Mavis 合并，1,697 测试 |
| **4.0.0** | **2026-07-19** | **16 新模块、Plugin/ReAct/知识图谱/成本、Vite SPA** |

### 3.2 名称空间变化

| 旧（PEV） | 新（MAOP） | 已同步？ |
|----------|-----------|---------|
| `py/pev/` → `py/MAOP/` | ✅ `pyproject.toml` 确认 |
| `pev_loop.py` → `maop_loop.py` | ✅ |
| `pev_execute.py` → `maop_execute.py` | ✅ |
| `pev_verify.py` → `maop_verify.py` | ✅ |
| `pev_plan.py` → `maop_plan.py` | ✅ |
| `pev.ps1` → `maop.ps1` | ✅ |
| `pev.db` → `MAOP.db` | ✅ |
| `PEV_audit_report.md` → `MAOP_audit_report.md` | ✅ |
| `PEV_COMPREHENSIVE_ANALYSIS.md` → `MAOP_COMPREHENSIVE_ANALYSIS.md` | ✅ |
| README 内结构图 | ⚠️ 仍写 `py\\pev\\`、`pev_loop.py` |
| docs/adr/ 内容 | ⚠️ 仍引用 PEV |
| py/README.md | ⚠️ 仍写 pev |

### 3.3 依赖变化

| 依赖 | PEV v3.5.0 | MAOP v4.0.0 | 影响 |
|------|-----------|-------------|------|
| `pydantic-settings` | ✅ 已加 | ✅ | — |
| `requirements.lock` | ✅ 已加 | ✅ | — |
| `numpy` | ❌ 无 | ✅ `>=1.24.0` | 向量计算必需 |
| `cryptography` | ⚠️ 可选（明文降级） | ✅ `>=42.0` **核心依赖** | API Key Vault 加密 |
| `pytest-cov` | ⚠️ 手动安装 | ✅ dev 依赖 | CI 覆盖率门控 |
| `types-PyYAML` | ❌ 无 | ✅ dev 依赖 | mypy 类型检查 |
| mypy 配置 | ❌ 无 | ✅ 完整配置 | 类型安全 |

---

## 4. 七层架构全景

> **[t17 修正 2026-07-21]** 标题沿用历史命名"七层架构"，但下方架构图实际仅绘出 **6 层**（CLI / 编排 / 引擎 / 服务 / 基础设施 / 展示）。原报告将 plugin 列为"跨层扩展点"（图中位于服务层内的 `plugin/` 模块），若将其视为独立扩展层则可勉强凑成"7 层"。建议未来版本要么修订标题为"六层架构 + 跨层插件扩展点"，要么将 plugin 提升为架构图中的独立一层（位于服务层右侧或基础设施层之上）。本报告保留原"七层架构"标题不动，仅在图后追加层次清单核对。

### 4.1 架构图

```
 ┌──────────────────────────────────────────────────────────────────────────┐
 │                            CLI 层 (cli.py)                                │
 │         `MAOP start|stop|status|run|validate|migrate`                     │
 ├──────────────────────────────────────────────────────────────────────────┤
 │                       编排层 (MaopLoop + ReActLoop)                       │
 │  ┌──────────────────┐   ┌──────────────────┐   ┌──────────────────┐     │
 │  │   MaopLoop       │   │   ReactLoop      │   │   Engine (DAG)   │     │
 │  │  Plan→Exec→Verify│   │  Thought→Action→ │   │  拓扑排序 并行层  │     │
 │  │  →Evolve 闭环    │   │  Observation     │   │  多步工作流      │     │
 │  └────────┬─────────┘   └────────┬─────────┘   └────────┬─────────┘     │
 │           │                      │                      │               │
 ├──────────────────────────────────────────────────────────────────────────┤
 │                       引擎层 (5 大引擎)                                   │
 │  ┌───────────┐ ┌───────────┐ ┌───────────┐ ┌───────────┐ ┌───────────┐ │
 │  │ maop_plan │ │maop_execute│ │maop_verify│ │  engine   │ │  evolve   │ │
 │  │ 路由规划   │ │ 委托执行   │ │ 三门验证   │ │ DAG 引擎  │ │ 自进化    │ │
 │  └───────────┘ └───────────┘ └───────────┘ └───────────┘ └───────────┘ │
 ├──────────────────────────────────────────────────────────────────────────┤
 │                         服务层 (6 大服务)                                 │
 │  ┌───────────┐ ┌───────────┐ ┌───────────┐ ┌───────────┐ ┌───────────┐ │
 │  │ model/    │ │ control/  │ │ delegate/ │ │ memory/   │ │   Vault    │ │
 │  │ 模型管理   │ │ 控制平面   │ │ Agent 分发 │ │ 存储管理   │ │ 密钥加密  │ │
 │  └───────────┘ └───────────┘ └───────────┘ └───────────┘ └───────────┘ │
 │  ┌───────────┐                                                          │
 │  │ plugin/   │  ← 新增：插件层（跨层扩展点）                              │
 │  └───────────┘                                                          │
 ├──────────────────────────────────────────────────────────────────────────┤
 │                     基础设施层 (46 个核心模块)                            │
 │                                                                          │
 │  ┌─── 弹性 ────────────────────────────────────────────────────┐        │
 │  │ circuit_breaker · cache · cache_guard · load_balancer       │        │
 │  │ worker_pool · rate_limiter · bloom_filter · filelock        │        │
 │  └─────────────────────────────────────────────────────────────┘        │
 │  ┌─── 持久化 ──────────────────────────────────────────────────┐        │
 │  │ data · message_queue · kv_store · vector · timeseries       │        │
 │  │ migration · db_backup · db_utils · artifact_store          │        │
 │  │ image_store · session · conversation                        │        │
 │  └─────────────────────────────────────────────────────────────┘        │
 │  ┌─── 安全 ────────────────────────────────────────────────────┐        │
 │  │ auth · tls · middleware · guardrail · permission             │        │
 │  │ api_key_vault · change_tracker · plugin (沙箱)              │        │
 │  └─────────────────────────────────────────────────────────────┘        │
 │  ┌─── 可观测 ──────────────────────────────────────────────────┐        │
 │  │ monitoring · event_bus · log_rotate · error_schema           │        │
 │  │ cost_tracker · streaming                                     │        │
 │  └─────────────────────────────────────────────────────────────┘        │
 │  ┌─── 编排 ────────────────────────────────────────────────────┐        │
 │  │ analyzer · runtime · sandbox · context_compressor            │        │
 │  │ state_classifier · services · evolution_strategies          │        │
 │  └─────────────────────────────────────────────────────────────┘        │
 │  ┌─── 通信 ────────────────────────────────────────────────────┐        │
 │  │ mcp_client · mcp_transport · mcp_registry · protocol         │        │
 │  │ subagent · worktree · hook_manager · chat_engine            │        │
 │  │ react_loop · streaming                                       │        │
 │  └─────────────────────────────────────────────────────────────┘        │
 │  ┌─── Agent 管理 ──────────────────────────────────────────────┐        │
 │  │ agent_registry · agent_scanner · capability_matcher          │        │
 │  └─────────────────────────────────────────────────────────────┘        │
 │  ┌─── 知识 ────────────────────────────────────────────────────┐        │
 │  │ knowledge_extractor · knowledge_graph · vector_search        │        │
 │  └─────────────────────────────────────────────────────────────┘        │
 ├──────────────────────────────────────────────────────────────────────────┤
 │                        展示层 (Dashboard)                                 │
 │  ┌──────────────────────────────┐  ┌──────────────────────────────┐     │
 │  │  FastAPI 后端                │  │  前端（统一 Vue3 SPA）       │     │
 │  │  26 个路由模块               │  │  · dashboard/dist-enterprise │     │
 │  │  ~100+ API 端点              │  │    (Vite 构建，原生 JS 已归档) │     │
 │  │  WebSocket 实时推送          │  │  · 11 JS 模块                │     │
 │  │  Prometheus 指标             │  │  · 3 级边框设计系统          │     │
 │  └──────────────────────────────┘  └──────────────────────────────┘     │
 └──────────────────────────────────────────────────────────────────────────┘
```

### 4.2 数据流（带 ReAct 分支）

```
Task Input
   │
   ▼
┌──────────────────────┐
│  MaopPlan            │
│  · _route_by_config  │──→ 配置路由（agent.yaml）
│  · _route_by_keyword │──→ 硬编码兜底
│  · _build_fallback   │──→ primary→fallback→tertiary
└──────────┬───────────┘
           │ routing_key + selected_agent
           ▼
┌──────────────────────┐      ┌──────────────────────┐
│  MaopExecute         │─────▶│  ReActLoop (可选)     │
│  · Guardrail 预检    │      │  Thought→Action→Obs  │
│  · CircuitBreaker    │      │  · FunctionCallBridge │
│  · Permission.check  │      │  · ChangeTracker     │
│  · CostTracker.record│      │  · ConvManager       │
│  · Streaming         │      └──────────┬───────────┘
└──────────┬───────────┘                 │
           │                             │
           ▼                             │
┌──────────────────────┐                 │
│  Delegate Dispatcher │◀────────────────┘
│  · 5 drivers         │
│  · Subagent dispatch │
│  · Worktree isolation│
└──────────┬───────────┘
           │ PevResult
           ▼
┌──────────────────────┐
│  MaopVerify          │
│  · exit_code         │
│  · output            │
│  · content-safety    │
│  · schema (新增)     │
│  · state_classifier  │
└──────────┬───────────┘
           │
           ▼
┌──────────────────────┐
│  Evolve              │
│  · analyze           │
│  · suggest           │
│  · apply / promote   │
│  · evolution_strategy│ (新增)
└──────────┬───────────┘
           │
           ▼
┌──────────────────────┐
│  Memory Store        │
│  · store (FTS5)      │
│  · vector_search     │ (新增)
│  · consolidator      │
│  · dream             │
│  · knowledge_graph   │ (新增)
└──────────────────────┘
```

### 4.3 层次清单核对（t17 修正 2026-07-21）

根据上方 4.1 架构图，实际绘制的层次为：

| # | 层 | 主要组件 | 备注 |
|---|---|---------|------|
| 1 | CLI 层 | `cli.py` (`MAOP start/stop/status/run/validate/migrate`) | |
| 2 | 编排层 | `MaopLoop` + `ReactLoop` + `Engine (DAG)` | |
| 3 | 引擎层 | `maop_plan` / `maop_execute` / `maop_verify` / `engine` / `evolve` | |
| 4 | 服务层 | `model/` / `control/` / `delegate/` / `memory/` / `Vault` / `plugin/` | plugin 在图内标注为"跨层扩展点" |
| 5 | 基础设施层 | 9 个子分类（弹性 / 持久化 / 安全 / 可观测 / 编排 / 通信 / Agent 管理 / 知识）共 ~46 → 实测 94 模块 | t17 修正模块数 |
| 6 | 展示层 | FastAPI 后端 + 前端（**t20 修正**：已统一为 Vue3 SPA `dashboard/dist-enterprise/`，原生 JS 版本归档至 `archive/js-dashboard/`） | |

**结论**：实际架构图为 **6 层 + 1 跨层扩展点（plugin）**。如计入 plugin 为独立层则可凑成 7 层，但其在图中位置属于服务层内部，并非独立水平分层。原报告标题"七层架构"系命名精度问题，不影响架构实质。

---

## 5. v4.0.0 新增 16 个核心模块详解

### 5.1 插件系统 —— `core/plugin.py`（632 行）

**架构地位**：MAOP v4.0.0 最重要的新增模块。让 MAOP 从"应用"变为"平台"。

```
PluginManager
├── discover(root)
│   └── 扫描 plugins/*/MAOP-plugin.yaml → list[PluginManifest]
├── load(name)
│   ├── SHA-256 checksum 验证
│   ├── PluginSandbox._restrict_imports()  → 导入守卫
│   ├── PluginSandbox._restrict_builtins() → 受限执行
│   └── importlib.import_module() → 获取 maop_plugin_init / maop_plugin_shutdown
├── start(name)
│   └── 调用 maop_plugin_init()（带超时）
├── stop(name)
│   └── 调用 maop_plugin_shutdown()
├── reload(name) → stop + load + start
├── list() → list[PluginState]
└── bridge_event_bus() → HookManager 桥接
    └── 插件可注册任意生命周期钩子
```

**PluginManifest（`MAOP-plugin.yaml`）**：
```yaml
name: my-tool
version: 1.0.0
entry: main.py
checksum: sha256-xxx
allowed_imports: [json, re, httpx]
hooks: [agent_pre_dispatch, loop_complete]
timeout: 30
```

**安全层一览**：
| 防护层 | 机制 | 绕过难度 |
|--------|------|---------|
| 路径白名单 | 仅 `plugins/` 目录 | 高 |
| SHA-256 校验 | manifest checksum 强制验证 | 高 |
| 受限 builtins | exec/eval/open/__import__ 被安全替换 | 中 |
| 导入守卫 | 仅 `allowed_imports` 清单内的模块 | 中 |
| 超时保护 | init 超时限制 | 低 |
| HookManager 隔离 | 插件只能注册钩子，不能直接操作 MAOP 内部 | 中 |

### 5.2 ReAct 循环引擎 —— `core/react_loop.py`（348 行）

**核心逻辑**：

```python
async def run(self, task, agent, dispatcher):
    while iteration < self._config.max_iterations:
        # 1. THOUGHT: 调用 LLM 生成推理
        response = await self._llm.chat(messages)
        thought = parse_response(response)  # 含 tool_calls 或 final_answer
        
        if thought.type == "final_answer":
            return FinalResult(answer=thought.content)
        
        # 2. ACTION: 执行工具
        for tool_call in thought.tool_calls:
            permission = await self._permission.check(agent, tool_call.name)
            if permission.decision == "deny":
                raise PermissionError(tool_call.name)
            
            tool_result = await self._function_bridge.execute(tool_call)
            self._change_tracker.record(tool_call, tool_result)
            self._cost_tracker.record(...)
        
        # 3. OBSERVATION: 注入结果
        messages.append({"role": "tool", "content": tool_result})
        iteration += 1
```

**配置项**（`ReactConfig`）：
- `max_iterations` — 最大循环次数（默认 10）
- `tools` — 可用工具列表
- `provider` — LLM 供应商
- `model` — 使用的模型
- `temperature` — 推理温度

**集成点**：
- `FunctionCallBridge`（`core/function_call.py`）— 工具执行
- `ConversationManager`（`core/conversation.py`）— 消息历史
- `ChangeTracker`（`core/change_tracker.py`）— 文件变更监控
- `PermissionManager`（`core/permission.py`）— 操作审批
- `CostTracker`（`core/cost_tracker.py`）— Token 记录

### 5.3 实时成本追踪 —— `core/cost_tracker.py`（366 行）

**数据模型**：

```python
CostEntry {
    id, session_id, agent, model,
    prompt_tokens, completion_tokens, total_tokens,
    cost_usd,         # 自动计算（按模型定价）
    latency_ms,
    metadata, created_at
}

CostSummary {
    total_prompt_tokens, total_completion_tokens, total_tokens,
    total_cost_usd, total_calls
}
```

**内置定价表**（支持自定义覆盖）：

| 模型 | 输入价格（$/1M tokens） | 输出价格（$/1M tokens） |
|------|----------------------|-----------------------|
| GPT-4o | 2.50 | 10.00 |
| GPT-4o-mini | 0.15 | 0.60 |
| Claude 3.5 Sonnet | 3.00 | 15.00 |
| Claude 3.5 Haiku | 0.80 | 4.00 |
| DeepSeek V3 | 0.27 | 1.10 |
| DeepSeek R1 | 0.55 | 2.19 |
| Gemini Pro | 0.35 | 1.05 |
| Gemini Flash | 0.075 | 0.30 |

**Dashboard API**：
- `GET /api/cost/summary` — 总览
- `GET /api/cost/by-model` — 按模型汇总
- `GET /api/cost/by-agent` — 按 Agent 汇总
- `GET /api/cost/by-session` — 按会话汇总

### 5.4 变更追踪 —— `core/change_tracker.py`（345 行）

**数据模型**：

```python
FileChange { path, change_type, old_hash, new_hash, old_size, new_size }
SnapshotInfo { id, workdir, label, created_at, total_files, total_changes }
ChangeDiff { added: [FileChange], modified: [FileChange],
             deleted: [FileChange], has_unauthorized: bool }
```

**三种变更类型**：
- `added` — 新增文件
- `modified` — 修改文件（内容哈希变化）
- `deleted` — 删除文件

**Dashboard API**：
- `GET /api/react/snapshots` — 快照列表
- `POST /api/react/snapshots` — 创建快照
- `GET /api/react/diff` — 比较差异
- `POST /api/react/rollback` — 回滚到快照

### 5.5 工件存储 —— `core/artifact_store.py`（270 行）

**数据模型**：

```python
ArtifactVersion {
    id, artifact_name, version, content, content_hash,
    size_bytes, tag, metadata, created_at
}

ArtifactInfo {
    name, latest_version, total_versions, total_size_bytes,
    first_created, last_modified
}
```

**Dashboard API**：
- `GET /api/react/artifacts` — 工件列表
- `POST /api/react/artifacts` — 保存工件
- `GET /api/react/artifacts/{name}` — 获取最新
- `GET /api/react/artifacts/{name}/versions` — 版本历史
- `POST /api/react/artifacts/{name}/restore` — 回滚

### 5.6 知识抽取 —— `core/knowledge_extractor.py`（436 行）

**支持的实体类型**：`function` / `class` / `module` / `api` / `concept` / `dependency`

**支持的关系类型**：`uses` / `depends_on` / `extends` / `implements` / `calls` / `related_to`

**抽取流程**：
```
Text Input
   ↓
tokenize + sentence split
   ↓
regex pattern matching（函数签名、类定义、import 语句）
   ↓
Entity + Relation 置信度评分
   ↓
SQLite 持久化（entities 表 / relations 表）
```

### 5.7 知识图谱 —— `core/knowledge_graph.py`（354 行）

**查询能力**：
- `get_neighbors(entity, depth=1)` — 邻居节点
- `find_paths(source, target, max_depth=5)` — 路径发现
- `build_context(entity, max_depth=2)` — LLM 上下文组装
- `infer_transitive()` — 传递闭包推理（uses、depends_on 等）
- `export_cytoscape()` — Cytoscape.js 可视化导出
- `subgraph(nodes, depth=1)` — 子图提取

**使用示例**：
```python
kg = KnowledgeGraph(root_dir="/path/to/MAOP")
# 获取 AuthService 的关联上下文
context = kg.build_context("AuthService", max_depth=2)
# → 返回：AuthService 相关的模块、类、依赖关系描述文本
```

### 5.8 LLM 供应商统一抽象 —— `core/llm_provider.py`（604 行）

**统一接口**：
```python
class LLMProvider:
    async def chat_completion(self, messages, model, **kwargs) -> ChatResult
    async def completion(self, prompt, model, **kwargs) -> CompletionResult
    async def embedding(self, texts, model, **kwargs) -> EmbeddingResult
```

**适配器**：
| 供应商 | 适配器 | 状态 |
|--------|--------|------|
| OpenAI | `OpenAIProvider` | ✅ |
| Anthropic | `AnthropicProvider` | ✅ |
| Google Gemini | `GeminiProvider` | ✅ |
| Ollama | `OllamaProvider` | ✅ |
| 自定义 | `CustomProvider` | ✅ |

**特性**：
- 自动重试（指数退避）
- 上下文窗口管理（超长输入截断/压缩）
- Token 计数（供应商原生计数 + 估算兜底）
- 模型降级链

### 5.9 Agent 自动发现 —— `core/agent_scanner.py`（399 行）

**扫描流程**：
```
scan_local_clis()
   ↓
每个 CLI 执行 {cli} --version → 检测版本
每个 CLI 执行 {cli} --help  → 解析参数签名
   ↓
能力推断（基于命令名 + help 文本）
   ↓
AgentDef 建议 → 可导出为 agents.yaml 片段
```

**检测的典型 Agent**：claude / codex / mavis / opencode / aider / goose / swe-agent / autogen 等

### 5.10 Agent 注册中心 —— `core/agent_registry.py`（341 行）

```python
AgentRegistry
├── register(agent_def)     → agent 元数据
├── unregister(name)        → 移除 agent
├── get(name)               → AgentDef
├── list()                  → 所有 agent
├── search(capability)      → 按能力搜索
├── health_check(name)      → 检查 agent 可运行
└── suggest_agent(task)     → 按任务推荐 agent
```

### 5.11 能力匹配 —— `core/capability_matcher.py`（196 行）

```python
class CapabilityMatcher:
    def match(task: str, agents: list[AgentDef]) -> list[ScoredAgent]:
        # 关键字匹配 → capability 映射
        # 支持语义近似（Levenshtein）
        # 返回按匹配度排序的 agent 列表
```

### 5.12 聊天引擎 —— `core/chat_engine.py`（375 行）

```python
class ChatEngine:
    async def chat(self, session_id, message, agent) -> ChatResponse
        # 1. 加载会话上下文
        # 2. 调用 LLM
        # 3. 支持流式响应
        # 4. 支持工具调用
        # 5. 保存到会话历史
```

### 5.13 配置运行时修改 —— `core/config_mutator.py`（253 行）

```python
class ConfigMutator:
    def set_agent(agent_name, field, value)    # 修改 agent 配置
    def set_model(model_name, field, value)     # 修改模型配置
    def set_routing(key, field, value)          # 修改路由配置
    def rollback()                              # 回滚配置变更
    def diff()                                  # 查看配置差异
```

### 5.14 进化策略引擎 —— `core/evolution_strategies.py`（284 行）

**5 种策略**：

| 策略 | 目标 | 方法 |
|------|------|------|
| `strategy_success_rate` | 提升成功率 | 优先选择高成功率 agent |
| `strategy_latency` | 降低延迟 | 优先选择快速 agent |
| `strategy_diversity` | 提升多样性 | 轮询多 agent |
| `strategy_fallback` | 优化降级链 | 动态调整 fallback 顺序 |
| `strategy_balanced` | 均衡 | 综合加权评分 |

### 5.15 图片存储 —— `core/image_store.py`（247 行）

```python
class ImageStore:
    def save(path_or_bytes, metadata) -> ImageRecord
    def get(image_id) -> bytes
    def list(agent=None, tags=None) -> list[ImageRecord]
    def delete(image_id)
```

---

## 6. 基础设施层模块矩阵

### 6.1 完整模块清单

| 分类 | 模块 | 行数 | 功能 |
|------|------|------|------|
| **弹性** | | | |
| | `circuit_breaker.py` | 529 | 三态断路器 + 故障转移链 |
| | `cache.py` | 415 | LRU + TTL 缓存 |
| | `cache_guard.py` | 350 | SingleFlight + 防击穿/雪崩 |
| | `load_balancer.py` | 310 | 智能路由 |
| | `worker_pool.py` | 280 | 并行执行 |
| | `rate_limiter.py` | 210 | Token bucket + 滑动窗口 |
| | `bloom_filter.py` | 95 | 布隆过滤器（mmh3） |
| | `filelock.py` | 85 | 跨进程文件锁 |
| **持久化** | | | |
| | `data.py` | 552 | 数据库层 |
| | `message_queue.py` | 675 | 消息队列（优先级/死信/幂等） |
| | `kv_store.py` | 375 | 键值存储 |
| | `vector.py` | 527 | 向量存储 |
| | `timeseries.py` | 380 | 时序存储 |
| | `migration.py` | 290 | 数据迁移 |
| | `db_backup.py` | 230 | 数据库备份 |
| | `db_utils.py` | 180 | SQLite 工具 |
| | `artifact_store.py` | 270 | **版本化工件存储** |
| | `image_store.py` | 247 | **图片存储** |
| | `session.py` | 275 | **会话管理** |
| | `conversation.py` | 275 | **对话管理**（原 `loop_models.py` 提取）|
| **安全** | | | |
| | `auth.py` | 397 | 认证（JWT + bcrypt） |
| | `tls.py` | 120 | TLS 加密 |
| | `middleware.py` | 210 | 中间件 |
| | `guardrail.py` | 180 | 护栏 |
| | `permission.py` | 159 | **权限管理** |
| | `api_key_vault.py` | 148 | **密钥加密** |
| | `change_tracker.py` | 345 | **文件变更追踪** |
| | `plugin.py` | 632 | **插件系统（含沙箱）** |
| **可观测** | | | |
| | `monitoring.py` | 458 | 结构化日志 + 指标 |
| | `event_bus.py` | 386 | 事件总线 |
| | `log_rotate.py` | 110 | 日志轮转 |
| | `error_schema.py` | 95 | 错误模型 |
| | `cost_tracker.py` | 366 | **成本追踪** |
| | `streaming.py` | 194 | **流式输出** |
| **编排** | | | |
| | `analyzer.py` | 460 | 语义分析 |
| | `runtime.py` | 424 | 运行时 |
| | `sandbox.py` | 280 | 沙箱 |
| | `context_compressor.py` | 406 | 上下文压缩 |
| | `state_classifier.py` | 230 | 状态分类 |
| | `services.py` | 151 | **服务层** |
| | `evolution_strategies.py` | 284 | **进化策略** |
| **通信** | | | |
| | `mcp_client.py` | 249 | MCP 客户端 |
| | `mcp_transport.py` | 274 | MCP 传输层 |
| | `mcp_registry.py` | 165 | MCP 注册中心 |
| | `protocol.py` | 266 | 协议注册 |
| | `subagent.py` | 238 | 子代理 |
| | `worktree.py` | 185 | 工作树 |
| | `hook_manager.py` | 576 | 钩子管理 |
| | `chat_engine.py` | 375 | **聊天引擎** |
| | `react_loop.py` | 348 | **ReAct 循环** |
| | `streaming.py` | 194 | 流式输出 |
| **Agent 管理** | | | |
| | `agent_registry.py` | 341 | **Agent 注册** |
| | `agent_scanner.py` | 399 | **Agent 扫描** |
| | `capability_matcher.py` | 196 | **能力匹配** |
| **知识** | | | |
| | `knowledge_extractor.py` | 436 | **知识抽取** |
| | `knowledge_graph.py` | 354 | **知识图谱** |
| | `vector_search.py` | 289 | **向量搜索** |

### 6.2 v3.5.0 → v4.0.0 模块变化

```
v3.5.0 core/ (30 模块)               v4.0.0 core/ (46 模块)
──────────────────────                ──────────────────────
(原有 30 模块)                        原有 30 模块（大部分不变）
                                      + 新增 16 模块：
                                      agent_registry.py       341 行
                                      agent_scanner.py        399 行
                                      artifact_store.py       270 行
                                      capability_matcher.py   196 行
                                      change_tracker.py       345 行
                                      chat_engine.py          375 行
                                      config_mutator.py       253 行
                                      cost_tracker.py         366 行
                                      evolution_strategies.py 284 行
                                      image_store.py          247 行
                                      knowledge_extractor.py  436 行
                                      knowledge_graph.py      354 行
                                      llm_provider.py         604 行
                                      plugin.py               632 行
                                      react_loop.py           348 行
                                      services.py             151 行
                                      ────────────────────────
                                      新增小计：5,601 行

memory/ (3 模块)                      memory/ (5 模块)
store.py                              store.py
search.py                             search.py
consolidator.py                       consolidator.py
                                      + manager.py (460行)
                                      + vector_search.py (289行)

dashboard/routers/ (19 路由)           dashboard/routers/ (26 路由)
(19 原有路由)                          (19 原有路由)
                                      + agents.py      (160行)
                                      + chat.py        (248行)
                                      + cost.py        (103行)
                                      + info.py        (360行)
                                      + knowledge.py   (179行)
                                      + plugin.py      (119行)
                                      + react.py       (137行)
```

---

## 7. Dashboard 体系

### 7.1 技术栈

| 组件 | 技术 | 版本 |
|------|------|------|
| 后端框架 | FastAPI | 0.115.0 |
| ASGI 服务器 | Uvicorn | 0.30.6 |
| 后端 Pydantic | Pydantic | 2.13.4 |
| 旧前端 | 零构建 SPA（HTML/CSS/JS） | — |
| 新前端 | **Vite + React SPA** | — |
| 实时推送 | WebSocket（15s 间隔，5s 快照缓存） | — |
| 指标端点 | Prometheus | — |
| 静态资源 | Chart.js（本地供应商，无 CDN） | — |

### 7.2 26 个路由模块

| 路由 | 行数 | 功能 |
|------|------|------|
| `system.py` | 419 | 系统管理、审计、概览、配置 |
| `auth.py` | 405 | 登录、认证状态、API 密钥、RBAC |
| `info.py` | **360（新增）** | 系统信息、版本、健康 |
| `model.py` | 263 | 模型 CRUD、模型切换、供应商管理 |
| `chat.py` | **248（新增）** | 聊天端点、会话 |
| `control.py` | 215 | run/stop/pause/resume/validate |
| `knowledge.py` | **179（新增）** | 知识图谱查询、可视化 |
| `agents.py` | **160（新增）** | Agent 注册、扫描、管理 |
| `session.py` | 151 | 会话 CRUD、恢复、预算 |
| `memory.py` | 143 | 存储 CRUD、搜索、注入 |
| `react.py` | **137（新增）** | ReAct 循环管理 |
| `hook.py` | 136 | 钩子注册、触发 |
| `data_knowledge.py` | 147 | 知识端点 |
| `data_overview.py` | 133 | 概览数据 |
| `protocol.py` | 129 | 协议管理 |
| `subagent.py` | 128 | 子代理管理 |
| `tls.py` | 120 | TLS 管理 |
| `plugin.py` | **119（新增）** | 插件管理 |
| `mcp.py` | 102 | MCP 管理 |
| `cost.py` | **103（新增）** | 成本管理 |
| `state.py` | 97 | 状态查询 |
| `evolve.py` | 92 | 自进化管理 |
| `data_system.py` | 91 | 系统数据 |
| `worktree.py` | 87 | 工作树管理 |
| `permission.py` | 87 | 权限管理 |
| `error_handler.py` | 64 | 错误处理 |
| `data_graph.py` | 56 | 图表数据 |

### 7.3 Dashboard 安全特性

| 特性 | 实现 |
|------|------|
| 认证 | `AuthMiddleware` + JWT + bcrypt |
| 速率限制 | `RateLimitMiddleware`（30 RPS / 60 burst） |
| CORS | allowlist 配置（默认 localhost:9079/8080） |
| TLS | TLSv1.2+，拒绝 TLSv1/TLSv1.1 |
| WebSocket 认证 | 连接前校验 JWT token |
| 全局异常处理 | 捕获未处理异常，返回通用错误信息 |

### 7.4 前端架构（双轨并存）

```
dashboard/（零构建 SPA，保留兼容）
├── index.html
├── style.css（3 级边框设计系统）
├── favicon.svg
├── js/
│   ├── app-core.js
│   ├── app-overview.js
│   ├── app-control.js
│   └── ...（共 11 模块）
└── .backup/

dashboard-vite/（Vite + React SPA，新增）
├── package.json
├── vite.config.js
├── index.html
├── src/
│   └── （React 组件源码）
├── public/
└── node_modules/
```

---

## 8. 测试体系

### 8.1 测试规模

| 指标 | v3.5.0 | v4.0.0 | 增长 |
|------|--------|--------|------|
| 测试文件数 | 100 | **109** | +9 |
| 测试函数数 | 2,306 | **2,640** | **+334** |
| 契约测试 | 4 文件 | 4 文件 | — |
| CI 矩阵 | 3×2 | 3×2 | — |

### 8.2 v4.0.0 新增测试

| 测试文件 | 测试函数 | 测试内容 |
|---------|---------|---------|
| `test_plugin_cost.py` | 43 | PluginManager(19) + CostTracker(24) |
| `test_react_loop.py` | 31 | ReactLoop + ChangeTracker + ArtifactStore |
| `test_session.py` | 36 | Session + Conversation + ProjectContext |
| `test_function_call.py` | 25 | FunctionCallBridge + ToolSchemaGenerator |
| `test_output_parser.py` | 27 | OutputParser + SchemaGate |

### 8.3 CI 流水线

`.github/workflows/ci.yml`：

```
lint (ruff + mypy)
   ↓
unit tests (pytest, --cov-fail-under=40)
   ↓
contract tests (pytest tests/contract/)
   ↓
Docker build
   ↓
pip-audit（安全扫描）
```

---

## 9. 配置体系

### 9.1 配置文件

| 文件 | 大小 | 内容 |
|------|------|------|
| `config/agents.yaml` | 8,041 字节 | 18 agent 定义 + 14 路由 + hooks |
| `config/models.yaml` | 12,544 字节 | 7 供应商 + 12 模型 + 4 策略 |
| `config/rules.yaml` | 158 字节 | 通用规则 |
| `config/mcp_servers.yaml` | 693 字节 | MCP 服务器配置（模板） |
| `py/.env.example` | 2,887 字节 | 环境变量模板 |

### 9.2 Agent 配置结构

```yaml
agents:
  claude:
    driver: cli
    cli: "claude"
    cli_args: "-p '{task}'"
    model_ref: "claude-sonnet-4"
    capabilities: [chat, code, design]
    fallback: codex
    timeout: 120
    retry: 1
    hooks:
      pre_dispatch: [my_guard]
    
  mavis:
    driver: cli
    cli: "mavis"
    subagents:
      verifier:
        cli_args: "--verify '{task}'"
        capabilities: [verify, review]
      coder:
        cli_args: "--code '{task}'"
        capabilities: [code, refactor]
```

### 9.3 加载流程

```
ConfigLoader.load()
   ├── agents.yaml → AgentDef[] + routing: dict[str, RouteEntry] + hooks
   ├── models.yaml → ModelDef[] + ProviderDef[] + PolicyDef[]
   ├── rules.yaml  → retry/timeout rules
   └── PevConfig (Pydantic model)
        │
        ├── ConfigHotReload (watches files)
        ├── ConfigMutator (runtime changes)
        └── Dispatcher._resolve_agent() (supports parent/child)
```

---

## 10. 依赖与构建体系

### 10.1 核心依赖（`pyproject.toml`）

| 包 | 版本规格 | 用途 |
|----|---------|------|
| fastapi | ==0.115.0 | Web 框架 |
| uvicorn[standard] | ==0.30.6 | ASGI 服务器 |
| pydantic | >=2.9.0,<3.0 | 数据模型 |
| pydantic-settings | ==2.5.2 | 配置加载 |
| pyyaml | ==6.0.2 | YAML 解析 |
| httpx | >=0.27.0,<1.0 | HTTP 客户端 |
| python-dotenv | ==1.0.1 | .env 加载 |
| mmh3 | ==5.2.1 | MurmurHash3（布隆过滤器） |
| **numpy** | **>=1.24.0** | **向量计算** |
| **cryptography** | **>=42.0** | **密钥加密** |

### 10.2 开发依赖

| 包 | 用途 |
|----|------|
| pytest>=8.0 | 测试框架 |
| pytest-asyncio>=0.23 | 异步测试 |
| pytest-cov>=5.0 | 覆盖率 |
| ruff>=0.4 | 代码检查 |
| types-PyYAML>=6.0 | YAML 类型存根 |

### 10.3 可选依赖

| 额外 | 包 | 用途 |
|------|----|------|
| dev | 上表 | 开发/测试 |
| ml | sentence-transformers>=2.0 | 语义搜索 |

### 10.4 锁文件

`requirements.lock` 精确锁定 32 个包（含 24 个传递依赖），实现可重现构建。

### 10.5 构建系统

| 工具 | 版本/配置 |
|------|---------|
| 打包 | hatchling |
| Python | >=3.10 |
| lint | ruff（line-length=100, target=py310） |
| 类型 | mypy（完整配置，含路由器覆盖） |
| 测试 | pytest（asyncio_mode=auto） |

### 10.6 Docker 化

```
Dockerfile（2,033 字节）：多阶段构建
docker-compose.yml（2,144 字节）：编排
```

---

## 11. 安全审计状态

### 11.1 已修复漏洞（S-01～S-11 + ADR-010）

| 编号 | 漏洞 | 严重程度 | 状态 |
|------|------|---------|------|
| S-01 | `db_backup.py` VACUUM INTO 注入 | Critical | ✅ |
| S-02 | `auth.py` JWT 密钥临时生成 | Critical | ✅ |
| S-03 | `auth.py` APIKeyStore 线程不安全 | High | ✅ |
| S-04 | `dispatcher.py` PS cli_args 注入 | Critical | ✅ |
| S-05 | `system.py` pip 白名单绕过 | High | ✅ |
| S-06 | `model.py` model/switch 验证缺失 | High | ✅ |
| S-07 | `auth.py` 登录暴力破解 | High | ✅ |
| S-08 | `middleware.py` 公开路径遗漏 | Medium | ✅ |
| S-09 | `tls.py` 占位符证书 | High | ✅ |
| S-10 | `data.py` query() 内部 API 暴露 | Medium | ✅ |
| S-11 | `kv_store.py` 连接泄漏 | Medium | ✅ |
| SQL 注入 | `message_queue._count()` 表名注入 | Critical | ✅ |
| 路径注入 | `db_backup.py` VACUUM INTO 路径 | Critical | ✅ |
| 校验缺失 | `migration.py` 校验和 | High | ✅ |
| GET→POST | `/api/control/run` 状态变更 | High | ✅ |

### 11.2 v4.0.0 新增安全层

| 安全能力 | 实现 | 新增 |
|---------|------|------|
| 插件安全沙箱 | `PluginSandbox`（受限 builtins + 导入守卫 + 超时） | ✅ |
| SHA-256 校验 | 插件 manifest 强制校验 | ✅ |
| 变更审查 | `ChangeTracker` 检测未授权文件修改 | ✅ |
| 权限引擎 | `PermissionManager` allow/ask/deny | ✅ |
| 密钥加密核心依赖 | `cryptography` 从可选升为核心 | ✅ |
| CORS 强化 | 环境变量 `PEV_CORS_ORIGINS` 配置 | ✅ |
| TLS 版本强制 | `PEV_TLS_MIN_VERSION` 配置 | ✅ |
| mypy 类型检查 | 完整 mypy 配置覆盖所有模块 | ✅ |

### 11.3 多层次安全架构

```
传输层:     TLSv1.2+ (PEV_TLS=1)
认证层:     JWT + bcrypt + 暴力破解保护
授权层:     PermissionManager (allow/ask/deny) + RBAC
速率限制:   30 RPS / 60 burst (PEV_RATE_LIMIT_*)
输入验证:   AST safe_eval + 正则白名单 + 标识符验证
密钥管理:   cryptography Fernet 加密 (PEV_KEY)
审计:       ControlPlane 记录所有控制操作
日志:       结构化日志 + 轮转 (不含敏感信息)
扩展安全:   插件沙箱 + 导入守卫 + SHA-256 checksum
变更检测:   文件快照 + diff + 回滚
```

---

## 12. 旧报告错误纠正清单

### 12.1 此前报告中的错误

| 旧报告断言 | 实际状态 | 错误原因 |
|-----------|---------|---------|
| "项目版本 3.2.x" | **4.0.0** | 基于过时文档，未检查最新文件 |
| "pydantic-settings 缺失" | ✅ `pyproject.toml:11` 已声明 | 当时未读取最新 pyproject.toml |
| "data_bridge 有 PS 回退" | ✅ 纯 Python，零 PS 引用 | 未验证 data_bridge.py 内容 |
| "状态分裂脑" | ✅ ADR-011 已实施 | 未验证 pev_loop.py 实际路径 |
| "无锁文件" | ✅ requirements.lock 存在 | 未验证 |
| "README 陈旧" | ✅ 已重写 | 未读新版本 |
| "动态路由未移植" | ✅ dynamic_router.py (364 行) | 未检查文件 |
| "migration.py 空壳" | ✅ 实装含 checksum 校验 | 未检查 |
| "核心模块 13 个" | **46 个** | 未遍历实际目录 |
| "测试 34 个文件" | **109 个（2,640 测试）** | 未检查实际文件 |
| "无 MCP 支持" | ✅ 完整 MCP 框架 | 未检查 |
| "无子代理" | ✅ subagent.py (238 行) | 未检查 |
| "无权限管理" | ✅ permission.py (159 行) | 未检查 |
| "无流式输出" | ✅ streaming.py (194 行) | 未检查 |

### 12.2 教训

1. **每次评估前必须完整遍历目录结构**，不全依赖于会话记忆
2. **所有结论必须基于实际文件内容**，而非文档或旧的记忆
3. **版本号是最容易过时但最关键的信号**——始终从 pyproject.toml / \_\_init\_\_.py 获取
4. **测试规模和代码量是项目健康度的核心指标**

---

## 13. 剩余问题与行动建议

### 13.1 P1：名称空间统一

| 位置 | 问题 | 建议 | 工作量 |
|------|------|------|--------|
| `README.md` 结构图 | `py\\pev\\` / `pev_loop.py` 等旧名 | 执行 `scripts/rename_pev_to_maop.py` | 极小 |
| `docs/adr/` 全部 12 个文件 | 内容仍引用 PEV | 批量 sed 替换 | 小 |
| `py/README.md` | 包说明仍写 pev | 手动更正 | 极小 |

### 13.2 P2：质量优化

| 问题 | 位置 | 说明 | 工作量 |
|------|------|------|--------|
| `_ensure_db_schema` 吞异常 | `data_bridge.py:77-78` | `except Exception: pass` 不记录日志 | 极小 |
| `MCP 服务器未激活` | `config/mcp_servers.yaml` | `servers: []` 为空模板 | 小 |
| 双 Dashboard 并存 | `dashboard/` + `dashboard-vite/` | 过渡期，最终应统一到 Vite | 大 |
| Vite `node_modules/` | `dashboard-vite/node_modules/` | 需要确认是否在 .gitignore | 极小 |
| `requirements.lock` 未同步 v4.0.0 新增依赖 | `py/requirements.lock` | 缺少 numpy、cryptography 条目 | 极小 |

### 13.3 P3：架构优化（非紧急）

| 建议 | 说明 | 工作量 |
|------|------|--------|
| ADR-012 路由重构 | 配置路由语义匹配落地 | 中 |
| IR 知识图谱可视化 | Dashboard 前端增加图谱交互 | 中 |
| agent 自动扫描与 agents.yaml 同步 | 扫描结果自动写入配置 | 中 |
| CI 全绿验证 | 在 CI 环境完整跑通 2,640 测试 | 小 |
| 插件市场 | 建立插件注册中心 | 大 |
| 分布式 Agent 执行 | Docker 化 + 水平扩展 | 大 |

---

## 14. 演进路线图

### 14.1 短期（立即）

- [ ] 执行 `scripts/rename_pev_to_maop.py` 同步名称空间
- [ ] 修复 `data_bridge.py` 吞异常问题
- [ ] 更新 `requirements.lock` 反映 `numpy` + `cryptography`
- [ ] 确认 `dashboard-vite/node_modules/` 在 `.gitignore` 中

### 14.2 中期（1-2 周）

- [ ] ADR-012 路由重构落地
- [ ] MCP 服务器配置激活
- [ ] 知识图谱前端可视化
- [ ] Dual Dashboard 合并策略确定
- [ ] CI 全绿验证

### 14.3 长期（1-2 月）

- [ ] 插件市场建立
- [ ] 分布式 Agent 执行
- [ ] Agent 自动扫描与 agents.yaml 联动
- [ ] 知识图谱 LLM 上下文注入深度优化
- [ ] 成本告警与自动化预算管理

---

## 15. 附录

### 15.1 核心文件行数清单

| 文件 | 行数 |
|------|------|
| `maop_loop.py` | 681 |
| `engine.py` | 576 |
| `maop_execute.py` | 390 |
| `evolve.py` | 408 |
| `maop_verify.py` | 293 |
| `maop_plan.py` | 158 |
| `deploy.py` | 439 |
| `cli.py` | 175 |
| `dashboard/server.py` | 448 |
| `dashboard/data_bridge.py` | 637 |

### 15.2 工具脚本

`scripts/` 目录：

| 脚本 | 用途 |
|------|------|
| `rename_pev_to_maop.py`（2,832 字节） | PEV→MAOP 批量重命名 |
| `rename_dist.py`（1,268 字节） | 分发版本同步 |
| `check_admin_coverage.py`（2,628 字节） | 管理端点覆盖检查 |
| `smoke_test_agents.py`（12,533 字节） | Agent 冒烟测试 |

### 15.3 关键数据路径

```
data/
├── MAOP.db       → 主数据库（熔断/委托/指标/检查点）
├── queue.db      → 消息队列
├── human_queue.db → 人工审批队列
├── memory.db     → 存储
├── auth.db       → 认证
├── permissions.db → 权限
├── kv_store.db   → 键值存储
├── prompts.db    → 提示词
├── tools.db      → 工具
├── vectors.db    → 向量
├── timeseries.db → 时序
├── api_keys.db   → 加密密钥
├── mcp_registry.db → MCP 注册
├── worktree.db   → 工作树
├── migrations/   → 迁移 SQL
└── backups/     → 数据库备份
```

---

## 总结

### 核心数据

```
PEV v3.5.0                           MAOP v4.0.0
──────────                           ──────────
📦 28,448 行 Python                  📦 36,755 行 Python  (+29%)
🧪 100 测试文件 / 2,306 测试          🧪 109 测试文件 / 2,640 测试 (+14%)
🏗️  30 core 模块                     🏗️  46 core 模块
🖥️  19 dashboard 路由                🖥️  26 dashboard 路由
📋 零构建 SPA                         📋 零构建 SPA + Vite + React SPA
🔌 无插件系统                          🔌 完整插件系统 + 安全沙箱
🧠 静态 JSON 图谱                     🧠 动态知识图谱 + 抽取 + 推理
💰 无成本管控                          💰 实时成本追踪 + 预算告警
🤖 无自主推理                          🤖 ReAct (Thought→Action→Observation)
🔑 cryptography 可选                  🔑 cryptography 核心依赖
🔢 无 numpy                            🔢 numpy ≥1.24.0 (向量计算)
```

### 评语

MAOP v4.0.0 在不到一天的时间内，从 PEV v3.5.0 完成了项目重命名、新增 16 个核心模块（5,601 行新代码）、7 个 Dashboard 路由、Vite + React 前端、并实现了**从"编排框架"到"企业级多智能体平台"的质变**。插件系统带来了平台化扩展能力，ReAct 循环带来了自主推理能力，知识图谱带来了知识管理能力，成本追踪带来了商业管控能力。这四项核心支柱的同时落地，使得 MAOP 在同类开源项目中具备了显著的差异化优势。

---

## 16. t17 综合修正说明（2026-07-21）

本节集中记录 v4.0.0 评估报告中所有数字与架构表述的实测校正，未在前文就地标注的位置在此统一修正。

### 16.1 规模数字校正对照

| 报告位置 | 原文 | 实测（2026-07-21） | 备注 |
|---------|------|-------------------|------|
| 报告头 / Section 1.1 | 36,755 行 / 2,640 测试 | 82,660 行 py/ 全目录 / 49,092 行 maop/ 主包 / 3,145 测试 | 见 Section 2.1 修正表 |
| Section 2.2 测试规模 | 109 测试文件 / 2,640 测试函数 | **144 测试文件 / 3,145 测试函数** | t06-t16 期间新增 35 个测试文件 |
| Section 2.3 v4.0.0 新增净增量 | +8,307 行 / +16 模块 / +334 测试 | 数据保留（v3.5.0 → v4.0.0 历史快照） | 此为版本迁移时刻的快照，不代表当前累计 |
| Section 3 PEV → MAOP 跃迁 | 28,448 → 36,755 行 / 100 → 109 测试文件 | 数据保留（v3.5.0 → v4.0.0 迁移时刻快照） | 当前累计已达 82,660 行 / 144 测试文件 |
| Section 6 基础设施层模块矩阵 | 46 模块 | **94 模块**（maop/core/ 实测） | 增长来自 t07-t13 新增的 change_tracker/plugin/hook_manager/evolution_loop/a2a/three_layer_memory 等模块及企业版扩展 |
| Section 8 测试体系 | 109 测试文件 / 2,640 测试函数 | **144 测试文件 / 3,145 测试函数** | 同 Section 2.2 |
| Section 11 安全审计 | "109 个（2,640 测试）" | **144 个（3,145 测试）** | 同上 |
| Section 13 行动建议 CI 全绿验证 | "在 CI 环境完整跑通 2,640 测试" | **"在 CI 环境完整跑通 3,145 测试"** | 同上；另注：CI 实际状态见 t06 修正（已移除 continue-on-error） |
| Section 14 演进路线图 | "📦 36,755 行 / 🧪 109 测试文件 / 2,640 测试" | **📦 82,660 行（py/ 全）/ 49,092 行（maop/）/ 🧪 144 测试文件 / 3,145 测试** | 历史快照数字保留，仅追加实测对照 |
| Section 15 评语 | "新增 16 个核心模块（5,601 行新代码）" | 数据保留（v3.5.0 → v4.0.0 迁移时刻快照） | 当前累计新增更多 |

### 16.2 架构表述修正

| 报告位置 | 原文 | 修正 |
|---------|------|------|
| Section 4 标题 | "七层架构全景" | 实测为 **6 层 + 1 跨层扩展点（plugin）**；详见 Section 4.3 层次清单核对 |
| Section 4.1 架构图 | 图示 6 层（CLI / 编排 / 引擎 / 服务 / 基础设施 / 展示） | 图示正确，仅标题与图示不符 |
| Section 6 标题 | "基础设施层模块矩阵（46 模块）" | 实测 **94 模块**；详见 Section 2.1 修正表 |

### 16.3 修正方法说明

- 历史快照数字（v3.5.0 → v4.0.0 迁移时刻）保留不动，因其反映迁移时刻的真实状态
- 当前累计数字以 `[t17 修正]` 标注追加，不覆盖原文
- 架构表述问题就地追加修正说明，不删除原内容
- 所有修正均可在 Section 2.1 修正表中查到对照来源

### 16.4 前端架构统一为 Vue3（t20 修正 2026-07-21）

v4.0.0 评估撰写时，前端处于"双轨并存"过渡期（零构建 SPA + Vite React）。该状态已于 2026-07-21 收敛为**统一 Vue3 SPA**：

**当前架构（t20 后）**：
- 个人版与企业版共享同一份 Vue3 构建（`dashboard/dist-enterprise/`）
- `py/maop/dashboard/server.py` 静态文件查找顺序：`dashboard/dist-enterprise/` → `dashboard-enterprise/`（dev 源码 fallback）→ 仅 warning fallback
- 原生 JS Dashboard（`index.html` / `style.css` / `js/` / `dist` / `.backup`）已整体归档至 `archive/js-dashboard/`
- `dashboard/` 目录已清理，仅保留 `dist-enterprise/` 与 `favicon.svg`
- CI workflow `frontend` job 在 `dashboard-enterprise/` 跑 `npm ci + npx vite build`，产物上传至 `dashboard/dist-enterprise/`
- Dockerfile `COPY dashboard/ dashboard/` 正确携带统一构建产物
- Vitest 9 passed；Vite 构建 17/17 页面

**原文修正对照**：

| 行 | 原文（v4.0.0 过渡期表述） | 当前实际状态 |
|---|---|---|
| L68 | `零构建 SPA + Vite + React SPA` / `dashboard/ + dashboard-vite/` | 统一为 `Vue3 SPA` / `dashboard/dist-enterprise/`（`dashboard-vite/` 已重命名为 `dashboard-enterprise/`） |
| L103 | `前端（Dashboard）：2 套：零构建 SPA + Vite React SPA` | 统一为 `1 套：Vue3 SPA`（原生 JS 已归档，非运行时双轨） |
| L125 | `新前端：Vite + React SPA（dashboard-vite/）` | `Vue3 SPA（dashboard-enterprise/，构建产物在 dashboard/dist-enterprise/）` |
| L248-250 | 架构图标注"前端（双轨并存）/ index.html（零构建 SPA）/ dashboard-vite/（Vite + React）" | 已就地修正为"统一 Vue3 SPA / dashboard/dist-enterprise（Vite 构建，原生 JS 已归档）" |
| L329 | `展示层：FastAPI 后端 + 前端（双轨：零构建 SPA + Vite React）` | 已就地修正为"统一为 Vue3 SPA `dashboard/dist-enterprise/`，原生 JS 归档至 `archive/js-dashboard/`" |
| L782 | `旧前端：零构建 SPA（HTML/CSS/JS）` | 历史快照保留；现状：原生 JS 已归档，不再是运行时 fallback |

**剩余低优先级项**（来自前端整合记录，非阻塞）：
- SSE composable 重构
- 静态说明页合并到 Settings
