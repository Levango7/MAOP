# MAOP 架构债治理 · 三方案审核稿

> 生成：2026-08-15 · 架构师（高见远）三份方案回传 · 项目总监（大湾区靓仔）整合 + 一致性检查
> 状态：**待用户审核（Phase 1 唯一交互点）** —— 通过后拆分任务、分配专家、调度执行

---

## 执行摘要

三项架构债治理（工具白名单 / 上帝模块拆分 / facade 迁移），架构师已实测代码核实（含具体行号），三份方案 verdict 均为 pass。共同前提：**所有改动走本机 git、每步全量 pytest**（吸取 653f743 测试漂移教训）、沙箱不执行 `git rm`。

---

## 任务1：工具白名单（待你拍板默认策略）

**需求**：`tool_manager.py` 对任意已入库 tool 直接 `shlex.split + subprocess` 执行，无命令级权限校验（审计 P1-4 属实）。

**方案**：
- 新增 `tool_policy.py` + `config/tool_whitelist.yaml`：`mode: audit|enforce` + `allow`（tool_id/命令 glob）+ `deny`（优先级高于 allow）+ 环境变量 `MAOP_TOOL_POLICY_MODE` 覆盖
- 决策顺序：deny 命中→拒绝；allow 命中→放行；均未命中→按 mode（audit 放行+warning / enforce 拒绝）
- 拦截点：`call()` 与 `_call_sync_fallback()` **双执行路径**全覆盖（tool_manager.py:315/432），拒绝返回 `ok=False` 不抛异常（兼容 function_call.py:242）
- 兜底 fail-open：配置文件缺失→audit+warning，避免全平台工具瘫痪
- 绕过面（明示边界）：只约束 ToolManager 三条路径；Dispatcher/CLI 直调子进程旁路不覆盖，登记 open-decisions

**审核结论**：pass，**blocking = 默认策略需你拍板**（架构师推荐 enforce + 三阶段过渡：audit 收集 → `list()` 导出初始 allow 清单 → 人工评审高危命令 → 切 enforce）

---

## 任务2：上帝模块拆分 + 死代码清理（需本机 git）

**需求**：三个 P0 文件违反 ≤300 行规则（1393/1347/1151）；26 个扁平 shim 实测零引用。

**方案**：
- **拆分原则**：按职责用 **Mixin 组合**（主类继承，公开 API 零变化，52 处调用零改动），主文件保留为聚合 facade
- **拆分粒度**（行号已核实）：
  - `three_layer_memory.py` → 6 块：working / episodic / consolidation / semantic / transform / protocol_aliases
  - `evolution_loop.py` → 5 块：perf_loop（纯搬移，最安全先做）/ collectors / analyzers / agent / phases
  - `mcp_hub.py` → 3 块：metrics / ops / compat（call_tool 超限再抽 gate）
- **死代码 26 个**（逐个精确 import 核实）：批次1 删 22 个有子包副本（`core/__init__.__getattr__` 自动 fallback）；批次2 删 4 个真孤儿（含 protocols.py 全库零 import，删前跑 type-check 确认无显式引用）
- **迁移步骤**：每拆一个职责域 = 建子模块 + 主类改继承 + **从主文件删方法体**（防 Mixin 覆盖）+ 立即跑该域测试；每批 `git rm` 后 `git status` 核对无连带删除
- **风险**：MRO/循环 import（子模块禁 import 主文件）、行为回归（每步全量 pytest）、误删（本机分批 + git revert）

**审核结论**：pass，无 blocking。advisory：protocols.py 删前 type-check；mcp_hub 超限再抽。

---

## 任务3：facade 迁移（生产 6 处，批次 A 可沙箱执行）

**需求**：MemoryFacade 已实现+有测试，但生产零迁移（实测生产仅 6 处直连：chat_engine 1 + agent_performance 1 + evolution_loop 1 + dashboard memory 3；"52 处"口径含测试约 40+ 处，本任务只迁生产）。

**方案**：
- **迁移映射**：chat_engine → `mode="chat"`；agent_performance/evolution_loop → `mode="agent"`；dashboard memory router → 按层路由
- **批次**：
  - **A（纯增量，可沙箱安全执行）**：facade 加 3 个 chat 透传方法（get_messages_for_llm/add_exchange/conversation 属性）；`ThreeLayerMemory.short_term_search` dict 输出补 metadata 字段。零调用点改动
  - B（读路径）：evolution_loop consolidate 属性→dict；memory router stats/search
  - C（写路径）：memory store 端点；agent_performance sync_from_episodic
  - D（chat 主链路，最后）：chat_engine 构造 + 三处方法替换
- **关键约束**：`facade.retrieve` 不优先透传底层 → 调用点禁用统一 retrieve，用层专属方法；不物理合并两套底层（L1 语义不同）
- **验证**：每批"直连底层 vs facade"行为一致性比对（store 可对端读到、search 字段集合一致、consolidate dict 键集合==原字段）

**审核结论**：pass，无 blocking。advisory：short_term_search 补字段是公共 API 变更（查 test_three_layer_memory.py 字段断言）；chat_engine 最后迁。

---

## 一致性检查结论（项目总监）

| 检查项 | 结论 |
|--------|------|
| 任务间文件冲突 | ⚠️ 任务2 与任务3 都动 `three_layer_memory.py` → **执行顺序：先做任务3 批次 A**（facade 增强 + short_term_search 补字段，纯增量可沙箱执行），再做任务2 拆分；或将补字段合并进任务2 的 episodic 拆分 |
| 任务1 与任务2/3 | 无冲突（tool_manager.py 不在拆分清单） |
| 共同前提 | 本机 git / 全量 pytest / 沙箱禁 git rm，三方案一致 |

**建议执行顺序**：
```
第 1 步（可沙箱执行）: 任务3 批次 A（facade 增强 + short_term_search 补 metadata）
第 2 步（需本机 git） : 任务2 拆分三个 P0 文件（perf_loop 纯搬移先行）
第 3 步（需你拍板）   : 任务1 工具白名单（enforce + 三阶段过渡）
第 4 步（依赖 A）     : 任务3 批次 B → C → D（读/写/chat 主链路）
第 5 步（需本机 git） : 26 个死代码分批清理（可穿插在第 2 步后）
```

---

## 需要你拍板的决策点

| # | 决策 | 架构师推荐 | 备选 |
|---|------|-----------|------|
| 1 | 工具白名单默认策略 | **enforce（默认拒绝）+ 三阶段过渡**（audit→清单→enforce） | 仅 audit 记录不拦截（低风险低收益） |
| 2 | 上帝模块拆分范围 | **P0 三个全拆**（1393/1347/1151 → ≤300 行）+ 26 死代码清理 | 只拆风险最低的（evolution perf_loop 纯搬移） |
| 3 | facade 迁移范围 | **生产 6 处全迁**（批次 A-D）；测试 40+ 处另立项 | 只迁读路径（批次 A-B） |

**我的建议**：三项全按架构师方案执行（1 选 enforce+三阶段、2 全拆、3 全迁），执行顺序按上表。你确认后，我立即拆分任务、分配给对应专家（后端贝洛奇 / QA 严过关 / 运维卜宕机）、逐个调度执行并每步门禁验证。
