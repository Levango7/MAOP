# 前端交互运行时抽查 + 顺带发现（2026-11-08，第二轮）

> 方法：本地起真栈（`py/start_dashboard.py` @9079 + `dashboard-enterprise` vite dev @5174，
> `MAOP_AUTH_ENABLED=0` + `MAOP_OWNER_MODE=1` + `MAOP_MULTIMODAL_PROVIDER=hash`），
> 用浏览器自动化对高频页做**点击级**验收：导航 / 页内标签 / 表单输入 / 主题切换 /
> 空态与错误态，每页同时取 DOM 快照与截图双证。
> 证据截图：`/f/tmp/maop-gui-20261108/`（t1_home / t2_blank_lazy / t2b_tasks_rendered /
> t3_run / t5_cost / t7_blackboard / t8_settings / t8b_dark / t9_search / t10_alerts）。
> 上一轮全仓排查报告：`quality-audit-2026-11-07.md`；本文件只记本轮新增。

## 一、本轮确认的问题

### P2-1 成本台账「无法计价」被显示成「免费」（models.yaml 与 agents.yaml 不同源）

**现象**：成本页「总成本 $0.0000 / 按模型成本 unknown $0.0000 / agnes-2.5-pro $0.0000」。

**实证**（`data/maop.db` 的 `cost_entries`，15 行）：

| agent | model | provider | cost_usd |
|---|---|---|---|
| copilot | 'unknown' | '' | 0.0 |
| copilot | 'unknown' | '' | 0.0 |
| …（14 行同类） | | | |
| mmx | agnes-2.5-pro | agnes | 0.0（有 estimated_cost 0.00018） |

**根因链**（`py/maop/maop_loop_phases.py:396-440`）：

```
model_name = exec_result.model or ""            # CLI 路径取 AgentConfig.model（drivers.py:64/145）
model_def  = ModelRegistry(...).get_model(model_name)
provider    = model_def.provider if model_def else ""     # ← 未收录即空
estimated_cost = 0.0 if not model_def                   # ← 永远 0
tracker.record_actual_cost(model=model_name, provider=provider, estimated_cost=0.0, ...)
```

而两份配置**不同源**：

- `config/models.yaml`（ModelRegistry）只登记 **3** 个模型：`agnes-2.5-pro` / `step-3.7-flash` / `yi-large`
- `config/agents.yaml` 声明 **14** 个模型值，其中 **12 个未收录**，且包含真实跑过的
  `gpt-5.4`（copilot）与 `qwen3.5-plus`（codex）

⇒ 任何走 agents.yaml 真实模型的运行，`get_model()` 返回 None ⇒ `provider=''` / `cost_usd=0.0`。
代码本身是诚实的（`maop_loop_phases.py` 的 run event 里带了 `priced: bool(model_def)`、
`tokens_source`，并在缺 usage 时打 WARN），**但成本表没有任何「未计价」标记**，
运营在成本页只能读到「$0.0000」=「这次不要钱」。这与此前 GAP-2「token 协议行对不上」、
告警 `<unknown>` 属同一族：**字段形状/来源失配导致静默降级**。

**建议修法**（三选一或组合，按代价从小到大）：
1. 成本条目 metadata 增加 `priced` 字段（`record_actual_cost` 已有 `model_def` 判据，
   透传即可）；成本页对 `priced=false` 显示「未收录·无法计价」而不是 `$0.0000`。
2. 配置漂移门禁：`agents.yaml` 声明的 model 必须出现在 `models.yaml`，缺失即启动 WARN/CI 红
   （与现有 config-drift 棘轮同思路，把静默降级变成显式告警）。
3. 补齐 `models.yaml` 的定价（需真实单价数据，不可臆造）。

### P2-2 告警 ingest 对「无名告警」静默兜底，且测试基建写活了活动告警

**现象**：`/api/alerts` 返回一条 `alertname="<unknown>"`、`status="firing"`、
severity/summary/instance 全空、`receiver="business-sim"` 的告警，前端告警页把它渲染成
「触发中」（运营视角：一条永远在触发、却无法处置的告警）。

**根因**：
- `py/maop/core/alert_ingest.py:112`：`_clip(labels.get("alertname") or common_labels.get("alertname") or "<unknown>", …)`
  —— webhook 载荷缺 `alertname` 时不校验、不拒绝，直接落 `<unknown>`。
- `py/scripts/business_sim/run_sim.py:273-282`：sim 采集器传的是**顶层 `name`**
  （`{"name": "BusinessSimCostBudget", "severity": …}`），而 `record_alerts` 只读
  `labels["alertname"]` ⇒ 键名失配 ⇒ 静默变成 `<unknown>`。
  这不是 Pydantic 校验能拦的：`record_alerts` 收的是 `list[dict]`，键名写错悄无声息。

