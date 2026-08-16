# MAOP 架构债治理方案 — 记忆系统统一 & 上帝模块拆分

> 生成日期：2026-08-14 · 基于 master @ 3ab2a27 实测调查
> 性质：**方案文档**（非已执行的重构），供在有安全文件操作的环境（本机 git）分阶段执行

---

## 一、执行摘要

本次调查发现两个关键事实，直接改变任务的执行方式：

1. **记忆系统"统一"已有半成品**：`maop/memory/facade.py` + `maop/memory/unified.py` 已经存在，是前序迭代做的"Facade 模式统一入口 + 公共接口契约"。项目作者已明确评估过"强行合并风险极高（L1 层语义不同：LRU vs ConversationManager）"，故保留双实现、用 Facade 收敛。**所以本任务不是"从零统一"，而是"推进 facade 收敛 + 清理第三套重叠"**。
2. **当前沙箱环境 `git rm` 删除文件会误删大量无关文件**（实测：`git rm` 单个文件触发 180 个连带删除、且 staged 不生效），故"删文件/拆文件"类破坏性操作**不能在本环境执行**，需本机 git 或经用户确认后分批处理。

---

## 二、三套记忆系统现状（实测）

| # | 系统 | 位置 | 核心类 | 语义 | 活跃调用方 | 引用数 |
|---|------|------|--------|------|-----------|--------|
| 1 | MemoryManager 系列 | `maop/memory/`（10 文件） | `MemoryStore`/`MemoryManager` | L1 ConversationManager（对话上下文）/ L2 MemoryStore / L3 知识图谱 | `chat_engine`、`dashboard/routers/memory`、`control`、`data` | 17 |
| 2 | ThreeLayerMemory 系列 | `maop/core/memory/`（10 文件） | `ThreeLayerMemory` | L1 LRUCache（working）/ L2 episodic / L3 semantic | `agent_performance`、`evolution_loop`、`dashboard/routers/memory` | 35 |
| 3 | Agent 上下文 | `maop/core/agent/memory_ctx/`（4 文件） | `AgentMemory`/`ProjectContext`/`Worktree` | agent 会话记忆 + 项目上下文 + git worktree 状态 | `agent_evolution`、`core/agent/__init__.py` re-export | 少量 |

**关键澄清**：这三套**不是"重复实现"**，而是**职责分工**（chat 上下文 vs agent 任务经验 vs agent 工作上下文）。`unified.py` 已通过 `shared_db.py` 共享 `maop.db`、`LAYER_ALIASES` 做术语映射（episodic↔short_term、semantic↔long_term），前两套已"逻辑统一、物理分离"。

### 统一路径（推荐，分三步，非破坏性）

1. **P0 — 强制新代码走 facade**：`MemoryFacade` 已存在，但新代码仍在直接 `import ThreeLayerMemory` / `MemoryManager`。加 lint 规则（ruff 自定义规则或 grep 门禁），禁止新调用直接 import 两套底层类。
2. **P1 — 收敛第三套 `memory_ctx`**：评估 `AgentMemory`/`ProjectContext`/`Worktree` 是否可并入 `MemoryFacade` 的 mode 路由（新增 `mode="agent_ctx"`），或明确其为"独立职责"保留（worktree 状态与记忆无关，建议独立）。
3. **P2 — 物理合并（仅当 L1 语义可对齐时）**：`facade.py` 已说明"强行合并风险极高"，故物理合并**不是短期目标**。若未来 L1 统一为单一实现，再合并 `MemoryManager` 与 `ThreeLayerMemory`。

**结论**：三套记忆的"统一"正确动作是 **facade 收敛 + 第三套归类**，而非物理合并。数据迁移（legacy → Unified）在 `unified.py` 已有 `LAYER_ALIASES` 基础，无需一次性大迁移。

---

## 三、上帝模块清单（>500 行，共 30+）

按"拆分价值"排序（职责是否混杂 > 行数大小）：

| 优先级 | 文件 | 行数 | 拆分建议 |
|--------|------|------|---------|
| P0 | `core/memory/three_layer_memory.py` | 1393 | 拆 L1/L2/L3 三层为独立模块（`working_memory.py`/`episodic.py`/`semantic.py`），主类保留为 facade |
| P0 | `core/evolution/evolution_loop.py` | 1347 | 拆 evaluate/suggest/apply 三阶段为独立函数模块 |
| P0 | `core/mcp/mcp_hub.py` | 1151 | 拆 server 管理 / tool 注册 / transport 三块 |
| P1 | `core/tenant/compliance.py` | 1049 | 拆审计 / 合规策略 / 报告生成 |
| P1 | `dashboard/server.py` | 976 | 拆中间件 / 路由注册 / 生命周期（已有 routers/ 子目录，主 app 组装可瘦身） |
| P1 | `core/memory/vector.py` | 959 | 拆 sqlite-vec / HNSW / 索引管理 |
| P2 | `dashboard/routers/agents.py` | 949 | 已拆出 `agents/` 子目录，旧单文件为兼容层，待清理 |
| P2 | 其余 20+ 文件 | 500-900 | 按职责逐个评估，多数"大但职责单一"，无需机械拆分 |

**原则**：拆分按**职责**而非行数。`license_manager.py`(816)、`quota.py`(778)、`llm_provider.py`(886) 等是"单一职责的大文件"，机械拆分反而破坏内聚，**不建议拆**。

---

## 四、死代码清单（26 个零引用扁平文件，待安全环境删除）

实测 26 个 `maop/core/X.py`（扁平）为**零引用**死代码（旧扁平层遗留）：

**有子包副本（22 个，删除后 `core/__init__.py` 的 `__getattr__` 自动 fallback，安全）**：
`auth.py` `backends_redis.py` `cache.py` `data.py` `db_utils.py` `filelock.py` `knowledge_graph.py` `kv_store.py` `message_queue.py` `middleware.py` `monitoring.py` `prompt_version.py` `regression.py` `session.py` `three_layer_memory.py` `analyzer.py` `chat_engine.py` `context_compressor.py` `function_call.py` `hook_manager.py` `react_loop.py` `runtime.py`

**真孤儿（4 个，需逐个确认后删）**：
`cache_guard.py`（shim→reliability.cache，目标存在）、`subagent.py`（shim→subagent_delegation，目标疑已删）、`subagent_manager.py`（shim→subagent_lifecycle，目标存在）、`protocols.py`（独立 Protocol 定义，需再查引用）

**执行方式**：本机 `git rm` 逐个删除 + 每删一个验证 `git status` 无误删（当前沙箱 `git rm` 会误删，不可用）。

---

## 五、执行约束与建议顺序

1. **记忆统一**：纯"加 lint 规则 + 改 import"类操作（非破坏性），可安全执行。建议先做 P0（facade 收敛）。
2. **上帝模块拆分**：涉及"拆文件 + 删旧文件"，破坏性，需本机执行 + 每次拆分跑全量测试。
3. **死代码清理**：26 个文件删除，需本机 `git rm`，当前沙箱不可用。

**建议第一步（可立即安全执行）**：记忆统一的 P0（facade 收敛）——加 ruff/grep 门禁禁止直接 import 两套底层记忆类，这是纯代码规范、零破坏。是否要我先把这一步做了？
