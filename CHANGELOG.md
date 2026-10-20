# Changelog

All notable changes to MAOP (Plan-Execute-Verify) are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## 发布节奏规范（M7 修复）

> **背景**：2026-08-11 至 2026-08-14 四天内发布 4 个版本（5.0.0 / 5.0.1 / 5.0.2 / 5.1.0），
> 发布节奏过快，版本质量难以保证。现制定以下发布节奏规范，约束后续发布频率。

### 发布频率约束

| 版本类型 | 频率上限 | 触发条件 | 审批要求 |
|---------|---------|---------|---------|
| **patch**（x.y.Z） | 每周最多 1 个 | 紧急 bug 修复（P0/P1） | 1 名 reviewer 批准 |
| **minor**（x.Y.0） | 每两周最多 1 个 | 新功能或非紧急修复 | 2 名 reviewer 批准 + CI 全绿 |
| **major**（X.0.0） | 每季度最多 1 个 | 破坏性变更 | 架构评审 + 3 名 reviewer 批准 |

### 发布前 checklist

1. [ ] CI 全绿（lint + type check + unit + e2e + coverage 门禁）
2. [ ] CHANGELOG.md 已更新本次版本段
3. [ ] 版本号在 pyproject.toml / __init__.py / Dockerfile / package.json 同步
4. [ ] 至少经过 1 个完整工作日的 staging 环境验证
5. [ ] 无未解决的 P0/P1 issue（紧急 hotfix 除外）
6. [ ] 发布说明已撰写（含变更摘要 + 破坏性变更 + 迁移指引）

### 禁止行为

- **禁止**同一天发布 2 个及以上版本（紧急安全 hotfix 除外，需附事后复盘）
- **禁止**跳过 staging 验证直接发布到 production
- **禁止**在 CI 红灯状态下发布

---

## [Unreleased]

### M1 补全：运行记录列表进 UI（`GET /api/runs` 此前没有任何调用方）

第一片写完事实源与两个只读端点，但前端**只用了详情那一条**：`grep -rn "api/runs" src/`
实测只有 `/api/runs/${traceId}`（作业抽屉里的时间线）。列表端点与 `fetch_recent_runs()`
没有任何 UI 消费 ⇒ **从命令行 `maop run` 发起的运行在界面上完全看不见**，
"可复盘"只覆盖了从面板发起的那一半。

- `ControlPanel.vue`（`/operate/run` 的 structured Tab）新增"运行记录"卡片：任务、agent、
  步数、耗时、开始时间与三态结论（成功 / 失败 / 未结束），点行开运行抽屉看完整时间线。
  未写 `run.finished` 的运行**不许显示成成功或失败**，只能是"未结束"（中性色）。
- 时间线渲染抽成 `src/components/RunTimeline.vue`，作业抽屉与运行抽屉共用一份；
  状态→色调的映射由调用方以函数 props 传入，不在组件里再写一套口径。
- 卡片读账本失败时只让列表为空、由空态说明，不参与 `loading`——
  不能让"读历史"把任务输入框一起锁住。
- i18n 补 `runHistory`/`viewRun`/`runSteps`/`runIncomplete` 等 15 个键（en+zh 成对）。

验证：`ControlPanel.test.js` 新增 2 例——列表两行的渲染与 finished/incomplete 的色调区分
（断言 `.badge--success` 与 `.badge--neutral`，不吃翻译文案）、点开抽屉断言
`.run-timeline` 含事件正文；另加"账本读 500 时面板不炸"一例。
变异反证：把 `runDrawer.run` 恒置 null ⇒ 恰好 1 例红，且报的是我写的断言消息
（`run timeline missing`），还原后文件与备份 sha1 逐字节一致、CR 计数 0。
`npx vitest run` 相关两文件 15 passed；`npm run build` 18.58s 通过；
`py/scripts/check_api_contract.py` 仍 `OK: 无新增错配`（新增的 `/api/runs?limit=20` 被解析命中）。
本机全量 vitest 分两段才凑齐：默认 forks 池只起得来 26 个文件（31 个 worker 启动超时），
`--pool=threads` 起到 49 个（仍 8 个起不来），把那 8 个单独串行再跑 ⇒ **8/8、64 passed**，
合计 **57 个文件 / 489 个用例全过**（57 = 本机 `src/__tests__/*.test.js` 的文件数）。
worker 起不来是 worktree 的 `node_modules` 是指向主仓的 junction、加上本机同时压着
两桩长跑导致的，与被测代码无关；终判仍以 CI `Frontend Build` 为准。

### 数据层纠偏：看板统计此前一直读一张没人写的表（#54）

`delegations` 表 schema 完备、列齐全，但 `MaopDatabase.insert_delegation()`
**只有测试在调用**（实测：产品码里除定义处零调用点）。于是所有读它的地方不是报错、
而是**自信地报 0**——`_query_maop_sync` 把异常吞成 `logger.warning` + `[]`。
`delegations` 的读者按 AST 数（剥掉 docstring/散文）共 **13 处**，本次改掉其中 3 处：

- `DataProxy.report()`（`/api/report`）、`agent_stats()`（Overview 的每 agent
  `total_delegations`/成功率）、`live()`（`recent_delegations` 与 `requests_per_min`）
  改读 `run_events`：一次 `run.step` 事件 = 一次委派尝试（`status='ok'` 计成功）。
  新增 `run_events.agent_rollup(since)` 与 `recent_steps(limit, since)`，
  由本模块自己解析库路径（`MAOP_DB_PER_MODULE=1` 时与 `get_db_path()` 不同文件，
  把口径留在事实源那一侧）。
- 时间窗改为**秒精度的 ISO** 比较（`timespec="seconds"`）：事实源写的是秒，
  旧代码写的带微秒，字典序比较在"同一秒"上会把该算进去的行漏掉。
- 保留期：`prune_run_events(older_than_days=30)`（append-only 不等于不许有界），
  挂进 `POST /api/control/maintain` 的 `prune` 动作并回 `run_events_pruned`，
  排在 memory store 之前执行——记账清理不该被别的子系统的失败带走。
  `older_than_days` 下限钳到 1，防一句 `0` 把审计线清空。
- 新增棘轮门禁 `py/tests/test_delegations_table_readers.py`：产品码里
  "`FROM/INTO/UPDATE/JOIN delegations`" 的字符串字面量只准减不准加，
  未登记的文件、超基线的总数、台账里的虚账三类都判红。基线 **13** = 本表自己的
  写入/读取方法一对（2，只有测试调用）+ 还没改的读者 11：`provider.py` 6、
  `data_proxy.failures()/timeseries()` 2、`channels/prompts.py` 1、`evolve.py` 1、
  `history_analyzer.py` 1 —— 后 11 处是下一片的清单。
- `docs/database-schema.md` 补 `run_events` 表节（§2b）并把 `delegations` 标成
  "无写入方、读它一律 0、不要再加读者"；`evolve.py` 的 docstring 原话是
  "This is the primary data source in production — delegations are persisted via
  `insert_delegation()`"，与实测相反（那条路恒空，真正喂演进的是
  `logs/delegations.json` 兜底），改为如实描述先后顺序与去处。

验证：`tests/test_run_events.py` 13 例（新增 rollup/window/limit/clamp/错误退化各例）
+ `test_data_proxy_coverage.py` 改写 4 例 + 新增 `live()` 1 例 + `test_router_control.py`
新增 1 例（从 HTTP 入口跑通 prune 真的删掉了过期事件）—— 相关三文件 **170 passed**；
门禁自检另证：喂一份"docstring 里提到 `FROM delegations` + 代码里真读一次"的合成文件，
只数到 1（散文不误报），且真树数到 13 == 基线。ruff/mypy/文档一致性门禁通过。

### e2e 可移植性：Windows 上整条 Playwright 腿"根本没跑"（#53）

`dashboard-enterprise/playwright.config.js` 的 webServer 用 POSIX 内联赋值起后端
（`MAOP_AUTH_ENABLED=0 python …`）。Windows 的 cmd.exe 把 `MAOP_AUTH_ENABLED=0` 当命令名，
本机实测报 `'MAOP_AUTH_ENABLED' is not recognized`——症状不是"红了"而是"一条都没跑"。

- 改走 Playwright 的 `webServer.env`，并显式以 `process.env` 打底（否则会连 PATH 一起覆盖掉）。
- 加 `MAOP_MULTIMODAL_PROVIDER=hash`：否则后端启动先去 huggingface 拉 CLIP 权重，本机实测
  **87 秒**；网络被重置时靠 5 次重试耗满启动超时。e2e 不测多模态嵌入，哈希回退是确定性且
  快的选择——与 `py/scripts/first_run_smoke.py` 里同一开关、同一理由。
- webServer `timeout` 60s→180s：给慢机器留余量，CI 上不改变行为。
- `docs/contributing.md` 记两条当场踩到的坑：worktree 里的 e2e 默认 import **主仓**的
  editable `maop`（要 `PYTHONPATH=<worktree>/py` 才测得到本 worktree 的代码）；以及上面的权重开关。
- `.gitignore` 新增 `dashboard-enterprise/data/` 规则：e2e 的 webServer 以前端目录为 cwd 起后端，
  会在该目录落 `notifications.db` 与 `degradation.log`；仓库根的 `data/*` 规则带斜杠、只匹配根目录，
  所以这两件此前对 `git add -A` 是可见的（改前实测 `git check-ignore` 两条都 rc=1，改后均命中）。
- `e2e/seeded-pages.spec.js` 两处 agent 名断言加 `exact: true`：同一页还挂着实时事件流，其
  `event-msg` 元素里是原始 JSON（实测命中 `{"type":"snapshot",…,"live_agents":["copilot"]}`），
  子串匹配会同时命中健康行与事件流 ⇒ strict mode violation；撞不撞取决于那一刻快照里有没有这个
  agent 名，是一条与时序有关的假红。

验证：`--workers=1` 下单跑 3 条目标用例（agent-billing / settings / agent health rows）50.6s 全过；
`e2e/seeded-pages.spec.js` 整文件 8 条（含两处改后的断言）39.9s 全过。

**未解决，如实记录**：本机全量 chromium 套件（88 条）仍会在约第 65 条之后成批失败——25 条红里
22 条根因是 `net::ERR_CONNECTION_REFUSED at localhost:5174`（vite dev server 中途消失，
`[WebServer]` 日志同步停在同一处），其余 3 条单独复跑全绿。同一 commit 在 CI 的
`Playwright E2E`（chromium + firefox + webkit）为 success ⇒ 判本机 dev-server 稳定性问题，
不据此宣称已修好，也不把"本地全量绿"当验收口径。

### M1 第一片：单次运行可复盘（run_events 事实源 + /api/runs）

M0 之后作业详情只有捕获到的 stdout，"这次运行到底发生了什么"在进程退出后
就查不到了。Explore 实测定位到根因（file:line 已核）：`delegations` 表 schema
完备但 `insert_delegation` **只在测试里被调用**；plan 步骤/stdout/verify 结果
只活在内存 `LoopResult`（`maop_loop_phases.py`）与 `logs/MAOP-structured.log`
文本行；`cost_entries` 只在 metadata JSON 里带 trace_id；无任何 run_events 表。

- 新增 `maop/core/run_events.py`：append-only `run_events`（trace_id + seq +
  kind + agent/step/status/duration/payload），字符串入库前截断到 2000 字符；
  写失败只记日志不抛出（记账不能弄死运行），读失败退化为空列表。
- 落点：`run.started`（`maop_loop.py`）、`run.plan`、`run.step`（含 feedback
  重试的每一次尝试）、`run.verify`（门禁结论与 gates 明细）、`run.cost`、
  `run.finished`（`maop_loop_phases.py`）。
- 新增只读端点 `GET /api/runs`、`GET /api/runs/{trace_id}`（admin gate；
  个人版回环 owner 模式即满足），响应走 `{status,data}` 信封；
  router 薄、逻辑在 `dashboard/services/run_service.py`。
- `maop run` 现在打印独占一行的 `[TRACE] <id>`，`system_service` 解析后写进
  job 记录（`trace_id`），作业详情因此能链到时间线；正则要求整行且十六进制，
  agent 自己引用这个标记不算数。
- 前端 job 抽屉增加"运行时间线"（步骤/校验/成本按序展示，DetailDrawer 内容
  Teleport 到 body，故测试断言走 `document.querySelector`）。
- README 的 `core/` 计数 7→8 files（`scripts/doc_reconcile.py` 复验通过）。

实测：`test_run_events.py` 6 条（含**真跑一次 `MaopLoop.run`** 断言事件序列与
seq 连续，证明写入点不是死代码）、`test_router_runs.py` 6 条（列表/详情/未知
trace 404/只读角色 403）、`test_router_control.py` 62 条（含新增 trace 解析与
落库 2 条）、loop + 文档门禁 + 质量门禁 + 死接线清单合计 190 passed；
前端 lint 0 error、`vue-tsc` 0、相关用例 20 passed。


### 修复：自引用 agent 的跨进程 fork 炸弹（2026-10-20）

`config/agents.yaml` 的 `MAOP` 自引用条目 `cli_args: -m maop.cli run --task "{task}"`
**从未传递 `--depth`**（`cli.py` 的 docstring 曾声称它传了）。进程内的
`max_self_ref_depth` 只统计单进程的 call chain，而每个子进程都从 0 开始，
于是"路由到 MAOP 自己"的任务会无限 fork。实测一次普通中文任务在 90 秒内
拉出 **16 个以上**同任务 python 进程，父进程退出后子链仍继续增长。

修法是在进程边界传递深度，而不是依赖配置模板：

- 新增 `maop/core/agent/delegation/depth.py`：`MAOP_DELEGATION_DEPTH`
  标记，`next_depth_env()` 始终把父深度 + 1 写入子进程环境。
- `delegate/drivers.py`（5 个 driver）、`adapters/cli_adapter.py`、
  `lifecycle/runtime.py` 的 env 构造统一经过该函数；`blocked_env` 不能移除
  这个守卫。
- `maop run` 取 `max(--depth, 继承值)`，到 3 层直接以退出码 1 拒绝，
  错误信息写明 "hard cap"。
- `delegate/drivers.py` 把 `cli: python` 固定到当前解释器（`sys.executable`）：
  venv 启动的服务器下，PATH 上的 `python` 常是系统解释器，子进程会 import
  另一份 MAOP 源码而丢掉这个守卫（实测正是如此）。
- `test_delegation_depth.py` 6 条 + 更新后的 adapter/driver env 测试覆盖
  "无 config.env 也注入"、"blocked_env 移不掉"、"到顶拒绝"、解释器钉定。

修复后同一任务实测：并发链峰值 6 个 python（分支而非无限增长），
25 秒内完成，退出码 0，服务器停止后残留进程 0；重启后该作业仍以
`completed` 状态与完整 stdout 返回。

### 首次运行闭环（M0）：装完 wheel 就能打开并派任务（2026-10-20）

把"`pip install` 之后跑不起来"这条链上的每一环都补上了。实测起点：wheel 里**没有**
`config/agents.yaml` 与前端产物，`MAOP_ROOT` 由 `__file__` 推导后指向 `site-packages`，
因此 `maop start` 既找不到 agent 配置也找不到 UI；个人版本地启动又默认要求登录，
关掉认证后 `/api/overview` 仍返回只读 403；`/run` 页面没有任务输入框，
Tasks 的"查看详情"跳去一个不存在的 `/run?session=`。

- **安装布局**：新增 `maop/config/home.py` 统一根目录解析
  （`MAOP_ROOT_DIR` > 旧 `MAOP_ROOT` > 源码检出 > `~/.maop`），并实现 `maop init`
  （复制默认 config 与预构建前端到 `MAOP_HOME`，默认不覆盖已有文件）。
  `maop start` 在首次运行时自动初始化 home；数据落 `MAOP_HOME/data`，不再写进仓库。
- **打包**：`pyproject.toml` 用 hatch force-include 把 `../config` 与
  `../dashboard/dist-enterprise` 装进 `maop/_assets/`（wheel 3.5 MB，含 254 个前端文件）。
  sdist 同样携带资产，因此从 sdist 重建 wheel 仍可用；新增 `py/hatch_build.py`
  钩子在"前端未构建 / 从 sdist 构建"时跳过缺失的可选资产，避免
  `pip install "...#subdirectory=py"` 直接失败。Dashboard 是构建产物：
  `maop init` 与版本变更后的首次启动会刷新它（config 仍只补缺失文件）。
- **个人版本地免登录**：仅当"个人版 + 回环地址 + 用户未显式设置认证"时启用
  `MAOP_OWNER_MODE`，授予 owner 角色；远程绑定仍默认开启认证；非回环且显式关闭认证时
  只保留只读能力。安全边界收在 `core/security/middleware.py`，并有回归测试覆盖。
- **任务派发**：`/api/control/run` 现在记录子进程的 stdout/stderr/exit_code/finished_at，
  新增 `GET /api/control/jobs/{job_id}` 供详情查看；任务文本作为单个 argv 传递，
  不再用会误伤中文与标点的名称白名单。作业快照追加写入
  `MAOP_HOME/data/control_jobs.jsonl`，服务重启后仍可查看历史与输出；
  重启前仍标记为 running 的记录会显示为 `interrupted`，不再永久转圈。
- **前端首公里**：ControlPanel 增加"新建任务"输入与运行按钮、Job 详情抽屉；
  Tasks 详情改为抽屉；Overview 删除 PEV 演示段、Math.sin 假曲线与死链 `/licenses`，
  KPI 收敛为 4 个真实指标；标题/版本改为构建期注入，去掉 localStorage 版本缓存
  （此前会出现顶栏 v5.3.0、页脚 v5.4.0）。
- **门禁**：既有 `wheel-install` CI 作业现在先构建前端、再装 wheel，并执行
  `py/scripts/first_run_smoke.py`（在仓库外目录跑 `maop init` + `maop start`，
  断言 `/`、`/api/health`、`/api/overview`、`/api/info/edition`、`/api/control/status`
  均 200 且 `data/maop.db` 生成）；publish / release-assets / publish-manual
  补上前端产物来源，避免 force-include 依赖未跟踪目录。

实测（2026-10-20，全新 venv + 仓库外工作目录）：`maop init` 写入 5 个配置与 254 个前端文件；
5 个端点全 200；通过 `/api/control/run` 提交中文任务，job 状态 `completed`、
`exit_code=0`、stdout 捕获到输出；停止服务器再启动，该 job 仍以 `completed`
状态、退出码与完整 stdout 可查。真实浏览器（Playwright）从 Composer 提交中文任务，
详情抽屉自动打开并显示 `completed / exit 0 / 你好！…`。后端全量
**11192 passed / 98 skipped / 1 xfailed（0 failed，覆盖率 84%）**；前端
57 个测试文件 **486 passed** 且覆盖率门禁通过；文档门禁 22 passed；ruff 与 mypy 干净。
wheel 与 sdist 均含 5 个配置和 254 个前端文件，从 sdist 重建 wheel 成功；
未构建前端时 `python -m build` 仅告警跳过 UI，不再安装失败。
设计文档见 `docs/product-v6-blueprint.md`（M0 已落地，M1–M3 待排期）。

### env 读取棘轮：229→240 的逐条记账（2026-10-20）

CI 的 `Lint (ruff + mypy)` 作业里 `scripts/check_config_drift.py` 只允许
`os.getenv` 与 `os.environ` 的计数下降。M0 让它 +11（240 > 229），首跑 PR 因此红。
先压掉可省的 4 处：`cmd_start` 复用 `_runtime_root()` 不再自己写第二遍根目录、
不再 `setdefault("MAOP_HOME")`（与 `default_home()` 同值）、owner+auth 两次写入
合成一行 `os.environ.update(...)`、`cli_evolution` 不再重复导出根目录。

剩余 +11 记入基线并在代码里逐条写明理由：`cli.py` +6（引导层必须在 settings
实例化之前决定 owner/auth 并把根目录交给子进程）、`config/home.py` +2
（解析器先于 settings 存在）、`delegation/depth.py` +2（向子进程传深度是 OS 级
操作，与 2026-10-04 `_agent_env` 的先例同形）、`security/middleware.py` +1
（owner 只允许回环，必须每请求取现值，不能进 settings 缓存）。
复跑 `python scripts/check_config_drift.py` → `OK: no drift.`



### CI 第二次红：两条 home 测试只在 macOS/Windows 失败（2026-10-20）

重跑 `39410157612` 有 5 条腿红（macos/3.13 + windows 3.10~3.13），Linux 四条全绿。
两条用例都是同一个形状：

```
FAILED tests/test_home_bootstrap.py::test_ensure_runtime_root_refreshes_stale_assets
  AssertionError: assert PosixPath('/var/folders/.../home') == PosixPath('/private/var/folders/.../home')
FAILED tests/test_home_bootstrap.py::test_ensure_runtime_root_bootstraps_installed_home
  AssertionError: assert WindowsPath('C:/Users/RUNNER~1/.../home') == WindowsPath('C:/Users/runneradmin/.../home')
```

被测函数返回的是 `.resolve()` 后的路径，而用例把 **未 resolve 的 `tmp_path` 子目录**
塞进 monkeypatch 再比较。Linux 的 tmp 不是符号链接、也没有 8.3 短名，所以只在
macOS（`/var` → `/private/var`）与 Windows CI 账号（`RUNNER~1`）上暴露；本机是
Windows 但临时目录是长名，因此"本机全绿"没提供任何信号。

- 测试面：新增 `tmp_home` fixture（返回已 resolve 的家目录，两处复用），两条用例改用它。
- 产品面：新增 `test_returned_root_is_normalized`，用 `somewhere/../home` 这种
  跨平台等价物钉住"解析器必须返回规范化路径"。
- 变异验证：把 `Path(value).expanduser().resolve()` 改成不 resolve 后该用例转红
  （实测 `1 failed`），还原后 `cmp` 证与备份字节一致、10 passed。

顺带修掉排查中发现的测试基建漏洞（与 2026-10-04 的 `_no_real_config_write` 同类）：
`tests/test_routers_batch_coverage.py` 批量打写端点时，`POST /api/control/run`
会**真起** `python -m maop.cli run --task test`（一次几十秒，会话结束后留下孤儿进程），
并把作业历史写进仓库工作树的 `data/control_jobs.jsonl`（本机反复出现未跟踪文件）。
现补 autouse fixture：stub `asyncio.create_subprocess_exec` + 历史落盘置 no-op。
实测该模块 232 passed（30s），跑完工作树无 `control_jobs.jsonl`、无残留子进程。

### 分布式调度：`deadline=None` 不再等于无限等（原 #116 "未做"项，本次落地）

`_dispatch_and_collect` 的 `while pending` 只在 `deadline is not None` 时检查超时，
而 `run(deadline_s=None)`（`run()` 自己的默认值）一路会传成 `deadline=None` ⇒
**结果消息一丢就永久空转**：进程活着、CPU 空转、run 永远不前进，且**不报任何错**。
这条自 M0 L0.1 起一直挂在"未做 / 风险"里，本次补上兜底。

新增构造参数 `max_collect_wait`（默认 **3600s**，下限 1s）：不显式传 deadline 时用
`run_started + max_collect_wait` 作实际上限；调用方显式传入的 deadline 若更短，仍以调用方为准。
默认值刻意取远大于任何合法层时长的 1 小时，只在"丢消息 / worker 全死"时触发，
不会影响正常长任务的语义。

错误信息区分两种由来并给出排查方向（不再是一句干巴巴的 "exceeded its deadline"）：

```
Distributed run 'x' gave up after 60.0s waiting for layer 0 nodes ['n1']
  — safety cap=3600s (no explicit deadline).      # 或 — caller deadline=30.0s
Most likely the result message was lost or every worker died.
```

新增 `TestCollectSafetyCap` 4 条：不传 deadline 也超时、显式 deadline 更短时优先、
默认 cap 为 1 小时、cap 下限为 1s。

> 测试自查记录：第一版把 `max_collect_wait` 设成 0.3，被构造器的 1s 下限悄悄抬成 1.0，
> 断言区间又写得宽（`0.3 <= waited < 5.0`），于是"测的就是错的值"也显示通过。
> 定案改为 cap=2.0 + 断言落地值 `safety cap=2s`。

**实测观察（未修，非本次代码缺陷；含一次自我纠正）**：

`tests/test_distributed_execution.py` 每条用例的 fixture 都给一个**新的** `fakeredis.FakeRedis()`
并新建 `DistributedScheduler`，而 `__init__` 末尾的 `_ensure_task_group()` 会对新实例执行
`xgroup_create(..., mkstream=True)`。分解计时（`fakeredis` 层，非产品代码）：

| 步骤 | 耗时 |
|---|---|
| `fakeredis.FakeRedis()` | 0.003s |
| `WorkerRegistry()` / `FailurePatternDetector()` / `get_failure_detector()` | 各 0.000s |
| `DistributedScheduler.__init__`（**新** fakeredis 实例） | **1.3–1.4s** |
| `DistributedScheduler.__init__`（**同一个** fakeredis 实例再次） | **0.000s** |
| `r.xgroup_create(key, 'maop_workers', id='0', mkstream=True)`（新实例） | **1.396s** |

⇒ 1.4s 落在 **fakeredis 首次建 stream/consumer group 的内部懒初始化**上，
`__init__` 本身无固定成本。

> **自我纠正**：#121 的提交信息与 PR 描述把这条归因成
> "`FailurePatternDetector` 单例的初始化时机"，依据只是"第一次慢、后面快"这一现象，
> 没有分解计时。分解后 `FailurePatternDetector()` 与 `get_failure_detector()` 均为 0.000s，
> 该归因**错**。同一段里"63 条合计 144s 里约 100s 是这个固定开销"的量级方向对
> （fixture 每个用例换新 fakeredis ⇒ 63 次 × 1.4s ≈ 88s），但成因是 fakeredis，
> 不是本仓单例。

**为什么没有继续修**：唯一"省下这 1.4s"的办法是让 fixture 共享同一个 fakeredis 实例，
但那会让 63 个用例共用 task stream 与 consumer group 状态，构成跨用例泄漏，
风险远大于 88s 的测试提速。**不建议**用测试提速换隔离性。
另外该开销只在装 lupa 的本机复现；CI 未装 lupa，实际贡献需从腿长实测，尚未量化。

### 桌面快照资产入库（69 个文件）：清掉 7 条红，并交出 5 处"文档/口径领先于实现"的实测结论

把 2026-10-16 误推成孤儿提交的那份桌面快照里**确实属于本仓**的 69 个文件逐字节搬进来
（5 个新模块 + 2 个测试文件 + 60 个合成 PNG 夹具 + ADR 029/030；来源 `c7826c51`，
69 个 blob OID 与源全等，对 base 的 diff 是 69 条 `A`，未动任何既有文件；
`gitleaks detect --no-git` 对这批文件 `no leaks found`）。

搬进来之后全集实测 **11010 passed / 7 failed**，逐条处置：

- **ruff 24 条**清零（`--fix` 23 条 + `F841`）。`F841` 那条不是删掉：
  `test_rank_best_plan_first` 里声明了 `ext = PlanFeatureExtractor()` 却全程手搓训练向量，
  等于绕开抽取器 —— 改成断言"手搓向量的 dim0/dim4/dim5 极性与抽取器一致"，
  这样夹具与真实特征口径脱钩时会当场转红。
- **`test_train_promote_rollback_active_chain`** 是**测试写坏**，不是实现坏：
  `assert vm.promote(active2.version_id) or vm.promote(m2._version)` 里 `active2` 已被上一条断言
  证明等于 v1，而 `promote()` 的返回值只表示"这个 id 在链上" ⇒ 永远短路、v2 从未被晋升，
  该用例**在任何实现下都不可能过**。改成直接 promote v2；反向变异（让 `promote` 返回 True 但不落盘）
  确认会红，还原后 `cmp` 证字节一致。
- **结构排序主张不成立，按事实降级为 `strict=True` 的 xfail**（原用例名一拆为二：
  "在线学习抬高被喂计划的分数"0.4145→0.9601 是真行为，留作通过的锁；"学完后结构更优者排前"是更强主张）。
  机制实测：批量训练特征由 `ingest_from_agent_performance` 用 `_infer_gates(routing_key)` + 默认预算
  **重建**，而夹具的 rk `gen`/`sec` 都不命中该启发式的任何分支 ⇒ dim1/dim2 恒 0、dim4 恒 1，
  这三维权重是不可识别的偏置吸收（实测 `w1=-0.4248`、`w4=+0.2682`），推理期却把 `max_retries=3`
  外推 3 倍 ⇒ 30 次在线更新后仍差 0.011 把 garbage 排在前面。前置条件（执行期真值落库 +
  持久化模型加 `feature_schema` 标记）写在 xfail reason 与 `ALLOWED_OVERSIZE` 理由里。
- **`plan_quality.py` 926 行**进 `ALLOWED_OVERSIZE` 并写明拆分排期与先决条件（不拆的理由是
  拆分前必须先改特征来源，否则拆出来的文件仍守着同一个缺陷）。
- **README:27** 的 `core/` 文件数按实测 5→7（`ls *.py` 含 `__init__.py`，与门禁同口径；
  17 个子包也重新数过）。
- **`# nosec B608`（`knowledge_graph_reasoning.py:264`）逐行核过再登记**：`where` 只能由
  `source = ?` / `target = ?` / `1=1` / ` AND relation_type = ?` 四段字面量拼成，
  `direction` 与 `relation_type` 都不进 SQL 文本、值全走 `params` ⇒ 无注入路径，登记而非改写。
  bandit `-lll` 硬门 rc=0；另有一条新的 LOW（`B311` 用于非加密场景，:461）不在本仓判据内。
- **覆盖率**：`cost_router` / `visual_builder` / `multimodal_av_contracts` 原本在 `tests/` 零引用，
  补 65 条用例后实测 **98% / 100% / 98%**（这三块也是 #116 卡在 `Coverage ratchet gate` 的主要缺口）。

顺带查出并修正的四处"文字强于代码"，都带实测出处：

1. `mypy` 报的 `sqlite_connect(db_path)` 传 `None` **不只是类型问题**：该函数走
   `sqlite3.connect(str(db_path))` ⇒ 会静默打开/新建一个**字面名叫 `None`** 的库文件，
   读到的永远是空表。新增 `_resolve_relations_db()`，缺省口径与 `relations` 表的归属方一致
   （`get_db_path("knowledge_graph")`）。其余 4 条 mypy 错误一并清零（本机 mypy 2.3.1，CI 钉 2.4.0，终判在 CI）。
2. `multimodal_av_contracts` 的 docstring 声称 MAOS 已提供 `WhisperAudioProvider` /
   `ClapAudioProvider` / `FramePoolVideoProvider` —— 实测 **MAOS tip `1a19c7f`** 对这三个类名、
   `embed_audio`/`embed_video` 以及对本模块的 import **全部 0 命中** ⇒ 措辞降级为"预期由企业版提供，尚未落地"。
3. `get_cost_router()` 的 docstring 写"参数显式传入时覆盖已有配置"，实测**只在首次构造时生效**，
   单例存在后任何参数都被静默忽略（本仓出过 `get_route_scorer` 单例污染导致生产路由退化的事故）
   ⇒ docstring 按实测改写，并由测试钉住 + 给出 `reset_cost_router()` 的正对照。
4. `RoutingDecision.degraded` 注释写"因预算被强制降级"，实现是"没选中 `tier.preferred_model` 就算"
   ⇒ 调用方自己点名便宜模型也会被记成降级；测试按真实行为命名，注释同步改正，语义不在无人消费时改动。

**两条转交项（我没擅自改判据）**：① `compute_orphans()` 把"孤儿导入了它"也算作有生产导入方，
所以 `cost_router` 躲过了死接线清单（它唯一的导入方 `visual_builder` 自己就在清单里）——
按真实可达性这批是 5/5 不通；改成不动点求解会一次性掀出全仓同形态模块，需要单独决定。
② `visual_builder` 的节点级 `condition` 键是**只出不进**的派生副本，可逆性靠 `config.expression` 成立。

### T2.3 收口：泄漏源（线程内 patch）+ `_leak_probe` 的结构盲区，并把收尾检查升成门禁

追一个泄漏源追出一个**守卫设计缺陷**。`py/tests/test_sandbox_executor.py::TestThreadSafety`
的 `patch("subprocess.run", ...)` 装在 `_run()` 里 —— `patch` 打的是**进程全局**属性，
10 个线程各自 enter/exit 同一个目标，恢复顺序一交错就把替身留在进程里。

盲区在于 `_leak_probe` 是"**下一条用例开始时**"才检查（而 `repair_subprocess_run()` 又必须在
`yield` 之前 —— 那条顺序本身有守卫），所以"最后一个用例弄脏全局函数"这种泄漏**结构上看不见**。
本地实测把两件事同时钉住：把 `TestThreadSafety` 显式排到别的用例前面 ⇒ 探针 **5/5** 报
`修复被替换的 subprocess.run 1 次 … culprit: impl=MagicMock@unittest.mock`；整文件按自然顺序跑
（它是最后一个类）⇒ **0/5**。所以 CI 上"最近几次零条 leak-probe 注解"从来不能用来证明泄漏源
消失 —— 那个源恰好排在末尾，探针永远不会看见它。（本机未装 pytest-randomly，收集序=源码序。）

两处一起改：

- **源头**：`patch` 移出线程体，改成主线程一次性 `with`，线程构造与 `join` 都在块内；再加一条
  自证断言 `subprocess.run is standard_run`。改完两种顺序各测 3 次：**0/5、0/5** 无泄漏痕迹。
- **门禁**：`conftest.py` 新增 `_residual_leak_attribution()` + `apply_leak_gate()`，在
  `pytest_sessionfinish` 里对"会话结束时仍是替身"打 `::error title=leak-gate::`（含 culprit 与
  worker）并把 `session.exitstatus` 改成 `TESTS_FAILED`。不 `raise` —— 这层的 `print` 曾在
  windows-3.10/3.13 抛 `UnicodeEncodeError` 把会话崩成 exit=1 且 junit 里零失败用例（红得莫名），
  所以注解仍走"按需降级成 ASCII 转义"，改退码才是判红的手段。`_emit_session_warning` 顺势抽成
  `_emit_session_annotation(text, flavor, title)`，并有用例钉住老调用点的 flavor 没被改错。

验证（`py/tests/test_conftest_leak_probe.py` 新增 8 条）：

- **真入口端到端**：临时塞一个故意泄漏的 scratch 用例 ⇒ `1 passed` 但**退出码 1**，stdout 有
  `::error title=leak-gate::… culprit: impl=tests.test_zz_scratch_leak:…<lambda>`；删掉 scratch
  后同目录 22 passed / 退出码 0。（scratch 已删，不在提交里。）
- **四处变异各有红**：门禁永不触发 ⇒ 2 红；残留自检永远说"干净" ⇒ 1 红；把 `apply_leak_gate()`
  从钩子里删掉（只剩函数没人调）⇒ 接线守卫 1 红；把 `patch` 装回 `_run()` ⇒ 结构守卫 1 红
  —— 为此那条守卫改成数"全函数只允许一处 `with patch(`"+位置顺序，只看第一次出现会漏掉嵌套写法。
  还原 `cp` + `cmp -s` 逐字节回证后 54 passed。
- **误红风险实测**（升成 gate 前必须量的那一步）：全量 `pytest tests -n auto --no-cov` =
  **10764 passed / 106 skipped、退出码 0、`leak-gate` 命中 0 次** ⇒ 干净会话不会被判红；
  同时这也是"没有别的用例在收尾留泄漏"的正面证据（至少在这个门禁能看见的形态下）。
- `ruff` 对三个改动文件干净；`docs/ci-gates.md` §6 守卫清单补了这一层（连同 5/5 与 0/5 那组数字，
  写清第 ② 层为什么必须存在）。

**留在原地的**：`repair` 计数（"修过但已修干净"）仍然只是 `::warning::`。把它也做成"必须为 0"的
棘轮是下一步 —— 现在拦只会把既存欠账变成长驻红，而定位还没做完。

### M0–M4 多智能体协调包过门禁：一个被 183 条绿测掩盖的 NameError，与一次我自己引入又被复跑抓住的手误

`wip/snapshot-20261017` 固化的在途资产（M0–M4 协调：a2a 传输服务、blackboard 桥、任务上下文、
分布式调度/检查点/租约、事件联邦、触发引擎、健康探针、M3 运维面板与租户黑板 API）转交本分支时，
**六道静态门禁五道不过**。逐项处置，先说结论：143 个 ruff 问题与 31 个 mypy 问题里，各埋着一个
**测试全绿也发现不了的真 bug**。

**门禁层**

- **config drift 229→230**：`engine_router.py` 直接 `os.environ.get("MAOP_REDIS_URL")`，绕过
  `MAOPSettings` 单例造成同一配置两个真相源。改为 `get_settings().redis_url`，并用
  `model_fields_set` 区分"未设（False）／显式设值（True）／显式空串（True, ''）"——若简单按默认值
  取，未配 Redis 的环境会无故建外部连接，Personal 版"未配置=本地引擎"的语义就丢了。
- **静默 except 20 处**（ADR-023，baseline 0）：19 处是有理由的 best-effort（optional-dep /
  lock-race / ws-disconnect / scan-continue 等 10 类闭集），打 `# silent-ok: <类> — <理由>`；
  1 处是真漏——`agent_registry` 状态持久化失败被吞，黑板上的 agent 状态会静默不一致，补
  `logger.warning`。`contextlib.suppress` 47 处双向棘轮持平（只许降不许升）。
- **ruff 143 errors**（干净 master 全绿 ⇒ 全部来自本批新文件），`--fix` 自动修 111 + 手工 32。
- **mypy 31 errors**（干净 master 0 错误）。
- **doc_reconcile**：README 的 core/ 子包计数 16→17。
- **空断言测试**：`test_distributed_e2e.py::test_result_mapping` 只构造对象、不验证契约——改为
  断言 trace_id 透传、`steps[0].id`、`status == SUCCESS`、`output == "mapped-output"`、
  `duration_ms == 150`（预投结果流 + scheduler 首轮 poll 即收集，无挂起风险）。

**两个测试抓不住的 bug**

- `RedisAgentStore.remove()` 调 `_json.dumps`，而 `json` 从未 import（ruff F821）。Redis 后端的
  remove 一调就 NameError；**183 条测试全绿也没暴露，因为测试只覆盖 InMemoryAgentStore 路径**。
  补 `import json as _json`。
- `a2a_blackboard_bridge.py`：`a2a_msg.task_id` 为 None 时会把 `None` 当键写进 `_task_routes`，
  此后任何 None 查表都假命中。加 `if a2a_msg.task_id:` 守卫后才写（同一个 mypy index 错误）。

**一次自己引入又被自己抓住的手误**：PERF102 批量修复 `.items()`→`.values()` 时，有一处写成
`for v in raw.items()`（v 成元组 → `json.loads` 全抛 → 被 scan-continue 吞掉 → 测试红在
`test_incomplete_runs_query`）。复跑定位后改回 `.values()`——**"作业绿了"不等于"改对了"，批量
替换后必须逐点复跑**。

其余 mypy 站点：`distributed_scheduler._dispatch_and_collect` 返回标注写成 `dict`、实际返回
`(dict, list)` 二元组（调用侧 430/438/584 三处连带错）；`a2a_transport_server` 路由表
`dict[str, dict[str, Callable]]` 精确化为 `_RouteHandler` 别名；删两个从未引用的私有 Protocol、
三处死变量；`debate_engine._run_round` 参数原标 `Blackboard`、实收 `BlackboardEntry`。

**门禁口径数字**：config drift 229==229；静默 0/0（suppress 47/47）；mypy 510 文件 0 错误；
ruff 全绿。单元测试与覆盖率棘门（baseline 81.0%）见 PR 检查。

**一个 flaky 的根因在测试、不在实现**：`test_coordination_m4.py::test_ws_receives_conflict_alert`
等 scan 的 conflict_alert 广播，但该广播由 scan 请求内的 `asyncio.create_task` 发出——TestClient 每
请求新 event loop、返回即取消 pending task（隔壁 `test_ws_receives_broadcast` 的注释早已写明这条
限制），广播在 TestClient 下**可能永不到达**；而收包循环的 try/except 只防“连接断开”、防不了
“一条都不发”，`receive_json()` 无限阻塞，只能等 pytest-timeout 判红。同一份代码、同一顺序，
两轮 5 文件复跑一轮 183 passed、一轮直接挂 120s。修法忠于该测试自己的注释原意
（"may or may not appear... we verify the scan itself worked"）：广播等待改为**有界** —— daemon
线程 drain + 10s 超时后从测试线程 `ws.close()`（close 只走 send 侧、永不受阻，可让 drain 抛
`WebSocketDisconnect` 退出，`with` 块退出不僵）；硬断言只留 scan 的 `active_count >= 1`。
修后该用例连跑 3 次 + 与 `test_blackboard.py`（全局单例 reset 源）组合各 3 次全过。

**第二个挂起：测试 patch 的目标已经不在执行路径上**：`test_distributed_execution.py::test_distributed_fallback_on_error`
patch 的是 `_get_distributed_scheduler`（legacy 桥，现在只有 `_run_distributed` 用），而
`run(distributed=True)` 早已改走 `_distributed_backend`（TaskBackend 抽象）。于是"模拟调度失败"根本
没发生，请求直落真的 `DistributedTaskBackend`，在 fake Redis 的结果流上无限轮询，把 pytest 进程拖到
**无汇总退出**——表现是"跑到 28% 就没了"，连 FAILED 行都没有，只有 pytest-timeout 的线程栈
（MainThread 卡在 `pytest_asyncio` 的 `run_until_complete` → Windows IOCP）。修法：patch 目标改为
`_distributed_backend`，装一个 `run()` 直接抛 `RuntimeError` 的假后端，`engine.py` 219–227 的回退
分支才真正被验证。修后该文件 59 passed、0 Timeout。

顺带记一条**产品侧同类风险**（本 PR 未改）：`DistributedScheduler._dispatch_and_collect` 的
`while pending` 只在 `deadline is not None` 时有界；生产上若结果消息丢失且未设 deadline，会无限
等待。本 PR 只修测试契约，不动调度器的超时语义——那需要单独设计（默认 deadline 取值会影响长任务）。

**全量跑到 100% 后剩下的 3 条红，都是"契约改了、调用方没跟"**：

- `test_distributed_audit_fixes.py::TestReadResultsCursor` 两处仍按二元组解包 `_read_results`，而
  spawn 扇出已把它改成 `(results, spawns, cursor)` ⇒ `ValueError: too many values to unpack
  (expected 2, got 3)`。改成三元组解包（该测试只关心 cursor 语义，spawns 用 `_` 忽略）。
- `test_dead_wiring_inventory.py::test_orphan_set_matches_the_inventory`：7 个新模块零生产导入方。
  逐条判定而不是一把塞进清单：`ks_agent_bridge` / `event_federation` 经 `reliability/__init__`
  的 `_SYMBOL_TO_MODULE` + `__getattr__` 惰性解析可达 ⇒ `dynamic`；
  `blackboard_engine_bridge` / `distributed_checkpoint` / `distributed_lease` /
  `distributed_checkpoint_coordinator` 确实没有调用方 ⇒ `unwired`；
  `ws_blackboard_bridge` 的 docstring 原本声称"由 lifespan 自动启动"，而全仓**除它自己外无人
  import** ⇒ 登记 `unwired` **并同步修正 docstring**——不修就等于让清单给一个假声明背书。

### CI 首轮红在覆盖率，顺着覆盖率又挖出六个真 bug（分布式恢复/租约/能力查询其实不工作）

全量在 Linux 上跑到 100%、单元测试全过，红的却是 `Coverage ratchet gate`：master 是 81.43%，
本 PR 降到 **80.59%**（新增 3261 行只有 **65.6%** 覆盖），跌破 baseline 81.0%。本地 Windows
测出 81.63% 是**平台虚高**——Windows 专属分支在 Linux 上不计覆盖（平台差集 1550 行，全在既有文件）。

补 `tests/test_coordination_unwired_components.py`（59 条）覆盖那些"登记为未接线、但接口契约
必须先钉住"的组件时，**测试立刻推翻了实现**——四条都是"测试只走 InMemory / 只走 happy path"
掩盖的缺陷：

1. `DAGCheckpoint.__init__` 用 `x or default` 处理 `pending_layers` / `created_at`。空集合与
   `0.0` 都是 falsy ⇒ 从 Redis 反序列化（`pending="[]"`）后被**重新填满全部层**：`is_complete`
   永假，`recover_run()` 会把已完成的层再跑一遍。改 `is None` 判定。
2. `mark_layer_done()` 只原子更新 `:pending` / `:completed` **集合**，从不重写 hash 里的 JSON
   副本 ⇒ `get_run()` 返回 `create_run()` 时的陈旧 pending，恢复点永远是第 0 层。改以集合为
   权威（`get_run` / `list_interrupted` 都用集合覆盖 hash 值）。
3. `_renewal_loop` 里 `await self._redis.set(...)` —— 这个类用的是**同步**客户端（`acquire`
   就不 await），`await bool` 抛 `TypeError` 被 `except Exception` 吞掉 ⇒ **自动续租从未生效**，
   实例在 TTL 过期后仍认为自己持有租约（双主风险）。去掉 await。
4. `RedisAgentStore.get()` 的 `hget` 漏了 `await`（异步客户端）⇒ 每次查询 TypeError；
   `list()` 是对的，只有 `get()` 漏了。
5. `RedisMCPRegistry.async_find_tools()` 的 `hgetall` 同样漏 `await` ⇒ `items` 是 coroutine、
   `isinstance(items, dict)` 为假 ⇒ **每次搜索都返回空列表**。同步版 `find_tools()` 则是直接
   `return []`（"没查到"和"查不了"长得一样）——改为打 warning 说明它查不了 Redis。
6. `RedisAgentRegistry.find_agents(capability)` **完全忽略 capability 参数**：只按 status 过滤后
   把所有 agent 都返回。docstring 写的是"providing the given capability"。改为注册时缓存
   capabilities 并真正过滤。

覆盖率变化（这三个模块）：`distributed_checkpoint` 22.4%→94%、`distributed_lease` 26.1%→91%、
`bb_task_context` 38.2%→69%；另给 `RedisAgentStore` 补了 save/get/remove/list 的回归用例，
给 `mcp_hub` 的四个传输补了"未 connect 就发请求必须报错"的守卫用例（覆盖我加的那条
`self._proc.stdin is None` 判定），给调度器 `DistributedCheckpointCoordinator` 补了
save/load/list/cleanup/serialize 的契约用例，并给 `engine_router._create_redis_client_from_env`
补了四条分支（未配置 / 显式空串 / 显式有值 / 建连失败）—— 那正是 config drift 修复的语义本身，
不钉住就等于只换了个真相源。

### 主干 `Secret Scan` 的 69 条命中曾经查不动：报表路径与上传路径对不上，还被 `ignore` 消音

2026-10-16 主干 run `38902904241` 里 `Secret Scan (gitleaks)` 判红，扫描步自己打出
`leaks found: 69`，但**没有任何地方能看到是哪 69 条** —— 因为 `History scan` 那步没有
`working-directory`（`gitleaks git .` 就必须在仓库根跑，才覆盖全库），报表实际落在**仓库根**的
`gl-history.sarif`，而紧随其后的上传步去找 `py/gl-history.sarif`，还配了
`if-no-files-found: ignore` ⇒ 报表"静默地没上传"，上传步自己仍是 success。
我列过那次 run 的全部 artifact 来确证：`gitleaks-history-report` 根本不存在。

改三处：

- 上传路径对齐为 `gl-history.sarif`（口径由扫描步的工作目录决定，不是由惯例猜）。
- `if-no-files-found: error`：安全扫描的证据链断掉本身就是一条可见失败，不许当成"没有就算了"。
- `tests/test_ci_workflow_hygiene.py` 加三条结构守卫（解析 YAML 看**步骤字段**，注释不算数）：
  ① 声明了 `--report-path` 的作业必须有上传步，且**判据集合为空即红**（防止守卫自己空转成
  全称命题恒真）；② 报表"生效工作目录 + 文件名"必须与某个上传步的 `path` 完全一致；
  ③ 扫描报表的上传步不许用 `if-no-files-found: ignore`（只约束扫描器产出的报表，覆盖率/前端
  产物那些可选上传不在内 —— 它们缺席没有取证价值，红一次反而扰民）。

变异验证三处各有红、且红在该管的那条上：上传路径改回 `py/` ⇒ ② 的路径守卫 1 红；
`error` 改回 `ignore` ⇒ ③ 1 红；删掉 `--report-path` ⇒ ① 的空集守卫 1 红。还原用 `cp` + `cmp`
逐字节回证，之后 6 passed。

顺带记一条与本次相关的实测：`main` 被覆盖成新提交后，那笔含 `nvd api-key.txt` 的桌面快照提交
已**不能从任何 ref 走到**（`git log --all` 与逐 ref 的 `merge-base --is-ancestor` 都是 0/无），
所以 gitleaks 的全历史扫描理应不再撞见它 —— 本 PR 的 CI 就是这件事的验收现场：
`Secret Scan` 若回到 `no leaks found`，即证 637 那 69 条是外部误推、非本仓引入。
**但这不等于泄露已收回**：覆盖 ref 不删对象，GitHub 侧那个 commit 仍按 sha 可取（我实测 API
仍能取到），密钥吊销与对象清除得人工走。

### `compose_smoke.py`：超时不再红得零诊断（主干 run 38867035723 的 `Compose Smoke`）

主干 run `38867035723`（`b4a5900`）唯一红项是 `Compose Smoke`，而日志里除了一句
`subprocess.TimeoutExpired: ... timed out after 1800 seconds` 什么都没有 —— 作业红了 30 分钟，
却回答不了"哪个服务起不来"。原因在实现层：`sh()` 用 `capture_output=True` 收着 compose 的输出，
而 `subprocess.run` 超时时**抛** `TimeoutExpired`，异常一路冒出 `main()`，已经把到的部分输出
整个丢掉；那条"红也要带病历"的分支（`returncode != 0` 时 dump 尾部 + 错误行）根本没机会执行。

- `sh()` 不再让超时外抛：折成 `rc=124` 并保住掐断前的 stdout/stderr（bytes/None/str 三种形态
  统一过 `_text()`，下游 `.splitlines()` 不会崩）。
- up 失败与 health 超时**共用一条诊断** `dump_stack_state()`：`ps -a`（不带 `-a` 时已退出的容器
  压根不在清单里）+ `config --services` 列出的**每个**服务的日志尾部。原来 health 那条只 dump
  `dashboard`，pg/redis 卡住时病历不在场。
- 服务清单取不到 ⇒ 明说"compose 文件本身就没解析过"并**不**再逐个打 logs —— 那时每个服务只会
  把同一条文件级错误再抄一遍。
- 诊断按**病因内容**折叠重复（`PS x` 与 `LOG[pg] x` 是同一件事），并打出折叠了几行；抄 N 遍会把
  唯一那条线索埋掉。
- 诊断那几步各自 60s 上限（`DIAG_TIMEOUT_S`），且断言它必须显著小于 1800s —— 否则"取病历"能把
  作业再拖死一遍，把一个看得见的红换成一个看不见的红。

`py/tests/test_compose_smoke_diagnosis.py` 20 条，不需要 docker（假 `subprocess.run` / 假 runner）。
变异验证三处各抓到 2/2/3 条红：超时改回外抛、折叠改成永不折叠、诊断改回只看 dashboard；
还原用 `cp` + `cmp` 逐字节回证后 20 passed。入口也在本机真跑过两遍（`docker compose` 客户端可用、
守护进程不在 ⇒ 天然是一条真实的失败路径），第二遍的输出从 12 行重复收敛到 9 行且带折叠说明。
**注意**：这条作业的 `if` 只在 `develop/master/main` 的 push 上跑（有守卫
`test_smoke_script_is_the_only_compose_entrypoint_in_ci` 钉住这点），所以 PR 上不会验到它 ——
本改动的最终判定仍要等下一次主干 push。

### §7.7 金丝雀补上"归因前置"：过期 run 不再能把自家没跑成判成平台漂移（假红修复）

2026-10-15 实测到一次**我自己写的门禁的假红**：PR #110 的旧 head `cb67725`（run
`38729427212`）被同 ref 的新 push 抢占取消，`Docs consistency gate` 判 `cancelled`、
`CI merge gate` 判 `failure`，而带 `if: always()` 的金丝雀在 3.5 小时后仍被调度起来。它读的
`statusCheckRollup` 跟着 **PR 现 head** 走，而那一刻现 head 的新 run 一条 check 都还没产出 ⇒
清单 4 条全 `<缺席>` + `mergeStateStatus=BLOCKED` ⇒ 判出 `DRIFT: 4 处`；同一个 PR 四分钟后在真
head（run 632）上判 `MATCH`。

根因不是时序运气，而是**判据缺一个前提**：`absent && BLOCKED ⇒ DRIFT` 只在"本轮 run 确实有权
代表这个 PR"时才成立，而"清单与保护真脱节"与"本轮 run 已过期"在汇总里长得一模一样。

- 判定前先过 `attribution()` 两道**独立**归因：① `${{ toJSON(needs) }}` —— 清单作业没全
  success（failure / cancelled / 被切 / needs 里查不到）就没资格说"缺席是平台造成的"；
  ② `pullRequest.headRefOid` 对比本轮 run 的 head sha —— 不等即过期 run。两种都判
  `NOT-APPLICABLE` 弃权（退出码 0，但**不是** MATCH，且把原因写成 annotation）。
- 两个入参任一为空（env 没接上、JSON 坏）判 `UNVERIFIED`（3），不静默退回旧行为 ——
  "没接线"不能冒充"接了线且通过"。
- `kind=scope-gated` 的清单作业允许 `SKIPPED`（docs-only 时合法不产出 check），现在清单里
  没有这种条目，先写对，免得将来加一条就恒弃权。
- 测试同时钉**两个方向**（各 4 条）：复刻过期 run 的用例必须**不出现** `DRIFT`；而"needs 全成功
  + head 未变 + 一条 required 缺席 + BLOCKED"必须**仍是** `DRIFT`。只有前一半的话，这道守卫
  就是又一个"把自己的判据换成永不表态"的自我失明门禁。变异验证：把 `if reasons:` 改成
  `if False:` ⇒ 5 条红；把 `return "OK", []` 改成恒弃权 ⇒ 4 条红。

`ci.yml` 的 canary 作业多喂两个 env（`NEEDS_JSON` / `PR_HEAD_SHA`），并有接线守卫
`test_canary_wiring_feeds_the_attribution_inputs` —— 注释掉 env 或表达式打错时，判定端只会看见
空串，金丝雀会退化成"永远 UNVERIFIED"，那条守卫就是为了让这种断线当场变红。`docs/ci-gates.md`
§7.7 的判定表补两行、要点补第 4 条并记下这次假红的完整证据链。

### 把 §7.7 的"带外对账"从单机脚本收进仓库：`tools/drift-audit/`

方案二此前只活在一台机器的目录里（无版本控制、无回滚点），仓库里 §7.7 只能写通用措辞。
现在工具入库，路径全部参数化，**不含任何机器本地路径**：

| env | 默认 | 说明 |
|---|---|---|
| `MAOP_DRIFT_WT` | 本脚本所属检出 | 对账用哪个检出 |
| `MAOP_DRIFT_UPDATE` | 关 | `=1` 才 `fetch + --ff-only`（脚本不擅自改别人的工作树） |
| `MAOP_DRIFT_REPO` | 从 `origin` URL 解析 | 四种 URL 形式实测均得 `owner/name` |
| `MAOP_DRIFT_TOKEN` | `gh auth token` | 仍**不要求** PAT、不要求仓库 secret |
| `MAOP_DRIFT_LOG` | `${TMPDIR}/maop-drift-audit.log` | 结论日志 |
| `MAOP_DRIFT_GAP_H` | 36 | 缺勤阈值（小时） |

`run.sh` 每轮开头做**缺勤可见性**自检：漏跑的轮次不会在日志里留痕，而日志是这条守卫对外的唯一
凭据，所以超阈值就补一行 `GAP(...)`，并分三态 —— `NO-PRIOR-RUN`（没历史/被删）、`PARSE-FAILED`
（时间戳解不开）、`SCAN-FAILED`（扫描工具本身缺失）。三态与"确实新鲜"分开是必要的：把"我扫不动"
说成"没历史"会凭空造一个不存在的事故，把"没历史"说成"新鲜"则让缺勤重新隐形。

`selftest.sh` 11 探针 / `PASS=26 FAIL=0`：正向对照、双向合成漂移各自点名、令牌为空与依赖缺失都
fail-closed、缺勤四态 + **新鲜时的负对照**（没有负对照，正例可能只是空转）。P10 顺带自己证明了
自己存在的理由：农场里 `grep` 缺席 ⇒ 打出 `SCAN-FAILED 无法扫描日志（grep 退出码 127）`，而不是
误报"日志被删"。若把那句写成 `hits="$(grep … | tail -1)"`，`$?` 取到的是 `tail` 的退出码，
这个区分就不复存在。

`docs/ci-gates.md` §7.7 改为指向 `tools/drift-audit/`；`.gitignore` 增加自测临时目录。

### KV 层补上原子 insert-if-absent（`KVStore.add`）：认领协议缺的那一步

`docs/design-blackboard.md` 早就写了 `LOCKED` / 乐观锁 `version` / `lock(ttl_s)`，但主干符号命中为 0
（`EntryState`、`VersionConflictError`、`blackboard_locks`、`SUPERSEDED`、`def lock` 各 0）。更关键的是
**底座本身缺一环**：`kv_store.py` 的 `cas()` 只能对已存在的行做版本比对（`row is None → success=False`），
`set()` 是无条件 upsert —— 于是"抢空槽的第一人赢"**无处表达**：两个 agent 都读到"没人认领"、都 `set()`、
都以为自己拿到了。

新增 `KVStore.add(key, value, *, namespace, ttl) -> CASResult`：

- 单条 `INSERT ... ON CONFLICT(key, namespace) DO UPDATE ... WHERE 过期` 完成，**不读后写**；连接池
  `acquire()` 在 `threading.Lock` 下 pop、一连接不同时给两线程，新连接设了 WAL + `busy_timeout`，原子性成立。
- **过期的行算"不存在"**：`get()` 本就把过期行藏起来（`ttl_expires > now` 严格），而物理删除受 30s prune
  节流 —— 若只写 `DO NOTHING`，一个已超时的认领会在行被清掉前**一直抢不到**，锁卡死超过它的 TTL。
  守卫直接判过期即可抢，不依赖 prune 何时跑。
- **替换过期行时 `version` 递增而非复位为 1**，使版本号可当 fencing token 用（旧持有者回来能被认出）。

`tests/test_kv_store_add.py` 11 例，两例用真线程跑 8 路并发（断言恰一个赢、败者看到赢家的 version、
落库的是赢家 payload）。**变异测试**：① 改成"先 SELECT 再 INSERT" ⇒ 并发例转红；② 去掉过期守卫改回
`DO NOTHING` ⇒ 过期例转红。

过程里抓到一个**空断言**：过期用例最初在两次 `add` 之间调了 `kv.get()`，而 `get()` 会触发
`_maybe_prune()`，全新库的第一次读**立刻**清掉过期行 ⇒ 变异 ② 当时照样绿。改成先用 `sqlite3` 直查、
断言"行仍在表里"之后，这条测试才真的在测守卫。教训：断言"过期行还在"不能靠那个会顺手清理的读接口。

`cas()` 不能创建这点由 `test_cas_cannot_create_a_missing_key` 钉住 —— 不是遗留 bug，而是
`SQLiteKVBackend.cas` 拿 `expected_version=0` 当探针的前提（版本从 1 起，0 永不写）。

本条只做底座，不含 claim/lease 层：`RedisDistributedLock`（实现正确、零生产调用方）与
`core/coordination/`、`distributed_lease.py` 属他人正在进行中的批次，接在它们之上更合适。

### CI 修复：`test_embedding_is_not_on_the_request_path` 摘掉两条可假红的判据

主干 run `38473901358`（`08435817`）只在 **windows-latest / Python 3.10** 一条腿红，
`1 failed, 9180 passed`，失败点是 `assert elapsed < 0.25` → `assert 0.36 < 0.25`，
且 `3 rerun` 恰好等于这一条被重跑 3 次仍失败 ⇒ **4 连败，不是一次性 flake**。

- **假红的根据**：紧挨着它的上一行 `assert provider.image_calls == []` **是过的** —— provider
  一次都没被调用，即嵌入根本没有走请求路径。0.36s 是 `save_image` 自身的文件 IO +
  `sha256` 读盘 + 单例初始化在重载 runner 上的成本，与被守护的契约无关。同文件模块
  docstring 第 8-10 行本就写着"asserted as zero embed calls，**not as a wall-clock budget** ——
  墙钟在 Windows runner 的 `-n auto` 下会 flaky"，这条断言违背的是文件自己声明的准则，已删。
- **顺带修第二条同族隐患**：`assert memory.drain_ingest() == 1` 只在"调用方抢在刚起的 worker
  线程之前取走队列"时成立 —— `maop/memory/multimodal.py` 里 `drain_ingest` 的 docstring 明写它
  **看不见 worker 已取进 partial batch 的条目**。该腿此前在上一条断言就中止，所以这行从未在
  windows-3.10 上执行过。改成本文件既有测试（`test_reupload_of_identical_bytes_*`）已经在用的
  `close_ingest()` + 断言嵌入调用次数：无论批次由谁写出，join 之后计数都是确定的。
- **覆盖率没有净损失**（反向变异实证）：临时把 `MultimodalIngestQueue.submit` 改成"入队即
  `drain()`"（等价于嵌入回到请求路径），保留的那条断言立刻转红
  ——`assert [WindowsPath('…b2321dd.png')] == []`，而"变异是否生效"的自检未触发。

### M2.2 B2 存储量化评估：PG vs SQLite 实测（ADR-022 D7 补齐）

ADR-022 D7 此前只说明 SQLite 路径的预算，PG 侧一直没有实测数字。用真
pgvector 库 + 真代码路径补上（脚本 `scripts/measure_multimodal_storage.py`，
合成条目写自有前缀后删除，不碰真数据）：

- 万条规模：**PG ~2.7KB/条**（TOAST 压缩后堆均值）vs **SQLite ~4.2KB/条**
  （4KB 页装不下"2048B 向量+元数据"，页内碎片推高）⇒ 换 PG 的理由是
  并发/运维而非容量，量级相同。
- 写入吞吐：PG 16.4s vs SQLite 129.7s（万条，单事务批写 vs 逐行 upsert）
  ⇒ SQLite 上跑大规模回填会很慢，`sqlite_to_pg` 迁移大批数据时预料这个量级。
- "NFR 增量 < 原始 1%"的数学记账不受影响（元数据开销比净载假设估的还小）；
  ANN 索引仍未纳入审计，支撑 ADR-022 D2"小表走精确扫描"的决策。

### M2.2 B2 收尾：多模态运行时接线（provider 工厂 + 上传自动索引 + 停机闭环）

B1/B2 交付的是"引擎"，本条补"传动轴"。实测发现**两处真断链**：异步摄入队列
零生产调用方；`ClipEmbedding`（真 CLIP provider）**全仓无任何构造点**——
即生产路径上多模态记忆一直跑在 hash 回退上（非语义）。既有 dead-wiring
门禁只管模块级孤儿，类级死代码不在其口径内，所以它能长期绿着。

- **provider 工厂** `core/memory/multimodal_embed.resolve_multimodal_provider()`：
  解析顺序 = 显式注入 > `MAOP_MULTIMODAL_PROVIDER` > 自动探测；自动 = 装了就
  上 CLIP、否则 hash 回退（缺 extra 记 warning，装了一半/权重不可达记 error），
  进程内缓存（CLIP ~600MB，绝不允许每请求加载）。`force="clip"` **失败即报错**，
  不静默降级。接入 `_build_store` 的 SQLite/PG 两个分支（此前只默认 hash），
  并新增 `embedding=` 注入 seam。
- **上传自动索引**：`chat_service.save_image()` 落盘后走
  `maybe_index_uploaded_image()` 异动入队（fail-soft，索引失败不影响上传）。
  条目 id 由**图片 checksum** 派生（`img-<sha256[:16]>`）——上传库每次上传都发
  新 uuid，按 uuid 派生会造成重复入库。开关 `MAOP_MULTIMODAL_AUTO_INDEX`：
  默认**自动**（provider 语义型才开；没装 extra 的部署行为逐字节不变），
  三态闭集解析 + 非法值 warning。
- **停机闭环**：dashboard lifespan shutdown 由 `drain()` 改为
  `close(drain=True)`——后者才覆盖 worker 已取走、攒在批里的条目，否则**每次
  优雅停机必丢** ≤`flush_interval_s`(2s) 的上传；启动段预热 provider，首个
  上传不再独扛模型加载。停机用**非创建入口**取单例（没上传过的部署不白加载）。
- **真丢数据路径修复**：`index_many` 图像组原为"一坏全丢"（8 张里 1 张被删/
  损坏 ⇒ 整批 skip 只留 warning）——正是接线后最易触发的路径（上传→删除→2s
  后批次失败）。改 per-item 降级：先整批、失败逐张重试、只跳过失败者，
  SQLite 与 PG 两后端同修。`drain()` 另改按 `batch_size` 分块（防积压 50 张
  一次前向拖爆 close 的 join 超时）。
- **单例卫生（ADR-019）**：`reset_multimodal_memory()` 现在 close 所有队列 +
  清 provider 缓存，并进 conftest autouse teardown 与 sessionfinish；
  `get_multimodal_memory()` 只在 `text_search is not None` 时重绑（上传路径
  不再静默抹掉 RRF 文本腿）。
- **门禁补齐**：新增 `tests/test_multimodal_wiring_guards.py`——AST call-site
  检测（grep 会被类定义/docstring 骗过）+ 扫描器自证用例 + 真变异实测
  （删掉 CLIP 接线 ⇒ 门禁红）。`test_multimodal_provider_factory.py` 9 例、
  `test_multimodal_autoidx.py` 12 例。

验证：多模态相关 13 个套件 187 passed；chat/server/settings/env-drift 81 passed；
ruff+mypy 干净；四门禁全绿（`.env.example` 两变量同步）。

### M2.2 B3：多模态空间扩展契约（注册表 + 通用写入 + 参数化 DDL/迁移）

兑现 ADR-022 D5 承诺的另一半。此前 `MultimodalStore` 把表名/维度/模态硬编码，
**第二个空间加不进来**——企业侧要上音频空间必须改 core，正是 D5 隔离承诺要排除的。
本轮（独立审核 REJECT 12 项后修订）：只参数化维度不参数化表名 ⇒ 新空间静默写错表，
且 `_write` 的 warning 会把它吞成"入库全丢"。

- **空间注册表** `core/memory/multimodal_spaces.py`：`MultimodalSpaceSpec`
  （name/table_name/dimension/modalities/backend/model_id）+ 注册/查询/列举/重置。
  默认空间 `multimodal-512` import 时自注册，**取值与 B3 前逐字节一致**（既有
  三处钉子测试零改动通过）；同名重注册定义不同即报错。
- **构造路径收殓为一条**：`_build_store` 经注册表取 spec，PG/SQLite 两 backend
  都把 spec 传到底 ⇒ 注册表是唯一入口而非装饰。
- **通用写入 `index_modality`**（两后端 + Protocol）：调用方自带向量 ⇒ 不触发
  图像校验（音频字节不再被 `validate_image_input` 拒）、**modality 不被覆写**
  （旧 `index_text` 会强制盖章）；不接受该模态的空间抛错并列可选值。
- **DDL/迁移参数化**：`sqlite_ddl(table,dim)`/`pg_ddl(table,dim)`（默认输出不变，
  索引名按表名派生 ⇒ 同库两空间不撞名）+ `render_pg_migration()` 生成 alembic
  revision（测试断言它复现已提交的 003 迁移，防运行时与迁移再漂移）。
- **按空间隔离**：单例 key 加空间分量；`get_multimodal_memory_if_exists()` 改为
  返回**全部**实例、停机 drain 遍历关闭（此前任意取一个 ⇒ 第二空间队列丢）。
- **诚实降级**：provider 维度与 spec 不符 ⇒ `_build_store` 降级同维度 hash 并
  warning；**显式注入**的 provider 维度不符直接报错（配置错误不该被吞）。
- 门禁补齐：`test_multimodal_spaces.py` 23 例（注册表语义/默认空间等价/DDL 派生/
  两空间 SQLite 真跑 + PG stub 引擎断言/迁移渲染/单例隔离）；死代码守卫加
  `index_modality`/`get_multimodal_space` 两个符号 + "服务层必须经注册表构造"。

**边界（如实记账）**：音频/视频**实现**（Whisper/CLAP/ffmpeg）按 ADR-016 归私有仓
MAOS，不在 MIT 本仓；MAOS 接入三步见 ADR-022 D11。跨空间融合检索仍不做（RRF 是
单实例内两腿，跨空间聚合留 B3+）。

### M2.2 B4：检索质量可度量（NDCG harness + 标注语料 + 判别力门禁）

F2-02 此前只有二值断言（"跨模态 top-1 8/8"），既答不了"质量多少分"也比不了
两个 provider。现在有了可复现的度量，并且产品化成一个自检命令。

- **`maop eval multimodal`**（`core/memory/retrieval_eval.py` + `cli_eval.py`）：
  对当前进程解析出的 provider 跑 NDCG@5，打印报告。hash 回退会拿到接近随机的
  分数并附"装 extra"提示——这既是用户自检，也是该模块的生产引用方
  （工具级死代码是 dead-wiring 门禁的盲区，见 ADR-022 D10）。
- **指标与语料**：NDCG@5（IDCG 同 k 截断、全零相关 ⇒ 0.0），分级相关
  （2=同色同形 / 1=同色异形 / 0）；12 张确定性合成图（4 色 × 3 形状）+
  8 条查询（exact 4 + paraphrase 4），真 CLIP 向量冻结成
  `fixtures/clip_eval.json`（`scripts/build_clip_eval_fixture.py` 复算，
  `--check` 模式无 torch 可校验；revision 钉子 + 图片字节哈希双防腐化）。
  查询文本**不入**被检语料——否则 hash 对相同字符串恒等、#1 白拿。
- **判别力门禁（本 PR 最重要的三条用例）**：同一数据集、同一代码路径，
  CLIP **0.8661** vs hash **0.1005**（边际 +0.766）。测试钉 CLIP ≥ 0.75 /
  hash ≤ 0.25 / 边际 ≥ 0.5；变异实测：把排序改成按 id 排 ⇒ 三条全红。
- **先量后标**：建夹具时先跑 12×12 sanity 矩阵，发现 CLIP 颜色强、形状弱
  （circle↔triangle 两处失手）⇒ grade 设计成"同色异形 = 1 分"与模型能力对齐。
- 测试 23 例（22 常规 + 1 `slow` live-CLIP 互证：本机实测 cosine ≥ 1−1e-4，
  CI 无 torch 自动 skip）；NDCG 定义用手算值钉住（`log2(i+1)` vs `(i+2)`、
  IDCG 截断、k 截断三类变异都杀）。
- **边界**：不宣称 PRD 的"人工评估 NDCG≥0.8"；语料 12+12 而非原计划 50-100
  （规模缩减按规则 #3 记账在 ROADMAP，扩容随时可做）；多模型横评 defer
  （触发条件：第二候选模型 / 中文查询低于水位——中文查询是已知盲区）；
  只评 text→image 单向。

### M2.2 B2 PR-4：真 PG 腿（pgvector 镜像 + migration 链 + 非 mock 集成）

- CI「Alembic migrations」作业的 service 容器从 `postgres:16-alpine` 换成
  **`pgvector/pgvector:pg16`**（env 语义与官方一致，审核实测；pgvector 0.8.7）。
- 新增 **pg 树迁移链**真跑步骤（`alembic -c migrations/pg/alembic.ini` 的
  upgrade→downgrade→upgrade）——该树才是 PG schema 的真源（根树是 SQLite-only DDL）。
- 新增 `tests/test_multimodal_pg_integration.py`（8 例，非 mock）：真实表/列/索引、
  跨模态排序（真 CLIP 夹具向量）、模态过滤、图找图、批写原子性、delete/clear、
  确定性（连续同查询同序）。**无 PG 或 psycopg2 时整文件 skip**，默认套件不受影响。
- **设计修正（实测驱动）**：partial-HNSW 每模态索引改为 **modality B-tree 表达式索引**——
  pgvector 0.8.7 上 partial HNSW 在只有寥寥数行时会把明显更优的向量排到后面
  （实测 "a red square" 查询下 img-blue 一度压过 img-red）。空表层上近似索引无收益、
  有风险；pgvector 官方文档同样建议小数据集走精确扫描。ANN 索引留给后续按数据量评估
  的迁移（多模态 `D2` 索引决策的偏差，记入 ADR-022）。
- 踩坑记录（测试隔离）：service 容器被所有 xdist worker 共享 ⇒ fixture 用
  per-worker **schema**（`search_path` 保留 `public` 以看到库级 vector 扩展）；
  `pg_indexes`/`information_schema` 是全库视图 ⇒ 断言必须按 `schemaname` 限定，
  否则旧 DDL 世代的 `public` 表会替 worker 的表作答。
- 顺带修 `sqlite_to_pg.migrate()` 的 mypy 回归（`pq_ready` 分支 + `results` 重名）。
- **修复 CI 红（驱动二义性，本轮实测第 6 个 bug）**：alembic 作业只装 `-e py/`+
  alembic，**没装任何 PG 驱动**；且裸 `postgresql://` 的驱动由 SQLAlchemy 版本决定
  ——2.0 解析成 psycopg2、**2.1+ 解析成 psycopg (v3)**，于是本机（2.0 绿）与 CI
  （拉到的 2.1.4）行为不同，PG 腿一上真环境就 `ModuleNotFoundError: No module
  named 'psycopg'`。修法两件：① 新增 `maop/db_url.py` 的 `resolve_pg_driver()`——
  按**实际可导入的驱动**显式钉 URL 驱动（优先 psycopg3，回落 psycopg2；都没装则原样
  返回，让报错仍然指名字段），并接入全部 6 个入口（pg env.py、sqlite_to_pg、
  vector_migration、db_utils、pg_backend、multimodal_pg）；② CI 安装步骤补
  `psycopg2-binary`。新增 `tests/test_pg_url.py`（15 例，含两驱动均缺失时的回落分支）。

本地证据：`pgvector/pgvector:pg16` 容器（7655）真跑 8/8（含 xdist 模式 8/8）。

### M2.2 B2 PR-3：异步摄入队列，闭合延迟预算（ADR-022 D4）

spike/B1 实测：CLIP 单张图像嵌入热机 65-293ms，贴着 PRD 的 <200ms；而 batch=8
单次前向摊销 ~53ms/图。⇒ 达标路径就是「到达即入队、后台批处理嵌入」。

- 新 `core/memory/multimodal_ingest.py`：`MultimodalIngestQueue` —— 有界
  `queue.Queue` + 后台守护线程；`submit()` **不等嵌入**（<10ms 返回，测试钉住）；
  攒批到 batch_size 或 flush_interval 即一次 `index_many`（后端按 modality 分组、
  各组一次前向、PG 单事务）；`drain(timeout)` 显式排空（整队合**一批**写——逐条写会
  破坏摊销，测试钉住）；`close(drain=True)` 关停前排空；批量失败只记日志不杀 worker。
- `MultimodalMemory.store_async(content, ...)` / `drain_ingest()` /
  `ingest_queue`：服务层入口（Facade 侧按需再接）。
- 关停语义：关闭后 submit 返回 `""`（不静默丢数据），测试钉住。
- 边界：不做跨进程持久化队列（2s 批窗口上，SQLite 队列只会加磁盘故障面）；
  PG 侧批处理本就单事务（PR-1），队列只是把异步语义补上。

验证：14 例守卫 + 多模态相关 48 例通过。

### M2.2 B2 PR-2：迁移工具多源引擎 + multimodal BLOB→vector 转换

多模态记忆在独立库文件 `multimodal.db`（ADR-022 D2），而 `sqlite_to_pg`
此前只用一个源引擎自省 `maop.db` ⇒ `multimodal_entries` 对迁移不可见、
复制**报 0 行且成功**（绿谎）。

- `migrate()` 新增 `_multimodal_source()`：按"显式 url 指向 / 主库同级 /
  不存在则跳过"发现多模态库，复制时按表选源引擎（不存在时零影响）。
- 新 `migrations/pg/value_converters.py`：按 (table, column) 注册转换器
  （唯一一条 = multimodal vector 的 float32 LE BLOB → pgvector 文本字面量，
  位相等；长度非 4 倍数抛错不补零）。接进 `_transform_row`（转换优先于
  JSON 解析）；`_JSON_COLUMNS` 只登记 metadata。
- **dry-run 零 PG 依赖**：修复前无条件 `create_engine` 会让没装 psycopg2
  的机器连预演都跑不了（本 PR 测试首跑即踩）。现 dry-run 只读 SQLite 侧
  计数；live 模式建不了 PG 引擎则响亮失败，不静默复制 0 行。

验证：14 例新守卫 + 既有迁移/向量测试 103 例零回归。

（下批工作区：M2.2 B2 PR-3 异步队列、PR-4 CI PG 腿；巨型文件拆分；keyring 后端。）

### CI：required checks 可满足性金丝雀（PR 侧，无需 secret）

新增 `py/scripts/check_required_checks_satisfiability.py` 与 `ci.yml` 的
**`Required checks satisfiability canary`** 作业。它用默认 `GITHUB_TOKEN`
（`pull-requests: read` + `checks: read`）读 PR 的 `mergeStateStatus` 与 check 汇总，在 PR 上
发现两类结构性漂移：① 清单里的 required **从未上报**（作业改名/删除 → 所有 PR 永久 `blocked`）；
② 清单全绿却被判 `BLOCKED`（存在清单未列出的 required）。读不到实况一律 `UNVERIFIED` 且**非零退出**，
绝不静默判绿。刻意做成**非 required** 的诊断作业（自身 pending 会污染 `mergeStateStatus`）。
方向性局限（证不了"平台要求得更少"）由本机定时对账兜底，两处互补，见 `docs/ci-gates.md` §7.7。

## [5.4.0] — 2026-10-12

### 复评处置·第四批：bandit B608×141 逐站点处置清零 → CI 恢复 medium 门禁 `-ll`

141 处**逐站点核过**（84 处按分类加带理由 `# nosec B608`；57 处人工读构造器——结论全部
安全：子句构造器只含固定子句与 `?`；表名来自字面量闭集 / `_VALID_TABLES` 白名单 /
`validate_identifier` 校验 / 迁移元数据自省；列名来自 pydantic 模型字段或自省结果；
`kw`/值一律绑定为参数）。**未发现真注入面**；每处理由写在代码里（13 类模板 + 12 处按文件
的具体理由，如 `search.py` 的「field 经 allowed_fields 白名单，kw 为绑定参数」）。

**技术坑（实测，踩过一次并回滚）**：bandit 的 nosec 必须落在**报点同一行**——上一行独立
注释、赋值行注释都不生效；多行 f-string 的报点行末在字符串**内部**，直接追加会把注释写进
SQL 文本（且 Python 3.14 的 tokenize 把 f-string 拆成 `FSTRING_*` token，按 token 探测会漏）。
最终应用器改用 AST 的 `JoinedStr`/`Constant` 节点 `end_lineno`：注释落在字符串**收尾行**。

**顺带硬化**：`compliance_manager` 的表名白名单从 `assert` 改为 `if … raise`
（`-O` 下 assert 失效，白名单必须是硬校验）。

**门禁**：`ci.yml` 的 `bandit -r maop/ -lll` → **`-ll`**（兑现挂了两个月的承诺）；
`bandit -r maop/ -ll` 计数 **148 → 0**。nosec 清单门禁升级为 `path:RULE:count`
（44 条目 / 146 行；计数变化即红）——仍**不用行号**（行号位移假红在 ADR-023 已定案）。

### T2.1 验收达成：补文件系统级零副作用断言 + ROADMAP 勾选

PR #84 已交付哨兵契约/pipeline 预演/接线，但 AC 的「写操作零副作用（临时目录断言）」
一直缺**文件系统级**证据（既有断言只查 fake 的 `executed` 标志）。新增
`test_dry_run_writes_nothing_into_output_dir`：预演后配置的输出目录必须为空、临时根下
无残留，且哨兵行照常产出。ROADMAP v5.3.x 节 T2.1 勾选。

### 复评处置·第三批：bandit 真安全修复（B615/B310/B103）+ nosec 清单门禁

报告建议 2（bandit Medium 债）的**真修复部分**先落地（批量 B608 标注是独立批次，
需逐站点核构造器——处置表已生成入 `docs/audits/review-disposal-plan-2026-10-12.md` 附录 A）：

- **B615×2（本轮 B1 新引入的回归）**：CLIP 加载钉 40-hex 模型 revision
  （`DEFAULT_CLIP_REVISION`，当前 main 的实测 SHA——缓存命中、无需重下）；金标夹具
  `clip_gold.json` 记同一 revision；守卫测试断言常量是 40-hex 且与夹具一致
  （bandit 只看字面量，钉成 `"main"` 它不会报——所以必须有这条测试）。
- **B310×3**：MCP `_StreamableHttpTransport`/`_SSETransport` 增 **scheme 白名单**
  （`http`/`https`，挡 `file://` 本地文件外泄等；**刻意放行 loopback/私网**——本地
  MCP 服务器走 127.0.0.1 是常态，封私网属行为回归，与 marketplace 的远端 SSRF 规则
  分工不同；空 URL 保持可构造、`is_alive=False` 语义不变）；marketplace 已有
  `_assert_safe_url`（http/https + 私网阻断）→ 仅补 nosec 理由；deploy 健康检查
  校验 `dash_host` 不得夹带路径/userinfo（`/@?#`）→ nosec + 理由。
- **B103×1**：`agent_repair` 修复执行位不再 `chmod` 固定 0o755（那会把读权限**放宽**到
  other-readable），改为 `原 mode | S_IXUSR/GRP/OTH` 只补执行位。
- **nosec 清单门禁**（`tests/test_bandit_security_fixes.py`）：扫全仓 `# nosec` 逐条登记
  （新增即红——防"批量 nosec = 静默消音"），并要求每条 nosec 附近有理由文本。
  当前清单 5 条（3 新 + sandbox_executor 既有 ×2）。

**效果**：`bandit -r maop/ -ll` 147 → **142**（B615×2/B103/B310×2 消失，B310 余 1 已
带理由 nosec）；B608×141 待逐站点处置后切换门禁 `-lll`→`-ll`（先决条件已写入附录 A）。

### 复评处置·第二批：静默吞异常存量清尾（81 处全部处置，基线归零）

接 ADR-023 的门禁：81 条静默 `except` 逐条分类——设计性静默按闭集类别加
`# silent-ok: <类别> — <理由>`（类别集扩到 10：新增 `platform-limitation`
（Windows chmod/fsync）、`best-effort-cleanup`（清理失败不掩盖主错误）、
`expected-signal`（超时即等待信号））；宽异常 `except Exception` 的理由**按站点写具体**
（不做通用文案——复核阶段逐个读上下文后修正了 6 处不准确的理由）。
清尾后**基线为空集**：任何新增静默处理立即红，无豁免缓冲。
`contextlib.suppress` 维持 47 处棘轮（暂只记数，逐处语义审查排期在 ADR-023）。

### 复评处置：三条防复发门禁 + CHANGELOG/版本锚点结构修复（ADR-023）

外部工具《MAOP 复评报告 2026-10-12》的遗留项处置。方案经独立对抗性审核（首版 REJECT，
5 项硬伤修订后开工），核对阶段更正了报告的两处误判：`5.1.0-personal` 是**合法 SemVer
prerelease**（不改）、静默存量的真实根因是旧脚本只匹配 `except Exception` 而非"多行正则逃逸"。

**新增三条门禁（纯 stdlib 脚本，并入 docs-gate job，不新增 job 名）**：
- `check_silent_exceptions.py`（ADR-023 D1）：AST+tokenize 扫描静默
  `except … pass/continue/...`；必须有 `# silent-ok: <闭集类别> — <理由>`（类别不在闭集即红）
  或按 ADR-018 记日志。存量 **81 条静默 + 47 处 `contextlib.suppress`** 进基线，
  **双向棘轮**（新增红、基线条目失效也红，只许减）。源码按 `utf-8-sig` 读（本仓 17 个文件带 BOM）。
- `check_file_size.py`（D2）：单文件 800 行上限；`migrations/` 整体豁免（SQL 迁移本体）；
  存量 7 个 >800 行文件进 allowlist，**每条带拆分排期**，双向一致（拆完必须移除条目）。
- `check_docs_consistency.py` 新增 CHANGELOG 结构校验（D3）：单一 `[Unreleased]`、
  版本段按数字核心降序（核心相同者不强制，保留 `5.1.0`/`5.1.0-personal` 真实时间线）、
  版本号须为 SemVer、历史归档段双向豁免。

**结构性修复（内容零改动，附证据）**：
- CHANGELOG：第二个 `[Unreleased]`（内容跨 2026-08~09，晚于 5.2.0 的条目）**原地改名归档段**
  `[未归属记录]`——并入顶部会伪造"未发布工作"；`[5.2.0]` 段整体平移至正确位置
  （块 sha256 前后一致，纯移动）。
- 版本锚点：`config/agents.yaml`（v4.3）与 `docs/troubleshooting.md`（v4.3.0）不再内嵌
  MAOP 版本号，防再次漂移；守卫测试扫 `MAOP v\d` 字面量即红（第三方 CLI 版本不受影响）。

**守卫**：`py/tests/test_quality_gates.py` 26 例（三闸门各自的变异自证 + 双向一致性 +
ci.yml 接线断言 + 真实树基线成立断言）。控制流零改动（存量清尾是独立批次，排期见 ADR-023 后续表）。

### F2-02 多模态记忆 Batch-1：CLIP 空间 + 跨模态 RRF 融合（M2.2 / ADR-022，部分交付）

ROADMAP v5.4.0 主题的首批落地（ADR-022 经两轮独立对抗性审核通过后开工）。
范围 = 个人版文本+图像（PRD R06）、SQLite 路径。**PG 路径/异步队列（B2）、
音频/视频（B3）、NDCG 数据集（B4）明确未做**，验收映射按诚实口径标注（见 ADR-022）。

**选型（ADR-022，附前置实测）**：PRD R02 原文"pgvector 多向量列"经实测证否——
`pg_backend.py` 是单 `vector(N)` 列 + 初始化 ALTER 架构（表中已有 384 维行时
`USING embedding::vector(512)` 直接 cast 报错并被 except 吞掉），CLIP(512) 与
MiniLM(384)/OpenAI(1536) 无法共列。落地为**独立嵌入空间表** `multimodal_entries`
（自己的库文件 `multimodal.db`、独立维度/编码），既有 `vector_entries` 及其数据零改动。
偏差记账见 ADR-022 D2 + 本节。

**新增**：
- `core/memory/multimodal_embed.py`：`MultimodalEmbeddingProvider`（文本+图像共用空间）、
  `ClipEmbedding`（惰性 import，`pooler_output` 双形态兼容）、`HashModalityEmbedding`
  （无 torch 的确定性回退，`is_semantic=False` 明示非语义）、`PrecomputedEmbedding`
  （夹具真 CLIP 向量，供 CI 做**真语义**断言）、`validate_image_input`（扩展名白名单
  取 ImageStore 子集去掉 .svg、20MB 上限、Pillow verify、`allowed_root` 防穿越）。
- `core/memory/multimodal_store.py`：`MultimodalStore(VectorStore)` —— 向量存
  **float32 BLOB（2048 B/条）**而非 JSON（~10.5KB）；`index()` 覆盖走 BLOB 防记账漂移；
  `index_many` 按 modality 分组单次前向（`index_batch` 覆盖为 LSP 兼容的 int 计数、仍走 BLOB）；modality 一律从 `_meta_cache` 派生
  （继承的 delete/clear 不会留脏状态）；`stats()` 自建含按模态计数 + degraded 标记。
- `core/memory/fusion.py`：`reciprocal_rank_fusion` 纯函数（k=60、可加权、
  ties 按 id 稳定、同列表重复 id 只计最优 rank）。
- `memory/multimodal.py`：`MultimodalMemory` 服务层（多模态腿 + 注入式文本记忆腿 →
  RRF 融合；文本腿失败只降级不抛；文本命中形状兼容 dict 与 pydantic 两种）+ 模块级单例
  `get_multimodal_memory()/reset_multimodal_memory()`。
- `[multimodal]` optional extra（transformers>=4.40,<6 / pillow / torch），
  默认安装零增重；未装 extra 走回退 provider 且 `degraded=True` 明示。
- `MemoryFacade.store_multimodal() / retrieve_multimodal() / multimodal_stats()`（PRD R04）。

**修改（全部为默认值即现状的加法）**：
- `VectorStore` 新增 `table_name` / `create_sql` / `expected_dim` 参数 +
  存储向量解码容忍 JSON/float32-BLOB 双格式 + `_load_cache` 坏行加固
  （异维/坏编码/坏 metadata 跳过并告警——此前一条坏行会让 `np.array` 抛
  `ValueError` 穿透，拖垮全库检索）。
- `db_utils.resolve_data_dir()` 公开（原 `_resolve_data_dir`，旧名保留）+ 惰性导出映射同步。
- `test_dead_wiring_inventory.py`：`maop.core.vector.factory` 从 KNOWN_ORPHANS 移除——
  B1 的 PG 告警探针是它的**首个生产导入**（只读解析、不做存储；存储语义上仍为预留形态，
  见 ADR-022 D2）。

**顺带修复（独立审核发现）**：chat 模式 `MemoryManager.long_term_index` 调用**不存在的**
`VectorSearch.index`（真实类是 `index_entry`），AttributeError 被 except 吞掉 →
单条长期记忆写入静默失效（旧测试用 MagicMock 的 `.index` 掩盖）。修复 + 新增
"接口面扫描"回归测试（解析调用点逐一核对真实类成员，防同类再发）。

**实证（本机真跑，非推演）**：CLIP ViT-B/32 经 hf-mirror 可用；单张热机 65-293ms 波动
（贴着 200ms 阈值，不可依赖）；`index_many` 单次前向 **42ms/图**（含 SQLite 写入）；
跨模态 top-1 **8/8** 全对（真 CLIP + 真存储 + 真融合）；CI 语义断言用预计算真 CLIP
向量夹具（`tests/fixtures/clip_gold.json`，对角线矩阵入夹），无 torch 也能跑真几何。

### CI：新增 `wheel-install` 作业 —— 验证"构建出的 wheel 本身"可用（2026-10-12）

本文件其余作业（test / lint / e2e / perf-smoke / docker …）一律 `pip install -e .`。
editable 把源码目录挂上 sys.path，**不会**因"少打包一个模块 / 包元数据有误 /
控制台入口不可用"而失败；而真实客户 `pip install maop-orchestrator` 拿到的 wheel
只在这一刻暴露这三类问题。这不是假想风险：v5.3.0 首上 PyPI 当天就要补发
v5.3.1"补全包元数据"（见 `a0a9e27`），而 `release-assets` / `publish` /
`publish-manual` 三个作业只**构建** wheel，从不安装并冒烟。

- 新增作业 `wheel-install`（`Wheel install (built artifact)`）：构建 wheel →
  **全依赖**（不 `--no-deps`，依赖声明写错必须在这里红）装进干净 venv →
  五条断言：
  1. `maop` 的导入来源必须落在 venv 内（**自锁**：防止断言跑在源码上而绕过待测机制）；
  2. `maop.__version__` 存在；
  3. `from maop import __version__` 可用（与 2 不是同一个异常，单独钉）；
  4. `python -m maop --help` 走通（CLI 入口）；
  5. **结构完整**：源码每个 `.py` 都要在已安装产物里（469/469），
     这是此类作业的独有价值——editable 测试作业结构上看不见"少打一个模块"。
- 断言步骤 `working-directory: /tmp`：仓库根下有 `maop/`，cwd 会抢在 venv 前面，
  断言就会跑到源码上。MAOS 的同名作业 2026-10-11 实测踩过这个坑并已修（见 MAOS
  ci.yml 注释），此处一开始就避开 + 用断言 1 自锁。
- 新作业纳入 `CI merge gate` 的 needs 与 `REQUIRED_UPSTREAM`（docs §7.2 同步），
  **未进** required checks 清单 —— 经 gate 折叠即可强制，无需改分支保护。

本机完整彩排（干净 venv + Python 3.13.14 + 全依赖安装 wheel）：五条断言全过；
变异验证：把源码树换成含多余模块的目录 ⇒ 断言 5 红；在仓库目录内跑 ⇒ 断言 1 红。

### T2.1 dry-run 真预演（ROADMAP 路线图项）

此前的形态：dry-run **门**自 2026-09 就挂着，但没有任何**执行器产出**它验证的信号——`MAOP_DRY_RUN_ENFORCE=1` 时 deploy/pipeline/fileops 三条路由只会全体 fail-closed（“没信号”而不是“没预演”）。本 PR 把契约、产出方、接线一次落地：

- **行锚定哨兵契约**（新模块 `py/maop/core/reliability/dry_run.py`）：
  `MAOP_DRY_RUN_MARKER: {"artifacts": [...], ...}` 必须是**独立成行**的
  stdout 行。`emit_marker` / `extract_markers` / `DRY_RUN_PREAMBLE`。
  散文里提到 “dry-run” 再也骗不过受信任通道（旧的模糊子串匹配降级为
  deprecated 兼容，保留一个 minor）。
- **门改哨兵为首要信号**（`maop_verify._gate_dry_run`）：哨兵行 /
  legacy 子串（deprecated）/ structured_output 三类信号；期望 artifact
  校验优先取哨兵载荷。
- **pipeline 执行器真预演**（`doc_pipeline_adapter.run_pipeline(dry_run=)`）：
  经 `orch.plan()` 解析执行计划（纯解析、零副作用）后打印哨兵行；
  `dry_run=None` 时从 `MAOP_DRY_RUN=1` 环境变量自动探测（CLI 包装器
  零改动获得该能力）。**顺带修集成断裂**：doc-pipeline 仓库已更名
  nexusloom，候选路径认新名（此前所有 pipeline 路由 FileNotFoundError）。
- **dispatch 接线**（per-task，无进程级全局状态）：`plan.dry_run` →
  `_phase_execute` → `loop_executor` → `Dispatcher.dispatch(dry_run=)`：
  (a) env 通道 `MAOP_DRY_RUN=1` 随 driver 子进程环境（adapter 自动
  探测）；(b) 对声明 `dry_run_supported` 的 agent 前置 DRY_RUN_PREAMBLE
  （agentic CLI 按指令打印哨兵行；门仲裁真假，不合规 fail-closed）。
- **`AgentConfig.dry_run_supported`** + agents.yaml 声明 5 个 agent
  （doc-pipeline + cline/cloudbase/codewhale/cursor 四个 fileops）；
  未声明的 agent 完全不受影响。
- **顺手修 `AgentResolver._make_config` 漏透传**：逐字段重建丢了
  env / supports_vision / image_arg_template / dry_run_supported——
  agent 级 env 声明此前同样失效。

测试 `py/tests/test_dry_run_rehearsal.py` 22 例：哨兵契约（往返/行锚定/
散文拒绝/畸形跳过/多标记）、门（哨兵通过/无 legacy 关键词散文失败/
deprecated 兼容/artifact 校验）、pipeline 预演（marker 打印+零执行+
env 自动探测+默认执行）、dispatch 注入（声明 agent 收到 preamble+env /
未声明不受影响/常规派发零变化）。

### 前端 E2E：交互流 spec（搜索重取 + 聊天发送）

- 新 spec \`e2e/interaction-flows.spec.js\`：渲染契约由 seeded-pages 钉死，
  本 spec 钉**交互**——防"点了没反应"类回归：
  - **tasks 搜索**：query-aware 桩（服务端按 search 参数过滤，与真实 handler
    同语义）→ 填充搜索框 → 300ms 防抖 → 服务端重取 → 行过滤；清空恢复。
  - **chat 发送**：选 agent（发送按钮前置）→ 输入 → 点击发送 → 用户消息
    乐观上屏（先于任何网络交互）；SSE 端点空流→前端错误兜底分支也被执行。
- 调试记录：任务名同现表格与"执行时间线"两个组件（严格模式）——作用域到
  \`getByRole('table')\`；v-show 隐藏页签内容用 \`toBeAttached\`。
- 验证：spec 2/2 绿；全量 E2E 155 passed（7 例失败均为本机负载 flaky——
  失败 spec 隔离复跑 18/18 全过，与新增文件无关；CI 专用 runner 为仲裁）。

### 前端：M-1 信封漏解包全类清扫（Tools.vue 两处）+ 静态扫描法

- **静态扫描器一次扫完全类**：WorkBuddy 关闭后接手全仓，写了一次性扫描——
  解析全部 `@X.get` handler 的返回形状（含 APIRouter prefix 归属），筛出
  `{"status":"ok","data":…}` 信封端点 **28 个**，与前端 130 个 `api.get`
  调用做交集，再逐个人工核对消费侧解包与否。**除已修的 Tasks/Monitor 外
  新扫出 Tools.vue 两处**（此前 4 个消费端点对了 2 个——同一 bug 类命中
  50%，剩下不查必漏）：
  - `/api/routing`（v4_misc）读 `rt.value.routes` → routing 规则表恒空
  - `/api/security/config`（v4_misc）读 `sec.value` → 安全面板恒为信封对象
  - Blackboard/Chat/Scheduling/RoutingRules/Agents 经核对均为正确解包
    （routing.py 的 decisions 系是 `{"status":"ok",**stats}` 裸铺开，
    扫描的前缀归属误报、已排除）。
- 修复：两处均改 `data.data || data` 容错解包（与已修四处同款）。
- 测试：seeded-pages 追加 tools 用例（信封形状 + 行渲染 + 安全键），
  **先红后绿证明确有捕获力**（暂存 fix 后红、恢复后绿）；种子 spec 累计 8 例。
- 扫描器调试三坑（皆因正则）：修饰器名不固定（`@tasks_router.get`）要先解析
  APIRouter prefix；body 截断正则把 handler 自身 `def` 当边界（且 `async def`
  匹配不上）；跨行返回要空白归一化后再子串匹配。

### 前端 E2E：数据渲染扩展（agents / memory 两页）

- `e2e/seeded-pages.spec.js` 追加两页的种子数据用例（形状同样取后端 handler 构造处）：
  **/capability/agents**（`{status, agents:[model_dump]}`——生产有 31 个 agents.yaml 条目、
  测试态 403，数据路径此前零覆盖）：断言卡片名称/描述/模型驱动指标渲染；
  **/memory**（bridge.memory_stats() 裸统计字典，前端顶层读、无信封层）：
  断言 StatCard 渲染种子数值。
- 协调：新增 `AGENT-COORD.md`（多代理工作区防撞纪律——显式路径提交、禁 amend、
  CHANGELOG 段序、E2E 各用各的 spec 文件）。
- 验证：spec **7/7 绿**；全量 E2E 156 passed（3 例本机负载 flaky——隔离全过、
  上轮 PR #79 的 CI 22/22 绿为证）；vitest 485 passed；typecheck 干净。

### 前端 E2E：数据渲染 spec + 抓修两个 M-1 信封漏解包 bug

- **新 spec** `e2e/seeded-pages.spec.js`：no-auth 模式下所有数据端点 403、既有 spec 用
  `{}` 桩——**数据渲染路径从来没被执行过**（"有数据才崩"的页面抓不到）。本 spec 按
  **后端真实响应形状**（取自 router handler 构造处，不凭空造）灌种子数据并断言渲染，
  把后端↔前端字段契约钉住：任一侧改名字，这里就红。覆盖 tasks 会话表 / monitor
  agent 健康面板 / overview KPI+失败榜+活动流 / models 模型与 provider 表。
- **抓到的真 bug 1**：Tasks.vue 读 `data.items`，而后端返回 M-1 统一信封
  `{status, data:{items,total,total_pages}}`——不 waffle 解包让**任务历史页永远显示
  空态**（与 403 的空结果同形，既有测试和肉眼都发现不了）。修为 `data.data || data`。
- **抓到的真 bug 2**：Monitor.vue 对同一端点 `/api/scheduling/failure-stats` 同样漏
  解包（同端点的 Scheduling.vue 早已正确解包——M-1 修复时漏了这两个视图）→ agent
  健康面板恒空。同款修复。
- **顺带加固**：健康面板 `a.avg_latency.toFixed(2)` 等字段缺一个就让 render 抛
  TypeError → **整页空白**（桩数据实测复现）。加 `?? 0` 防御（与本文件 resources 行
  同款写法），并加"部分载荷不白页"钉子用例。
- 验证：spec **5/5 绿**（两个 bug 修复前红、修复后绿——先红后绿证明确有捕获力）；
  全量 E2E 153 passed（knowledge-graph 1 例负载 flaky、隔离跑 41 passed 恢复）；
  vitest 57 文件 485 passed；typecheck 干净。

### 前端 E2E：全路由冒烟矩阵 + 每页加载的 403 错误日志修复

- **路由矩阵** `e2e/route-matrix.spec.js`：51 个视图此前仅 6 个 spec 覆盖约
  12% 的页面。矩阵给**每条个人版主路由**（38 条；8 条企业专属路由由
  enterprise-route-guard 覆盖）断言：页面挂载、主内容非空、无意外重定向、
  可见文本不含原始 i18n key（PR #73 那类 bug 的运行时防线——静态守卫看不
  见动态 key）、零应用级 console error、零未捕获异常。真实后端联调
  （playwright webServer 起 9079+5174）；仅 chromium 跑（冒烟网浏览器无关，
  全量三跑会把 Playwright 作业时长翻三倍）。
- **矩阵当场抓到的真 bug**（本 PR 一并修）：`useEditionStore.fetchEdition`
  把版本信息端点的 403 记成 `console.error`——该端点 admin-only，而无鉴权
  模式匿名用户只获得 read 角色（P0-4 安全修复明令禁止授予 admin），403 是
  预期路径；守卫按 P1-H1 的安全失败默认走 personal，功能本就正确。修：
  403 降为 `console.debug`，其余失败（网络/5xx）保持 error。**在此之前，
  无鉴权个人版与普通用户态下每个页面加载都向控制台刷一条 error**。
- 守卫：admin-only 端点的 403 与企业专属路由的 404（个人版不注册）是
  no-auth 测试环境下**设计内**的浏览器级网络噪声（页面 fail-closed 降级），
  矩阵按此过滤；应用自身 error/JS 异常/5xx 仍全量抓捕。
- 测试：新增 store 用例 2 例（403 不记 error 且版本保持 personal 安全默认 /
  非 403 失败仍记 error）。
### 分布式注册表批量化（派发热路径性能）

- `capable_workers`（每次派发的选工路径）与 `detect_failures`（失败检测循环
  每秒一次）原来对每个 worker 一次 redis 往返（1+N 次）——100 节点层 ×
  50 worker = 5000 次往返/层。修为 pipeline 批量化：恒 2 次往返
  （smembers + 一批 hget/get），死亡 worker 清理合并为一次 pipeline。
- 顺序/筛选/tie-break 语义不变（`capable_workers` 仍返回 sorted 顺序）。
- 守卫：`_CountingRedis` 调用计数代理钉住往返数（20 workers 下 ≤3 次，
  修前 21 次），防批量化回归。
- **不做全异步客户端迁移的理由**：批量化后每派发仅剩 1-2 次往返（本地
  亚毫秒），而 redis.asyncio 迁移要动 20 个调用点 + 全部 fakeredis 测试
  夹具，收益/风险不成比例；将来 redis 迁远程网络且延迟成瓶颈再评估。

### 工程完善批 C（审计/审查发现的可执行化）

- **safe_eval 资源上限**：`_SAFE_BINOPS` 的 `ast.Pow` 无限制，`9**9**9**9`
  可把进程算爆（CPU+内存双杀）。BinOp 分支加指数上限（1024）与结果规模
  护栏（整数 8192 位 / 序列 8192 项）。调用方均为无 Call 的条件表达式，
  阈值不伤合法用法。
- **结果流生命周期**：分布式 per-run 结果流运行后永不清理，历史 run 的流
  在 Redis 里无限堆积。`run()` 的 finally 里 best-effort 删除（正常/异常/
  deadline 全覆盖；删不掉只 debug）。刻意不用 XTRIM——MAXLEN 淘汰可能删掉
  "已投递未收集"的结果，会重演 count=100 挂死那类风险。
- **失败可见性（ADR-018）**：worker_pool 四处 debug 级吞异常（checkpoint
  begin/end、worktree create/abandon）——正是 γ-3 导入路径错误被隐藏整整
  一版的同类病根。修为 warning（exc_info）。
- （前端 i18n key 完整性守卫见同批前端 PR。）

### Changed：lint 工具链钉版与 CI 同源

- `pyproject.toml` 的 dev extra 原写 `ruff>=0.4` / `mypy>=1.10`，而 CI 的 lint 作业
  手写 `pip install "ruff==0.16.3" "mypy==2.3.1"`（`.github/workflows/ci.yml:127`）。
  两处各说各话且没有东西强制相等：本机与 nightly（`pip install -e ./py[dev]`）装到的是
  当时最新的 ruff，规则集比 CI 那版更严 ⇒ 同一段代码本地绿、主干红（2026-10-03 两次
  主干红即此形状，F401 只在 CI 的老 ruff 上复现）。现把 dev extra 收成与 CI 相同的
  精确 pin，并加守卫 `py/tests/test_lint_toolchain_pins.py`（7 条用例：两处版本一致性、
  精确 pin 形状、以及"CI 里找不到钉版行/pin 自相矛盾"时 fail closed）。抬版本必须
  同一笔改两处，否则测试直接红，而不是变成一次莫名的 lint 失败。

## [5.3.1] — 2026-10-10

### 发布链修复 + PyPI 首次上线

- **packages-dir 路径 bug**：`ci.yml` publish 作业的 `packages-dir` 按**工作区根**
  解析（容器 action 不受 `defaults.run.working-directory` 影响），原写 `dist/`
  而构建在 `py/` 下执行（产物 `py/dist`）——v5.2.1 与 v5.3.0 两次 tag 发布都倒在
  `FileNotFoundError: /github/workspace/dist`，且被原注释预期的「401/403
  （trusted publisher 未配）」掩盖，从未真正验证过。修为 `py/dist/`。
- **手工发布逃生舱** `publish-manual`：GitHub 的「重跑」使用**原 run 提交里的
  workflow 文件**，发布链自身有 bug 时修好 ci.yml 也无法让旧 tag run 变绿；
  新作业从 master 侧 checkout 指定 tag（`workflow_dispatch` 输入 `publish_tag`）、
  重建并走同一条 OIDC trusted publishing 上传。
- 守卫：`test_ci_publish_reachable.py` 新增 `TestPublishArtifactPath`
  （产物目录与构建产出的对齐，参数化两个发布作业）+ `TestManualPublishEscapeHatch`。
- **PyPI 首次上线**：`maop-orchestrator` 5.3.0 经该通道发布（wheel 1,708,100 B /
  sdist 2,458,015 B，与 Release 附件字节数一致）；全新 venv 直接
  `pip install maop-orchestrator` 装出 5.3.0 已验证；README 安装块补该最简途径。

### PyPI 包元数据补全

- `pyproject.toml` 补全 PyPI 展示元数据（此前四字段为空）：署名
  `authors = [{name = "Levango7"}]`（与 GitHub commit/tag 身份一致）、`keywords`
  10 个（multi-agent / agent-orchestration / llm / ai-agents / workflow / dag /
  orchestration / automation / plan-execute-verify / agent-framework）、
  `classifiers` 12 条（Python 3.10–3.13 与 CI 矩阵逐条对齐；不虚报
  `Typing :: Typed`——仓内无 py.typed；`Development Status :: 4 - Beta` 为诚实档位）、
  `[project.urls]` 六条（Homepage / Repository / Documentation / Issues /
  Changelog / Releases）。`description` 微调为含关键词的自然句。
- 影响面：不触碰 dependencies 与 `[enterprise]` extra，锁文件漂移守卫与 MAOS
  版本地板守卫零影响；构建后端与 license 写法保持 hatchling / `{text="MIT"}` 不变。
- logo：PyPI 项目页无 logo 字段，展示面仅 README；本次按既定决策不引入图形资产。
- **同日双版说明**（发布节奏规范的已知偏差）：本版与 v5.3.0 同日（2026-10-10），
  为用户拍板的元数据即时动作——元数据烘进发布物、无法原地替换已发布的 5.3.0。
  非安全 hotfix，此处如实记录。

## [5.3.0] — 2026-10-10

### 前端修复：Chat 页 8 个 i18n key 缺失

程序化前端审查（无头 Chrome 抓渲染后 DOM，30 条核心路由）发现
Chat 页把**原始 i18n key 当可见文本**展示：输入框 placeholder
显示 `view.chat.inputPlaceholder`、标题显示 `view.chat.title`。
根因：`Chat.vue` 引用的 8 个 key（`view.chat.title` /
`newConversation` / `inputPlaceholder` / `selectAgentFirst` /
`selectAgentToStart` / `chars` + `a11y.send` / `a11y.stop`）不在
词典中，`t()` 查不到 key 时回退为原始 key。词典补全 en+zh 双语；
全量扫描 2566 个使用 key vs 2943 个定义 key 确认零缺失。
（同一轮审查的其余结论：30 路由零应用级控制台错误、nav/router
一致、en/zh 对齐 98.8%——无待修项。）

### 分布式执行路径审计修复

2026-10-09 后端审计发现并修复的四个分布式路径缺陷：

- **结果读取挂死**：`_read_results` 每轮轮询都从
  流头重读且 ``count=100``——单层节点数超过 100
  时，尾部结果永远读不到，运行永久挂起；且失败
  检测器每轮重复记录每个已完成任务（权重窗口被
  重复样本淹没）。结果读取改为按流 ID 游标增量
  读取（``(ms, seq)`` 数值比较——流 ID 非字典序），
  每个条目恰好处理一次。
- **任务取消不安全**：`DistributedWorker._handle_task`
  被取消时（如 worker 停机）会泄漏 in-flight 槽位、
  不 ACK、不上报结果——调度器永久等待该节点。现在
  用 ``try/finally`` 保证任何退出路径都上报终态
  结果（``cancelled``）、ACK 任务、释放槽位；handler
  协程句柄存入 ``_task_handles`` 强引用集合（事件
  循环对任务仅弱引用，丢弃句柄可能被 GC 回收）；
  ``stop()`` 在 5 秒优雅排空后强制取消仍卡住的
  handler。
- **run 级超时**：存活但卡死的 worker 会持续刷新
  心跳，故障检测永不触发，运行无限等待。
  ``DistributedScheduler.run`` 新增 ``deadline_s``
  参数（默认无限制），超时抛 ``SchedulingError``。
- **重复扫描**：``_dispatch_node`` 每次派发把注册表
  的 capable-workers 列表算了两遍（选择 + 告警），
  现在只算一遍。

测试：``py/tests/test_distributed_audit_fixes.py``
9 例（游标一次性/意外节点推进/150 节点大层回归/
超时触发/默认无超时/单次扫描/检测器每任务一次/
取消后上报+ACK/stop 强杀卡死 handler）。
### 泄漏与可见性审计修复

2026-10-09 后端审计第二批（分布式路径之外）：

- **WorkerPool 簿记泄漏**：`_tasks`/`_futures`
  每提交一个任务永久保留一条——长生命周期池
  无界增长。改为有界保留：完成条目最多保留
  ``_COMPLETED_TASK_RETENTION = 4096`` 条、按
  完成时间淘汰最旧；**运行中的任务永不淘汰**
  （保留 ``get_task``/``wait`` 的查询语义）。
  ``stop()`` 现在会取消存活的任务协程（此前
  stop 后任务继续跑）。
- **worktree 功能复活**：`create_root(task_id=...)`
  关键字参数名错误（应为位置参数 ``task``）
  且 ``WorktreeManager`` 没有 ``cleanup`` 方法
  ——两处异常都被 ``except Exception`` 静默吞掉，
  worktree 隔离实际从未生效（死代码）。已改为
  正确的 ``create_root(wt.id)`` + ``abandon()``。
- **spawn 队列泄漏**：``Engine._run_single``
  的运行时扇出队列在 run 被取消/暂停时泄漏
  ``_spawns[trace_id]`` 条目——用 ``try/finally``
  保证任何退出路径都清理。
- **真抢占降级静默**：``PreemptableWorkerPool``
  在 ``contextlib.suppress(Exception)`` 内自动
  创建 checkpoint 存储——这正是 γ-3 导入路径
  错误被隐藏整整一个版本的根因。现在失败时
  记 ``error`` 日志（best-effort 保留，但可见）。
- **热路径**：``safe_eval`` 每次调用都
  ``ast.parse``（现 ``lru_cache`` 512）；
  ``_resolve_template`` 由「逐 key replace 链」
  改为单遍 ``re.sub``——既消除 O(上下文×模板)，
  又修复二阶注入（插入值含 ``{{ other }}``
  会被再次展开）。
- **CONDITION 可见性**：表达式求值失败此前
  静默当作 ``false``（步骤 SKIPPED、作者无
  感知）——现在记 ``warning``。
- **license 密钥掩码**：绑定/解绑/吊销的
  ``info`` 日志与 ``KeyError`` 消息不再包含
  明文密钥（前 4 + 后 2 位掩码）。

测试：``py/tests/test_backend_audit_batch_b.py``
16 例（保留语义/最旧淘汰/运行中不淘汰/stop
取消/spawn 清理（正常+取消）/二阶注入/未知
key/连字符 key/解析缓存/CONDITION 告警/掩码）。

### 动态编排（第一刀）——运行时 fan-out

执行器现在可以在步**成功后**向当前运行注入新步（运行时
扇出），而不再受「提交时冻结的 DAG」限制：

- **机制**：执行器返回的对象携带 ``spawn`` 属性
  （:class:`maop.engine_types.SpawnDirective`，含
  ``steps`` 与可选 ``depends_on``）。引擎在每层执行完毕
  后把新步并入运行图——``depends_on`` 缺省指向发出扇出的
  步，即新步总在其发出者之后执行；也可显式引用运行中
  任何已存在的步。
- **执行循环**：`_run_single` 由「预计算分层」改为
  「逐轮计算就绪层」（增量 Kahn）——静态 DAG 的执行顺序
  与分层结果与原实现完全一致，但扇出的步可在运行中途
  加入。环/悬挂依赖仍在运行结束时抛 ``ValueError``
  （与原 ``_topological_sort`` 行为一致）。
- **安全校验**（并入时逐条检查，不合格则丢弃并记
  error 日志，运行继续）：重复 step id、未知 ``depends_on``
  引用、以及每运行 ``_MAX_SPAWNED_STEPS = 100`` 的扇出
  总量上限（防执行器死循环无限扩张）。扇出的步只能引用
  并入时已存在的步，故**不可能形成环**。
- **失败纪律**：仅成功步交付扇出（失败步的指令随运行
  丢弃）；``fallback_to`` 提升时以 fallback 执行器的
  指令为准；扇出的步失败会使整个运行失败。
- **并发**：扇出队列按 ``trace_id`` 隔离，``engine.run()``
  可安全并发调用。
- **边界**：仅单进程运行。分布式执行中执行器运行在
  worker 上，指令无法跨进程传递（worker 的结果上报
  忽略该属性）——如需分布式扇出需扩展调度协议，留待
  后续。

测试：``py/tests/test_dynamic_orchestration.py`` 12 例
（扇出基础/排序/显式依赖/链式/去重/未知依赖/上限/失败
纪律/向后兼容/环检测/并发隔离）。

### 分布式调度增强——批量优先级重排 + 负载感知选工

两项 F1-01（分布式执行）的增强，均默认生效、向后兼容：

- **Worker 侧批量优先级重排**：`DistributedWorker` 的消费
  循环此前按纯 FIFO 读取任务——调度器文档声称的「worker
  侧本地优先级重排」并未实现（文档/代码不一致）。现在每
  个读取批次按 `priority`（1 最高 .. 5 最低）稳定排序后
  派发：worker 不会先启动它已读到的低优先级任务。残余边
  界如实标注：Redis Streams 本身是 FIFO，跨 worker 的全
  局严格排序不做保证（批次内保证）；同一优先级保持 FIFO。
- **负载感知选工**：`DistributedScheduler._select_worker`
  的打分从「仅失败探测器权重」改为
  `weight / (1 + 在途任务数)`——健康但空闲的 worker 优先
  于健康但繁忙的；被排空（weight=0）的 worker 仍然永不
  入选（无论多空闲）。在途数来自 `WorkerRegistry` 已有的
  进程内跟踪（`assign_task`/`complete_task`），属尽力而
  为：多调度器共享 worker 池时各自只看到自己派发的部分。

**已知边界**（非本次范围）：更强的保证（worker 本地优先
级缓冲池、按优先级分多层 Streams）需要单独的关闭排水设
计，见 PR 讨论。

### 真抢占（Phase γ-3）——opt-in，默认行为不变

**背景**：γ-2 的软抢占只记录「would-be preemption」需求信号
（`MAOP_task_preemption_total`），从不取消运行中的任务——因为
`PipelineCheckpoint` 未接入执行路径，取消即丢进度且无法恢复。
本批把该缺口补上：

- **检查点接线**：`WorkerPool._run_task` 现在把每个执行的任务
  写入 `PipelineCheckpoint`（`start_run`/`start_step`/
  `complete_step`/`fail_step`，`WorkerPool(checkpoint=...)`
  注入，不传则不写——零行为变化）。被抢占的任务留下
  `running` 状态的步骤，`pending_steps()` 将其报告为待重试，
  步骤的 `attempts` 计数使「抢占-重试」循环可见。
- **真抢占**：`PreemptableWorkerPool(true_preemption=True)`
  （默认 `False`，存量部署保持软抢占语义）。当无空闲槽位且
  队首任务优先级**严格高于**某个运行中任务时，取消该
  最低优先级任务（`WorkerPool.cancel(preempt=True)`）并以
  **原 enqueue token 重新入队**——提交方的 `wait()` 由重新
  调度的副本解析，任务只会变慢、不会丢失。
- **防抖保护**：任务被抢占 `max_preemptions` 次（默认 2）后
  不再选为受害者，高优先级突发不能饿死同一个任务。
- **`TaskPreemptedError`**：抢占取消的任务以其解析结果 future
  （区别于普通取消的 `CancelledError`），完成 watcher 据此走
  「重入队」分支而非记错误。
- **`_run_task` 重构**：try/except 外扩到信号量获取之外——
  修掉一个潜在挂起：取消若落在等信号量期间，旧代码会让
  CancelledError 逃逸出协程，结果 future 永不解析（watcher
  挂死）。
- **指标口径**：软/真抢占共用 `MAOP_task_preemption_total`
  （沿用原设计「该计数器将来记录真实取消」），两者以日志行
  区分（`soft preemption:` vs `TRUE preemption:`）。

**已知边界**（非本次范围）：`MaopLoop.run` 内部相位级进度
（LLM 对话中途状态）不保留——重新调度的副本完整重跑任务；
相位级恢复是独立的后续增强。分布式调度侧（`DistributedScheduler`
的 Redis Streams 优先级弱保证）未动。

测试：`py/tests/test_true_preemption.py`（17 例：取消机制、
检查点接线、受害者选择/防抖/竞态撤销、watcher 分支、
dispatch loop 集成）。

## [5.2.1] — 2026-10-09

**本版性质**：patch。发布动因是**解除与已发布 MAOS `enterprise-v5.2.3` 的兼容缺口**（P0）——
MAOS 该版起"空 `tenant_id` 不再等于所有租户"，其 CHANGELOG 明确要求"MAOP v5.2.1 起声明
要求 MAOS ≥ enterprise-v5.2.3"，本版号即为对齐该契约而取。

**本版声明（版本兼容地板）**：**MAOP ≥ 5.2.1 要求 MAOS ≥ `enterprise-v5.2.3`**。
依据与双向失效模式见 README「版本兼容地板」节；该约束无法用 pip 依赖表达（`maop-enterprise`
是私有包），故由 README 声明 + `py/tests/test_maos_version_floor.py` 守卫。运行时强制检查
属候选改进，**本版未实施**。

**范围**：v5.2.0（2026-09-08）之后主干累积的 **243 个提交**（其中 119 个 `feat`/`fix`）
收口为一份可分发制品。下方五个 `### 批次 …` 小节是同一批内容在当时的滚动记录分段，
本次归位一并并入本版（原均写作 `## [Unreleased] - <日期>`）。ROADMAP 侧的范围与验收标准
见 [ROADMAP.md](ROADMAP.md) 的 v5.2.1 节。

> **簿记残留（已知，本次未处理）**：本文件另有一个**无日期**的 `## [Unreleased]` 段
> （内容为「阶段二启动定稿」，PRD/HLD v1.1.0 评审于 2026-09-03），**疑属 v5.2.0** 却从未
> 并入其版本段。凭日期猜归属会制造新的错账，故保留原状并加注待核实。

### 2026-10-09 Ubuntu 26.04 迁移：按 canary 实测把 Linux 腿显式钉住

`ubuntu-latest` 将在 **2026-10-19 ~ 11-19** 窗口内被 GitHub 迁到 26.04（要留旧版须显式钉
`ubuntu-24.04`）。10-05 加的 `ubuntu26-canary` 已把差异量出来——**连续绿**（4m50s，口径与
本腿对齐：`-n 2`、`--reruns=0`）。本批据此收尾：

- `ci.yml` 的 pytest 矩阵 Linux 腿 `ubuntu-latest` → **显式 `ubuntu-26.04`**；同时改掉依赖
  它的条件 `if: matrix.os == 'ubuntu-latest'`（Enterprise extra smoke 步骤）——不改会让
  该步骤**静默不再执行**（新 OS 名匹配不上旧字面量），是典型的"看不见的失效"。
- 删除 nightly 的 `ubuntu26-canary` 作业（38 行）；其使命已由 ci.yml 接管。
- nightly `flaky-detection` 的 Linux 基线腿同步改 `ubuntu-26.04`。**这不是可选项**：守卫
  `test_every_leg_is_a_real_ci_leg` 要求每条腿都在 ci.yml 矩阵内，不改就红（nightly 不该
  观察一个 CI 已不执行的组合）。`REQUIRED_LEGS` 与 `docs/ci-gates.md` §8 表格同步；
  §9 的腿清单去掉已删除的 canary 行。

**已知并接受的残留（不是遗漏）**：`ci.yml` 另有 **17 个作业**仍是 `runs-on: ubuntu-latest`
（lint / coverage / e2e / docker / sast / container-scan / publish …），它们不在本批内，将随
10-19 窗口一起变成 26.04 —— 这正是 10-05 加 canary 时注释写明的方案（"走完 10-19 的窗口
即为 26"）。canary 只验证了 pytest 腿，**其余作业的 26.04 行为未单独取证**；若那批在 10-19
后转红，处置方式是各自显式钉版本，而不是回退本批。

验证：`test_nightly_flaky_coverage.py` 11 passed、`test_ci_workflow_hygiene.py` 3 passed、
`test_ci_required_checks.py` 6 passed；两个 workflow YAML 可解析且作业集合与预期一致。

### 2026-10-06 评估批 I：把"分发"做成不需要外部账号也能用的一条路

**I1 `Release Assets` 作业（零账号分发）+ 端到端实测**

`Publish to PyPI` 的触发面虽然已打通，但仍卡在"需要 PyPI 账号 + trusted publisher 配置"这个外部动作上，
而维护者的实际处境是**没有该账号** ⇒ "发布管线已就位"对他等于没有分发路径。本轮先实测澄清两条旧断言：
① **不需要梯子** —— 本机 `pypi.org` TCP 0.22s / `https://pypi.org/` HTTP 200、`upload.pypi.org`
TCP 0.35s、`docs.pypi.org` 可抓正文；② **公开包不涉及费用** —— 官方 FAQ 原文
"PyPI does not support publishing private packages… the recommended solution is to run your own
deployment of the devpi project"，即"要花钱/要自建"只出现在想要**私有索引**时。

新增 `release-assets` 作业：tag push 时构建 wheel + sdist 并挂到对应 GitHub Release，只用 runner
自带的 `gh` 与 `contents: write`，不引入第三方 action、不需要任何外部账号。
`py/scripts/attach_release_assets.py` 三条硬规矩：空产物目录直接失败（"构建成功但什么都没产出"
是同形失效）、缺 wheel 失败、**上传后回读 release assets 校验**（命令 0 退出 ≠ 附件真挂上）。
守卫 `TestReleaseAssets` + 6 条脚本用例；变异验证：改掉作业键名 ⇒ 守卫按"作业缺失"红。

端到端已跑通并留下可安装地址：在 `v5.2.0` 的 tag 提交（`665e3524`）上 `python -m build` 出
wheel（1,241,526 B）/ sdist（1,749,318 B），METADATA 实测 `Name: maop-orchestrator /
Version: 5.2.0`，上传后回读两个附件都在，`pip install <release-url>` 成功且装出来的包
`python -m maop --help` 正常输出。（该 release 原本 `assets: []`，是这次补挂的；
**附件必须从对应 tag 构建**，否则就是版本名与实际内容不符的发行物。）

**I3 租户隔离：我先否掉了自己上一轮给的"最小切法"**

上轮建议"从派发链上的 `check_agent_access` 起，代价小"。实测**不成立**：dashboard 的 DAG 执行路径
`routers/dag.py` → `execution_service.execute_dag()` → `_default_step_executor` → `dispatch`
这四层里 `tenant` 出现次数是 **0** —— 漏斗根本读不到租户身份。照原计划加 `MAOP_TENANT_ENFORCE`
只会做出一个"开了也拦不住任何东西"的空开关，正是要避免的假门禁形态。
准确前置（4 步下传 + 1 个未定策略：开启状态下"没有租户身份"的派发算拒还是算放行 ——
个人版 `maop run` 与 chat 回退都没有租户身份）已写进 `docs/configuration.md`；
**本批没动运行时代码**：宁可留一个明确的未接线事实，也不要一个看起来已启用的门禁。

**I4 compose 里空转的 worker 写实**

`agent-exec` 容器消费的 `agent_tasks` 队列在开源侧**没有生产者**（全仓零 enqueue，
`queue_worker.py` 注释自认 "intentionally excluded"），该服务会正常启动、健康检查通过、然后空转。
在 compose 项上方写清这条边界（保留部署是为与 MAOS 交付形态对齐），避免读者把它当"分布式执行已生效"的证据。

**I5 三家凶再测：把 run 460 现场那条线索量出来了（并修掉，但不越界归因）**

上一轮 run 460 的失败现场里有一条 "`subprocess.run` 被 MagicMock 替换 + 5 条
`mcp-adapter-bg` 线程存活"。本轮定向复现：单进程跑 `tests/test_mcp_adapter.py`
后实测残留 **5 条** 存活 daemon 线程 —— 与现场数字一致。机制是清楚的：
`mock_hub` 只替换了 `MCPHub`，`_BackgroundLoop` 是**真的**（文件 docstring 明写），
而该文件从头到尾**没调用过 `MCPAdapter.disconnect()` / `shutdown()`**。

加了 autouse fixture `_reap_background_loops`：记录本用例起过的 loop，用例结束时全部
`shutdown()`，并**断言线程确实死了**（`shutdown()` 将来退化就会红）。
修后实测：残留 5 → **0**，34 条用例全绿。变异验证：把 `shutdown()` 的 `join` 去掉 ⇒
断言红（"起了 1 个后台 loop，shutdown 后仍有 1 个线程存活"）。

⚠️ **口径要收紧**：线程残留与 `subprocess.run` 被替身污染**不是同一件事**，
本轮只是排除一个真实存在的干扰源（跨用例存活的后台 loop 会让"谁改了全局状态"的归因不可信）。
**原始泄漏者仍未归位**，flaky 也仍未复现 —— 不说"已治本"。

另一条方法学收获：第一次测量得到"0 线程"是**假的** —— `pyproject.toml` 的
`addopts` 带 `-n auto`，`pytest_sessionfinish` 跑在 xdist 控制进程里，那里本来就没有
测试线程。加 `-n 0` 才测到真值。这和本仓"xdist 下每 worker 各报各的"是同一类坑。

**I6 本机全量 3 红：两条是跨仓契约漂移，其中一条让 admin 的列表恒空**

批 I 收口前跑本机全量（`pytest py/tests/ -q -n 2 -m "not slow and not serial" --timeout=60 --reruns=0`）：
**3 failed, 9783 passed, 56 skipped**（919s）。先做归属判定，再谈修复 ——
在 `origin/master`（8fb8dad）建临时 worktree 用同一解释器跑同样三条：**同样 3 红**，
所以不是批 I 引入的回归；批 I 的 diff 里 `py/maop/` 运行代码为 0 改动。

三条红各自的真因都在**双仓契约**上（公开 CI 看不见它们，因为 CI 环境没有 `maop.enterprise`
—— 又一次"依赖必须两头实测"）：

1. `test_module_integrity.py::test_intact_tree_verifies`：MAOS `591a092`（10-04）给防篡改清单加了
   **反向校验**（磁盘模块集合必须 ⊆ 清单声明集合，未登记即 fail-closed，因为"新增一个 .py 而不重签"
   就是绕开整套防篡改的后门）。MAOP 侧的测试 fixture 却仍只签**顶层 `*.py` 并跳过 `__init__.py`**，
   于是真实清单里合法存在的 `maop/enterprise/__init__.py` 与 `notification/` 子包 6 个文件被判成
   未登记模块。修法是把收集口径对齐 MAOS：`rglob("*.py")`、排除 `__pycache__`、**含 `__init__.py` 与子包**。
2. `test_notifications.py` 两条（store 级 + router 级）：MAOS `f9cbc70`（10-03）把
   `list_channels/list_rules/list_templates` 的空租户语义从"所有租户"收紧为**"没有租户的行"**，
   跨租户必须显式 `all_tenants=True`。MAOP 的三个列表端点仍在给 admin 的"不加过滤"传空串 ——
   结果是**企业版 admin 的渠道/规则/模板列表恒空**（实测 `count: 0`），而路由注释与测试都写着"absent means all tenants"。
   这不是测试过期，是**真实功能断了**。修法：`notification_service` 三函数增加 `all_tenants` 转发，
   路由用新的 `_read_scope()` 统一判定 —— admin 且无显式过滤才给 `all_tenants=True`；
   非 admin 一律 `False`（租户仍只来自 token，缺身份仍 403 fail-closed，语义未削弱）。

**兼容性事实（必须先说）**：`f9cbc70` **不在任何已发布的 MAOS tag 里**
（`enterprise-v5.2.2` 指向 `843976e`，2026-09-26，早于该硬化提交）。因此 MAOP master 从本批起
要求 **MAOS ≥ `f9cbc70`**；对着已发布的 5.2.2 wheel 调用会 `TypeError: unexpected keyword 'all_tenants'`。
按本仓惯例不加兼容垫片 —— 双仓 lockstep 交付，缺能力就该响亮地失败，
而检测点就是这三条测试（装了 MAOS 才跑，正是开发机场景）。**MAOS 侧需要切一个 5.2.3，属待你定夺。**

变异验证两组：① 去掉路由里 `all_tenants=all_tenants` 的转发 ⇒ 2 条红
（`test_admin_sees_all_and_can_filter_by_tenant`、`test_router_masks_plaintext_config_from_service`，
后者现在断言服务边界真收到 `all_tenants=True`，不再只回数据）；
② 把 `_read_scope()` 的非 admin 分支改成 `True`（越权授予跨租户）⇒ 2 条边界红
（`test_non_admin_ignores_client_tenant_param`、`test_tenant_param_has_no_effect_without_tenant_claim`，
后者补了 `count == 0` 断言，把"空租户声明 = 拿不到任何带租户的行"钉成边界锁）。
两处变异均已还原（`grep MUT` = 0）。

修后实测：`tests/test_notifications.py tests/test_module_integrity.py -n 0` = **107 passed, 0 failed**；
`ruff` 4 个改动文件 All checks passed；`mypy` 两个源文件 Success。
顺带记录一处**未收敛的不对称**：MAOS 的 `list_notifications` 仍把空 `tenant_id` 当"无约束"
（该提交没动它）。MAOP 侧靠非 admin 必须有身份（否则 403）挡住了越权读取，所以现状不暴露；
但它是同类的洞，值得在 MAOS 侧一起收口 —— 本轮未动，因为那是另一仓的行为语义。

### 2026-10-06 评估批 H：把"注释认错"改成"行为打通"（发布链、版本站点、过期标记、两处口径自我更正）

本批没有新架构决策，全部是**把已经承认过的问题真的接上**，以及两处我自己上一轮说错的话。

**H4 `Publish to PyPI`：从"永不产生"到可达（并加防回潮守卫）**

2026-09-29 的复核在 `ci.yml` 注释里写清了"本 job 当前不可达"，但**没改行为** —— 于是
`if: ... refs/tags/v` 依旧恒假，PyPI 上 `maop-orchestrator` / `maop-enterprise` / `maos`
实测仍全 404（`maop` 是他人的 0.0.0 占位包）。本轮补 `on.push.tags: ['v*']`，并逐环核对依赖链
`scope → lint → test / frontend / e2e`：tag push 拿不到 diff 基线，`ci_path_scope` 走
"空变更集 = 全量"的保守分支判 `code=true`，链上**没有**任何分支限定条件会把 publish 级联跳过
（`docker` 那种 `ref == refs/heads/master` 的条件不在发布链上）。守卫
`py/tests/test_ci_publish_reachable.py` 覆盖两类同形失效（触发面缺 tag / 链上有分支条件），
含"删掉 `tags` 就 3 条变红"的变异验证。新增 `py/scripts/check_release_tag.py`：tag 与
`__version__` 不符就拦在构建之前，并把"仍需在 PyPI 配 trusted publisher"打在成功路径上 ——
**这一条是外部动作（需 PyPI 账号），我没有替它宣称完成**；未配置时第一条真 tag 会 401/403 红，
该红就红。`Publish to PyPI` 仍不在 required 清单（也有用例守着，避免它把主干 PR 锁死）。

**H3 版本同步守卫补齐后两处站点**

`check_config_drift.version_drift()` 只比 `__init__.py` 与 `pyproject.toml`，而 CHANGELOG 发布
checklist 第 3 条要求四处同步。实测漏掉的正是最容易半抬的两处：`py/Dockerfile` 的
`LABEL version`、`dashboard-enterprise/package.json` 的 `version`。清单改为单一来源的
`VERSION_SITES`，新守卫 `py/tests/test_version_sync_guard.py` 用合成树**逐站点**证明"单独改它
会被抓到"以及"文件缺失/JSON 坏掉不会被当成全绿"，并核对文档点名的站点与守卫读的站点一致。

**H1 一个过期标记**

`py/tests/e2e/test_boundary_conditions.py` 的 `xfail(reason="已知脆弱点 BUG-001…")` 是非 strict
标记，但 CI（ubuntu py3.13 ×3，`--reruns=0`）与本机（Windows py3.14）全部 **XPASS** ——
`5a9709cb` 已在 `core/backends/db_utils.py` 实现"损坏则删库重建"，`84ed174f` 又收紧为
"非损坏类错误原样抛出"。缺陷修了、标记没删，等于长期对外宣称"这里还有个已知脆弱点"。
删除标记使其成为回归锁；变异验证：把恢复分支改成恒 `raise`，该用例立刻红。
**判据：XPASS 是标记过期的信号，不是运气好。**

**H5/H6 两处口径自我更正**

- 我 2026-10-05 在 `ROADMAP.md` 写的"`.gitignore` 忽略整个 `deliverables/`，这类交付物
  **结构性地不可能**作为仓库内物证"**不成立**：`.gitignore` 只挡未跟踪文件，
  `git ls-tree origin/master -- deliverables/` 实测该目录仍有 9 个文件在版本控制内。
  真实情况是验收标准点名的 `v4.4.1-fix-report.md` / `env-audit-4.4.2.md` **从未进入任何提交**
  （该目录唯一真实交付物 `comprehensive-review-maop-2026-07-20.md` 已由 `495eaca3` 归档到
  `docs/archive/audits/`）。条目按"不可满足 + 物证在别处"作废，不再挂成待办。
- `docs/configuration.md` 补「派发权限门怎么真正打开」：门默认关是已知设计，但此前没写**运营路径**。
  实测规则入口只有 dashboard API（无 CLI），且 `PermissionManager.check()` 无匹配时返回 `ask`
  → 按拒处理；更关键的是规则里的 `action` 取的是**派发 `routing_key`（空则 `"execute"`）**，
  不是 agent 名 —— 写错就永远 `ask`，这是开开关最容易踩的坑。顺带补齐该文档目录（批 G 新增的
  两节此前也没进目录）。

**H2 行尾归一化落主干**

`* text=auto eol=lf` 此前只在本地工作分支（治的是 Windows 工具写 CRLF 造成的 1179 文件假改动，
它还会污染 `.gitattributes` 里已登记的哈希类比对）。本轮实测 **1338 个 tracked blob 零 CRLF**
⇒ 加属性不会改写任何历史内容，索引 blob 无变化；随本批进入 master。

**H9/H10 写文档时顺带发现的两个盲区**

- 权限门的 **action 轴没有用例覆盖**：`test_dispatch_gate.py` 里所有真实 `PermissionManager`
  用例都填 `action="*"`，于是"规则里的 `action` 取的是派发 `routing_key`（空则 `execute`）"
  这条恰好被通配符遮住 —— 而它正是运维最容易填错的地方（把 agent 名当 action 填 ⇒ 永远 `ask`
  ⇒ 开关"配了却不生效"）。补双向用例并做变异验证：`action=routing_key or "execute"` 改成
  `action=agent` ⇒ 两条同时红。
- `ROADMAP.md` 的「当前状态」还留着批 C 已核实**无出处**的"7 个 evolution API 端点"——
  当时只在 v5.2.0 节写了更正，漏了这一行（实测 `evolve_insights.py` 15 个路由、其中闭环
  6 个 `/api/evolution/*`；`evolution_experiment.py` 16 个）。
  **教训：同一个错误数字常出现在两处，改一处不算改完。**

**H11 顺着"总数对不上"追出一族用例从来没被执行**

核对本机全量 collect（10339）与 CI 主腿结果（8766）时没有停在"口径不同"，逐条追下去是
真漏：**所有腿的 `-m` 表达式都排除 `slow`**（ci.yml 主腿 `not slow and not serial`、
serial 步 `not slow and serial`、nightly 两条腿同样 `not slow …`），而 `perf-smoke` 只对
`tests/performance/` 显式跑 `-m slow`、对 `reliability/ + stability/` 无标记全跑 ——
于是**根级 47 条 slow 用例**（`test_ldap_real_env.py` 21、`test_stress.py` 14、
`test_k8s_operator.py` 12）在 CI 与 nightly 上**一次都不执行**，坏了主干照样全绿。
其中那 12 条 k8s 静态一致性用例是 2026-10 刚从弃用分支捞回来的（任务 #24）——
捞回来却没接上执行面，等于白捞。

处理：nightly 新增 `Slow-marker suite` 跑整个 `py/tests/ -m slow`（`-n 0`、`--timeout=300`、
`--reruns=0`）。本机实测 76 条 **57 passed / 22 skipped / 0 failed / 2 分 44 秒**
（skip 那批需要真 LDAP 服务），不占 PR 时间，也不写 `|| true`。
守卫 `TestSlowMarkerCoverage` 扫出所有含 `pytest.mark.slow` 的文件并要求存在一条
"路径命中且未排除 slow"的腿，含反向对照。变异验证：把该腿的 `-m slow` 改成 `-m "not slow"`
⇒ 2 条守卫红并精确点名那 3 个文件。
两处扫描器自伤当场修掉：守卫会匹配到**自己**文件里的 `pytest.mark.slow` 字面量；
以及"把作业改名"不算删除其步骤，第一次变异因此无效。
**判据：`-m` 是选择器，每条腿都可以合法地看不见一整族用例；总数对不上必须逐条追到"由哪条腿负责"。**

### 2026-10-05 评估批 G：沙箱环境过滤真接线、租户身份收口、租户边界写实

**G1 沙箱：从「只约束工作目录」到「也约束凭据面」**

`build_sandbox_env()`（白名单，只转发 PATH/HOME/SYSTEMROOT/TEMP 等运行必需项 + 显式放行的
`MAOP_SANDBOX_*`）原先写在 `core/marketplace/sandbox.py` 里，而那个模块**零生产导入方**；
真正在跑的沙箱 `core/security/sandbox.py` 在起子进程时**根本没传 `env=`** —— 沙箱内的命令
继承**整份**服务器环境，包括 `MAOP_JWT_SECRET` / `MAOP_PG_PASSWORD` 与各类云凭据。

现把白名单实现统一到 `core/security/sandbox.py` 并接到**两个**调用点上。踩到并记录一处教训：
该模块有**两套并行实现**（同步 `run()` 走 `subprocess.run`，异步 `arun()` 走
`create_subprocess_exec`），我第一版只补了异步那份，而**结构守卫当时只枚举
`create_subprocess_exec`** —— 守卫绿、行为用例红。守卫已扩为枚举全部启动方式，并加了
"两套实现都必须被覆盖"的断言。

**G2 租户身份收口（消掉已经发生的漂移）**

`request.state.tenant_id` 是唯一来源，但读它的函数在各路由里**手抄了 7 份**，且已经分歧：
`compliance.py` 无租户即 403（fail-closed），`rbac.py` 同名同注释却软回退空串 ——
"没租户怎么办"取决于走到哪个路由。现统一到 `core/tenant/context.py` 的两个语义明确的函数
（`tenant_id_from_request` 软读 / `require_tenant_id` fail-closed），7 个旧函数名保留为转发，
`sso.py` 里另外 4 处直接读取也一并收口。新增守卫：路由层**不许**再直接读
`request.state.tenant_id`（判据只认请求状态上的，不认 `getattr(result/notif/payload, ...)`
—— 早先版本一律命中、一次报出 3 处误报）。

另提供 `tenant_context()` 上下文原语作为把租户身份带进编排链的**唯一**注入点（本轮只提供
原语，不改变任何执行路径）。

**G3 租户边界写实**

新增 `docs/configuration.md`「多租户（企业版）现状与边界」：明确列出**已隔离**的
（请求侧 JWT claim + 各租户路由过滤 + TenantRLS）与**未隔离**的（LB 统计 / 路由评分 /
熔断器状态 / 数据路径全局单一；`check_agent_access`/`check_model_access`/配额在派发链上
一次都没被调用）。避免"以为隔离了、其实没有"的误判。

### 2026-10-05 死接线清单：把"哪些模块写了却没人用"变成可执行事实

评估这个仓时最容易出的两种错都是"凭印象数接线"，本次两样都踩了：

- **把活的当死**：我第一次扫全仓导入关系时漏了**相对导入**
  （`from .routers.agents import crud`）—— 于是把一批正常注册的路由误判成孤儿；
  也漏了 `python -m` 入口，把 docker-compose 直接拉起的
  `worker/agent_executor.py`、`worker/queue_worker.py` 当成了"无启动入口"。
- **把死的当活**：`dashboard/lifespan.py` 看着像应用生命周期钩子，实际
  `server.py:118` 自内联了一个 `lifespan()`，模块版本**从来没被引用过** ——
  改这个文件不会生效，是个会被白改的坑。

所以新增 `py/tests/test_dead_wiring_inventory.py`：每次跑测试都用 AST 重算一遍
"零生产导入方"的模块集合，与清单逐条对齐。三种漂移都会红：

1. **出现新孤儿**（有人加了模块却没人调用）；
2. **清单里的被接上线**（该把它从清单删掉）；
3. **清单里的被删除**（僵尸条目要清）。

口径（写在测试 docstring 里）：只统计 `py/maop`；排除包 `__init__.py`（包由子模块隐含
引用）与 `migrations/`（alembic 按路径加载）；相对导入按定义文件所在包解析；
`docker-compose*` / `Dockerfile` / `pyproject` / `scripts/` / workflows 里出现点分路径
也算"被引用"（外部拉起）；**测试文件不算引用方** —— "只在测试里被用到"正是本清单要
暴露的状态。

清单里 37 个模块分三类：`dynamic`（按名字解析）、`superseded`（已被取代，要点名取代者
文件）、`unwired`（写了没接线）。`unwired` 是真正待决策的一批，其中成组出现的尤其值得
注意：`core/agent/ops/*` 整 5 个、`core/reliability/*` 3 个、`core/marketplace/*` 3 个 ——
不是零散遗漏，是整块功能没接。

自证与变异：扫描器先自证不空转（已知活模块不得在孤儿里、已知死模块必须在）；
实测两个方向都会红（新建一个假模块 → 报新孤儿；给 `lifespan` 加一行导入 → 报"已被接线"）。

### 2026-10-05 评估批 D/E：verify 工件门、三处"接线没接上"、主派发链端到端守卫

**D：verify 第一次真的看磁盘**
此前七个内置 gate（exit_code / output / content-safety / syntax-check / lint / dry-run /
schema）**全部只看退出码与 stdout 的正则**，`workdir` 一路传到 `VerifyEngine.verify()`
却从不下传给 gate —— "验证通过"的真实含义只是"进程退出 0 且打印了点什么"，agent 一个
文件都没产出也算通过。新增 `expected_files` 门（存在性 / `min_bytes` 防占位 /
`kind: file|dir`），路径必须落在 `workdir` 内（`../`、绝对路径、符号链接逃逸一律拒），
**不声明就不跑**（对既有 plan 零行为变化）。`verify()` 按签名自适应下传 workdir，
既有两参数自定义 gate 继续可用。

**D2：`agent_executor` 两处语义 bug**
（前提：`agent_tasks` 这条链在本仓**没有生产者**，docker-compose 却部署了 `agent-exec`
—— 容器起得来但永远拿不到任务，所以 bug 一直没人发现。）
① **失败也 ACK**：原实现只在抛异常时 NACK，而 `dispatch` 失败是**正常返回**
（`result.result.exit_code != 0`）⇒ 失败任务被 ACK 掉、永不重试，与"重试到上限再进死信"
的意图相反；② 日志取的是 `DispatchResult.agent/exit_code`（字段其实在 `.result` 上），
恒为 `agent=unknown exit_code=-1`。

**D3：主派发链的端到端守卫**
`tests/test_dispatcher.py` 大量直接替换 `_DRIVERS`，所以 **Dispatcher → driver 注册表 →
子进程** 被剪断时可以全绿。新增 `test_dispatch_e2e.py`：一个替身都不用，真实
`AgentConfig` + 真实注册表 + 真实子进程，且子进程**往磁盘写哨兵文件**。

**E1：`execute_workflow` 不再报假成功**
它对每个步骤只调 `maop_plan()`（`sr.status="planned"`），却把 `step_outputs` 写成
`"success"` 并 `steps_completed += 1`——**不是报错，是报成功**。现拆出 `steps_planned`
（它真正做的事），`steps_completed` 只在真执行过才计数（本函数恒 0），状态如实写
`"planned"`，docstring 说明名不副实与替代路径。既有 6 处断言与实现"错得一样"，已同步更正。

**E2：`/api/bridge/*` 从"恒 404"到可用**
`AgentProxy.register()` 全仓只有定义处与测试调用 —— 注册表从来是空的。现由
`bridge_call` **按需**从 agents.yaml 构建并注册（复用 `AgentResolver` + `adapter_factory`）；
刻意不启动时批量注册 30+ 个适配器。构建失败只记日志、最终仍 404，不许在 500 里塞假成功。

**E3：主循环理会 `.maop_pause`**
`pause_control()` 写的标记文件此前**只有 engine.py 查**，`maop run` 这条主链从不查 ——
运维以为按下了暂停，任务照常派发。现与 engine.py 同一 helper/同一标记文件。
⚠️ 语义是"等待恢复"（最长 `MAX_PAUSE_SECONDS`=3600s 后放弃），残留标记文件会让
`maop run` 最长停 1 小时 —— 与 engine.py 一致，已在代码注释标明与删除办法。

### 2026-10-05 Ubuntu 26.04 金丝雀：把"被动换 OS"变成"量出来的迁移"

官方 changelog（2026-09-17）：`ubuntu-latest` 将在 **2026-10-19 ~ 11-19** 窗口内迁到
Ubuntu 26.04；要留在旧版需显式钉 `ubuntu-24.04`，`ubuntu-26.04` 已 GA。

与其等主干在 10-19 那天被动换 OS（会同时影响 9 条 pytest 腿、容器构建与 Playwright），
不如先量：nightly 新增 `ubuntu26-canary` 作业，**显式**跑 `ubuntu-26.04` + py3.13，
命令口径对齐 ci.yml 的 Linux 腿（`-n 2`），但**不带 `--reruns`** —— canary 要看真结果，
重试只会把"这个 OS 上会挂"掩盖成绿。

刻意不挂在 PR 上配 `continue-on-error`：那就是又一个"吞失败的门"，本仓明令禁止
（`py/tests/test_ci_workflow_hygiene.py` 会拦）。挂在 nightly 上，红了只说明"我们还没
准备好迁"，不堵任何 PR。

迁移完成（26.04 持续绿）后：把 ci.yml 矩阵的 os 换成 26.04 并删掉本作业。

### 2026-10-04 评估批 B1–B6：两处真 bug、门禁措辞、泄漏可见性、两处新守卫

对 `26645340` 做只读评估后发现并修复的六项。每一项都做了**变异验证**（改回旧写法即红）。

**真 bug（一行级，各带回归用例）**
- `chat_engine.py` 流式回退读 `getattr(result.result, "output", "")`，而 `MaopResult`
  只有 `stdout` —— 带默认值的 `getattr` 把"字段名写错"变成**静默空流**：provider 流
  失败时 chat 一个 token 都不产，且没有任何异常可查。同一 bug 的非流式副本
  （`_call_llm_fallback`）2026-08 已修，**流式那份被漏掉了**。现直读 `stdout`，并补
  `TestStreamLlmStdoutRegression`（成功产出内容 + 失败仍产错误文本，后者防"无条件吐
  stdout"也能过第一条）。
  - **顺带发现一处"测试与实现错得一样"**：`test_llm_provider.py::test_stream_llm_fallback`
    用 `MagicMock` 并设 `mock_result.result.output = ...`（同一个不存在的字段）。
    MagicMock 对任意属性名都收，所以它替这个 bug 打了很久的掩护 —— 改完实现后它才红。
    已改为用**真实** `MaopResult`/`DispatchResult`：字段名再写错会当场抛错。
    这正是"替身对被测量那一维失明"的典型：替身越宽容，越测不出接线错误。
- `AgentConfig.env` 在 `delegate/` 这条**主派发链**上被静默忽略：同一个 agent 配置
  走适配器路径（`core/agent/adapters/cli_adapter.py`、`core/agent/lifecycle/runtime.py`、
  `core/mcp/mcp_hub_transport.py`）时 `env:` 生效，走 `delegate/` 时 5 个 driver
  全都不传它 —— 配了不生效且没有任何提示。现由 `drivers.py::_agent_env()` 补齐到
  5 处 `create_subprocess_exec`，合并口径 `{**os.environ, **config.env}` 与既有 4 处
  保持一致；空 env 返回 `None`（= 不传 env，与历史行为逐字节一致）。
  收窄继承面是独立的安全议题，**本次不做**（agent CLI 普遍依赖 PATH/HOME 等基础变量）。
  - 订正：本条初版写作"全库无一处读取"，**不准确**。起因是我排查时用了
    `grep ... | grep -vE "os.env"` 过滤噪音，而既有那 4 处的形态正是
    `{**os.environ, **self.config.env}` —— 过滤器把要找的行一起删掉了。准确说法是
    "适配器路径读、delegate 主链漏"。
  - `scripts/check_config_drift.py` 的 BASELINE 随之 227→228（该门禁是逐行文本计数，
    只允许减少）。本次是既有既定形态的第 5 处实例，非新模式，理由已写进脚本注释。

**回归与本批修复（自曝）**
- 给 `pytest_sessionfinish` 加会话结束报告时，`print(中文)` 在 en-US runner 的 cp1252
  控制台上抛 `UnicodeEncodeError`，异常从钩子冒出去把整个 pytest 会话崩成 exit=1，
  而 junit 里"没有任何失败用例"—— 于是"守卫报了个警"变成"测试作业失败"。CI 实测红在
  windows-latest 3.10 与 3.13。**这是本仓栽过的同一个坑**（`ci_merge_gate.py` /
  `ci_path_scope.py` 都为此做了 stdout reconfigure 并写了警告注释），我照抄了那套写法
  去写 gate，却在测试钩子里漏了同一件事。现改为按需降级（UTF-8 → ascii 转义），
  并补一条模拟 cp1252 stdout 的用例把它钉死。
- **随之发现修复本身是假阳性**：报告在 CI 与本地都开始报"仍有测试在泄漏"，归因指向
  `tests/test_agent_adapters.py::_mock_subprocess_run.<locals>._run`（CI 21 次 / 单文件 13 次）。
  实测证伪：把"进入用例前"的状态逐条打印，该文件 **64/64 条都是 STDLIB** —— 跨用例污染
  本就不存在。
  根因是**检查点位置错了**：pytest 夹具终结是 LIFO，而 `tests/conftest.py` 里
  `_isolate_data_dir(tmp_path, monkeypatch)` **先于** `_leak_probe` 建立，于是 `monkeypatch`
  的撤销排在 `_leak_probe` teardown **之后** —— teardown 侧检查必然把用例**自己**作用域内的
  `monkeypatch.setattr("...subprocess.run", ...)` 看成泄漏。**T2.3-2 的 teardown 侧修复因此
  一直在报假警**（只是当时只在日志里、没人看）。
  修法：观测与修复一起挪到 **setup 侧**（`yield` 之前）——下一条用例进来时真泄漏仍在，
  作用域内的 patch 已还原，两个方向都不误伤。已加结构守卫钉住"必须在 yield 之前"。
  另修自检用例：`TestHermeticGuard::test_a_repair_restores_dirtyed_global` 故意弄脏再修，
  会把计数 +1 却不还原 ⇒ 一次正常全绿跑也会打出假警报，现用 monkeypatch 还原。

**CI merge gate：级联跳过不再背"该跑没跑"的锅**
run 476 现场：`test` 失败 ⇒ `needs: test` 的 `audit`/`sbom` 被连带跳过。旧实现把三条
都当"该跑的作业没跑"，注解里真正的红点 `test` 反而被淹。现在有硬失败时 skipped 只作
`::notice::` 提示（新增 `cascade_skips()`），无硬失败时才保留"该跑没跑"的判定。

**泄漏修复必须在绿跑里也可见**
`repair_subprocess_run()` 的 WARNING 走 logging，而 pytest 默认只打印**失败用例**捕获
的日志 —— 修复生效后用例不再失败，于是"泄漏源仍在活跃"彻底隐身。现于
`pytest_sessionfinish` 无条件汇总并输出 `::warning::`（xdist 下每 worker 各报各的）。

**推翻上一条（T2.3-2）的根因结论**
`bd64d0b`（引入 `TestHermeticGuard`，即"上一轮我自己引入的泄漏源"）= 2026-10-02，
而 run 460 的 head `962d3492` = 2026-10-01；`git merge-base --is-ancestor bd64d0b
962d3492` → **NO**。它不可能是 run 460 的泄漏源。且该"自泄漏"存在期间的
run 461/462/465/466/467 共 **45 条 pytest 腿全绿** —— 是理论隐患，不是活跃肇因。
**原始泄漏者至今未归位**：会话级守卫挡住的是症状，因此现状是"被掩盖"而非"已修复"。
可用线索：run 460 现场的 5 条 `mcp-adapter-bg` 线程；`MCPAdapter.__init__:167`
构造即起 daemon 线程，只有 `disconnect():295` 才回收。

**新增守卫（各带变异验证）**
- `test_nightly_flaky_coverage.py`：nightly 的 flaky 检测原先只在 ubuntu + 3.13 上
  串行跑 3 遍，而两家凶出现在 **macos-latest/3.13** 与 **windows-latest/3.12** ——
  探针与症状不相交，结构上守不住它声称守的东西。现改为矩阵（ubuntu/macos/windows，
  复刻 ci.yml 的 `-n 2` 与 Windows `-n 0`）＋ serial-marker 步骤，全程 `--reruns=0`；
  守卫钉住覆盖面、并发配置、"每条腿都是 ci.yml 真跑过的组合"，并对每条腿做
  `bash -n` 语法校验（矩阵值插错位置会让整条腿静默跑成别的东西）。
- `test_env_example_drift.py`：`ROADMAP.md` 把"`.env.example` 与代码 `MAOP_*` 差异为
  零"勾成 `- [x]`，却**没有任何守卫**。首轮扫描（只看 `os.getenv`）漏 18 个；补上
  第二条来源（pydantic `MAOPSettings` 的 `env_prefix="MAOP_"` 映射 + `AliasChoices`
  别名）后**实漏 29 个**，含 v5.2.0 旗舰开关 `MAOP_EVOLUTION_LOOP_ENABLED`。29 个
  已全部补齐（含真实默认值）；反向断言（声明了但代码全不读需有说明）与
  `ENTERPRISE_SIDE_VARS` 豁免清单（消费方在 `maop.enterprise`／MAOS 仓）一并入库。

### 2026-10-04 T1.5 兼容审计（spike）：Cython AOT 22/22 编译通过，路线成立

投资计划 T1.5 的可行性问题（"不做不知道做了多贵"）本机实测收口，结论写进
`docs/investment-plan-2026Q4.md`：

- **零采购**：Cython 3.3.0（Apache）+ 本机既有 MSYS2 mingw gcc 即可链接
  CPython 扩展，**不需要 MSVC、不花钱**——原设计"要装 Visual Studio Build Tools"
  的假设实测不成立，最大不确定项消除；
- **22/22 模块编译成 .pyd**，唯一不兼容点是 `clock_guard.py` 同作用域重复标注
  （MAOS 侧 `cecb3f5` 已修）；
- **行为零差异**：22 个 .pyd 顶替源码后，MAOS 全量 473 passed / 22 skipped；
  pydantic / cryptography / SQLite / 反射全部无恙，"薄壳模式风险"不成立；
- **体积代价** 0.55 MB 源码 → 7.86 MB 产物（14×）；
- **正式方案前置**：完整性 manifest 现按 `*.py` 收集，走二进制化后必须改为
  对 `.pyd`/`.so` 产物签名（含 `verify_module_integrity` 的反向枚举），否则
  防篡改覆盖的是源码而非真正执行的二进制。

### 2026-10-04 T2.3-2：泄漏源定位 + 会话级守卫（`subprocess.run` 污染跨用例的治本）

三元凶（`TestCallSyncFallback` 三条在 macOS/Windows 随机红）的开放题收口。
上一轮只做了文件级"密闭守卫"（`test_tool_manager.py` 内 autouse 修复），本轮
先**定位**再**治本**。

**定位结论（AST 静态取证 + 动态复现）**：
- AST 扫全库 `subprocess.run` 的 123 处触碰点（37 处相关）：`patch` 用法全部是
  with 块或同步装饰器、`monkeypatch.setattr` 全部自动还原，**无一处裸 start 或
  直接赋值泄漏**；
- 真正的活跃泄漏源是**上一轮我自己引入的**：`TestHermeticGuard.test_a_dirty_the_global_subprocess_run`
  故意把全局 `subprocess.run` 换成 MagicMock 且**不还原**，靠"下一条用例的守卫修复"
  ——而守卫是**文件级** autouse，xdist `--dist load` 又会把**任意**后续用例发到同一
  worker。等于修 flaky 时复刻了同一个隐患，且足以再次打红三元凶。

**治本三层**：
1. **守卫自检自还原**：`TestHermeticGuard` 改为"自己弄脏 → 直接断言
   `repair_subprocess_run()` 能修回 → finally 还原"，不再依赖下一条用例；
2. **会话级不变量**：conftest 的泄漏探针夹具在 **teardown 也复核一次**，发现污染
   立即恢复标准库原版并记 WARNING——泄漏**跨不过用例边界**，xdist 随机分发也就无从传播
   （此前只有 setup 侧观测，污染可以无限存活）；
3. **可归因**：给 `unittest.mock._patch.start/stop` 挂幂等钩子，记录每个仍在生效的
   patcher 的目标、启动用例 nodeid、启动栈（滤掉 mock 包内部帧）。teardown 检测到污染时
   直接点名"哪条用例哪一行"，把"只知道 prev"升级为"知道责任人"。直接赋值型泄漏
   （不经 patcher）退化为 `impl=` 指纹，属预期上限。

**验证**：两条注入式复现（直接赋值型 / 未 stop 的 patcher 型）串行跑——受害用例均
PASSED（污染未跨界），REPAIR WARNING 分别输出 `impl=MagicMock@…` 与
`culprit: tests/…::test_leak_via_unstopped_patcher @ runner.py:174`。新测试 4 条
钉住修复/归因/幂等/栈裁剪，**变异验证**：把 `repair_subprocess_run` 改成 no-op
→ 3 条转红。相关文件 201 passed、ruff clean。

### 2026-10-03 T3.1-e：人工审批跨轮回流——批准的建议下一轮真被应用

v5.2.0 验收 #1 的最后一环。此前 `approved_suggestions` 只被写入
（`evolution_service.decide_evolution_approval` 落库）而**没有任何消费者**：
人工批准的建议要等同类错误再次触发、由规则引擎重新产出同一 id 才可能落地，
人工闸门形同"记录了但不执行"。本笔接通 approve → APPLY 全链路。

- **EVALUATE 阶段回放**（`evolution_phases._carry_approved_from_last_cycle`）：
  读上一条 cycle 报告的 `approved_suggestions`，从建议队列取回条目元数据，
  **把策略重新拦下的那一条从 pending 移到 approved**（人工决定高于策略判断，
  cooldown/限流都不否决人的决定），记录在 `details["carried_over"]` 供审计与
  前端呈现。边界：已应用/已移出队列的不重放；同 id 不重复；**仍走同一 APPLY
  路径**（真实变更 → 快照 → VALIDATE → 失败回滚），不新增旁路。
- **人工批准豁免 auto_applicable 前置检查**（`config_mutator.apply_suggestion`
  与 `strategy_engine.apply` 新增 `human_approved` 关键字参数）：那条检查的语义
  是"未经人确认不许自动应用"，而人工批准**正是**那道确认。没有这个豁免，
  批准过的建议每次 apply 都被 "Suggestion is not auto-applicable" 挡回——
  这是同一条断链上的最后一环，单独修 EVALUATE 回放仍跑不通。
- **宿主能力注入**：`EvolutionLoop.__init__` 把 `get_cycle_history` 以
  `_cycle_history_reader` 注入 mixin（`PhasesMixin` 被 `PerformanceEvolutionLoop`
  等复用，不能假设宿主都有该方法）；`run_cycle` 设 `_current_cycle_id` 供回放
  排除自身（历史按 `started_at` 排序，同秒开跑可能把自己排到"上一轮"）。
- **测试**（`tests/test_evolution_approval_carryover.py`，2 条，零 mock）：
  审批落库 → 下一轮即使该建议不再被规则产出也被回放进 APPLY 并真执行；
  回放在报告里留痕。**变异验证**：关掉回放 → 2 条全红。
- 演化全套 96 passed、ruff clean。
- 过程中踩到的两个环境坑（记此备查）：editable 安装使 `import maop` 永远解析到
  主工作树，**临时 worktree 里跑测试必须显式设 PYTHONPATH** 指向 worktree，
  否则测的是旧分支代码；`git log --oneline` 按日期排序会因并行提交时间戳交错
  误判"提交没合入"，必须 `merge-base --is-ancestor` 核实。

### 2026-10-03 T3.1-d：开关零回归基线落地 + ROADMAP v5.2.0 四条验收全部勾选

验收 #3「`MAOP_EVOLUTION_LOOP_ENABLED` 开启后主循环零回归」此前只是一句承诺——
AC-01/AC-02 只验接线点（单函数 `_phase_evolve` 被调/不被调），"全量测试零回归"
没有任何自动化承载。新增 `py/tests/test_evolution_switch_no_regression.py`
三条**可执行**基线：

- **相位与事件面基线**：开关 0/1 两种取值下 `_phase_evolve` 之后的相位计数一致，
  事件面唯一增量是预期中的 `loop.evolution_cycle`（闭环是独立分支）；
- **异常隔离**：闭环内部抛异常时主流程照常推进（"独立分支跑失败不影响主路径"
  这一核心承诺此前无测试）；
- **数据隔离**：闭环 SQLite 走统一 `MAOP_DATA_DIR`（**不跟 root_dir 走**——
  root_dir 管快照/配置面，SQLite 归统一数据面，此为设计而非缺陷），
  且断言绝不落进仓库。

**变异验证**：把接线点 `if evolution_loop_enabled:` 改成恒 `True` → 第 1 条立即
判红（相位计数被改变）；还原后 7 passed。

ROADMAP 同步：v5.2.0 四条验收标准**全部勾选**（#1 留注：approve→APPLY 的跨轮
回流仍未接线，见投资计划 T3.1 遗留）。

### 2026-10-03 T3.1-c：自演化闭环面板接上六个 AC-07 端点（验收 #4 落地）

此前 `/api/evolution/loop/*` 六个端点（status / trigger / approvals / decision /
ab / rollback）上线后**前端零页面调用**——v5.2.0"dashboard 可见闭环状态机与 A/B
结果"的验收一直悬空，运维只能 curl。`EvolutionHistory.vue` 新增"闭环"tab：

- **状态机卡片**：当前态（idle/evaluating/applying/validated/pending_approval/
  rolled_back）+ 闭环开关状态 + 四个统计卡（周期数/有改善/已应用/待审批）；
- **手动触发**：真调 trigger 端点，带 `dry_run` 参数（真跑 / 演练两枚按钮）；
- **人工闸门**：待审批列表 + 逐条批准/拒绝（调 decision 端点，approved_by 取
  当前登录用户）；
- **最近周期表**：应用数/是否改善/是否回滚/快照 id + 行内「A/B 结果」「回滚」
  两个动作（ab 与 rollback 端点）；A/B 结果就地展开 JSON。
- 面板按现有 Card/StatCard/EmptyState/Segmented 模式写，i18n 双语键 +30（en/zh），
  样式 scoped，与既有三个 tab 并存不改动其行为。
- **e2e**（`e2e/evolution-loop-panel.spec.js`，2 条）：闭环 tab 可渲染且状态机/
  触发控件可见；点击触发**拦截真实请求**验证确实打到 `/api/evolution/loop/trigger`
  且带布尔 `dry_run`（防"按钮存在但不连端点"的假通过）。三浏览器 21 passed
  （含 layout spec 回归），`vue-tsc --noEmit` 与 `npm run build` 均零错误。

### 2026-10-03 T3.1-a/b：劣化注入接真实入口 + 真回滚 E2E（v5.2.0 验收 #1/#2 落地）

v5.2.0 的两条验收此前是空的——AC-05 的测试全 mock（`rollback_cycle` 测的是 mock
出来的 `ChangeTracker`，<5min SLA 测的是"mock 快照 + 空调用耗时"），劣化注入的
能力只存在于测试代码里（源码注释自述"生产无入口"）。本笔让链路真跑。

- **真 bug：建议队列只写不读**（`evolution_phases._phase_suggest`）。
  `data/evolve-suggestions.json` 被 `_write_suggestions` 写进去，却从不被
  SUGGEST 阶段读取；而 `ConfigMutator.apply_suggestion` 恰恰按 id 从这个文件
  取建议（`config_mutator.py:63`）。结果：任何外部注入的建议永远到不了
  EVALUATE/APPLY——演练链路形同虚设。修法：SUGGEST 阶段新增
  `_merge_queued_suggestions()`，把队列中"未应用且本轮未重新产出"的条目并入
  本轮建议集（跳过 `applied=True`、跳过同 id 重生成项，避免陈旧条目当新工作）。
- **劣化注入公开入口**：`EvolutionLoop.build_degradation_suggestion()`（构造）
  + `inject_degradation_suggestion()`（**落盘**进队列，能被下一轮真实消费）；
  CLI `maop evolution <inject-degradation|trigger|status>`。
- **真实 E2E**（`py/tests/test_evolution_loop_real_e2e.py`，3 条，除数据目录外
  零 mock）：ErrorLedger 真写入 → auto_promote 真阈值 → EVALUATE 真分流 →
  APPLY 真改 `config/agents.yaml` → VALIDATE 判定失败 → ChangeTracker 真快照
  回滚，断言 agents.yaml **字节级**恢复原状 + 全程 <300s SLA。
- **过程中被真实语义纠正的三处**（原测试想当然，代码是对的，已按真实语义重写）：
  OBSERVE 的 `errors_observed` 是**热点数**（distinct pattern）非总次数；
  回滚条件含 `applied > 0`，且只恢复**快照后被改动过**的文件；零热点时
  `run_cycle` 按设计提前返回整轮。
- 演化全套 91 passed（新增 3 条真实 E2E），ruff clean。

### 2026-10-03 Secret Scan 门禁重构 + 历史凭据处置（真凭据一条已吊销归档）

secret-scan 首度以全量条目运行（workflow_dispatch run 37082948735）时红，
SARIF 34 条发现逐条核对后两类分明：

- **33 条假阳性**（新增泄漏不会命中，进 allowlist 无损门禁）：
  测试夹具假密钥（`test-env-secret-key-…` / `sk-e2e-secret-key-…` 等）、
  文档 curl 占位（`X-API-Key: admin-key` / `<64-char-hex>` / `<strong-password>`）、
  CI 测试 secret（`test-secret-at-least-32-chars-long` / `maop_dev`）；
- **1 条真实凭据**：**Qoder CN 个人令牌**
  `pt-zDIDIXoMabdSC3DlbiDcEK0f_019f1025-aac1-70ef-899c-1b58db89e992`
  出现在 `archive/ps-legacy/healthcheck.ps1:95` 与 `data/routes.json:10`（自
  commit `aa02e40` 起，历经 `62a8767` / `2c65b5d` / `b5126e1` 扩散，**`e722754`
  已从当前树移除，但 `git log -S` 可从历史取回**）。用户 2026-10-03 确认
  **该令牌已失效/吊销**，以 allowlist 归档记录（含指纹正则与 commit 线索）。
  若将来需彻底清史：先吊销其他任何同类凭据，再用 `git filter-repo` 重写历史
  并要求全员重新 clone（旧克隆须清理）。
- 另有 `scripts/dev_private_key.pem`（拆分前旧开发密钥，指纹 `4095462e…`，
  与任何信任锚均不配对；`dc4316b`「安全密钥清理」已从当前树移除）——同样归档。

**门禁形态重构（本次重点）**：原作业直接全历史扫描、发现一次性吐出后恒红
（与 T2.2 lock-drift 同款"门禁代人受过"病——每次 push 都要人肉甄别或加例外）。
现为两段式（`.gitleaks.toml` + ci.yml）：① **本次 push 的新提交**走同一扫描
（base 解析与 scope 作业同一保守逻辑，dispatch/首推为空时退化为全量，不制造
假绿）；② **全历史**扫描叠加**逐条带理由**的 allowlist。

**变异验证**（CI 同版 gitleaks v8.24.2，全历史口径）：
- 基准：635 commits 扫描 → `no leaks found`（本地两个版本均绿）；
- 变异：移除 `test_maop_verify.py` 一条 allowlist → 全历史立即报 **4 leaks**
  （exit=1），还原后复测转绿——证明 allowlist 是精确豁免而非关掉门禁。
- 禁则（写进 `.gitleaks.toml` 注释）：新指纹一律先判真假，真凭据走吊销流程，
  不许直接进 allowlist。

**本门禁连踩三坑（均为"红得没道理"，一并记账）**：
1. **同名 artifact 409**（run 37094969085）：同 job 内两次 `gitleaks-action`
   都要上传 `gitleaks-results.sarif` → HTTP 409 Conflict；两段扫描其实都报
   `no leaks found`，步骤仍失败。段②改直接跑 gitleaks CLI（钉 v8.24.2），
   产物独立命名上传。
2. **allowlist 语法被静默忽略**（run 37097802399）：全局 allowlist 的键是**单数**
   `[allowlist]`（复数 `[[allowlists]]` 是规则内语法）。写成复数时本地新版容忍、
   CI 的 8.24.2 完全不生效 → 报 43 条。已改单数。
3. **规则集随版本变**：同一仓库，本地自编译版报 0 条、CI v8.24.2 报 43 条
   （多出 9 条全是内容安全检测夹具 `sk-abcdefghijkl…` / `ghp_abcdefghijkl…`
   之类打码假串，逐条核对原文确认非真凭据）。allowlist 必须按 **CI 实际使用的
   gitleaks 版本**核对——已写入配置注释作纪律。

### 2026-10-03 Playwright E2E 全类修复：CoachMarks 引导遮罩全局预关 + rail 断言等动画收敛

run 37075633241 的 Playwright 腿红出两例，根因都不在业务代码：

- **knowledge-graph.spec 被 coach-marks 引导遮罩拦截点击**（30s 超时，错误日志
  明确 `coach-marks__scrim ... intercepts pointer events`）——上次只修了 layout
  spec 自己的 beforeEach，这次把注入提到 `playwright.config.js` 配置层（各
  project 的 storageState 内联 localStorage `'maop_onboarding_done'=1`，
  CoachMarks.vue:60 幂等标记），**所有 spec 自动继承，新 spec 不再漏**；
- **layout.spec 的 rail 断言**点击折叠后立即读 boundingBox，拿到 CSS 过渡的
  中间值（CI 实测 104-110px）误红——改 `expect.poll` 等宽度收敛 <100px 再断。
- 本地验证：两个 spec 三浏览器 55 passed；删掉 spec 内重复 beforeEach、仅靠
  配置层注入在 webkit 上 19 passed 零 flaky。

### 2026-10-03 lock-drift 假红修复：门禁改偏好模式（上游发新版不再红，真漂移照抓）

T2.2 引入的 `lock-drift` 作业首度运行即红（run 37029314736，唯一红条——三元凶
修复后全部 pytest 腿已转绿），但**这不是漂移，是门禁自身的假红**：作业冷启动
重新解析，而生成/推送的两天里上游发了新版（`sqlalchemy 2.1.1→2.1.2`、
`python-dotenv 1.2.3→1.2.4`、`uvloop 0.22.1→0.23.0`）→ 与提交版 diff 判红。
后果是"每次上游发个 patch，全仓 CI 就红一次，只有人肉重生成锁才能绿"——
追新版本来是 dependabot 的活，门禁不该代人受过；更糟的是这种假红会教人
"红了就重生成"，真漂移的信号价值被稀释。

- **修法**：作业改**偏好模式** —— 先把提交版锁 `cp` 到输出路径再 `uv pip compile`，
  uv 对已存在的输出文件默认以其 pin 为偏好集（实测 `--no-cache` 下同样成立，
  偏好来自输出文件本身、非 uv 缓存）。语义还原为"锁内容相对 pyproject 是
  否过期"：上游发新版不变（不红），pyproject 新增依赖/收紧约束致既有 pin
  失效才 red。
- **本地变异验证**（模拟 CI 脚本逐字复刻）：基准 = 绿（与提交版逐字节一致）；
  变异 = pyproject 副本新增 `tabulate>=0.9`（锁未重生成）→ 红并打印
  `+tabulate==0.10.0`；反向对照（从锁删行）会被偏好模式自动补齐、判绿——
  正是期望的"锁=偏好、pyproject=约束源"语义。
- `requirements.frozen.txt` 文件头补升级路径说明（删目标行重跑命令即升级；
  追新版走 dependabot）；uv 仍钉 `==0.11.30`（防解析算法漂移）。
- 本笔只动 ci.yml 一个作业块与锁文件头注释，无代码影响面。

### 2026-10-02 T2.3 阶段一：TestCallSyncFallback 三元凶 CI 红的止血（密闭守卫）

挂账已久的"三元凶机制未抓现行"在 CI 上现行了（run 36792737281 的 macOS 腿、
Nightly 36835438520 同族），探针这回把现场拍全了：

- **污染源画像**：存活到 `tests/test_tool_manager.py` 执行期的**全局
  `subprocess.run` MagicMock**（探针实锤 `subprocess_run_patched=True
  run_impl=MagicMock@unittest.mock`；最早污染边界=TestCallSyncFallback 执行
  期间——它前一条 `test_call_sync_normal` setup 时还干净，而 normal 走异步
  子进程路径不碰 `subprocess.run`，所以它自己能绿）。`_call_sync_fallback`
  是全库少数直接走全局 `subprocess.run` 的路径，三元凶（with_running_loop /
  timeout / with_stderr）于是随机红：`assert '42' in 'ok'`、两条
  `assert True is False`，失败的 `ToolCallResult` 带 `duration_ms=0` 的 mock 指纹。
- **泄漏源仍未归位**：全库常规 `patch/monkeypatch` 用法逐个排查作用域完好
  （无裸 start、无模块/类级残留），头号嫌疑是**异步/后台线程路径上未 unwind
  的 with-patch**（协程被弃置在 with 块内则 `__exit__` 永不执行）——
  探针同条还带出残留线程 `mcp-adapter-bg`。此为开放题，守卫与探针的告警就是找它的线索。
- **止血**：`test_tool_manager.py` 模块级 autouse 守卫
  `_hermetic_subprocess_run` —— 导入时冻结标准库原版，每条用例 setup 时把
  全局恢复之；本文件对泄漏免疫，master 转绿。conftest 探针（conftest 级
  autouse）先于守卫执行，泄漏照常留痕。守卫自检 `TestHermeticGuard` 两条：
  test_a 故意泄漏等价污染现场，test_b 断言守卫已修复——**拆守卫即红**。
- 本文件 81 passed（+2 守卫自检）、ruff clean。

### Fixed

- **required checks 漂移：GitHub 侧少了一条最关键的门禁（PR #56 的设计此前从未生效）**。
  2026-10-01 体检实测三方对账（`.github/ci-required-checks.json` / `ci.yml` 作业形状 /
  `docs/ci-gates.md` §7.2 散文）全部一致地写着 4 条 required（含 `CI merge gate`），
  而 **GitHub 分支保护实况是旧的 5 条**（`Lint (ruff + mypy)` 与 `Frontend Build` 单列，
  **没有** `CI merge gate`）。后果：`CI merge gate` 折进去的 9 平台 pytest / Playwright E2E /
  Alembic 迁移 / pip-audit / bandit / SBOM / perf-smoke **全无平台级强制**——真正在拦的仍
  只是 lint 与前端构建。这不是配置写错，是**带外配置漂移**：三方守卫能读的全在仓库里，
  GitHub 那份不在仓库里，仓库内任何测试都够不着它（详见 `docs/ci-gates.md` §7.6）。
  - **GitHub 侧已同步**为清单的 4 条（`strict: false` / `enforce_admins: false` 等其余保护
    项逐项核对未变）。同步前先用**真实 `needs` 输入**跑过 `ci_merge_gate.py` 三种情形，
    确认 docs-only 全 skipped 判绿（否则这条 required 会卡死所有文档 PR）、code=true 且
    pytest failure 判红、fail-closed 生效——不拿"应该会绿"去改一个会卡住所有 PR 的设置。
  - **补第四处对账守卫**（根因修复，否则漂移会再次静默复发）：
    `py/scripts/check_required_checks_drift.py` + `nightly.yml` 新作业
    `required-checks-drift` + `py/tests/test_ci_required_checks_drift.py`（13 例，含把
    2026-10-01 这次真实漂移钉成用例）。
  - **守卫刻意三态而非绿/红两态**：`MATCH`=0 / `DRIFT`=1（点名多出/缺失的具体条目）/
    `UNVERIFIED`=3（**读不到实况**）。读分支保护要 admin 作用域、默认 `GITHUB_TOKEN` 必然
    403，所以"验不到"绝不能判成通过——那会让它变成本仓最鄙视的"永远绿的门禁"，比没有
    门禁更坏，因为它让人以为门禁在。脚本与 nightly 在验不到时都打 warning 并明说
    "不等于一致"。启用真对账：PAT（`read:repo` 即可）存为 secret `REQUIRED_CHECKS_TOKEN`。
  - `.github/ci-required-checks.json` 的 `_comment` 与 `docs/ci-gates.md` §7.2/§7.6 同步
    记下这条边界，避免下一个改保护的人重蹈。

### 批次 2026-09-30

#### 2026-09-30 T3.0：v5.2.0 演化闭环哑弹排除（trigger / 审批 API）

`dashboard/services/evolution_service.py` 的三个恒 500 缺陷 + 一个持久化缺陷
（2026-09-29 体检发现；AC-07 首版测试全 mock 了 EvolutionLoop，所以从未暴露）：

- **trigger 恒 500**：`trigger_evolution_loop` 对同步的 `run_cycle`
  （`evolution_loop.py:139` 是普通 `def`）做 `await` → TypeError。改
  `asyncio.to_thread` 执行，顺带不再阻塞事件循环。
- **审批 API 恒 500**：`decide_evolution_approval` 调用全包不存在的
  `loop._load_report` → AttributeError（`:696` 的注释还宣称它是"内部协调接口"）。
  新增 `EvolutionLoop.get_report(cycle_id)` / `update_report(report)` 公开读写。
- **审批不落库（第四雷）**：approve 分支是 `pass  # 实际需更新 DB`；且旧
  `_save_report` 是纯 INSERT——对已落库的 cycle 再保存会撞主键
  IntegrityError。`update_report` 为 UPDATE 语义，幽灵 id 返回 False（调用方按
  404 处理），不抛异常。
- **决策语义落地**：`LoopReport` 新增 `approved_suggestions` / `rejected_suggestions`
  （带默认值，旧 JSON 行兼容）——建议从 pending 归入两支，全部决策完
  approval_state 收敛 approved/rejected/partial（枚举注释早就有 partial，此前无实现）；
  非 pending 的 suggestion 决策 → ValueError（路由 400）。
- **测试**：删除两个 mock 掩盖下恰好错过哑弹的用例（mock 了 async `run_cycle`
  与不存在的 `_load_report`，等于把 bug 钉进了测试），换 4 条非 mock 回归——
  trigger 真实落库断言、approve+reject 混合决策收敛 partial、404/400 错误路径、
  get/update_report 契约。演化全套 88 passed、e2e 7 passed、ruff clean。
- **已知遗留（T3.1）**：approve 后建议回流**下一轮** APPLY 仍断——`run_cycle`
  只消费当轮 evaluate 产出的 approved 列表；本轮保证决策正确持久化、状态可见。

施工总图（三工作流 13 任务 + T-UI 支线）：`docs/investment-plan-2026Q4.md`。

#### 2026-09-30 T2.2：requirements.frozen.txt 真锁（审计输入换精确 pin）+ T-UI 顶栏/侧栏布局

**T2.2 真锁**：
- 新增 `py/requirements.frozen.txt`：uv 全量解析（base + enterprise extra，
  universal 跨平台）的精确 `==` 锁，45 包带溯源注释。此前真锁缺位——
  `requirements.lock` 自认是"参考"（直依赖窄范围 + 传递 `>=` 下界），
  pip-audit 审范围等于审"能落进范围的任意版本"，漏洞结论随解析漂移。
- CI 改造：`pip-audit` 与 SBOM(cyclonedx) 的输入换为 frozen 文件；pip 缓存键
  三处同步；新增 `lock-drift` 作业（重新编译 + 剥注释按内容比对，改
  pyproject 不重生成锁即红），并纳入 merge gate 的 `REQUIRED_UPSTREAM`
  （`test_ci_merge_gate.py` 清单同步）。
- 守卫测试 `py/tests/test_frozen_lock.py` 3 条：文件存在且非空、每行精确
  pin、pyproject 直依赖 + enterprise extra 名字全覆盖（与内容级漂移检测互补）。
- `ml` extra 刻意未进 frozen 锁：sentence-transformers→torch 链在 universal
  解析下触发 numpy 构建失败，且不进生产运行时（文件头有记录，需要时单独补）。
- `requirements.lock` 定位改为"直依赖镜像参考"（文件头改写），镜像守卫不变；
  `requirements.txt` / `contributing.md` 的指引同步指向 frozen 真锁。

**T-UI 顶栏/侧栏（用户 09-30 拍板：顶栏是顶栏，侧栏不盖顶栏/整页）**：
- 侧栏从顶栏下方开始（`top: var(--topbar-h)`），废弃"展开盖品牌区"的层叠
  设计——顶栏通栏常驻，品牌大标题升档（`--fs-2xl`/800，logo 同步放大）；
  内容与页脚仍在侧栏右侧（push 语义不变）；移动端 drawer 同样从顶栏下方滑入。
- 新增 `dashboard-enterprise/e2e/layout.spec.js` 5 条布局契约（桌面侧栏不遮
  顶栏/内容在侧栏右侧/大标题完整落在顶栏内/rail 收窄不违约/移动 drawer 不遮
  顶栏），chromium 实测 5 passed；`npm run build` 通过。

#### Added

- **`CI merge gate`：把"测试必须绿"从人看变成平台拦**（`py/scripts/ci_merge_gate.py` +
  `ci.yml` 的 `gate` 作业 + `py/tests/test_ci_merge_gate.py` 18 条）。缺口是这样形成的：
  矩阵作业 `pytest (…)` 在 docs-only 时上报的 check 名是**未展开的模板字面量**，与代码 PR 上
  9 条展开名互不相同（两侧均实测），push-only 作业在 PR 上恒为 skipped —— 两类都当不了 required
  上下文，于是 required 只能覆盖 lint / 前端 / 三条恒跑作业，**最重要的 9 平台测试矩阵始终没有
  平台级强制**。现在用一条 name 稳定的聚合作业（`if: always()`，needs 覆盖 lint/test/frontend/
  e2e/迁移/pip-audit/bandit/SBOM/perf-smoke）把上游结论折进自己的红绿，required 从 5 条收敛为
  4 条而覆盖面变大。判定是白名单式且 fail closed：非 `success` 一律不通过（docs-only 时
  `skipped` 例外）；scope 判 `code=true` 却有该跑的作业 skipped ⇒ 判红（拦"分类器误判导致门禁
  没跑"）；needs 为空/缺 `result`/出现未知新状态/scope 无输出 ⇒ 一律红。`if: always()` 是硬要求
  （掉了它，上游一红这条 check 就不产出，required 变成"从未上报"→ 所有 PR 永久 blocked，
  即 §7.1 的 B 轮语义），由结构守卫钉住。明确**不**把 push-only 作业放进 needs：那样严格面下
  gate 会在每个 PR 上恒红；容器面仍由主干 push 验证。清单 kind 增加 `always-guard`，
  `test_ci_required_checks.py` 同步扩展。首版接线时 `test_cli_stdout_is_valid_utf8_bytes_under_cp1252_console`
  在 `windows-latest / Python 3.10` 独红（3.11–3.13 同测全绿，非负载抖动：重跑 3 次同点失败，
  8596 passed / 1 failed）—— 该用例把子进程 env 精简成只剩 `PYTHONIOENCODING` + `PATH`，
  3.10 的解释器在 `_Py_HashRandomization_Init` 阶段就取不到随机数而死（`Python runtime state:
  preinitialized`，returncode 1），根本没跑到脚本。修复是改回继承父 env 只覆盖目标变量
  （`{**dict(os.environ), "PYTHONIOENCODING": "cp1252"}`），与同族用例
  `test_ci_path_scope.py::test_cli_survives_non_utf8_console` 口径一致；模拟"非 UTF-8 控制台"
  只需要换编码变量，连带剥掉 `SystemRoot` 之类是超出意图的。zh-CN 本机 3.14 复现不出来，
  这条只能靠 CI 版本分裂证据定性。

#### Changed

- **泄漏探针补"谁 patch 的"指纹（`run_impl=`）**：`py/tests/conftest.py::_leak_probe` 此前只报 `subprocess_run_patched=True/False`，等于只说"进程被弄脏了"而不说"被谁"。新增 `_run_fingerprint()`，日志多一个字段 `run_impl=<定义模块>:<限定名>`（仅在 `subprocess.run` 被换掉时有值，否则为 `-`）。为什么这样取值：真实嫌疑人形状是测试文件里的局部闭包（`py/tests/test_agent_adapters.py::_mock_subprocess_run` 返回的 `_run`，以及若干 `test_xxx.<locals>._capture_run`），它们的 `__module__/__qualname__` 直接带出**所在文件与所在用例**，于是一次复发就能点名而不是再靠 `output='ok'` 这种间接指纹猜。对 Mock 类对象只报 `类型名@类型的模块`，**刻意不读 `return_value`** —— MagicMock 的属性访问会自动创建子 mock，那等于探针反过来改变自己要观测的对象（观察者效应）；这条约束由 `test_run_fingerprint_does_not_touch_the_mock_it_reports` 钉住（断言零调用、`mock_calls` 为空、且取证前后 `__dict__` 键集合不变）。新增 3 条用例（12 passed），变异验证 5 例各点亮对应断言：指纹退化成常量 / 去掉 `run_impl` 字段 / 取消 `-` 分支 / 去读 `return_value` / 取证时顺手调用 fake。**动机**：`tests/test_tool_manager.py::TestCallSyncFallback` 三条在 macOS 腿随机红（`assert '42' in 'ok'` 与两条 `assert True is False`，返回值都是 `ok=True/exit_code=0/duration_ms=0/output='ok'` 的罐头值），2026-09-29 的 PR #54 与当日主干 run 448 各复发一次、失败用例 ID 逐字相同；`threads=[]` 已排除线程泄漏嫌疑，但"永久 patch"也与"紧邻的前一条用例同断言却通过"矛盾 ⇒ patch 是窗口性的，机制仍未定位 —— 本改动是把"下次复发能点名"这件事先做完。纯观测，不改任何生产代码或用例断言。

---

### 批次 2026-09-29

#### Fixed

- **dry-run gate 接线修复（no-op → 显式 opt-in enforce）**：`maop_plan` 为 deploy/pipeline/fileops 路由挂 dry-run gate 但从不设置 `plan["dry_run"]`，`_gate_dry_run` 对未声明的 plan 恒 PASS（`maop_verify.py` 的向后兼容合同），闸门从未生效。修复：`Plan` 新增 `dry_run` 字段，`MAOP_DRY_RUN_ENFORCE=1` 时三条路由置 True，未设置保持历史行为。路由口径实测：`config/agents.yaml` 路由表无 "deploy" 键，真实任务只命中 pipeline/fileops（deploy 分支仅能经 `routing_key` 覆盖触达）。新增 `py/tests/test_dry_run_gate_wiring.py`（接线 5 例 + gate 行为 3 例，8 passed）。已知限制：enforce 为 fail-closed，且当前执行器不产出 dry-run 信号（全库无产出方），开启后 pipeline/fileops 任务会卡在 verify，须先接执行器信号产出。`MAOP_DRY_RUN_ENFORCE` 已同步 `.env.example` / `README.md` / `docs/configuration.md`。
- **委派指标双计数修复（success/total 告警分母膨胀）**：`MAOP_DELEGATIONS_TOTAL` 在 `maop_plan.py`（建 plan）与 `dispatch_core.py`（派发）各 +1，而 success/failed 只在派发侧计数 → 纯环路流量下 total:success ≈ 2:1，`monitoring/alerts.yml` 的 success/total < 0.8 与 `slo-alerts.yml` 的 burn rate 会在健康系统上误报，`prometheus-alerts.yml` 的 failed/total 被稀释一半（漏报）；`MAOP_DELEGATION_DURATION` 亦混入 plan 构建耗时（稀释 P95 延迟告警）。修复：移除 plan 侧两处指标写入（计数/耗时唯一在派发侧），`py/maop/monitoring.py` 注释同步更正。回归测试 `py/tests/test_maop_plan.py::TestPlanMetricsSideEffects`（变异验证：重加 `.inc()` 即红）。
- **RouteScorer 单例污染修复（config 路由进程级失效）**：`get_route_scorer()` 的热重载守卫要求 `_instance.config is not None`，单例若被无 config 调用先初始化（生产真实路径：dashboard 冷却查询端点 `routing_service.get_route_cooldowns()` 早于任何路由请求裸调），之后所有真实 config 被永久忽略，config 路由静默退化为默认 chat。修复：`_instance.config is None` 时接受首个真实 config 替换单例（冷却状态跨替换保留），identity / 版本热重载语义不变。确定性复现：裸 `get_route_scorer()` 后 `maop_plan("run the ci pipeline", config=load_config())` 修复前 `chat` / 修复后 `pipeline`。回归测试 `py/tests/test_route_scorer.py::TestGetRouteScorer::test_config_recovers_configless_singleton`（含冷却保留断言）与 `test_configless_call_does_not_displace_real_config`。
- **日志轮转链式再轮转 bug（Windows `WinError 123` 根因）**：`rotate_logs()` 扫描目录内全部 `.log/.jsonl/.json` 超限文件，但从不跳过**已轮转的备份**（`_ROTATED_RE` 此前只用于清理旧备份），备份自身也超限 → 被再次轮转成 `<name>_<ts1>_<ts2>...` 链式文件名，每次还会把源文件重建为空；链条长度增长到触发 Windows 路径上限后 `os.replace` 抛 `WinError 123`。实测残留：`data/degradation_*.log` 39 个链式文件、`logs/MAOP-structured_*.log` 19 个，均已移出仓库至本地暂存区（可再生成）。修复：轮转循环对命中 `_ROTATED_RE` 的文件直接跳过（`log_rotate.py`）；新增回归测试 2 条（超限备份不再轮转 / 连续两次轮转不产生链式名，冻结 `datetime` 断言文件名集合），`tests/test_log_rotate.py` 8 passed。
- **relay_platform 写端点鉴权补齐**：`dashboard/routers/relay_platform.py` 的 `POST /api/relay-platforms`（注册体含 `api_key`）、`DELETE /{name}`、`POST /compare` 三个写端点此前**没有任何守卫** —— 文件未导入 `require_admin`，docstring 却声称本 router 负责"权限检查"，而其同级模块 model_gateway / hooks 的写端点全部逐行调用 `require_admin(request)`。后果是未授权请求即可写入出网凭据 + 目标 base_url（等于把后续 LLM 流量与密钥导向攻击者指定端点）或删除平台。修复：三个端点首行加 `require_admin(request)`，docstring 声明与实际一致。回归测试 `py/tests/test_relay_platform.py` 新增 3 条（非 admin → 403 且 `code=HTTP_403`；被拒的注册不落库；GET 端点口径不变），全文件 30 passed。取证：`scripts/check_admin_coverage.py` 的发现数由 17 降至 14，剩余项均已分类为用户级端点或外部回调。
- **`config/settings.yaml` 幽灵加载层**：`docs/configuration.md` 的"配置加载优先级"与 `maop/config/settings.py` 的模块 docstring 都列了 `config/settings.yaml` 这一层，但全仓无任何代码读取它（grep 只命中这两处声明与一篇归档报告；`settings.py` 是 Pydantic `BaseSettings`，只有 env + `.env` 两个来源）。属"文档与注释承诺了代码没有的能力"，已按实核更正为 环境变量 > `.env` > Field defaults，并在 docstring 显式写明不存在 YAML 层，防止再次被当成事实引用。
- **影响面判定脚本在非 UTF-8 控制台崩溃（Windows 四条 pytest 腿全红）**：`ci_path_scope.py` 的中文摘要含全角括号 `（`，Windows 上 `sys.stdout` 按**区域代码页**编码，en-US runner 是 cp1252 → `UnicodeEncodeError` 让脚本以 1 退出；zh-CN 本机是 cp936、能编码这些字符，所以本地怎么跑都绿。由两个既有的子进程用例如实抓到（`test_cli_writes_github_output` / `test_cli_mixed_changeset_reports_code_true`）。修法在脚本侧固定 UTF-8 输出（异常时降级不失败），不给调用方塞 `PYTHONIOENCODING` —— 真实 CI 步骤不会设它，设了也只是把缺陷留在原地。新增可证伪用例 `test_cli_survives_non_utf8_console`（显式 `PYTHONIOENCODING=cp1252` 起子进程，因此在任何平台都能复现）；变异验证：去掉 `reconfigure` → 该用例即红；还原后 `test_ci_path_scope.py` 34 passed。 **但第一版只修了写侧**：下一条 push 的 CI 实测 Windows 仍红 1 条 —— 子进程已固定输出 UTF-8，而用例用 `text=True` 按**父进程区域编码**解码，en-US runner 上变成 `UnicodeDecodeError: 'charmap' codec can't decode byte 0x90`（崩溃只是从写侧搬到读侧）。补法：两处读侧显式 `encoding="utf-8"`；另加跨平台确定的字节契约用例 `test_cli_stdout_is_valid_utf8_bytes`（不用 `text=True`，取原始字节严格按 UTF-8 解）。为什么必须有它：zh-CN 本机 cp936 会把 UTF-8 字节吞成乱码且不报错 —— 实测去掉读侧 `encoding` 后本机仍 passed，即"父进程解码失败"这类问题本机不可测，只有 en-US runner 会暴露，故把契约钉在字节层。变异验证（去掉 reconfigure + 子进程强制 cp1252）→ 该用例判红；最终 `test_ci_path_scope.py` 35 passed、ruff clean。
- **Nightly 的规模判据踩在刀刃上（假红）**：`tests/e2e/test_boundary_conditions.py::test_large_batch_register_1000` 同机自校准上限是"基线 × 条数 × 3"，Nightly run `36395972637` 连跑 3 次里第 3 次报"耗时 11.0s / 上限 10.6s"—— 实测比值 **2.97 对 3.00**，只差 1%。上浮本身不是退化（register 每条单独写 SQLite、无批量事务，表从 100 行涨到 1100 行时 per-op 自然变贵），而它要抓的复杂度退化量级是几百倍。上限放到 5×：本机实测比值 1.17（余量 4.3×），对 Nightly 最坏观测仍有 1.68× 余量；判据仍咬得住 —— 变异验证把上限临时收到 1.05（低于实测 1.17）用例立即失败并报出实测/上限两个数。

#### Added

- **required checks 与 `ci.yml` 作业形状的三方一致性守卫**：master 的 required 上下文此前只活在两处 —— GitHub 设置（仓库里看不见）与 `docs/ci-gates.md` §7.2 的散文。新增机器可读清单 `.github/ci-required-checks.json`（5 条，各带 workflow/作业名/kind），配套 `py/tests/test_ci_required_checks.py` 6 条断言把**清单 ↔ `ci.yml` 作业形状 ↔ §7.2 散文**互相对账：context 必须唯一对应一个作业名（改名或删作业会让 required 永不上报、所有 PR 卡 `blocked`，实测）；required 作业必须在 `pull_request` 上**沿 needs 链递归可达**；kind 必须与作业真实 `if:` 一致；不许把矩阵作业列进清单（矩阵腿在 docs-only 时上报的是未展开的字面量名）；文档与清单不得各说各话。两个失败方向不对称：作业改名立刻可见，而"required 作业变成 PR 上恒不产出"是**静默假门禁** —— 所以可达性判据必须递归：`container-scan` 自己没有 `if:`，却 `needs: docker`（push-only），只查作业自身条件抓不到。变异验证 8 个破坏各点亮对应守卫；反证 —— 把递归摘掉后"祖先 push-only"这一例直接漏网。同时把 `.github/ci-required-checks.json` 加进 `ci_path_scope.py` 的 `CODE_FILES`：否则"只改清单"的 PR 被判 docs-only、这条守卫**永不执行**（正是本轮反复在治的"配了不跑"）。
- **`docs/ci-gates.md` §7.5：一次误判的证伪记录**：据"主干某次 push 只跑 18 秒、13 条 skipped"曾判定 job 级 scope 把 trunk 兜底验证裁掉了，实测两条推翻 —— ① `#52` 之前 `on.push.paths` 与 `on.pull_request.paths` 是同一份白名单且不含 `docs/**`、`CHANGELOG.md`，docs-only 合入主干本来就**完全不触发 workflow**（0 条 check），现状是"3 条真跑 + 可见的 skipped"，覆盖面不变、可见性变好；② 把旧白名单每一项喂给发布版分类器，16 条路径全部仍判 code=true、零漏判，且分类器另算 `deploy/`、`monitoring/`、`alertmanager/`、`nginx*.conf` ⇒ 净增。方法论：**比较两次 run 的时长必须同内容类型**；判"是否丢了覆盖率"要拿新旧判定面各自回放同一批输入。
- **`check_api_contract.py` 增补 README `/api/*` 引用检查**：README 的 curl 示例指向已不存在端点时，照抄会拿到 SPA 兜底 `200 + HTML`。新增 `parse_readme()`（完整 URL / 内联代码 / 散文裸路径三式提取）与 `readme_path_covered()`（方法无关、接受前缀族引用），复用同一份后端路由集判定；当前 README 4 处引用全部命中。能力并入自根 `scripts/doc_reconcile.py` —— 它用朴素子串匹配、看不见 FastAPI `prefix` 组合（`/api/cost/summary` 被误报不存在），且 CI lint job 的 `working-directory: py` 使其从未被执行；原文件已删除（CI 实际运行的是 `py/scripts/doc_reconcile.py`，与本次无关）。
- **`check_config_drift.py` 增补版本同步检查**：`maop/__init__.py` 的 `__version__` 与 `pyproject.toml` 的 `version` 必须一致（不一致 → FAIL；任一侧不可解析 → 显式打印告警，避免"没跑"与"全绿"同形）。同样并入自根 `scripts/doc_reconcile.py`。三项负例已实测（不一致 / 一致 / 不可解析）。
- **文档一致性检查接入 CI（新 `docs-gate` 作业）**：`py/scripts/check_docs_consistency.py` 此前"零引用、1400+ 条发现无人消费"，本轮治掉假阳性后接进 `ci.yml`。范围由 `docs/README.md` 自己声明的口径决定（第 1–6 章"当前权威文档"，第 7 章归档不算），历史快照/设计文档需文件头 `<!-- docs-gate: exempt=<理由> -->`、单行需 `skip=<理由>`，**理由不得留空且会被逐条打印**。作业**不挂 `if`**：只改文档会写进不存在的路径，只删代码会让文档里的旧路径失效，两边都必须跑（脚本只用标准库，本机约 3 秒）。新增守卫 `py/tests/test_docs_consistency_gate.py` 22 条：后缀简写放行边界（`core/worker_pool.py` 不得因 `core/reliability/worker_pool.py` 而放过）、跨仓/占位符/排版记号分型、gitignore 深层目录匹配、计数口径、假仓库端到端"注入死路径必红 / 空理由豁免不生效"、以及 `docs-gate` 作业不许被加 `if` 的结构断言。

#### Changed

- **master 开启 required checks（带外配置，故在此留痕）**：job 级 scope 落地后按 `docs/ci-gates.md` §7 复测 skipped 语义 —— 以 docs-only PR 为夹具，required 只放一条 success 作业 → `clean`；换成一条**永不上报**的上下文 → 8 秒内翻 `blocked`（正对照，证明判定在算而非配置空转）；换成一条 **skipped** 作业 → 回到 `clean`。结论：**上报为 skipped 即满足 required，从未上报则永久卡住**。据此 required 设 5 条：`CI scope (code vs docs-only)`、`Secret Scan (gitleaks)`、`Docs consistency gate`（恒存在）+ `Lint (ruff + mypy)`、`Frontend Build`（docs-only 时跳过即满足，代码 PR 上必须真过）；`strict: false`、`enforce_admins: false`（保留 owner 逃生门；这也解释了早前"保护形同虚设"的观察 —— admin 绕过的是强制，不是判定）。**pytest 矩阵腿明确不纳入**：docs-only 时那条 skipped check 的名字是未展开的字面量，与代码 PR 上 9 条展开后的名字不同（两侧均实测），按精确名设会把文档 PR 永久卡住；真要覆盖 pytest 应加一条名字稳定的聚合作业读 `needs.*.result`，而不是折腾上下文匹配。同理排除 `Docker build` / `Container Scan (trivy)` / `Compose Smoke` / `Publish to PyPI`：它们在 PR 上恒 skipped 或不产生，设 required 等于"永远空满足"，是假门禁。
- **并入 origin/master（PR #33–#51）后新增门禁首跑即绿**：合并 vitest 3→5、node 20→24、前端覆盖率门禁接入、codecov 吞错步骤删除等 41 个远端提交，冲突 4 处（`ci.yml` / `CHANGELOG.md` / `package.json` / `package-lock.json`）的解决依据见合并提交 `a25b06a`。CI 实测：`Docs consistency gate`（本轮新接的作业）在首条 push 上 **success**，`Lint (ruff + mypy)`、`Frontend Build`、`Playwright E2E`、`SAST`、`Alembic migrations` 与 ubuntu/macos 各 Python 腿全绿；Windows 腿的红源自下面 Fixed 里的控制台编码问题。版本口径随合并更正：`docs/product-consolidation.md` 的"vitest 3.2.7（实测 lock）"→ 5.0.2，并注明当年那条"ci.yml 假称 vitest@5"的更正记录关系已反转（现在真是 5）。
- **人工审计脚本硬化**：`scripts/check_admin_coverage.py` 修复同一函数挂多个写方法装饰器导致的重复报告（`register_platform` 报两次，18 → 17 条），docstring 补记盲区（router 级 `dependencies=[...]` 守护看不见、硬编码 PUBLIC_ENDPOINTS）与逐条分类。它当时报出的 relay_platform 无鉴权问题**已于同日补齐**（见 Fixed）。`py/scripts/check_docs_consistency.py` 已从"人工工具"升级为 CI 门禁（见 Added），两类工具的定位差异在各自 docstring 里写明。
- **docs/archive 的 emoji 清零（本轮补完上轮未做的部分）**：15 个文件 / 476 处 emoji（357 行）按上一批同一口径处理 —— 正文/标题里的删除（严重度词如"高/中/低风险""已修复"本就在上下文里），表格中"仅 emoji"的单元格依表头换成文字（`状态`→已修复/未修复、`是否导出`→是/否、`新（MAOP）`→已改、依赖列→已加），`✓/✗/○` 保留（排版记号）。`design-system-legacy.md` 豁免：该文件把 emoji 当作设计规范对象描述（`🔷 Emoji 中文 English Name`），删除会破坏内容本身。改动做过结构性核对：15 文件逐行行数一致、每行 `|` 数量不变、且只动原本含 emoji 的行（0 异常）。**但当时声明的"`✓/✗/○` 保留"未成立** —— 清理脚本的字符类把 U+2713 `✓` 一并纳入，实际吞掉了 2 个 `✓`（同句仍有"一致""不依赖"文字，语义未受损），已由下一批恢复并改正字符类，见下条。
- **deliverables 的 emoji 清零 + 一次自纠**：4 个被跟踪的交付文档按同批口径清理（正文删除；纯 emoji 单元格按表头换文字：`判定`/`是否通过`→通过，`个人版`/`企业版`→是/否；`✓` 作线框图勾选记号保留）。核对过程中发现并修正上条所述偏差：恢复 `docs/archive` 被误删的 2 个 `✓`，清理用字符类改为显式跳过 U+2713/U+2717。注：`deliverables/` 整目录在 `.gitignore:174` 内，仅历史被跟踪的 9 个文件可入库，`Delivery-Plan-v5.1.0-Dual-Repo.md` 属未跟踪文件（本地副本一并清理）。
- **当前权威文档的路径订正（由新门禁扫出）**：`performance-benchmarks.md` 引用 7 个重构前的扁平模块路径（`py/maop/core/worker_pool.py` 等 → `core/reliability|security|monitoring/` 下的真实位置，两个同名模块按内容判定归属）；`contributing.md` 的 `core/api_key_vault.py`、`core/sandbox.py` → `core/security/api_key_vault.py`、`core/agent/plugins_hooks/plugin_sandbox.py`；`troubleshooting.md` 把 `system.py` 更正为 `dashboard/services/system_service.py`，并删掉不存在的 `config/sso.yaml`（SSO IdP 参数实际存在 provider 记录的 `config` 字段里，经 `POST /api/sso/providers` 写入）；`user-guide.md` 删掉不存在的 `config/tenants.yaml`（租户由 TenantManager 经 `/api/tenants/*` 管理）；`privacy-policy.md` 的 `data/.api-key` 更正为代码实际使用的 `data/.enc_key`；`cla.md` 移除仓库内不存在的 `docs/cla-employers.md` 承诺；`technical-whitepaper.md` 两处表格乱码（`| 列 |' |`、`| Art. 28 |" |` 多出的游离引号导致列数错位）已修。

#### Removed

- **架构级死重清理（安全网 tag `archive-v4.0-before-removal`）**：`git rm -r archive/`（107 文件：js-dashboard 17 / ps-legacy 86 / legacy 4；零运行时引用，方案见 `docs/audits/archive-cleanup-plan.md`，2026-08-16 已成文未执行）；删除根 `otel-collector-config.yaml`（P2-M-02 起 compose 挂载 `deploy/otel-collector.yaml`，见 `docker-compose.yml:263`）、`scripts/migrate_bridge_to_proxy.py`（2026-07-26 重命名迁移已完成的一次性脚本）、`scripts/smoke_test_agents.py`（首 import 即断，功能已被 pytest 覆盖）、根 `scripts/doc_reconcile.py`（能力并入上述两脚本）、空目录 `plugins/`（`PluginManager` 启动自建，`plugin_manager.py:113`）。相关引用同步：README、`docs/DESIGN_RULES.md`、`docs/contributing.md`、`config/agents.yaml`、`maop.ps1`、`dashboard/server.py` 注释、`plugin_manager.py` 注释。
- **README 模块表死引用修正**：`core/security/tenant.py` 已于 2026-09-25 删除（严格子集并入 `core/tenant/`，见 `core/tenant/manager.py:14`），表项更新为 `core/tenant/manager.py`（由 `check_docs_consistency.py` 扫出，工具价值验证）。

### 批次 PR #33–#51：依赖、CI 与探针更正

#### websockets 版本范围改判（下界 12.0 → 14.0，上界 <15 → <17）

原来的 `websockets>=12.0,<15` 两头都不对，用回环真连接测试逐版本实测（每个版本都确认
`websockets.__version__` 真的是它）后改判：

| websockets | 结果 |
|---|---|
| 12.0 / 13.0.1 / 13.1 | **失败** `TypeError: BaseEventLoop.create_connection() got an unexpected keyword argument 'additional_headers'` |
| 14.0 / 14.2 / 15.0.1 / 16.1.1 | 通过（`tests/test_mcp_hub.py` + `tests/test_ide_extension_adapter.py` 各 72 passed） |
| 17.1 | 也通过，但先不放开 —— 留在护栏外，需要时再抬到 `<18` |

- **下界才是真问题**：`mcp_hub_transport` / `ide_extension_adapter` 用的 `additional_headers`
  与"连接对象有 `.state` 没有 `.open`"都是新 asyncio 实现的特征，而顶层 `websockets.connect`
  到 **14.0** 才默认指向它。所以 `>=12.0` 是一条从没成立过的承诺。
- 原注释"websockets 14+ made breaking asyncio changes; cap below 15"方向反了：14 不是破坏源，
  14 恰恰是能用的起点。
- 新增回归测试 `TestWebSocketTransportRealConnection`（真起 `websockets.serve` 做 JSON-RPC
  round-trip + `is_alive` 生命周期）；此前 `.open` 缺陷静默，就是因为所有用例都在测 mock。
- CI 侧证据：macOS job 按新约束实装 websockets 16.1.1 且该用例通过。
- dependabot `#15`（只抬上界到 `<17`、不修下界）由本节取代。
- ⚠️ 过程记录：本 PR 第一版误以**过期的本地文件**为基线（`git fetch` 被别的会话留下的坏 ref 挡死），
  把 `pydantic-settings 2.15.0 / uvicorn 0.53.0 / mmh3 5.3.0` 静默回退了；逐行 diff 复核时发现，
  已改为"一律以 master 内容为基准重建"。同期 #23 也因同一原因回退过前端 11 项依赖，见 #25。

#### requirements.lock 与 pyproject 的镜像关系加上机械强制

`requirements.lock` 的表头一直写着「Source of truth for DIRECT deps: pyproject.toml.
This file mirrors it」，但**没有任何东西强制它** —— 实测漂移：dependabot #13 抬了
`pydantic-settings / uvicorn / mmh3` 的 pin 只改了 pyproject 与 requirements.txt，
lock 仍停在 `2.5.2 / 0.30.6 / 5.2.1`；照 lock 装环境的人拿到的是旧版本。

- 新增守卫 `py/tests/test_requirements_lock_sync.py`（3 条）：直依赖段与 `enterprise` 段
  必须逐条镜像 pyproject（名字集合 + 约束文本都相等），requirements.txt 不得少包。
  不用 `tomllib`：CI 矩阵含 Python 3.10，它是 3.11 才进标准库的，故用一个只解析
  `name = [ ... ]` 数组的窄解析器。
- lock 里给两段各加显式结束标记（`# END DIRECT DEPENDENCIES` /
  `# END ENTERPRISE DEPENDENCIES`）：extras 段的标题只是普通注释，靠"下一条注释"划界会把
  enterprise 条目误读进直依赖段（第一版守卫测试就是这么误报的）。
- 消除现存漂移：`pydantic-settings==2.15.0`、`uvicorn[standard]==0.53.0`、`mmh3==5.3.0`、
  `pyyaml>=6.0.2,<7.0.0`（以上随 #24 顺带落了），本 PR 补最后一处
  `websockets>=12.0,<15 → >=14.0,<17`，并把只属 extras 的 `lxml` 移出直依赖段。
- 变异验证：把 lock 的 websockets 改回 `>=12.0,<15` → 守卫立刻失败并打印
  `{'websockets': ('websockets>=14.0,<17', 'websockets>=12.0,<15')}`；改回正确值 → 3 passed。
- ⚠️ 仍未解决（另案）：这个文件按自身表头就只是 **reference，不是真锁** —— 下半段的
  transitive 条目仍是手写的 `>=` 范围而非精确 pin。要做到"可复现构建"得
  在干净 venv 里 `pip install -r requirements.txt && pip freeze` 重新生成，属独立立项。

#### 类型门禁改为显式 CI 步骤（拿到 `workflow` scope 后的收尾）

#23 当时因为 gh token 无 `workflow` scope（GitHub 对改 `.github/workflows/*` 的写入直接拒绝），
只能把 `vue-tsc --noEmit` 借道 npm 的 `pretest` 生命周期生效。权限到位后按原计划改为显式形态：

- `.github/workflows/ci.yml` 的 frontend job 新增 `- name: Type check (vue-tsc)`（紧跟 `Lint frontend`）。
- `package.json` 删掉 `pretest`：门禁要在 CI 里**看得见、失败能归因到具体步骤**，不藏在 `npm test` 背后；
  本地 `npm test` 也回到只跑测试。`typecheck` 脚本保留。

#### 主干 flaky 的 CI 侧观测探针（只观测，不改行为）

`tests/test_tool_manager.py::TestCallSyncFallback` 3 条在 macOS/Windows 随机红（症状
`assert '42' in 'ok'`、两条 `assert True is False`），把主干和 PR 反复判红（master `88e75804`
的验证运行即 3 failed / 8453 passed，其余 8 平台全绿）。读代码排除的猜测：没有 module/class
作用域的 `subprocess.run` patch；`ToolManager` 无类级共享注册表；per-test `MAOP_DATA_DIR`
隔离有效。既然本地推不出机制，就让 CI 自己交代。

- `tests/conftest.py` 新增 autouse 探针 `_leak_probe`，在每条用例开始前记
  `prev=`（同一 worker 上一条跑过的用例 nodeid）、`subprocess_run_patched=`（全局
  `subprocess.run` 是否仍非标准库原版）、`threads=`（残留线程名）；只在"可疑"时打 WARNING。
  用例失败时 pytest 会把 setup 阶段捕获到的日志印进失败详情 —— 红的那一次自带嫌疑人。
- 自证有效：人为让上一条用例改 `subprocess.run` 不还原，探针准确报
  `subprocess_run_patched=True prev=…test_a_leaks_patch_without_undo`；正常链路不误报。
- ~~顺带抓到一处真实泄漏：每条用例开始时都有残留线程 `Thread-1 (run_server)`（dashboard 测试
  服务器线程从未 join）~~ **这条结论是错的，已撤回**（见下一节）：那条线程属于
  `pytest_rerunfailures` 插件自己，不是被测代码的泄漏。
- ⚠️ **过程记录（我自己造成的回归，已修）**：探针第一版顺手调了 `get_db_path("tool_manager")`
  想做"跨用例工具行串味"取证，但它定义在 `_isolate_data_dir` **之前**、autouse 按定义顺序执行，
  于是提前解析并初始化了 `data_dir` / settings 单例，把 `tests/test_secrets.py` 的 4 条用例
  直接弄红（那些用例依赖"密钥文件按当前 data_dir 查找"）—— 本地全量因此 31 failed + 19 errors。
  修法：探针不再触碰 DB/settings（该取证目标也已被证伪），并把夹具移到 `_isolate_data_dir` 之后。
  教训写进夹具 docstring：**观测型工具不许有副作用**。

#### 撤回 #39 的"真实泄漏"结论，并把探针改成可被证伪的

给 #39 的探针补变异验证时，两件事同时暴露：

1. **上一条结论是错的**。`Thread-1 (run_server)` 不是"dashboard 测试服务器线程从未 join"，
   栈帧实测是 `site-packages/pytest_rerunfailures.py:746 in run_server`（限定名
   `ServerStatusDB.run_server`）阻塞在 `socket.py:298 accept` —— 插件自己的 socket 服务线程，
   只要 `--reruns` 生效就整个 session 常驻。危害不是刷日志（pytest 只在用例失败时才打印捕获
   日志），而是**每条用例 setup 都 WARNING、红的那一次自带一个假嫌疑人**。
2. 探针此前**从没被证明过只会报该报的东西**。

修法（`tests/conftest.py`）：

- 新增 `_suspicious_threads()`：按线程 target 的**定义模块**剔除测试框架自己的线程
  （`_HARNESS_THREAD_MODULES`）。用模块名而不是线程名 —— `Thread-N (func)` 是自动格式，
  随 Python 版本与函数名漂移，模块名才是稳定指纹。
- 条目格式改为 `名字<-定义模块:限定名`；拿不到 target 的 Thread 子类记 `?:?`（宁可多报不漏报）。
- 判定逻辑从夹具里抽成 `_leak_probe_line()`：pytest 不允许直接调用夹具函数（会报
  "Fixture called directly"），而"什么时候才报"恰恰是最容易被改坏的一点（写成 `if False`
  也能全员绿），必须可被用例直接断言。

新增 `tests/test_conftest_leak_probe.py`（9 条）：正向命名泄漏并校验 `prev=` 记账、
`subprocess.run` 未还原路径、干净时返回 `None`、harness 过滤（伪造样本 + **真实**
rerunfailures 线程各一条）、allowlist 里模块名必须可导入、夹具仍是 autouse 且定义在
`_isolate_data_dir` 之后。自检用的线程全部带超时并在 `finally` 里释放 —— 自检工具自己不许泄漏。

变异验证（回滚验证，每次单独改实现再改回）：

| 变异体 | 结果 |
|---|---|
| `_leak_probe_line` 恒返回 `None`（探针永不报告） | 2 红 |
| allowlist 里 `pytest_rerunfailures` 拼错 | 3 红（含可导入性守卫） |
| 退回"拿 `模块:限定名` 整串 `split('.')`"的原始 bug | **仅**真实线程那条红 |

第三行是这次最值钱的发现：只用伪造样本（`_park` 的模块名里没有点号）**抓不到**这个 bug，
必须留一条对着真线程的回归位。

- ⚠️ **本 PR 自己造成的第一次红（已修）**：首版用 `getattr(_leak_probe, "_pytestfixturefunction").autouse`
  证明探针是 autouse —— 本地全量 9984 绿，CI 却 ubuntu/macos 两平台红。根因是 **pytest 版本代差**：
  dev extra 只写 `pytest>=8.0`，CI 解析到 **9.1.1** 而本地是 **8.3.4**；8.x 的 `@pytest.fixture` 返回
  "原函数 + `_pytestfixturefunction` 标记"，9.x 返回 `FixtureFunctionDefinition`，那个属性名根本不存在。
  改成**行为断言**（探针若真 autouse，必在本次 setup 把 `_PROBE_PREV["nodeid"]` 写成我的 nodeid），
  并在 8.3.4 / 9.1.1 两套环境各做一次变异验证（关掉 `autouse=True` → 两版本都恰好 1 红）。
  教训：断言第三方库的**私有属性名**就是把测试绑死在某个版本上；`pytest>=8.0` 这种开区间下，
  凡碰框架内部的用例都要两头实测（本地用 `pip install --target` + `PYTHONPATH` 叠版本，不动共享环境）。

仍未解决：`TestCallSyncFallback` 三元凶的机制还没抓到现行 —— 探针现在只报真嫌疑人，
等下一次红。

#### 依赖与 CI 批次补记（2026-09-27：#40 #41 #42 与 dependabot 6 条）

⚠️ **本段是补记**。下面 9 个变更当天直接落到了 master，**没有一个写了 CHANGELOG 段**，
违反本文件《发布前 checklist》第 2 条（"CHANGELOG.md 已更新本次版本段"）。
另有一条流程事实一并记下以免后人误判"每个 commit 都被验证过"：`ci.yml` 的 push 触发带
`cancel-in-progress: true`，连续 squash-merge 会把**前一个 master 验证跑取消**（实测一段
连续合并里 6 个 master 跑被 cancelled，只有队尾那个跑完）。

**CI 运行时抬版 —— #41 → `3c8a93c2`**

- `.github/workflows/ci.yml`：`NODE_VERSION` `"20"` → `"24"`。
- 为什么必须抬（实测，不是推测）：`jsdom@30` 与 `vitest@5` 的 `engines` 都是
  `^22.22.2 || ^24.15.0 || >=26.0.0`。node 20 下 vitest 5 在 `import jsdom → undici` 时抛
  `TypeError: webidl.util.markAsUncloneable is not a function`，**56 个测试文件全 error、
  `Tests no tests`**（#40 首跑的 `Frontend Build` 日志）。抬到 24 后同一棵树 56 files / 478 tests 全绿。
- 连带顺序约束：`pull_request` 跑用的是 **PR 自带的 workflow 文件**，所以依赖 #41 的 PR 必须
  先把 master 的 `ci.yml` 正向提交进自己分支，否则不可能变绿（#40 就是这么被挡了一轮）。

**vitest 协同批 —— #40 → `c4622029`**（取代 dependabot #31 / #34）

- `vitest ^3.0.0 → ^5.0.1`、`@vitest/coverage-v8 ^3.2.7 → ^5.0.1`（lock 解析到 5.0.2）。
- 两包是**精确相等**的 peer 关系，任何一侧单独升都 ERESOLVE：
  `peer vitest@"5.0.1" from @vitest/coverage-v8@5.0.1`（#31 方向）与
  `peer vitest@"3.2.7" from @vitest/coverage-v8@3.2.7`（#34 方向）。
- lock 从 413 条掉到 298 条，逐条核过是**合法收敛**而非回退：`vite@8` 的真实依赖是 `rolldown`
  （`esbuild` 只是 `peerDependenciesMeta` 里的 optional peer），vitest 3 是经
  `vite-node → vite@7` 才把 `esbuild`/`rollup` 全家拖进来；vitest 5 去掉 `vite-node` 后它们自然消失。
  所有 `@rolldown/binding-linux-*` 等平台 native binding 均保留，无任何条目版本变小。

**vis-network 协同批 —— #42 → `02c11010`**（取代 dependabot #37）

- `vis-network ^9.1.13 → ^10.1.2`、`vis-data ^7.1.10 → ^8.0.5`（lock 另含 `vis-util 5.0.7 → 6.0.2`；
  lock 413 → 413 条，added 0 / removed 0 / downgrades none）。
- 单升 vis-network 装不上：`peer vis-data@">=8.0.0" from vis-network@10.1.2` 与在架的 `vis-data@7.1.10` 冲突。
- 验证含真跑图页面的 `Playwright E2E`（96/96 通过）。**口径要说清**：`e2e/knowledge-graph.spec.js`
  把 `/api/**` 全部 `page.route` 打桩，断言到"画布容器可见 + 页面不崩 + 刷新会重新取数"这一层 ——
  它能当场抓住 v10 构造器/options API 破坏，但**不等于**真实数据下的布局/交互回归。

**dependabot 6 条（全部 squash 合并）**

| PR | 落点 | 内容 |
|---|---|---|
| #30 | `237eb068` | `dompurify ^3.4.15 → ^3.4.16`、`prettier ^3.9.8 → ^3.9.9`（npm-minor 组） |
| #32 | `70c3507d` | `actions/cache` 4 → 6（actions-batch 组） |
| #33 | `b718f0ba` | `gitleaks/gitleaks-action` 2.2.1 → 3.0.0 |
| #35 | `c27f347e` | `docker/build-push-action` 6 → 7 |
| #36 | `ad706415` | `docker/login-action` 3 → 4 |
| #38 | `0aca6f13` | `docker/setup-buildx-action` 3 → 4 |

这 6 条能安全批量落，靠的是 #28 给 dependabot 加的分组策略（`actions/*`、docker 系列按 minor/patch
成批）与两条人工规则：**peer 耦合必须同批**、**major 必须复核门禁覆盖面**。

**仍未纳管（本次不动，等定标）**：`dashboard-enterprise` 的 `npm run test:coverage` 阈值。
master 实测（vitest 5 之后）：statements 61.09% / branches 46.2% / functions 56.11% / lines 64.03%
（`56 files / 478 tests` 全通过），而 `vitest.config.js` 的阈值是 60/50/60/60 →
**functions 与 branches 不达标、命令 exit=1**。
配置注释显示这组阈值是 2026-09-17 从 40/40/30/40 抬到 60/60/50/60 的；抬的时候没有实测，
而 CI 的 `Frontend Build` 只跑 `npm ci` / `lint` / `typecheck` / `npm test` / `vite build`
**从不跑 `test:coverage`**（ci.yml 里所有 coverage 字样都是 Python 侧 ratchet），
所以这道门禁目前处于"配了但没人执行"的状态。

另外留一条**未解释的观察**，因为它直接影响"要不要拿它当门禁"：同一个隔离副本里连跑三次，
后两次数值一致（functions 56.11 / 56.14 有小数级抖动），但**第一次**跑出来的总量明显更低
（lines 49.53% / functions 39.02% / branches 33.25%）。我只跑了 40 秒就得到两种结果，没找到成因；
配置里有 `dangerouslyIgnoreUnhandledErrors: true`，它可能把中途死掉的文件吞成"通过"从而改变覆盖面 —— 这是猜测，不是结论。
**含义**：若要把覆盖率纳进 CI，第一步是先证明这个数可复现，否则门禁会变成新的随机红源。
处置二选一（不顺手改）：纳进 CI 并重新定标 + 加 ratchet（对齐后端做法），或明确降级为"仅本地工具"并在配置里写清楚。

#### dependabot：`docker/*` 也成批，并让"该不该成批"变成机器判的

#28 只给 `actions/*` 建了批量分组，`docker/*` 漏了。后果在 2026-09-27 的周一扫描里实测到：
`docker/build-push-action` 6→7（#35）、`docker/login-action` 3→4（#36）、
`docker/setup-buildx-action` 3→4（#38）**各开一个 PR** —— 三轮完整 9 平台矩阵（每轮 ~20 分钟），
而且三个 PR 都改 `ci.yml` 的相邻行、彼此冲突要反复 rebase。

- `.github/dependabot.yml`：新增 `groups.docker-batch`（`patterns: ["docker/*"]`，
  `update-types` 含 major/minor/patch）。同前缀、同厂商、只动 `uses:` 版本行 —— 批量风险低、CI 覆盖充分。
- **有意不扩大**到 `gitleaks/*`、`pypa/*`、`codecov/*`：它们在本仓各只有 1 个包（用 `uses:` 计数核实），
  组进去省不下一个 PR，反而会把"安全扫描器 major"和"发布工具 major"捆在一起、红了难归因 ——
  按 #28 写在文件顶部的人工规则，这类 major 就该单独复核门禁覆盖面。

机械强制（不靠人记）：`tests/test_dependabot_config.py` 新增
`test_owners_with_multiple_action_packages_are_batched` —— 直接从 workflow 里扫 `uses:`，
**凡同一前缀下有 ≥2 个不同包的，必须被某个 dependabot 分组 pattern 覆盖**。
这条判据正是本次缺陷的通用形式：下次再加一家 action（比如 `aws/*`）而忘了分组，CI 当场红。

- 变异验证：删掉 `docker-batch` → 该用例失败并精确点名
  `{'docker': {'login-action', 'setup-buildx-action', 'build-push-action'}}`；改回 → 6 passed。
- 该测试对 `actions/*` 亦成立（6 个包已在 `actions-batch` 内），不会误报单包前缀。

#### 前端覆盖率门禁：接进 CI、去掉会把半程运行吞成绿灯的开关

上一节记下的"这个数好像不可复现"**是误判，这里更正**。低总数那一轮不是抖动，而是
**冷启动时 forks worker 起不来**：日志里 31 条
`[vitest-pool]: Failed to start forks worker for test files …` + `Timeout waiting for worker to respond`，
实跑只有 **25/56 个测试文件、132/478 个测试**，vitest 自己都在提示
`This might cause false positive tests`。预热之后同一命令稳定（56/56、478/478，
statements 61.09 / branches 46.2 / functions 56.1 / lines 64.03）。

真正的危险因此比"数字会抖"严重一档：`dangerouslyIgnoreUnhandledErrors: true` 把这类
worker 风暴**吞成继续跑**，而 CI 的 frontend job 跑的是不带覆盖率的 `npm test` ——
那种"一半测试没跑"的情况在今天的 CI 里会直接报绿。

- `dashboard-enterprise/vitest.config.js`：删除 `dangerouslyIgnoreUnhandledErrors: true`。
  它当初是为 chart.js 在 jsdom 下调 `getContext` 的 unhandled rejection 加的；
  实测关掉该开关跑覆盖率 → **未处理错误 0 条**、56 files / 478 tests 全过（那条路径早已被测试
  stub 掉），也就是说这个全局吞异常的开关已经没有存在的理由，只有副作用。
- `.github/workflows/ci.yml`：`Frontend Build` 新增显式步骤 `Frontend coverage gate`
  （`npm run test:coverage`）+ 失败时上传 coverage 产物。这一步同时是"测试真的都跑了"的
  机械哨兵 —— 文件漏跑一定会把覆盖率打到下限以下，比再加一条断言更根本。
- 阈值改为**实测值留 0.5pp 抖动余量**的可执行下限：
  statements 60.5 / branches 45.5 / functions 55.5 / lines 63.5。
  数值比 2026-09-17 拍的 60/60/50/60 低（那两个够不到的维度），但门禁从"**CI 从不执行**"
  变成"每个前端 PR 都拦"，净强度是上升的；注释里写明**只许往上抬**，目标仍是对齐后端的 80%。
- 守卫：新增 `py/tests/test_frontend_coverage_gate.py`（4 条）—— CI 必须还在跑该命令、
  阈值不许低于记录的 FLOORS、`dangerouslyIgnoreUnhandledErrors: true` 不许回来
  （按配置键匹配，不按裸串，否则会误伤解释性注释 —— 写这条时先踩了自己一次）、
  `package.json` 里脚本必须还在。
- 变异验证：① `functions` 降到 50 → 阈值守卫红；② 重新加回开关 → 开关守卫红；
  ③ 从 ci.yml 删掉该步骤 → CI 步骤守卫红；恢复后 4 passed。
- 行为验证：在隔离副本（codeload tarball + `npm ci`）用改后的配置跑 `npm run test:coverage`
  → **exit=0**、56 files / 478 tests、无未处理错误、无阈值 ERROR。

#### 从弃用分支捞回 K8s Operator 一致性测试，并改掉"整模块 slow"这种永不执行的结构

先给那条悬了很久的分支定性（对 merge-base `a7253d1a` / 分支 / master 三方比 blob，不看
`compare` 的 ahead_by —— squash 合并下它永远非零、说明不了内容缺失）：
`fix/k8s-operator-test-setup` 是 **2026-08-25 一轮被放弃的替代实现**，它把 supervisor 拆成
`supervisor_state.py`，而 master 走的是 `supervisor_{action,dispatch,patrol,status}` 那套，
两套路数互斥，**不回并**。它的 11 个 commit 已用 tag `archive/k8s-operator-test-setup-20260825`
→ `95fd9b38` 永久保住，分支本身因此随时可删可恢复。

但它里面**有良**：`py/tests/test_k8s_operator.py` 36 个用例，其中 **35 个是 master 没有的**，
而且一大半是**不需要任何集群**的 Chart / CRD 静态一致性校验 ——

- sample CR：`spec.model` 必填与类型、`replicas` 类型与上下界、`model` 类型与 schema 一致；
- CRD：`scope=Namespaced`、`group/Plural/Names`、served version、`subresources.status`、
  `additionalPrinterColumns`、`status.phase` 枚举取值；
- Chart 布局：`Chart.yaml` / `values.yaml` / `templates/` / `crds/` / README 是否齐备。

在当前 master 资产上试跑：**28 passed / 8 skipped / 0 failed**（8 个 skip 全是缺 kind / k3s / 集群）。

关键改造是**标记策略**：原文件顶部一行 `pytestmark = pytest.mark.slow` 把整模块排出了默认运行集，
而 CI 与 nightly 都用 `-m "not slow and …"` 选例 —— 照搬进来就是"写了一整套却永远不会被执行"的
测试，与本仓反复清理的假门禁同一形状。所以按依赖分层：

- 静态层 / helm CLI 层 / kubectl dry-run 层 → **进 CI**，缺 `helm` / `kubectl` 时各自
  `skipif` 优雅跳过，不给 runner 引入新依赖；
- 只有会**真建集群**的 kind / k3s / 已有集群层保留 `@pytest.mark.slow`
  （GitHub runner 上有 Docker，不能让它在每次 push 时自动起集群）。

master 原有的 6 个基础结构用例**逐行保留**在文件末尾；合并后模块共 42 个用例，
CI 选择器 `-m "not slow"` 下为 27 passed / 3 skipped（原有 3 个占位）/ 12 deselected。

- 判定不捞的部分：分支对 `docs/adr/011-state-unification.md` 的差异只是把日期挪成一个
  `## Date` 段（master 版把日期写在 Status 行内），纯格式，不值得动。
- ⚠️ 过程记录（我自己两次踩坑，都已当场纠正）：① 第一次拼装时把 `CHART_DIR` 定义挪到了文件末尾，
  而 class 体在导入期就引用它 → collection 直接 `NameError`；常量必须留在模块顶部。
  ② 我一度用 `git checkout -- <file>` 取基线，而本地 `master` HEAD 是陈旧的（`git fetch` 仍被坏 ref 挡住），
  随后改为**从远端 master blob 取基线**并逐行核对"master 的 6 个用例是否原样保留"。

#### 删掉一个"每次都失败、却从不报错"的 codecov 上传步骤

`ci.yml` 的 test job 里原有 `- name: Upload coverage`（`codecov/codecov-action@v4`，
`fail_ci_if_error: false`）。它不是"版本旧"的问题，而是**从来没成功过一次**：本仓既没有
`CODECOV_TOKEN` secret，也没有 `codecov.yml`，每次 ubuntu/3.13 腿的真实日志是

```
error -- Commit creating failed:  {"message":"Token required - not valid tokenless upload"}
error -- Report creating failed:  {"message":"Token required - not valid tokenless upload"}
error -- Upload queued for processing failed: {"message":"Token required - not valid tokenless upload"}
```

而 `fail_ci_if_error: false` 让这些错误**一律不影响 job 结论**。净效果：每轮从
`cli.codecov.io` 下载并校验一个第三方二进制、产出为零，还留着一个看似存在的"覆盖率也上传了"的印象。
真正的覆盖率约束一直是 `scripts/check_coverage_ratchet.py`（含企业包那条件门禁）与 `coverage.xml` 产物。

- `.github/workflows/ci.yml`：删除该步骤，并在原位留一段说明**恢复条件** —— 建 `CODECOV_TOKEN`
  secret 且改回 `fail_ci_if_error: true`；否则加回来就是再造一个静默失败的假门禁。
- `.github/dependabot.yml`：把 `codecov` 从"有意不成批"的名单里去掉（本仓已不再引用它）。
- 因此关闭 dependabot 的 #47（`codecov/codecov-action` 4→7）：升一个不产出任何东西的集成没有意义。
- 防复发（`py/tests/test_ci_workflow_hygiene.py`，3 条）：
  ① 任何步骤不许设 `fail_ci_if_error: false`；② `continue-on-error: true` 的步骤必须逐条登记理由
  （现有一条 `Generate bandit report (JSON)` 是**正确的**用法：报告生产者不拦 job，
  真正的门禁是后面 bandit High=0 那条硬门）；③ 豁免清单里的僵尸条目也要报错。
- ⚠️ 守卫第一版踩坑并改正：最初用正则在原文里搜 `fail_ci_if_error: false`，结果被**我自己写的
  解释性注释**判红，同时误伤那处有正当理由的 `continue-on-error`。改成 `yaml.safe_load` 后
  只看**步骤字段** —— 注释不算数，配置才算数。
- 变异验证：加回一个 `fail_ci_if_error: false` 的步骤 → 守卫①红；新增未登记的
  `continue-on-error` 步骤 → 守卫②红；把已登记步骤改名 → 守卫②③同时红；恢复后 3 passed。

#### 容器构建对 PyPI 镜像加 3 次退避重试（抗瞬时空索引）

master `cd4099e5` 上 `Container Scan (trivy)` 红过一次，起因不是漏洞也不是代码：镜像构建里
`pip install -r requirements.lock -i https://pypi.tuna.tsinghua.edu.cn/simple` 得到
`Could not find a version that satisfies the requirement pyyaml<7.0.0,>=6.0.2 (from versions: none)`。
`from versions: none` 表示索引**返回了空候选集**（不是版本区间无解 —— 那样会列出可用版本），
属于镜像侧瞬时故障：同一条 commit 的 `Docker build` 与 `Compose Smoke` 都是绿的，重跑该作业即恢复。

- `py/Dockerfile`：把那层 `RUN pip install …` 包进最多 3 次、20s/40s 退避的重试环。
- 有意**不**加第二个 index 主机：多信一个源等于扩大供应链面，而用清华源本身是 P1-5 记录的
  网络可达性决定。3 次仍失败就照旧让构建红 —— 这是给瞬时抖动让路，不是把失败藏起来
  （本仓刚在 #50 里删掉一个"吞失败"的步骤，不会反过来再种一个）。
- 验证：`sh -n` 语法通过；用假 `pip` 驱动整段命令做功能验证 —— 第 3 次成功 → `exit 0`；
  三次全失败 → `exit 1` 并打印 `pip install 连续 3 次失败，放弃`。真实镜像构建由 CI 的
  `Docker build` / `Container Scan` 作业覆盖（`py/Dockerfile` 在 `on.pull_request.paths` 白名单内，
  所以本改动一定会触发它们）。

### 批次 2026-09-26（前端类型门禁）
#### 前端补 `vue-tsc` 类型门禁

`dashboard-enterprise` 此前**没有任何类型检查**：`build` 只是 `vite build`，CI 的 frontend job
跑 `npm ci` / `npm run lint`（eslint）/ `npm test`（vitest）/ `npx vite build`，全仓只有 1 个 `.ts`
文件（`src/env.d.ts`）——所以 `typescript` 那个 devDependency 升 5.9→7.0（dependabot #9）在 CI 上
零验证，发布 checklist 里却写着"lint + type check"。

- 新增 `typecheck` 脚本 = `vue-tsc --noEmit`（`vue-tsc ^3.3.11` 入 devDependencies，lockfile 重算）。
- **接到 `pretest` 生命周期**（`pretest` → `typecheck`）：CI 的 frontend job 本来就会跑 `npm test`，
  因此不改任何 workflow 文件就能生效（现实约束：本机 gh token 无 `workflow` scope，GitHub 对
  workflow 文件的写入直接 404）。等能改 workflow 时应换成显式 `- name: Type check (vue-tsc)` 步骤。
- 实测边界：现仓库 **0 errors**；变异测试确认门禁真会失败（把 `const x: number = "s"` 写进 `.ts`
  → `error TS2322` + exit 2，且 `npm test` 直接 exit 2、vitest 不再执行）。覆盖 `.ts/.d.ts` 的类型错误、
  `.vue` 里把 TS 语法写进 JS script 块（TS8010）；**不覆盖**纯 JS 表达式的类型错误
  （`checkJs: false` —— 打开会一次涌出 **2898** 个既有错误，属单独立项的 ratchet，不在本次范围）。

### 批次 2026-09-26

#### 文档与依赖口径更正（配合 MAOS 5.2.2）

- **适配器计数口径更正**（README）：`config/agents.yaml` 的 31 个条目实际为
  23 个开箱可派发的第三方 CLI 适配器 + 5 个需先配置（`enabled: false`）+
  1 个自研 python 适配器（`doc-pipeline`）+ 1 个 MAOP 自引用。原文写"第三方 30 个 /
  25 个开箱可用"并把 "claude 系" 列为适配器之一 —— 经核**不存在 claude 适配器条目**
  （"Claude" 只出现在 copilot/cursor 条目的描述文字里）；`omniroute` 虽
  `enabled: true` 但 `cli: ''`，派发直接返回 `exit_code=-1`，不计入"开箱可用"。
- **企业版安装方式更正**：`pip install maop-enterprise` 不可用（该包与主包均未发布
  到 PyPI，实测两个包名都返回 404），改为从 GitHub Releases 下载 wheel 安装，
  与 MAOS README 口径统一；企业版模块数 25 → 26。
- **MAOS 仓库可见性整改**：核实发现 MAOS 实际是 public（`gh repo view` 返回
  `{"isPrivate":false}`），与 ADR-017"企业代码移至**私有**仓库物理隔离"的前提矛盾 ——
  企业版源码任何人可读。经确认后已于 2026-09-26 转为 **private**。副作用：私有仓库的
  Releases 资产不再匿名可下，客户交付须改为"直接发 wheel"或授权后 `gh release download`。
- **移除 `aiohttp` 运行时依赖**：全仓无任何 `import aiohttp`（唯一命中是
  `plugin_sandbox` 的**禁用名单**正则），却为一个从不加载的栈承担 14 个 CVE 的审计面。
  外部插件若需要应自行声明。（pyproject / requirements.txt / requirements.lock 同步）
- 同类 CRLF 敏感哈希清理与完整性测试夹具修正见 `54bf1ef`；通知接口身份/租户
  fail-closed 隔离见 `eb8d914`。

#### 依赖批次（dependabot）与 MCP WebSocket 存活判定修复

- **合入**：#16 `actions/checkout` 4→7、#1 `setup-python` 5→7、#2 `setup-node` 4→7、
  #17 npm-minor 组、#13 pydantic-settings/uvicorn 等、#14 `hvac>=2.4`、
  #6 `eslint-plugin-vue` 9→10、#11 `vue-router` 4→5、#10 `jsdom` 26→30（待 CI 收尾）。
  逐项以"该 PR 自己的 CI run 全绿 + Playwright E2E 96 passed + vitest 478 passed"为准，
  未绿不合并。
- **拒绝/挂起（有据）**：#12 `vis-data` 7→8 关闭 —— `vis-network@9.1.13` 要求
  `vis-data@^7.1.0`，本地 `npm ci` 与 CI Frontend Build 双向证实冲突，须与 vis-network
  10 同批升级；#7 `eslint` 9→10 在 #6 落地前不可安装（`eslint-plugin-vue@9` 的 peer
  上界是 eslint 9，ERESOLVE）；#15 放宽 `websockets<15` 上限留待决策（见下）。
- **修复 `_WebSocketTransport.is_alive`**（`mcp_hub_transport.py`）：原实现
  `self._ws is not None and self._ws.open` 在**当前依赖范围内即失效** ——
  `pyproject` 允许到 14.x，而实测 websockets 14.2 下 `websockets.connect` 返回
  `websockets.asyncio.client.ClientConnection`，该类**没有** `.open` 属性
  （`AttributeError`），只有 `.state`；`start()` 传的 `additional_headers` 也正是新实现的
  签名。原测试只在 `_ws is None` 的短路分支覆盖，真实连接从未测到，故长期未暴露。
  现改为优先读 `.state`（新旧实现都有），拿不到才回退 `.open`；新增 4 条用例，
  回滚验证（把实现改回旧写法）确认新用例以 `AttributeError: open` 失败。
- ⚠️ **`websockets<15` 这条上限保护不了它声称的东西**（当时结论，已按实测在下节落地）：  上限内的 14.2 已经是新实现，真正的破坏（`.open` 消失）在 14 就已发生。

#### SQLite「损坏即删除重建」范围收紧 —— 主干 CI 崩溃根因（实测）

`sqlite_connect()` 此前 `except sqlite3.DatabaseError` 一律删库重建，而
`sqlite3.OperationalError` 是 `DatabaseError` 的**子类** —— 于是
`database is locked` / `unable to open database file` / `disk I/O error` /
`attempt to write a readonly database` 都会被判成"损坏"，把**别的连接正在写的库**直接
unlink（该共享 DB 一旦成为落点就会涨到很大：本机 `py/data/maop.db` 已 372MB，
其中 819,972 行 episodic_memory 经查为本地 soak 跑写入的 canned 任务）。
链路上还有第二个放大器：`_open_and_init()` 在
PRAGMA 抛错时不关闭已经建立的连接（句柄一直捏着文件），Windows 上使重建路径的
`unlink` 直接失败（`WinError 32 另一个程序正在使用此文件`），损坏恢复在 Windows 上其实
从未真正走通过。

崩溃链条（CI 上抓到的现场）：`record_feedback()` 触发的 fire-and-forget 守护线程
（`episodic_store.py`）活过测试的 `MAOP_DATA_DIR` 还原与 `tmp_path` 删除 →
`get_db_path()` 届时解析到共享的 `data/maop.db` → 多个 xdist worker 并发抢锁 →
`database is locked` → 触发上面的删除 → 另一个 worker 对已被 unlink 的页写入
→ macOS `Fatal Python error: Bus error` → `[gw1] node down: Not properly terminated`
→ pytest 仍报全绿但该 worker 的 `.coverage` 丢失 → `Coverage ratchet gate` 读到
66.09% vs 基线 81.00% 假红。**三种"flaky"症状同源**，此前被当成三个独立问题。

- 仅当 sqlite 明确报"文件不是/已损坏的数据库镜像"时才允许删除重建
  （`_recoverable_corruption` 白名单）；其余错误原样抛出。
- `_open_and_init()` 失败路径补 `conn.close()`。
- 进化线程登记 + `wait_for_evolution_threads()`，在测试隔离环境还原前收口。
- 新增 5 条用例：分类器双向断言、锁冲突下**文件必须存活且数据完整**、真损坏仍重建、
  线程登记与 join。锁冲突那条此前无法通过（它正是删除分支被误触发的路径）。

#### 并发用例的无界 `t.join()` 收口（32 处 / 19 文件）

unit job 的配置是 `--timeout=60 --reruns=3`，而并发用例普遍写成 `for t in threads: t.join()`
—— 真死锁或调度退化时表现为"永久挂起 + 一段线程栈 dump"，既看不出谁卡住，也白烧 4 次 rerun
（`pytest (windows-latest, 3.13)` 在 `test_result_cache` 上就是这样，见 #21）。

- 新增 `tests/thread_join_guard.py::join_all(threads, timeout_s=120)`：有界等待，超时直接
  报出仍未结束的线程名。
- 19 个测试文件里 32 处裸 join 全部改走 `join_all(...)`；对受影响用例补
  `@pytest.mark.timeout(240)`（31 处，1 处已有 mark），使 **120s 的断言先于 240s 预算触发**，
  不再被 60s 全局超时截成一段栈。
- **没有削减任何并发量**：线程数、轮数、断言强度保持原样 —— 放宽的是"等多久算失败"，
  不是"测多少"。

验证：19 个被改文件单独跑共 512 passed；`tests/e2e/test_boundary_conditions.py` 按 CI 口径
（`MAOP_AUTH=1 -n 0 --confcutdir=tests/e2e`）29 passed / 1 xpassed；全量 unit 套件
`-n 4 --timeout=60 --reruns=2` 见下条提交说明；ruff 干净。

## [5.2.0] — 2026-09-08

### Added
- **自演化闭环 MVP（F2-01 / M2.1）**：`MAOP_EVOLUTION_LOOP_ENABLED` 开关接入主循环，完成 observe→suggest→evaluate→apply→validate→consolidate 完整闭环，默认关闭（AC-01/AC-02）。
- **人工 gate（AC-04）**：`LoopReport` 新增 `pending_approval` / `approval_state` / `approved_by` / `approved_at` 字段；`_phase_evaluate` 返回 `pending_approval` 列表；`_phase_apply` 仅执行 `approved` 列表，跳过待审批项。
- **自动回滚 SLA（AC-05）**：`EvolutionLoop._build_degradation_test_suggestion()` 构造必然导致 VALIDATE 失败的测试建议；`run_cycle(auto_rollback=True)` 5 分钟内触发 `rollback_cycle()` 验证。
- **显著性检验对照（AC-06）**：`_z_test_p_value` 对照 scipy.stats.norm.sf，边界条件（p_pool=0/1、p1=0 vs p2=1）覆盖。
- **Dashboard /evolve 扩展（AC-07）**：新增 7 个 API 端点（`/api/evolution/loop/status`、`/api/evolution/loop/trigger`、`/api/evolution/approvals`、`/api/evolution/approvals/{id}/decision`、`/api/evolution/ab/{cycle_id}`、`/api/evolution/loop/rollback`、`/api/evolution/loop/status`）。
- **conftest evolution_loop_factory（AC-08）**：`evolution_loop_factory` fixture + autouse `_reset_evolution_singletons`，避免 ADR-019 模块级单例路径固化同类 bug。

### Changed
- **EvolutionLoop.run_cycle()**：改为 `async`，`_phase_evolve` 中正确 `await run_cycle()`（修复 coroutine 未 await 导致 `model_dump` 失败）。
- **_phase_evolve** 接线点：新增 `MAOP_EVOLUTION_LOOP_ENABLED` 环境变量开关，默认 `false` → 兼容 AC-01 零回归 / AC-02 开启完整闭环。

### Fixed
- **AC-04 人工 gate**：`LoopReport.pending_approval` / `approval_state` / `approved_by` / `approved_at` 字段（默认 `n/a`）。
- **AC-05 自动回滚 SLA**：`_build_degradation_test_suggestion()` + `rollback_cycle()` 路径验证（5min SLA）。
- **AC-06 显著性检验对照**：`_z_test_p_value` 对照 scipy.stats.norm.sf，边界条件（p1=0 vs p2=1 必 p<1e-10）。
- **AC-07 Dashboard**：`/evolve` 页面新增状态机、待审批列表、A/B 结果卡、回滚按钮。

### Soak Test
- **第一轮**：47.48h / 5504 样本 / RSS slope -0.028 MB/h / Handles +0.021/h / Pool 0 / 错误率 0% → PASS
- **第二轮**：31.23h / 3620 样本（用户主动停止） / 内存 14.4MB (min 11.7 / max 49.4) / Handles 153 (152-160) / Pool 0 / CPU 平均 63.6% → 趋势一致，**综合判定通过**。

### Security
- **MAOS audit.py** metadata JSON 字符串解析修复（`_coerce_event_dict` 新增 `metadata` 分支）：SQLite `metadata` 列存储为 `'{}'` 字符串，Pydantic `AuditEvent(metadata: dict)` 校验失败 → query_events 全路径抛 ValidationError：Input should be a valid dictionary [type=dict_type, input_value='{}', input_type=str]。

### Added
- **ADR-020**：演化闭环安全边界 Stub（Phase 2 评审待填充 5 个待决问题）。

---

## [未归属记录] — 2026-08~09（待核实归属）

> **归属待核实（2026-10-09 记录）**：本段无日期，内容为「阶段二启动定稿」（PRD/HLD v1.1.0 评审于 2026-09-03、ROADMAP 新增 v5.2.0 节），**疑属 v5.2.0**，但从未并入其版本段。本次归位**未动它**——凭日期猜归属会制造新的错账，留待核实其提交区间后再定。

### Docs
- **三阶段路线图阶段二启动定稿**：PRD/HLD 升 v1.1.0（Approved，评审 Levango7 2026-09-03）——Gantt 以 2026-09-05 为阶段二起点重排（消除 v1.0.0 中 Month 1 = 2026-09 与 F2-01 MVP 自 2027-01 起的口径矛盾）、v6.0.0 锚点由"阶段一末"移至阶段二 M2.3、HLD 3.1/3.2/3.3 补代码现状对齐注记；`ROADMAP.md` 新增 v5.2.0 节（自演化闭环 MVP，2026-09-05 启动，`MAOP_EVOLUTION_LOOP_ENABLED` 默认关闭）+ 阶段二后续里程碑锚点（v5.3.0 / v6.0.0 / v7.0.0）。

### Fixed
- **吞异常规范化（P1）**：全库 35+ 处 `logger.debug("Silent exception in ...")` 升级为 `logger.warning` 并带 `[module]` 上下文前缀；路由决策指标、认证 Token 撤销、Bloom filter mmh3 fallback 等关键路径从 debug 提升至 warning，production 可观测。控制流不变（仍为 best-effort）。
- **PG 迁移 CREATE EXTENSION 失败分级**：`vector` 必选扩展失败时 fail-fast 报明确错误；`pg_trgm` 可选扩展失败仍 best-effort 不中断。
- **HookManager 单例重置（test flaky fix）**：在 `conftest.py` autouse fixture 中加入 `reset_hook_manager()`，消除测试间 MAOP_DATA_DIR 变化导致的全局单例路径 dangling（既有 flaky，非回归）。
- **hot_reload 监视范围扩展**：从仅监视 3 个配置文件扩展至 5 个（新增 `mcp_servers.yaml` / `tool_whitelist.yaml`），防止 MCP 配置漂移未检测。
- **RateLimitMiddleware 死代码清除**：删除 `_lock_time: dict[str, float] = {}`（未在运行时读取）。

### Fixed（第六轮+第七轮 — 2026-09-14）

> 第六轮审查发现 28 项问题（2P0+8P1+16P2+2P3），第七轮验证发现 2 处遗漏，全部修复。测试 7742 通过，零回归。

#### P0 严重
- **登录限流多实例失效**：`auth.py` 将进程内 dict（`_login_failures`/`_login_failures_by_ip`）迁移到 SQLite 表 `login_failures`（复合主键 `(key, kind)`），多实例部署共享计数；时间戳从 `time.monotonic()` 改为 `time.time()`（跨进程可比）；保留 LRU 淘汰防表无限增长。
- **自演化闭环未完成却宣传**：`ROADMAP.md` v5.2.0 节标注"开发中（planned）"并附警告，不再标注"阶段二启动"。

#### P1 高
- **StdioTransport 并发竞态**：`mcp_hub_transport.py` `send_request` 中 `self._request_id += 1` 后用局部变量 `request_id` 快照，后续匹配全用局部变量，避免协程间竞态。
- **TenantManager 无锁保护**：`tenant.py` 加 `threading.RLock()`，9 个公共方法全部 `with self._lock:` 包裹。
- **asyncio.shield 超时后任务泄漏**：`subagent_lifecycle.py` `except TimeoutError` 分支增加 `atask.cancel()` + `await atask` 等待终止。
- **SQLite 数据库损坏崩溃**：`db_utils.py` 抽取 `_open_and_init()`，捕获 `sqlite3.DatabaseError` 后删除 db+侧车文件重建空库，仅重试一次。
- **Agent 可用数与宣传不符**：`README.md` 修正为"25 个开箱可用 + 5 个需额外配置"。
- **OnboardingWizard 死链**：删除废弃组件 `OnboardingWizard.vue`。
- **404 静默重定向无反馈**：新建 `NotFound.vue`（404 提示+3秒倒计时），`router/index.js` catch-all 从 `redirect` 改为 `component`。
- **JWT TTL 硬编码**：`auth.py` 提取 `_JWT_TTL_S = float(os.getenv("MAOP_JWT_TTL_S", "7200"))`，替换全部 7 处硬编码。

#### P2 中
- **choices 空列表 IndexError**：`(response.get("choices") or [{}])[0]` 模式修复 4 处（`maop_execute.py`/`react_loop.py`/`llm_providers.py`×2）。
- **context_compressor content 类型混淆**：新增 `_normalize_content` 静态方法，处理 None/str/list/其他类型，修复 8 处。
- **memory/search content 为 None**：`(m["content"] or "")` 保护修复 5 处（含第七轮验证发现的 2 处遗漏）。
- **desktop_app_adapter decode 缺 errors**：`.decode("utf-8", errors="replace")`。
- **LDAP 控件值索引越界**：`len()` 检查后再取索引。
- **web_adapter response_path 遍历错误**：try-except 包裹 `current[int(key)]`。
- **health_check_scheduler 线程泄漏**：`_leaked_workers` 列表跟踪 + `_max_leaked_threads` 阈值限制。
- **deploy.py Popen 管道未读取**：后台 daemon 线程排空管道。
- **跨线程 coroutine 循环绑定**：`evolution_phases.py`/`mcp_bridge_adapter.py` 捕获 `RuntimeError`。
- **SELECT * 改明确列名**：`agent_versions.py` 7 处、`feedback.py` 5 处，使用模块级列名常量。
- **prometheus-alerts.yml 注释过时**：更新为实际 metrics 来源。

### Changed
- **PyPI 包名变更**：`maop` → `maop-orchestrator`（PyPI 上 `maop` 被他人占用）。`import maop` 不变，仅 `pip install maop-orchestrator`。`pyproject.toml` 新增 `[tool.hatch.build.targets.wheel] packages = ["maop"]` 确保 import 名不变。14 个文档文件同步更新。
- **48h 长稳测试脚本**：新建 `scripts/run_soak_48h.ps1`（一键启动/停止/查看）+ `deliverables/soak-test-report-48h.md`（监控指南 + 结果模板）。
- **PyPI 上传指南**：新建 `deliverables/pypi-upload-guide.md`（供未来有账号+梯子时使用）。
- **GitHub Release**：已创建 v5.1.0-personal Release，上传 wheel + sdist。

### Added
- **自演化完善**：修复 `suggester.py` LLM 路径（`_SUGGESTION_PROMPT` 字面量花括号 bug）+ 新增 `narrative.py` 叙事模块 + API 端点 + 前端 `EvolutionHistory.vue` 3-tab + `EvolutionTimeline.vue` 增强 + `useMarkdown.js` / `useTextDiff.js` composable + `docs/evolution-guide.md` 使用指南
- **新手引导（P0）**：`docs/quickstart.md` 5 分钟上手文档 + `docs/README.md` 文档元索引（7 章 6 分类）+ `OnboardingWizard.vue` 3 步引导组件 + `view-onboard.js` i18n，集成到 `Overview.vue`
- **MAOP/MAOS 品牌定位统一（P1+P2）**：明确 MAOP = 个人版（免费开源）/ MAOS = 企业版（需 License），全站品牌引用统一（`view-overview.js`、`edition.py`、`docs/`、`docker-compose.yml`）+ Overview.vue 升级提示 banner + i18n 翻译补全

### Changed
- **Phase 1-3 模块拆分**：10 个大文件（`server.py`、`compliance.py`、`data_proxy.py`、`vector.py`、`llm_provider.py`、`dispatcher.py`、`maop_loop.py`、`plugin.py`、`engine.py`、`ldap_provider.py`）拆分为 re-export shim + 实现模块，保持 `from maop.xxx import Y` 零改动

### Fixed
- **全量测试 0 failed**：修复 `dispatcher.py` `otel_span` re-export 缺失 + `test_routing_trace.py` fixture（patch `dispatch_core.otel_span`）+ 22 个 enterprise 测试加 `pytest.importorskip("maop.enterprise")` 守卫 → ruff 0 + mypy 0 + pytest **7035 passed, 57 skipped, 0 failed**
- **ADR-017 enterprise branch test**：加 `importorskip` 守卫适配 Community 版无 `maop.enterprise` 模块

### Fixed（2026-08-29 审查修复批次）
- **P0-1 48h 长稳宣称修正**：`deliverables/release-notes-v5.1.0-personal.md` 与 `soak-test-report-48h.md` 明确标注"48h 完整测试尚未执行；当前仅有 1h 验证 PASS"，第 6 章结果区标为待填写（`soak-48h-logs/` 为空，`soak-test-data.csv` 为 1h 数据）
- **P0-2 本地 .coverage 重建**：原文件含垃圾路径条目导致 `coverage report` 报错；删除重建并导出核心模块覆盖率证据至 `deliverables/coverage-core-evidence.json`
- **P0-3 契约测试去机器路径**：`test_enterprise_contract.py` 移除硬编码 `F:\Nexus\MAOS` 回退，统一走 `MAOS_REPO_PATH`/同目录探测，缺失即 skip（个人版行为不变）
- **P1-4 并行子任务 stdout 截断修复**：`loop_executor.py` 聚合 stdout 不再硬截断 200 字符（原实现导致 verify 阶段拿到碎片），改为完整传递 + 可配置防膨胀上限（`LoopConfig.parallel_subtask_stdout_cap`，默认 200k，0=不限）
- **P1-5 PLAN 递归分解深度上限**：`engine.py` `_execute_step` 的 PLAN 递归分解加 `_MAX_PLAN_DEPTH=5`，超过即按原子任务执行，杜绝嵌套分隔符导致的无限递归
- **P1-6 JWT 撤销黑名单原子写**：`core/security/auth.py` `_save_revoked` 改 tmp+`os.replace`，崩溃/并发不再产生损坏 JSON（损坏会使全部已撤销 token 复活）；顺带删除 `create_key` 中 4 行重复代码
- **P1-7 登录限流淘汰策略**：`dashboard/routers/auth.py` 用户名/IP 双维度淘汰由"仅淘汰未锁定条目"改为整体 LRU（原策略可被预填 1 万个 4 次失败账户绕过，使受害者锁定计数失效）
- **P1-8 CLI 自委托深度兜底**：`cli.py` 新增 `--depth` 参数（`agents.yaml` MAOP agent 模板 `--depth {depth}` 此前未被 argparse 识别，自委托子进程必然报错）；`cmd_run` 在 depth≥3 时硬拒绝退出，与 SubagentManager 层防护形成纵深
- **P2 worker 事件循环复用**：`worker/agent_executor.py` 由每任务 `asyncio.run()` 改为单循环持续消费（`to_thread` 包裹同步 dequeue/ack/nack）
- **P2 tenant 配额事务**：`core/security/tenant.py` `check_quota` 的读-检-写包进 `BEGIN IMMEDIATE` 单事务，消除并发超卖（TOCTOU）
- **P2 前端 token 出 localStorage（M7）**：`useWebSocket.js`/`useStreamingFetch.js`/`useAgentTokenStream.js` 移除 `maop_token` localStorage 读取；后端 `/ws` 握手新增 httpOnly cookie 回退（`_register_routes.py`），SSE URL 不再携带 token（顺带消除 token 进 URL/访问日志的泄露面，与 WS P1-10 修复方向对齐）
- **代码清理**：`engine.py` 函数内 `import re as _re` 提升至模块顶层

### Fixed（2026-09-14 后端第五轮质量审查修复，83 项）

> 第五轮验证审查发现 83 个问题（5 P0 + 27 P1 + 29 P2 + 22 P3），全部已修复。
> 测试结果：后端 9807 通过，前端 477/478 通过（预先存在失败），MAOS 2560 通过。

#### P0 严重（5 项）
- `drivers.py`：streamer 超时后 kill 子进程，防止孤儿进程泄漏
- `drivers.py`：`_run_python` 使用占位符参数传递，防止 `shlex.split` 参数注入
- `pg_persist.py`：`delete_grant`/`delete_tenant`/`delete_rule` 使用 `RETURNING` 检查实际删除行数

#### P1 高（27 项）
- 路由层：8 端点添加 `@handle_api_errors` 装饰器，统一错误响应格式
- 核心层：`cost_tracker` 单例双检锁、`guardrail` 共享状态锁
- 编排层：SSE 异常脱敏（不泄露内部错误详情）、guardrail 多轮 re-dispatch 检查
- 数据层：N+1 查询批量优化、regex 全表扫描 `LIMIT`、`_task_states` 加锁、`FileLock` 原子写入、迁移回滚机制、fire-and-forget task GC
- MAOS：`ALTER TABLE` 异常精确化、summary SQL 聚合、N+1 查询改批量、`SELECT *` 改明确列名

#### P2 中（29 项）
- 路由层：Pydantic model 替代 `request.json()`、操作失败 raise `HTTPException`
- 核心层：`validate_token` 异常脱敏、`jwt_secret` 弱密钥校验、`credential_vault` 生产降级
- 编排层：pause 超时限制、PLAN 子步骤依赖检查、成本护栏 `logger.warning`
- 数据层：keyset pagination、`fetchmany` 流式读取、原子写入、内存累加器初始值
- MAOS：`builtins.list`、`type:ignore` 显式处理、`acknowledge_alert` rowcount 检查

#### P3 低（22 项）
- 代码风格：重复 import 清理、常量提升、docstring 精简、布尔优先级括号、死代码删除

## [5.1.0-personal] — 2026-08-27

> 个人版交付门禁（Phase 1）完成。基于 v5.1.0 + 140 项安全审核修复，补齐 3 项 P0 门禁后交付。

### Added
- **P1-1 成本兜底护栏**：新增 `PersonalCostGuard`（`py/maop/core/personal_cost_guard.py`），实现软/硬两档熔断。配置项 `MAOP_PERSONAL_COST_CAP`（全局累计花费阈值 USD）+ `MAOP_PERSONAL_COST_HARD`（硬熔断开关）。软熔断：达到阈值 → 告警 + 拒绝新 LLM 调用，运行中任务允许跑完。硬熔断：达到阈值 → 中断运行中任务。集成到 `maop_execute.py`（fail-open）。3 条单测覆盖三条路径。
- **P1-2 edition 切换提示**：前端 `Settings.vue` 切换 enterprise 失败时显式提示"需 MAOS 商业包 + 有效 License"。后端 `admin.py` 无 license 切换 enterprise 返回 403 + 明确错误消息（原先静默降级为 200+degraded）。9 条单测覆盖切换门禁。
- **P1-3 分布式边界声明**：`cli.py` 的 `maop worker start` 在个人版下提示"分布式 worker 是企业版特性"并退出 1。`README.md` / `docs/user-guide.md` 添加版本说明 + 分布式执行边界声明。
- **P1-4 长稳测试脚本**：新建 `py/tests/soak/soak_test.py`，48h 多指标压测（内存 RSS / 文件句柄数 / 连接池占用 / CPU 使用率）。15 分钟冒烟测试 PASS（29 样本，9397 次记忆操作，164 次 DAG 执行，所有指标平稳）。

### Changed
- `settings.py` 新增 `personal_cost_cap` + `personal_cost_hard` 配置项
- `maop_execute.py` 集成 `PersonalCostGuard.check_new_call()` 前置检查
- `admin.py` edition 切换逻辑：无 license 切换 enterprise 从 200+degraded 改为 403+error
- `test_distributed_execution.py` worker CLI 测试适配个人版门禁 + 新增拒绝测试
- `test_edition_switch.py` e2e 测试期望 403（适配 admin.py 变更）

### Fixed
- 修复 `soak_test.py` ruff 7 个代码风格问题（F401/UP037/I001/LOG014/TRY401/F841）

## [5.1.0] — 2026-08-14

### Added

#### 企业版功能（v5.0.2+）
- **许可证管理**：License 管理 UI + CRUD API + 过期预警 + 特性开关绑定
- **SSO/SAML 集成**：SAML 2.0 IdP 对接 + SP 配置 + 属性映射
- **审计日志**：全操作审计 + 审计日志查询/导出 + 不可篡改性
- **配额管理**：租户级配额（API 调用/Token/存储）+ 超额拒绝 + 用量看板
- **API Key 管理**：API Key 生成/轮转/吊销 + scope 权限绑定
- **通知中心**：邮件/Webhook 通知 + 通知模板 + 事件订阅

#### v5.1.0 新功能
- **LLM 任务拆分**：自动将复杂任务拆分为子任务 + DAG 依赖编排
- **工作流编辑器**：可视化 DAG 工作流编辑 + 节点配置 + 保存/加载
- **配置历史**：配置变更快照 + 一键回滚 + 差异对比
- **Skill 编辑器 + 市场**：Skill 在线编辑 + 模板市场 + 导入/导出
- **异常调度**：异常检测 + 自动重试策略 + 降级调度
- **Hook 配置**：Webhook Hook 配置 UI + 事件触发 + 执行日志

### Changed
- 版本号统一升级至 v5.1.0（pyproject.toml / __init__.py / Dockerfile / package.json / package-lock.json / Chart.yaml / values.yaml / controller.yaml）
- 移除 pyproject.toml addopts 的 --cov-fail-under=50，改由 ratchet 脚本渐进门禁
- 修复 /users 路由守卫缺失（补 meta.requiresEnterprise）
- 修复 Audit.test.js chart.js/jsdom unhandled rejection
- **统一错误响应格式对齐 `ErrorSchema`**：所有经 `handle_api_errors` 装饰器（含 `HTTPException`）的端点错误响应采用扁平结构 `{status, error, code, detail, request_id}`（全部 string 类型，`status` 默认 `"error"`，其余默认空串），取代历史嵌套 `{"error":{code,message}}` 描述。权威定义见 `py/maop/dashboard/error_handler.py` `ErrorSchema`。
- **Breaking（P2-1）：Engine 无 `step_executor` 时不再返回假成功**。AGENT/DAG/PLAN 步骤在未注入执行器时一律返回 `StepStatus.FAILED` + `error="No step executor configured..."`（此前 PLAN 回落路径与 AGENT/DAG 在无执行器时错误地返回 `SUCCESS` 占位文本，构成监控假阳性）。构造 `Engine()` 未传入 `step_executor` 会在日志打印 warning。下游测试改为注入 mock executor（如 `test_integration.py` 的 `_success_executor`、`test_distributed_execution.py` 的 `_mock_step_executor`）。

## [5.0.2] — 2026-08-13

### Added

- 前端设计规范文档 `docs/frontend-style-guide.md`（10 章节，沉淀迭代 A-C 组件契约）
- RFC-001 迭代 C 补全：DetailDrawer 迁移（Agents.vue memoryPanel + evolutionPanel）
- Users.vue 升级到 ListPageLayout（三态交给组件）

### Changed

- 布局重构：flex 并排 → 层叠覆盖（顶栏全宽 fixed + 侧栏全高 fixed 覆盖顶-左侧）
- 折叠时侧栏变窄，顶栏左侧品牌区自然露出，顶栏零抖动
- z-index 层级：顶栏 10 < 侧栏 20 < 移动端 drawer 30 < 遮罩 80 < modal 90 < toast 100

### Fixed

- ESLint warnings 51→0（48 no-unused-vars + 3 vue/no-template-shadow）

## [5.0.1] — 2026-08-13

### Fixed

- **M5 回归修复**: `agent/llm_chat/llm_provider.py` 的 `_record_cost` 改为 async，与 `core/llm_provider.py` 保持一致
- **enterprise_api_guard 中间件顺序**: 未认证请求现在返回 401 而非 404（guard 先放行让 AuthMiddleware 处理认证）
- **ADR010 回归测试路径修正**: `parents[3]` → `parents[2]`，`py/MAOP/MAOP_loop.py` → `py/maop/maop_loop.py`
- **enterprise guard 测试路径修正**: `/api/v1/tenant/list` → `/api/v1/tenant/create`（命中 404 分支）
- **rules.yaml 断言改进**: 检查非注释行中的 `per_agent`，避免误匹配注释

### Changed

- **前端硬编码颜色 → CSS var token**: KnowledgeGraph/McpTopology/DagGraph/Agents/Users/Observability/pages.css 中的硬编码 hex 替换为 CSS 变量
- **前端布局间距统一**: App.vue nav-section/nav-link 使用 `--sp` token 替代硬编码 px
- **重复 CSS 规则清理**: pages.css 中 `.deg-reason` 和 `.status-dot` 重复定义去重
- **ESLint warnings 自动修复**: 310 → 51（259 个属性排序等自动修复）

### Removed

- 6 个临时日志文件（docs/_review_*.log）


## [5.0.0] — 2026-08-11

### Breaking Changes

本版本为 major release，含不兼容变更。详见 [MIGRATION-5.0.md](docs/migration-5.0.md)。

#### Removed（废弃 ≥ 2 版本的 API）

- **`maop.dashboard.provider.create_app()`**：自 v4.0.0 起废弃，现移除。生产代码应使用 `maop.dashboard.server:app`。该函数创建的隔离 FastAPI app 与主 server 路由冲突，是 v3.x 遗留。
- **`maop.dashboard.provider._render_html()`**：自 v4.0.0 起废弃，现移除。v3.x 静态 HTML 渲染器，已被 Vue 3 SPA 取代。
- **`maop_plan.py` legacy keyword routing**：`_fallback_keyword_route()` / `_route_by_keyword()` 及 `_ROUTING_RULES`，自 v4.0.0 起标记为 DEPRECATED fallback，现移除。路由统一走 config routing，miss 时回退到 `"chat"/"claude"` 默认值。
- **`/api/batch` 端点**：`dashboard/routers/data.py` 中 `deprecated=True` 的批量端点，前端不再调用，现移除。

#### Deferred（推迟到 v6.0.0）

以下 API 虽已废弃，但因引用链复杂（多模块 re-export + 测试 mock.patch 路径绑定），在 v5.0.0 保留并加强 deprecation warning，将在 v6.0.0 移除：
- `maop.core.agent.delegation.subagent_delegation` re-export shim → `subagent_lifecycle`
- `maop.core.project_context` / `maop.core.agent.memory_ctx.project_context`

#### Changed（配置收敛）

- **短名环境变量加 DeprecationWarning**：以下短名仍可使用但会发出告警，推荐迁移到规范长名（将在 v6.0.0 移除短名）：
  - `MAOP_PORT` → `MAOP_DASH_PORT`（dashboard 监听端口）
  - `MAOP_WORKERS` → `MAOP_DASH_WORKERS`（uvicorn worker 数）
  - `MAOP_TLS` → `MAOP_TLS_ENABLED`（TLS 开关）
  - `MAOP_AUTH` → `MAOP_AUTH_ENABLED`（认证开关）
- **`MAOP_PORT` 不再被直接读取**：`.env.example` 中 `MAOP_PORT` 标记为 deprecated alias，实际端口由 `MAOP_DASH_PORT` 控制。

### Added

- **流式 Agent token 响应增强**：
  - 后端新增 `/api/stream/agent/{execution_id}` SSE 端点，支持 Agent 执行过程的 token-by-token 流式推送（区别于 `/api/chat/stream` 的 chat 流式，本端点针对 agent 任务执行）。
  - 前端新增 `useAgentTokenStream.js` composable，封装 EventSource 连接、token 累积、自动重连、AbortController 清理。
  - `Chat.vue` 集成增强：流式渲染时显示 token 计数与流速指示，`onMeta` 回调接收 `tokens` / `model` / `latency_ms` 元数据。
- **迁移指南 `docs/migration-5.0.md`**：覆盖后端 API 变更、配置环境变量迁移、Docker 部署变更、前端无变更说明。
- **ROADMAP.md 状态更新**：v4.5.0 标记为已发布，v5.0.0 标记为当前版本。
- **Phase 5b — 发布/性能/合规修复（G-08~G-17）**：
  - **G-12 SLA/支持体系**：新增 `docs/sla.md`（服务等级协议，含可用性 SLO、延迟 SLO、违约补偿）与 `docs/support-policy.md`（三级支持体系 L1/L2/L3、工单优先级、版本支持策略）。
  - **G-13 隐私政策/DPA**：新增 `docs/privacy-policy.md`（PIPL/GDPR/CCPA 合规）、`docs/terms-of-service.md`（双版许可条款）、`docs/dpa.md`（数据处理协议）、`docs/cla.md`（贡献者许可协议）。
  - **G-14 PG 高可用**：新增 `deploy/patroni/`（Patroni 3 节点集群配置 + HAProxy 读写分离 + WAL-G 备份回调）；`docker-compose.prod.yml` 增加 `patroni1/2/3` + `pg-haproxy` 服务（`--profile patroni`）；新增 `docs/runbook.md`（故障切换运维手册）。
  - **G-16 CI Playwright E2E**：`.github/workflows/ci.yml` 增加 `e2e` job（Playwright + chromium，浏览器缓存，HTML 报告上传）；`dashboard-enterprise/package.json` 增加 `test:e2e` 脚本。
  - **G-17 K8s Operator 集成测试**：`py/tests/test_k8s_operator.py` 扩展支持 kind/k3s 集成测试（`TestKindIntegration` 启动临时 kind 集群、`TestK3sIntegration` 对接 k3s、`TestStaticCRValidation` 纯 Python CRD schema 验证、`TestKubectlDryRun` 客户端 dry-run）。
  - **G-09 性能压测**：新增 `py/tests/performance/` 目录，含 `k6_maop_load.js`（k6 JavaScript 压测脚本，自定义指标 snake_case，SLO 门禁对齐 SLA）、`locust_maop_load.py`（Locust Python 压测脚本）、`test_performance_smoke.py`（压测脚本语法/SLO 对齐冒烟测试）；新增 `docs/capacity-planning.md`（容量规划与性能基准）。
  - **G-10 LDAP 真实环境验证**：新增 `py/tests/test_ldap_real_env.py`（真实 OpenLDAP 联调测试 + Docker OpenLDAP 容器化测试 + mock 单元测试）；新增 `docs/ldap-integration-guide.md`（LDAP/AD 集成指南）。

### Fixed

- 版本号统一：`py/maop/__init__.py`、`py/pyproject.toml`、`py/Dockerfile`、`dashboard-enterprise/package.json`、`dashboard-enterprise/package-lock.json`、`deploy/k8s/operator/Chart.yaml`、`deploy/k8s/operator/values.yaml`、`deploy/k8s/operator/controller.yaml` 全部从 4.5.0 统一到 5.0.0。
- `.env.example` 与代码实际环境变量对齐审计：补充 `MAOP_DASH_PORT` 规范名说明，标记 `MAOP_PORT` 为 deprecated alias。

## [4.5.0] — 2026-08-06

### Added

- **core/ 子包重构（Phase D-1）**：将 `core/`（116 模块）拆分为 9 个职责清晰的子包（`persistence`、`llm`、`vector`、`mcp`、`observability`、`security`、`config`、`runtime`、`utils`），保留 `core/__init__.py` re-export shim，现有 `from maop.core.xxx import yyy` 调用零改动通过。
- **流式 DAG 执行进度推送（Phase D-2）**：新增 `DagProgressEmitter`，在 DAG 节点状态变更时通过 EventBus 推送增量事件；新增 SSE 端点 `/api/stream/dag/{execution_id}`（支持 Last-Event-ID 断线重连）；前端 `useDagProgress.js` composable 实时渲染节点状态（pending/running/success/failed/skipped）。
- **知识图谱可视化前端（Phase D-3）**：新增 `/api/knowledge-graph` 端点聚合三层记忆（short/long/vector）实体-关系数据；新增 `KnowledgeGraphView.vue` 基于 vis-network 渲染交互式知识图谱，支持节点筛选与路径高亮。
- 新增测试套件：`test_dag_progress.py`、`test_knowledge_graph_v2.py`、`test_dag_sse_endpoint.py`、`e2e/knowledge-graph.spec.js`。

### Fixed

- **`_execute_with_retry` 源码 bug**：`py/maop/loop_executor.py` 中 `_execute_with_retry` 方法被错误定义在 `_NoopEmitter` 类中，导致 `ExecuteMixin`（`MaopLoop` 继承）调用时 `AttributeError`。已将方法移至 `ExecuteMixin` 类，修复 17 个测试失败。
- **`test_dag_sse_endpoint.py` 测试间污染**：`_disable_auth` patch `maop.core.middleware.require_admin` 模块属性，但 `stream.py` 通过 `from ... import require_admin` 在模块加载时已绑定函数引用，全量测试中 stream 模块被先前测试加载后 patch 失效。改为直接 patch `stream` 模块的 `require_admin` 引用，修复 7 个测试失败。

### Changed

- 整体测试覆盖率从 85% 提升至 87%（34234 stmts, 4556 missing, 6130 passed, 0 failed）。
- 前端 vitest 160 passed（21 files）。

## [4.4.2] — 2026-08-06

### Added

- 新增 `ROADMAP.md`：MAOP 版本规划单一真相源，覆盖 v4.4.2 / v4.5.0 / v5.0.0 方向与验收标准。
- 新增 `deliverables/engineering-assurance/v4.4.1-fix-report.md`：归档 v4.4.1 修复清单（102 项，后端 24 + 交互 6 + 前端 72 + 测试 32 用例）。
- 新增 `deliverables/engineering-assurance/env-audit-4.4.2.md`：`.env.example` 与代码实际环境变量对齐审计报告。
- 新增 `deliverables/engineering-assurance/_extract_env.py`：env 审计可复现脚本。
- 前端 e2e 测试实跑通过（`enterprise-route-guard.spec.js` 12 用例，Playwright + chromium 安装并执行）。
- 后端覆盖率测试套件扩充（新增约 800 个测试用例，覆盖路由端点、core 模块、memory 模块等），整体覆盖率从 75% 提升至 85%。
- 前端单元测试 `vitest` 138 passed（20 files）。

### Changed

- 同步 `docs/adr/016-dual-edition-architecture.md` 待完善表：SAML SSO 状态由 `Medium / fail-closed 拒绝` 更新为 `Done`（已实现 SP-initiated SSO + XML 签名验证，见 `docs/enterprise/saml-sso-guide.md`）。
- 整体测试覆盖率从 75% 提升至 85%（33187 stmts, 5099 missing, 5744 passed, 0 failed）。
- `py/maop/core/vector.py` `EmbeddingProvider` → ABC + `@abstractmethod`（文档化抽象接口）。
- `py/maop/core/runtime.py` `BaseRuntime` → ABC + `@abstractmethod`（文档化抽象接口）。
- `py/maop/dashboard/routers/agents.py` `get_agent` 统一返回 `JSONResponse`（mypy return-value 修复）。

### Fixed

- `.env.example` 与代码实际环境变量对齐：补齐 5 个代码直接读取但未文档化的 `MAOP_*` 变量，差异归零。
  - `MAOP_KEY` / `MAOP_KEY_FILE`（API key vault 主密钥，`core/api_key_vault.py`）
  - `MAOP_TLS_ALLOW_DEPRECATED`（允许废弃 TLS 版本开关，`core/tls.py`，默认 fail-closed）
  - `MAOP_DB_URL`（Alembic 迁移 SQLAlchemy URL 覆盖，`migrations/alembic/env.py`）
  - `MAOP_PLUGIN_STRICT_CHECKSUM`（插件校验和严格模式，`core/plugin.py`，默认 fail-closed）
- 经审计核实 `.env.example` 中 16 个未被 `os.getenv` 直接读取的 `MAOP_*` 变量均为 pydantic `MAOPSettings` 间接读取或向后兼容别名，非僵尸变量，保留。
- `dashboard-enterprise/playwright.config.js` baseURL/port 不一致修复（baseURL 9079→5174, webServer.port 5173→5174，与 `vite.config.js` 实际端口对齐）。
- `dashboard-enterprise/package.json` 补 `@playwright/test` devDependency。
- `dashboard-enterprise/src/stores/edition.js` 冷加载缺陷修复：新增 `loadInitialEdition()` 从 localStorage 快照读初始值，修复路由守卫死代码（首次 page.goto 守卫总看到硬编码 'enterprise' 放行）。
- `config/models.yaml` 恢复为纯 YAML 格式（被 `yaml.dump` 覆盖产生 Python 对象标签，导致 `yaml.safe_load` 加载失败）。
- `py/tests/test_circuit_breaker.py` fixture 隔离：monkeypatch `_load_agent_names_from_config` 返回空列表，强制 fallback 到 DEFAULT_AGENTS，不依赖全局 agents.yaml 配置。

## [4.4.1] — 2026-07-31

### Fixed

- **后端 P0-P3 修复（24 项）**
  - 恢复 `data/migrations/` SQL 文件；Docker 环境变量名统一（`MAOP_*_BACKEND`）
  - 依赖 CVE 升级（cryptography<49, python-dotenv>=1.2.2, fastapi>=0.115）
  - 移除私钥 `enterprise/keys/private_key.pem`；audit 路由冲突修复
  - 10 个文件 36 处硬编码 .db 路径统一到 `get_db_path()`
  - Ruff 6 个 unused import + Mypy 15 个类型错误修复
  - 生产 compose 补充 `cap_drop: ALL` + `no-new-privileges`
  - CI 覆盖率阈值 60→80；`GRAFANA_PASSWORD` 弱默认值修复

- **前后端交互修复（6 项）**
  - Audit summary 嵌套结构对齐（`d.summary.total` / `d.summary.total_events`）
  - Audit events 字段映射（time/timestamp, level/severity, target/resource）
  - Settings config 补充 6 个缺失字段（dash_workers/root_dir/data_dir/db_path/memory_db_path/rate_limit_burst）
  - Tenants list 补充 usage 数据；Chat mapMsg 从 metadata 提取 image/model/tokens
  - SSE done 事件补充 tokens 和 model 字段

- **前端深度修复（72 项）**
  - P0: useToast 解构错误修复（6 个视图 toast 功能恢复）
  - P0: Models.vue reactive→ref 误用修复（下拉框恢复可用）
  - P0: i18n `t()` 支持插值参数 `{var}` 替换
  - P0: Card overflow 裁剪、z-index 断裂、未定义 token（--sp-10/--r/--info/--z-*）
  - P0: section/feature-grid/edition-* CSS 类冲突修复
  - P1: PageHeader 顶层 await import 改同步 import
  - P1: Chat button 嵌套修复（HTML 规范合规）
  - P1: 浅色主题 15+ 处硬编码颜色替换为语义 token
  - P1: CSP 添加到 index.html；对比度修复（--text-faint/--text3 加深）
  - P1: img alt 补充；图片上传 5MB 大小限制
  - P2: computed sort 缓存修改修复；WebSocket 重连计数重置
  - P2: Cost.vue NaN 防御；Monitor.vue diagnostics 空值防御
  - P2: useToast timer 清理；Chat AbortController + onUnmounted
  - P3: 10 处 `== null` → `=== null`；console.log 清理

### Added

- 5 个新测试文件（i18n/useToast/Audit/Chat/Models），32 个用例
- 覆盖率 ≥ 80%

## [4.4.0] — 2026-07-31

### Added

- **OmniRoute 升级为默认 LLM 出口（Phase 2）** — 显式 `default_provider` 字段方案：
  - `models.yaml` 新增顶层 `default_provider` 和 `default_model` 字段。
  - `ModelSelector` 新增 Step 1.5：当 `default_provider` 设置时，优先选择该 provider 的模型作为 primary。
  - OmniRoute 作为 fallback agent 加入 9 个 routing key（codegen, chat, refactor, review, planning, quickfix, docgen, techdoc, verify）。
  - `ModelRegistry.get_default_model()` 和 `get_default_provider()` 方法。
  - `LLMProviderFactory._get_default_model()` 辅助方法，`chat_with_fallback()` 在无 model 时使用默认 model。
  - `ModelRegistryConfig` 新增 `default_provider` / `default_model` Pydantic 字段。
- Enterprise license validation (Ed25519 signature + 7-day grace period)
- **前端统一 Vue3** — 原生 JS Dashboard 归档至 `archive/js-dashboard/`，个人版和企业版共享同一 Vue3 SPA（17 页面）
- **统一审计路由** — `audit.py` 同时支持企业版（EnterpriseAuditLogger）和个人版（AuditLog），消除路由冲突
- **`__main__.py`** — 支持 `python -m maop` 入口
- **n8n 纳入企业版 API 守卫** — `/api/n8n` 加入 `_ENTERPRISE_API_PREFIXES`

### Changed

- Expanded `omniroute-auto-coding` capabilities: +chat, quickfix, docgen, techdoc, verify
- Expanded `omniroute-auto-reasoning` capabilities: +chat, verify
- **Docker 环境变量名修正** — `MAOP_BACKEND_*` → `MAOP_*_BACKEND`（与代码一致）
- **依赖升级** — `cryptography<49`（CVE 修复）、`python-dotenv>=1.2.2`、`fastapi>=0.115`
- **生产 Docker compose 安全加固** — 所有服务补充 `cap_drop: ALL` + `no-new-privileges`
- **36 处硬编码 .db 路径统一到 `get_db_path()`** — 10 个模块、36 处替换
- **迁移 SQL 文件恢复** — `data/migrations/` 目录重建

### Security

- **移除 `enterprise/keys/private_key.pem`** — 私钥不再随包分发
- **audit 路由冲突修复** — `system.py` 的 audit 端点移至 `audit.py`，企业版审计功能不再被覆盖

## [4.3.0] — 2026-07-25

### Added

- **安全审计修复（7 Critical + 2 High）** — 全面消除 RCE、认证绕过与事件循环阻塞风险：
  - Redis backend 序列化由 `pickle` 改为 JSON，消除反序列化 RCE 风险。
  - 认证环境变量名统一：`MAOP_AUTH_ENABLED` → `MAOP_AUTH`。
  - 路径大小写 bug 修复（`py/MAOP` → `py/maop`），恢复 Linux 兼容性。
  - async 端点 `subprocess.run` → `asyncio.create_subprocess_exec`，避免阻塞事件循环。
  - Swagger / OpenAPI 文档在生产环境禁用。
  - `DEFAULT_AGENTS` 改为从 `config/agents.yaml` 动态加载。
  - CI 覆盖率门槛由 40% 提升至 60%。
- **PreemptableWorkerPool 完善** — 实现 `wait()` 方法、跟踪 watcher 任务、异常不再被静默吞掉。
- **hot_reload 哈希算法升级** — MD5 → SHA-256，与项目完整性标准一致。
- **`.env.example` 补充** — 8 类环境变量文档化。
- **生产 compose 凭证策略** — 弱凭证默认值改为强制要求覆盖。

### Fixed

- `docker-compose.prod.yml` 中 5 处弱凭证（PostgreSQL / Vault / Redis / Grafana）。
- MCP Marketplace SSRF 漏洞（URL scheme 白名单）。
- MCP stdio 命令注入（命令白名单）。
- MCP dashboard 权限绕过（`user_context` 传递）。
- 认证默认配置（`MAOP_AUTH_DISABLED_ADMIN=0`）。
- Prometheus 配置挂载与服务名解析。
- dashboard / worker 结构化 JSON 日志支持。
- HAManager dual mode（Redis + Memory）协同。

---

## [4.2.0] — 2026-07-25

### Added

- **Phase δ：MCP 生态扩展** — MCP Marketplace（注册表 / 搜索 / 安装 / 校验）、MCP 权限审计日志、结果缓存与并发控制、命令白名单与严格模式。
- **Phase γ：智能调度系统** — SLA 感知优先级队列、多目标优化（Pareto 前沿 + TOPSIS）、软抢占 `PreemptableWorkerPool`、路由决策记录与查询。
- **Phase β：自我演化闭环** — 执行历史分析器、Prompt A/B 测试框架、Agent 策略学习器、缓存策略进化器。
- **Phase α：可观测性深化** — OpenTelemetry 集成、Grafana SLO 仪表盘、结构化 JSON 日志、SLO 告警规则。

### Fixed

- P0 timeseries bug 修复。
- CircuitBreaker 测试隔离。
- flaky 并发测试稳定化。
- Dashboard MCP router 注入 δ 组件。

---

## [4.1.0] — 2026-07-25

### Added

- **Phase 3：PostgreSQL + Vault + HA 架构升级**
  - PostgreSQL 生产化集成（95%）—— 连接池、混合存储、迁移管理。
  - Vault 密钥管理集成（85%）—— 动态密钥、lease 续期、密钥轮换。
  - 分布式 HA 高可用（90%）—— Redis 租约选举、fencing token、自动故障转移。
  - Redis Backend（Cache / Queue / Lock）三合一。
  - ADR-015：分布式 HA 设计文档；ADR-014：HA / backend 文档对齐实际实现。
- **企业版模块测试（87 个新测试）** — 覆盖审计、RBAC、租户、HA、容器、TLS 自动配置、PG 持久化层。
- **多因素路由评分算法** — dashboard 实时数据 + Copilot agent。
- **Stage 2 工程化** — API 版本化、类型注解、缓存、Alembic 迁移。
- **Stage 1 文档** — 用例、e2e 测试、契约测试、算法注释。
- **采纳审计报告建议** — 迁移、工具版本化、错误 schema、备份 WAL。

### Changed

- `docker-compose` 使用根目录作为单一来源；`py/docker-compose.yml` 已删除。
- 前端统一为 Vue3 SPA（`dist-enterprise`）。

### Fixed

- **CI 修复** — Docker build 在 `main` 和 `master` 分支均触发。
- **R3 / R4 项目审计修复** — 合计 8 P0 + 18 P1 + 15 P2 阻塞与关键问题（含 `deploy.py` SQLite 连接泄漏、`auth_login` 返回类型一致性、`data/data/` 嵌套、ADR-006 标题等）。
- **aiohttp session 泄漏 + codex driver `IndexError`**。
- **change_tracker `rglob` 性能 bug + `bloom_filter` 导入 + 部署脚本**。
- **依赖管理 + `ERR_ABORTED` 根因 + 8 处测试失败**。
- **10 处遗留测试失败 + CI e2e 支持**。
- **`DeprecationWarning` 静默 + `maop_loop` 测试提速**。

---

## [4.0.0] — 2026-07-19

### New Features

- **Plugin System** (`core/plugin.py`) — Full plugin lifecycle manager with discovery, loading, starting, stopping, and hot-reload. Plugins declare metadata in `maop-plugin.yaml` manifests, expose `maop_plugin_init`/`maop_plugin_shutdown` entry points, and can register hooks via HookManager bridge. SQLite persistence for plugin state. Dashboard API at `/api/plugins/*`.

- **Real-time Cost Tracker** (`core/cost_tracker.py`) — Per-call token usage recording with automatic cost calculation for 7+ models (GPT-4o, Claude 3.5, DeepSeek, etc.). Aggregated summaries by model/agent/session. Daily/monthly budget monitoring with HookManager alert integration. Customizable pricing. Dashboard API at `/api/cost/*`.

- **ReAct Loop Engine** (`core/react_loop.py`) — Thought→Action→Observation micro-cycle for autonomous agent reasoning. Integrates FunctionCallBridge and ChangeTracker. Configurable max iterations and tool selection.

- **Change Tracker** (`core/change_tracker.py`) — File system snapshot/diff/rollback with unauthorized change detection. Change log persistence. Dashboard API at `/api/react/snapshots/*` and `/api/react/diff`.

- **Artifact Store** (`core/artifact_store.py`) — Versioned artifact storage with save/load/history/restore/tag/diff. Blob file persistence. Dashboard API at `/api/react/artifacts/*`.

- **Session Manager** (`core/session.py`) — Full session CRUD with status management, token budget tracking, and SQLite persistence.

- **Conversation Manager** (`core/conversation.py`) — Multi-turn message history with context window sliding, auto-compression, and message search.

- **Project Context** (`core/project_context.py`) — Project structure tree, tech stack detection, config file reading, instruction file injection, git status.

- **MCP Client Runtime** (`core/mcp_client.py` + `mcp_transport.py` + `mcp_registry.py`) — Full MCP protocol client with Stdio/SSE transport, tool discovery, and execution.

- **Function Calling Bridge** (`core/function_call.py`) — Unified OpenAI/Anthropic/Ollama function calling → MAOP ToolCall → MCP/ToolManager execution → result re-injection loop.

- **Structured Output Parser** (`core/output_parser.py`) — JSON extraction from code blocks/raw/embedded, Pydantic validation, schema verification gate.

- **Streaming Integration** (`core/streaming.py`) — SubprocessStreamer + StreamRegistry, driver streamer support, SSE endpoint.

- **Permission Manager** (`core/permission.py`) — Allow/ask/deny policy, HumanProxy integration into maop_execute + maop_loop.

- **Hook Decision Influence** — HookResult now supports decision (allow/deny/modify) + modified_data, enabling hooks to veto or transform pipeline data.

### Changed

- `maop_execute.py` — Added tools/provider/max_tool_rounds + react_mode/react_max_iterations parameters, function_call re-injection loop, ReAct mode branch.
- `maop_verify.py` — Added `_gate_schema` verification gate for structured output validation.
- `error_schema.py` — MaopResult now has `structured_output` field.
- `dashboard/server.py` — Registered 7 new routers (stream, permission, mcp, session, react, plugin, cost).
- Version bumped from 3.5.0 → 4.0.0.

### Tests

- 43 tests in `test_plugin_cost.py` (PluginManager: 19, CostTracker: 24)
- 31 tests in `test_react_loop.py` (ReactLoop + ChangeTracker + ArtifactStore)
- 36 tests in `test_session.py` (Session + Conversation + ProjectContext)
- 25 tests in `test_function_call.py` (FunctionCallBridge + ToolSchemaGenerator)
- 27 tests in `test_output_parser.py` (OutputParser + SchemaGate)
- Total new tests: **162**

---

## [3.5.0] — 2026-07-18

### New Features

- **Mavis subagent merge** — Restructured `agents.yaml`: removed `mavis-coder`, `mavis-general`, `mavis-verifier` as standalone agents; merged into `mavis.subagents` configuration. Subagents are now only reachable via parent delegation (`mavis/verifier`, `mavis/coder`, `mavis/general`).

- **Subagent routing support** — `Dispatcher._resolve_agent()` now supports `parent/child` format (e.g. `mavis/verifier`). When a `/` is detected, the parent's `cli` + child's `cli_args` are combined to build the final `AgentConfig`. Routing table entries `mavis/verifier` for `verify` and `review` routes now resolve correctly.

- **ConfigLoader subagents parsing** — `ConfigLoader.load()` now correctly parses the `subagents` section from `agents.yaml` into `AgentDef.subagents: dict[str, SubagentDef]`. Added `SubagentDef` Pydantic model with `cli_args`, `capabilities`, `description`, `model_display` fields.

### Changed

- **agents.yaml routing** — `review.fallback` changed from `mavis-verifier` to `mavis/verifier`; `verify.primary` changed from `mavis-verifier` to `mavis/verifier`.

### Tests

- Added 9 tests in `test_dispatcher_extended.py`: subagent resolution (parent/child, coder, unknown child/parent, caching, parent still works), dispatch integration, ConfigLoader subagents parsing.

---

## [3.3.0] — 2026-07-18

### New Features

- **Subagent hierarchical delegation** — Added `core/subagent.py` (SubagentManager) with parent/child lifecycle tracking, depth-limited recursive delegation, and inter-agent message passing. Integrated into `Dispatcher.delegate_to_subagent()` for recursive agent→agent→agent dispatch. Dashboard API: `/api/subagent/spawn`, `/api/subagent/terminate`, `/api/subagent/children`, `/api/subagent/tree`, `/api/subagent/send`, `/api/subagent/receive`, `/api/subagent/purge`.

- **Worktree parallel workspaces** — Added `core/worktree.py` (WorktreeManager) with git worktree-based filesystem isolation for parallel task execution. Automatic branch creation, stale worktree cleanup, and fallback directory copy when git is unavailable. Integrated into `WorkerPool._run_task()` for automatic worktree creation/cleanup per task. Dashboard API: `/api/worktree/create`, `/api/worktree/remove`, `/api/worktree/list`, `/api/worktree/cleanup`.

- **Protocol registry system** — Added `core/protocol.py` (ProtocolRegistry) for dynamic agent communication protocol registration with schema validation, versioning, and protocol-validated messaging. Supports runtime protocol write without code changes. Dashboard API: `/api/protocol/register`, `/api/protocol/unregister`, `/api/protocol/validate`, `/api/protocol/send`, `/api/protocol/messages`.

- **LLM Provider enhancements** — Four sub-features:
  - **Ollama local model support** — Added `ProviderType.OLLAMA` and `ProtocolType.OLLAMA_CHAT` to schema, with `thinking_to_api_params()` mapping for Ollama's `options.num_predict`. Added `ollama` provider to `models.yaml` (disabled by default).
  - **Provider/Model dynamic CRUD** — Added `add_provider()`, `remove_provider()`, `add_model()`, `remove_model()`, `save()` to `ModelRegistry`. Dashboard API: `/api/model/provider/add`, `/api/model/provider/delete`, `/api/model/add`, `/api/model/delete`.
  - **API Key encrypted vault** — Added `core/api_key_vault.py` (ApiKeyVault) with Fernet symmetric encryption, MAOP_KEY env / `.enc_key` file key management, and plaintext fallback. Dashboard API: `/api/model/key/store`, `/api/model/key/delete`, `/api/model/key/list`.
  - **Runtime health check** — Added `core/provider_health.py` (ProviderHealthChecker) with actual API call verification via httpx, latency measurement, and model list discovery. Dashboard API: `/api/model/health/check`.

### Tests

- Added `test_subagent.py` (14 tests): spawn, terminate, get, list_children, tree, messaging.
- Added `test_worktree.py` (10 tests, 9 skip if no git): create, remove, get, list, cleanup.
- Added `test_protocol.py` (20 tests): register, unregister, get, list, validate, messaging.
- Added `test_provider_enhanced.py` (24 tests): Ollama types, CRUD, vault, health check.

### Bug Fixes

- **worktree.py FileNotFoundError** — `_git()` now catches `FileNotFoundError` when git is not in PATH instead of crashing.
- **registry.py save() Enum serialization** — Fixed yaml.dump producing Python object tags for Enum values; now serializes `.value` strings.

---

## [3.2.3] — 2026-07-18

### Security (P0)

- **SQL injection in message_queue._count()** — Added `_VALID_TABLES` whitelist to prevent arbitrary table name injection.
- **Path injection in db_backup.py VACUUM INTO** — Added regex validation for backup path components + single-quote rejection.
- **Missing checksum validation in migration.py** — Added SHA256 checksum verification for migration SQL files.
- **GET /api/control/run → POST** — Converted state-changing control endpoint from GET to POST; added independent `/api/control/pause`, `/api/control/resume`, `/api/control/stop` endpoints.
- **Unrestricted agent upgrade in system.py** — Added package name whitelist for agent upgrade operations.
- **SHA-256 legacy auth compatibility** — Removed SHA-256 password hash fallback from `auth.py`; only bcrypt is accepted.
- **Guardrail fail-open** — Changed guardrail evaluation to fail-closed: exceptions during rule evaluation now block execution instead of allowing it.
- **CI Python 3.14** — Pinned CI to Python 3.12/3.13 (3.14 not yet stable).
- **docker-compose.yml build context** — Fixed build context path to point to correct `py/` directory.

### Hardening (P1)

- **Password leak to stderr** — Removed password plaintext logging from `auth.py` stderr output.
- **CORS tightened** — Restricted CORS allowlist from wildcard to explicit origins.
- **Shared db_utils.py** — Extracted common SQLite connection management into `core/db_utils.py` (WAL mode, foreign keys, busy timeout).
- **Shared _find_project_root()** — Unified project root discovery across modules.
- **Extended core module tests** — Added 58 new tests across 4 test files: `test_maop_loop_extended.py` (13), `test_guardrail_extended.py` (20), `test_engine_extended.py` (14), `test_dispatcher_extended.py` (11).
- **ADR-010 regression tests** — Added 9 regression tests in `test_adr010_regression.py`.
- **Dockerfile version sync** — Updated Dockerfile to version 3.2.2.
- **doc-pipeline Python driver** — Added Python CLI driver support for doc-pipeline workflow.

### Optimization & Quality (P2)

- **VectorStore performance** — numpy-accelerated cosine similarity, batch loading with `executemany`, triple cache (`_cache`/`_text_cache`/`_meta_cache`).
- **Dashboard router error handling** — Added `@handle_api_errors` decorator to `evolve.py`, `memory.py`, `model.py` routers.
- **CI consolidation** — Merged two CI workflow files into single `ci.yml`.
- **Coverage gate** — Added `--cov-fail-under=40` to CI pytest command.
- **kv_store connection mode** — Unified to WAL mode with foreign key enforcement.
- **TLS version enforcement** — `settings.py` now rejects TLSv1/TLSv1_1 connections.
- **CJK token estimation** — `context_compressor.py` now uses character-based estimation for CJK text.
- **maop.ps1 PS 5.1 compatibility** — Fixed PowerShell 5.1 compatibility issues in entry script.

### Deep Audit Fixes

- **S-01: db_backup.py VACUUM INTO injection** — Added single-quote rejection for backup_path to prevent SQL escape.
- **S-02: auth.py JWT key persistence** — `JWTHandler.__init__` now calls `load_jwt_secret()` instead of generating ephemeral key, ensuring JWT tokens survive restarts.
- **S-03: auth.py APIKeyStore thread safety** — `_get_conn()` now protected by `self._lock` to prevent race condition.
- **S-06: model.py model/switch validation** — Added `models.yaml` lookup to validate new_model before writing to `agents.yaml`.
- **S-08: middleware.py public paths** — Added `/api/auth/login` and `/api/auth/status` to public paths list.

### Integration Fixes

- **test_router_control.py** — Merged duplicate `TestControlRunPost` classes; aligned tests with POST handler behavior (no-op → default task); migrated pause/resume/stop tests to independent endpoints; converted maintain/provider-health tests from GET to POST.
- **test_dashboard_auth.py** — Updated SHA-256 compatibility tests to verify legacy hashes are rejected.
- **evolve.py @handle_api_errors** — Added missing error handler decorator to `/api/evolve/analyze` endpoint.
- **system.py indentation** — Fixed indentation error introduced during P0-4 refactoring.

### Deep Audit Fixes (Round 2)

- **S-04: dispatcher.py PS cli_args injection** — Added regex whitelist validation for `cli_args` template; rejects unsafe characters before PowerShell execution.
- **S-05: system.py pip whitelist hardening** — Changed `_get_allowed_packages()` to intersect dynamic agent CLI list with a hardcoded safe package set, preventing `agents.yaml` modification from bypassing the whitelist.
- **S-07: auth.py login brute-force protection** — Added per-username login failure tracking; locks account for 15 minutes after 5 consecutive failures.
- **S-09: tls.py placeholder cert rejection** — `create_ssl_context()` now checks for placeholder comment lines in cert files and refuses to load them.
- **S-10: data.py query() internal API** — Renamed `query()` to `_query()` with warning docstring; updated all internal and test callers.
- **S-11: kv_store.py connection leak** — Added `close()` method and `__del__` destructor to KVStore for proper connection cleanup.
- **P-03: DataBridge singleton instances** — Cached `ToolManager`, `SandboxManager`, `HumanProxy` instances in `DataBridge.__init__` instead of creating new ones per request.
- **P-04: DataBridge queue stats** — Merged 3 independent `SELECT COUNT(*)` queries into single `GROUP BY status` query.
- **P-08: system.py overview cache** — Added 60-second TTL cache to `/api/overview` endpoint to avoid re-scanning source files on every request.
- **A-01: Unified _connect()** — Replaced duplicated `_connect()` context managers in `vector.py`, `message_queue.py`, `sandbox.py`, `data.py`, `tool_manager.py`, `human_proxy.py` with `db_utils.sqlite_connect()`.
- **A-02: Deduplicated validate_identifier** — `data.py` now imports `validate_identifier` from `db_utils` instead of re-implementing it.
- **T-01: test_db_utils.py** — 17 new tests covering `validate_identifier` and `sqlite_connect` (WAL, foreign keys, rollback, row factory).
- **T-02: test_sandbox.py** — 10 new tests covering SandboxManager create/get/list/cleanup/run.
- **T-03: test_runtime.py** — 12 new tests covering `_resolve_cmd`, `LocalRuntime`, `IsolatedRuntime`, `RuntimeConfig`.

---

## [3.2.2] — 2026-07-17

### Fixed

- **cache_guard.py SingleFlight deadlock** — `_wait()` was called inside `with self._mutex` but itself tries to acquire `_mutex`, causing a non-reentrant lock deadlock. Fixed by extracting event reference inside lock, calling `_wait()` outside.
- **cache_guard.py SingleFlight result cleanup race** — `finally` block deleted results before waiters could read them. Fixed by lazy cleanup on next call.

### Added

- **934 new tests** (763→1697 total, all passing, zero regression):
  - `test_store.py` (57) — MemoryStore: store/search/facets/JSON search/trace/trajectory/inject/stats/prune
  - `test_vector.py` (46) — VectorStore: cosine similarity, embedding, indexing, search, persistence
  - `test_analyzer.py` (49) — DependencyDAG, topo sort, parallel groups, cycles, rule decomposition, strategy selection
  - `test_context_compressor.py` (40) — All 9 section extractors, compress/to_prompt, trim-to-budget
  - `test_timeseries.py` (38) — Record/batch, raw & aggregated query, downsampling, retention policy
  - `test_kv_store.py` (52) — Get/set, TTL, CAS, namespaces, bulk ops, stats
  - `test_monitoring.py` (40) — Metrics recording, alerting, health checks
  - `test_tool_manager.py` (32) — Tool registration, execution, validation
  - `test_cache_guard.py` (23) — SingleFlight, cache guard, dedup
  - `test_rate_limiter.py` (28) — Token bucket, sliding window, burst control
  - `test_state_classifier.py` (29) — Task state classification, transition rules
  - `test_human_proxy.py` (30) — Human-in-the-loop proxy, queue management
  - `test_auth.py` (35) — Authentication, token management, RBAC
  - `test_registry.py` (53) — ProviderRegistry, ModelRegistry, agent model resolution
  - `test_budget.py` (30) — BudgetGuard: spend tracking, alerts, reconciliation
  - `test_selector.py` (26) — Model selection, fallback chain, routing
  - `test_schema.py` (35) — All Pydantic models, enums, serialization
  - `test_provider.py` (36) — DashboardState, agent status, delegation counting
  - `test_loader.py` (33) — Config loading, YAML parsing, reload detection
  - `test_hot_reload.py` (29) — File hashing, change detection, watch loop
  - `test_evolve.py` (38) — EvolveEngine: analyze/suggest/apply/promote/status
  - `test_prompt_manager.py` (37) — Template CRUD, render, search, export/import
  - `test_concurrency.py` (33) — TaskQueue, TaskPool, SSEStreamer, TokenStreamer
  - `test_consolidator.py` (23) — Dream pipeline, summary building, consolidation
  - `test_cli.py` (20) — Argument parsing, command dispatch, all subcommands

---

## [3.2.1] — 2026-07-16

### Fixed

- **maop_execute trace_id propagation** — `maop_execute()` now sets `result.trace_id` after dispatch, ensuring trace IDs are always present on returned `MaopResult` objects for observability.
- **pyproject.toml dependencies** — Added missing `python-dotenv` (pydantic-settings dep), `mmh3` (MurmurHash3), and `sentence-transformers` (optional ML, under `[ml]`).

### Added

- **100 new tests** (663→763 total, all passing):
  - `test_fallback.py` (16) — FallbackManager: chain building, failure tracking, should_fallback, reset
  - `test_quota.py` (15) — QuotaEnforcer: sliding window, check/consume, usage stats
  - `test_maop_plan.py` (21) — Task routing: keyword matching, gate selection, budget config
  - `test_maop_verify.py` (24) — Verification gates: exit_code, output, content-safety, syntax-check, lint, dry-run
  - `test_maop_execute.py` (14) — Execution: dispatch, guardrail pre/post, trace_id, error handling

---

## [3.2.0] — 2026-07-16

### Summary

MAOP completes its transformation from a PowerShell-based multi-agent prototype to a
production-ready Python orchestration package. The PowerShell engine is archived to
`archive/ps-legacy/` (EOL v4.0). Python is now the sole runtime.

### Added

- **Model Management** — `model/registry.py`, `model/selector.py`, `model/budget.py`,
  `model/quota.py`, `model/fallback.py`, `model/schema.py` (7 modules).
  Policy-driven model selection with budget guards, quota tracking, and fallback chains.
- **Control Plane** — `control/plane.py`, `control/audit.py` (3 modules).
  Process-level job management with audit logging.
- **Contract Testing** — `tests/contract/` (4 test files, 92 tests).
  Behavioral, dispatcher, model API, and control API contracts.
- **Dashboard Routers** — `dashboard/routers/` package with 6 router modules
  (data, control, model, evolve, memory, system). 83 API endpoints.
- **Data Migrations** — `core/migration.py` + `data/migrations/001_init.sql`.
  Version-tracked schema migrations with up/down SQL and SHA256 checksums.
- **Dynamic Router (Python)** — `core/dynamic_router.py`.
  Port of `dynamic-router.ps1` with agent health scoring and 30s cache.
- **Safe Expression Evaluator** — `engine.py` `safe_eval()` using `ast` module.
  Replaces bare `eval()` with whitelist-based AST node evaluation.
- **WebSocket Snapshot Cache** — 5s TTL cache on `_ws_push_loop` to avoid
  redundant DataBridge queries during 15s broadcast intervals.
- **Chart.js Local Vendor** — `dashboard/js/vendor/chart.umd.min.js` (205 KB).
  Eliminates CDN dependency for air-gapped deployments.
- **Requirements Lock** — `py/requirements.lock` with full transitive dependency pinning.
- **Design Rules** — `dashboard/DESIGN_RULES.md` documenting the 3-level border hierarchy
  and divider width rules (outer 3px / middle 2px / inner 1px).
- **CHANGELOG.md** — This file.

### Changed

- **README** — Fully rewritten to reflect Python-first architecture, 6-layer structure,
  port 9079, 7 sub-packages, 711 tests, ~80 API endpoints, 18 agents, 13 gate scripts.
- **Version Unification** — `pyproject.toml`, `__init__.py`, `index.html` all at 3.2.0.
- **Dependency Pinning** — All `pyproject.toml` dependencies changed from `>=` to `==`.
- **agents.yaml** — `doc-pipeline` workflow migrated from `driver: wrapper` (PS) to
  `driver: cli` (Python). Model fields normalized with `model_ref` for precise lookup.
- **Model Registry** — `resolve_agent_model()` accepts `model_ref` parameter for exact
  model reference; heuristic fallback emits `logger.warning`.
- **Dashboard Frontend** — Monolithic `app.js` split into 11 modular files (`js/app-*.js`).
  Version branding updated from `v7` to `v3.2`.
- **maop.ps1** — PS fallback path replaced with archive notice and `exit 1`.
  Python-first routing is the only path.
- **hot_reload.py** — Watch list updated: `agents.yaml` + `rules.yaml` + `models.yaml`
  (removed `routing.yaml`).

### Removed

- **PowerShell Engine** — 61 `.ps1` files moved to `archive/ps-legacy/`.
  `src/` directory cleared of engine scripts.
- **Startup Wrappers** — `start_dashboard.py`, `start.bat`, `start-server.ps1`,
  `run_dashboard.py` moved to `archive/`. Canonical entry: `python -m maop.dashboard.server`.
- **Dead Code** — `_invoke_ps_fallback()` and `fallback_to_ps` parameter removed from
  `data_bridge.py`. `app.js.bak` and `app.js.legacy` deleted.
- **Residual Files** — `circuit-breaker.json` deleted (pure legacy, no Python reads/writes).
  `human-queue.json` reset to empty (stale data from 2026-07-03).
  `py/.tmp/` cache directory removed.
- **eval()** — Bare `eval(condition_expr, {"__builtins__": {}}, context)` replaced by
  `safe_eval(condition_expr, context)` with AST whitelist.

### Security

- **Command Injection** — 4 Critical PS injection vectors closed (delegate-plugin.ps1).
  Python dispatcher uses `shlex.quote()` / `subprocess` list args (no shell=True).
- **Path Traversal** — Gate name validation (`^[a-zA-Z0-9_-]+$`), memory ID validation,
  sandbox workdir confinement.
- **Expression Safety** — `safe_eval` blocks function calls, attribute chains to
  `__class__`/`__subclasses__`, and all builtin access. 12 expression tests + 5 attack
  vectors verified.
- **API Security** — TLS support, API key auth middleware, rate limiting (30 RPS / 60 burst),
  CORS allowlist.

### Tests

- **711 total tests** (up from 547).
- **561 passed** in system Python (without fastapi/pytest-asyncio).
- **Full pass** with dev dependencies installed: `pip install -e ".[dev]"`.

---

## [3.1.0] — 2026-07-14

### Added

- Dashboard v7 UI rewrite with 18 navigation items in 5 groups.
- Six-layer architecture: CLI → MaopLoop → Engine → Services → Infrastructure → Data.
- 42 Python modules (up from 13).
- Docker multi-stage build, docker-compose, graceful shutdown.
- GitHub Actions CI/CD pipeline.
- Pydantic-based configuration with `.env` support.

### Changed

- PowerShell-to-Python migration: 8 phases completed.
- 220 tests all green (0 failures).

---

## [3.0.0] — 2026-07-10

### Added

- Initial Python engine: `maop_loop.py`, `engine.py`, `dispatcher.py`, `guardrail.py`.
- SQLite-backed persistence: `maop.db`, `memory.db`, `queue.db`.
- FastAPI dashboard server (port 9079).
- Circuit breaker, DAG engine, memory store with vector search.

### Changed

- Architecture shift from PowerShell-first to Python-first.
- Config-driven agent registration via `agents.yaml`.

---

## [2.x] — 2026-06-01 to 2026-07-09

PowerShell era. 48 scripts in `src/`, 13 gate scripts in `src/gates/`.
See `archive/ps-legacy/` for historical code. These versions are EOL and unsupported.

---

## [1.x] — 2026-05-01 to 2026-05-31

Initial prototype. Single-file `maop.ps1` with basic Plan-Execute-Verify loop.
No dashboard, no model management, no contract tests.