**建议修法**：`record_alerts` 对缺 `alertname` 的条目显式拒绝或标记 `incomplete` 并 WARN；
`run_sim.py` 的键名改成 `labels={"alertname": …}`（一行）；顺带把 sim 采集写成独立
test receiver 或可清理，避免污染运营视图。

### P2-3 路由懒加载期间整屏空白，无任何 loading 反馈

首次点击侧栏进入某路由时，主体区域**完全空白约 4–10 秒**（`t2_first_click_blank.png` /
`t2_blank_lazy.png` 实证：点击后 1.5s 截图主体纯白、无骨架屏、无进度提示），chunk 到位后才渲染。
生产构建同样按路由分包（`vite.config.js` 无预取配置），首访每一条路由都会复现。
用户视角=「页面坏了」，而不是「在加载」。

### P3-1 首载 KPI 直接渲染 `—`，不走骨架

`dashboard-enterprise/src/views/Overview.vue:33`：`{{ data?.agents_total ?? '—' }}`；
同文件 `load()` 里注释写「skeleton only on first load」，但 hero 条与 KPI 卡**没有**骨架分支
（`v-if="!error"` 下 `data=null` 即渲染 `—`）。慢网/弱机下与「数据丢失」不可区分。
修法：给 hero + KPI 卡补 `loading` 骨架（组件已有 `Skeleton`）。

### P3-2 成本页把未计价显示为 `$0.0000`

P2-1 的展示层同源问题：`dashboard-enterprise/src/views/Cost.vue` 对无定价条目直接打 `$0.0000`。
修 P2-1 时一并处理（`priced=false` ⇒ 「未计价」）。

## 二、本轮验证通过的点（防回归基线）

| 测试点 | 结果 | 证据 |
|---|---|---|
| T1 /home 概览 | KPI 四联 / 系统健康 / 近期委派空态 / 58 路由导航齐全 | t1_home.png |
| T2 /home/tasks | 搜索框 + 状态筛选 + 排序 + 「暂无任务」空态文案 | t2b_tasks_rendered.png |
| T3 /run 运行 | 任务描述框 + Ctrl+Enter 提示 + 执行控制（暂停/恢复/停止/校验配置/查看状态）+ 任务记录 | t3_run.png |
| T4 黑板四签 | 快照/域/历史/统计**四签切换全部正常**（见下「撤回」） | t7_blackboard.png + DOM 断言 |
| T5 /operate/cost | KPI / 预算状态 / 按模型 / 按智能体 / 近期明细表齐全 | t5_cost.png |
| T6 /memory/search | 输入→搜索→结果表 10 条 + 耗时显示（536ms） | t9_search.png |
| T7 /settings | 配置/配置历史/Hook 管理三签；外观（主题/密度/侧栏/语言）；版本与**降级项诚实展示**（`license → personal (no_license_key)`） | t8_settings.png |
| T8 深色模式 | 切换成功；实测对比度 **正文 14.03:1 / 卡片标题 12.9:1**（AAA 需 7:1） | t8b_dark.png + 计算色 |
| T9 /operate/alerts | 规则四联统计 + 告警表格 + 新建规则按钮 | t10_alerts.png |
| 后端健康 | `/api/health` ok / `active_agents=31`；`/api/overview` 正常返回 agents_total=31、timeseries 5 点 | curl 实证 |

## 三、我自己的假阳性（撤回，作为方法教训留在报告里）

排查过程中我先报了 3 条「缺陷」，复核后**全部不成立**，如实撤回：
1. 「黑板『统计』标签点击无效」——用 `offsetParent` + 计算样式重测，四签切换全部正常；
   假阴性来自只信 `innerText`。**教训：UI 可见性断言必须用 `offsetParent`/`getComputedStyle`，
   不能只读 `innerText`。**
2. 「首页 KPI 从 31 退化成 —」——那是首次加载未返回的瞬时态（见 P3-1），并非回归。
3. 「搜索输入框被重复注入（MAOPMAOP）」——前一次调用已执行打字、我重试又打一遍；
   清空后单次输入验证为「GRAPH」一次成型，产品无此缺陷。

**另记一条环境限制（非产品缺陷）**：本机 IAB 的 Playwright 定位面对本应用
`click()` **全部 actionability 超时**（`count()=1`、`isVisible()`、`elementFromPoint` 无遮挡均正常，
坐标路径 `cua.click()` 一击即中）。若后续要用 Playwright 写这类端到端验收，需先解决这一层。

## 四、与其它轮次的关系

- 本轮 P2-1/P2-2 与第一轮报告的「静默降级族」（GAP-2 token 协议行、`unknown` 模型归属）
  同根：**上游字段形状与下游读取键不一致，且失配点无校验无告警**。
- 建议把「配置漂移门禁」（P2-1 修法 2）与「ingest 键名校验」（P2-2）一并立项，
  这两处都是「加一道显式检查」就能把静默降级变成显式失败的典型位置。
