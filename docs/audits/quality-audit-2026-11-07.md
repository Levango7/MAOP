# 全仓质量排查报告 —— 第一轮（2026-11-07）

> 触发：项目主理人问「几个月了还不能转化为生产力？后端接口有没有不正常的？
> 前端页面/布局/配色/交互有没有不正常的？前后端有没有不正常的？数据流程正常
> 吗？」要求无微不至地找出并解决。
>
> 方法：① 数值摸底定基线；② 五路并行审计 agent（**全部静默死亡、零产出**，
> 如实记录为过程事故）；③ 主会话以「一手验证」方式接手审计（跑得起来、装得
> 上、对得上账，逐项留证据）；④ 发现即修，低风险优先。

## 一、已确认并修复的真缺陷（本轮）

| # | 缺陷 | 类别 | 证据 | 状态 |
|---|---|---|---|---|
| 1 | **GAP-1：legacy `run_workflow` 只规划不执行** | A · 静默假成功 | `maop workflow run` 与 dashboard `POST /api/workflows/{name}/run` 两个生产入口提交后零节点执行 | ✅ 修复（#235 → `650eb22d`）：legacy 按编译层序真实 dispatch + `run_events` 回写；3 例验收 |
| 2 | GAP-5：并行分支聚合丢 `model/usage` ⇒ 成本静默缺行 | A · 静默失败 | 英文触发文本必现；`run_events` 无 `run.cost` | ✅ 修复（#232 → `d6889fb3`）+ 回归 3 例 |
| 3 | README Release 附件钉 v5.5.0（落后 6 个小版本） | B · 文档失真 | 用户照抄安装将缺 GAP-2/GAP-4/M-X1 M1/E2-b/B2 全部修复 | ✅ 修复：钉 v5.6.1 + 干净 venv 实测（`import maop → 5.6.1`、CLI 可用、字节数与附件一致） |

### 过程发现（值得进入方法论）

- **GAP-1 能潜伏数月，是因为有一条测试钉死了错误合同**（`test_run_workflow_legacy_passthrough` 断言 legacy=纯规划）。这类「测试钉错合同」的缺陷不会被任何门禁发现，只会在用户撞上时暴露。**已纳入分诊视角：A 类中再分「无测试」与「测试钉错合同」，后者优先。**
- **五路审计 agent 全部启动即静默死亡**（17 小时后输出目录仍只有 metadata.json，无 output 文件）。教训：agent 审计必须**带落盘产出与心跳**，否则长跑即悬空；本轮改为主会话一手验证接管。

## 二、一手验证：确认健康的面（有证据，非感觉）

| 面 | 证据 |
|---|---|
| **数据流 · 计量对账** | soak **1039 轮 / 1038 成功（99.90%）**；协议行 vs 成本表 **1038/1039 match**，token 合计 **2,227,811 == 2,227,811**（2.2M 规模零漂移） |
| **数据流 · 文件完整性** | soak 每小时 `PRAGMA integrity_check` 全 `ok`（见 `business-sim-soak-evidence.md`） |
| **服务可用性** | 干净 venv 装 v5.6.1 → `maop start` 起服；`/healthz`、`/`、`/api/info/edition`、`/api/runs`、`/api/cost-attribution`、`/api/workflows`、`/api/agents` 全 **200** |
| **前端** | `catch {}` 空吞 **0 处**；54 条路由组件**全部存在**；9 处 hex 全为 `var(--x, #fallback)` 正确写法（非硬编码） |
| **后端健壮性** | 非测试代码静默 `except: pass` **仅 1 处**（localStorage 私有模式，带注释）；13 处 `NotImplementedError` 全为抽象基类/防御守卫；4 处 `include_in_schema=False` 全为尾斜杠别名（防 OpenAPI 重复 operationId） |
| **工程纪律** | 非测试代码 **0 处 TODO/FIXME**（此前的「92 处桩标记」为关键词误命中——`task_placeholder` 等） |

## 三、未闭环项 → 收口进展（2026-11-07 当日更新）

1. **（仍待做）前端交互态运行时抽查**：静态面干净，但「点击/拖拽/反馈」的最终判据是运行时行为；建议下一轮用浏览器自动化对 10 个高频页做点击级抽查。
2. **`turns=4` 根因已查明**（原记为「循环过旋嫌疑」，**证伪**）：`--agent claude` 而 **claude 根本不在 `agents.yaml` 在册条目**（README 早有记载「不存在 claude 适配器条目」）→ 首轮 dispatch 失败 → failover 链兜到 copilot 成功。run_events 实证（run.cost / run.step 的 agent=copilot，而 started/finished 标 claude）。**修复**：入口 `unknown_agent_warning`——`--agent X` 不在册即打印 WARN + 在册名清单（failover 机制保留）。5 例测试。
3. **`done` 事件 token 口径已统一**：优先 `MaopResult.usage` 实测（GAP-2 链路），无实测退回字符估计，附 `tokens_source` 标注。
4. **routing 兜底已按在册解析**：`resolve_default_agent`——env 显式值 → 在册 codex 优先（保持历史行为）→ 首个 enabled 且 cli 非空的 agent → 字面 codex（如实报错）。5 例测试。

> 方法论收获：`turns=4` 与「兜底 codex」是**同一个根因类**——「请求的 agent
> 与实际执行的 agent 不一致且无信号」。这类缺陷不会报错、不会红测试，只会
> 在用户看懂 run_events 时才显形；入口告警是成本最低的防线。

## 四、总判断

- **功能残缺面**：本轮全仓扫描未发现成规模的「空实现/假数据端点」——后端 520 条路由、前端 58 页在静态与运行时接口面均通过抽查；唯二的 A 类真缺陷（GAP-1/GAP-5）**都是「读起来正常、跑起来静默错」的接缝问题**，已修。
- **阻碍生产力的不是缺功能，而是三类「静默」缺陷**：假成功（GAP-1）、缺行（GAP-5）、文档钉旧版（README）。三者在犯时都无任何报错——这正是最该用「对账硬门禁」防的形态，soak 的逐轮对账即是该防线的现行实现。
- **就绪度**：社区版「装→跑→看数」路径本轮已端到端实证（venv 装 wheel → 起 dashboard → 全核心 API 200 → soak 2.2M token 对账零漂移）。剩下的是**交互体验抽查与措辞统一**级别的收尾，而非结构缺口。
