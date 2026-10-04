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

## [Unreleased] - 2026-10-01

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

## [Unreleased] - 2026-09-30

### 2026-09-30 T3.0：v5.2.0 演化闭环哑弹排除（trigger / 审批 API）

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

### 2026-09-30 T2.2：requirements.frozen.txt 真锁（审计输入换精确 pin）+ T-UI 顶栏/侧栏布局

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

### Added

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

### Changed

- **泄漏探针补"谁 patch 的"指纹（`run_impl=`）**：`py/tests/conftest.py::_leak_probe` 此前只报 `subprocess_run_patched=True/False`，等于只说"进程被弄脏了"而不说"被谁"。新增 `_run_fingerprint()`，日志多一个字段 `run_impl=<定义模块>:<限定名>`（仅在 `subprocess.run` 被换掉时有值，否则为 `-`）。为什么这样取值：真实嫌疑人形状是测试文件里的局部闭包（`py/tests/test_agent_adapters.py::_mock_subprocess_run` 返回的 `_run`，以及若干 `test_xxx.<locals>._capture_run`），它们的 `__module__/__qualname__` 直接带出**所在文件与所在用例**，于是一次复发就能点名而不是再靠 `output='ok'` 这种间接指纹猜。对 Mock 类对象只报 `类型名@类型的模块`，**刻意不读 `return_value`** —— MagicMock 的属性访问会自动创建子 mock，那等于探针反过来改变自己要观测的对象（观察者效应）；这条约束由 `test_run_fingerprint_does_not_touch_the_mock_it_reports` 钉住（断言零调用、`mock_calls` 为空、且取证前后 `__dict__` 键集合不变）。新增 3 条用例（12 passed），变异验证 5 例各点亮对应断言：指纹退化成常量 / 去掉 `run_impl` 字段 / 取消 `-` 分支 / 去读 `return_value` / 取证时顺手调用 fake。**动机**：`tests/test_tool_manager.py::TestCallSyncFallback` 三条在 macOS 腿随机红（`assert '42' in 'ok'` 与两条 `assert True is False`，返回值都是 `ok=True/exit_code=0/duration_ms=0/output='ok'` 的罐头值），2026-09-29 的 PR #54 与当日主干 run 448 各复发一次、失败用例 ID 逐字相同；`threads=[]` 已排除线程泄漏嫌疑，但"永久 patch"也与"紧邻的前一条用例同断言却通过"矛盾 ⇒ patch 是窗口性的，机制仍未定位 —— 本改动是把"下次复发能点名"这件事先做完。纯观测，不改任何生产代码或用例断言。

---

## [Unreleased] - 2026-09-29

### Fixed

- **dry-run gate 接线修复（no-op → 显式 opt-in enforce）**：`maop_plan` 为 deploy/pipeline/fileops 路由挂 dry-run gate 但从不设置 `plan["dry_run"]`，`_gate_dry_run` 对未声明的 plan 恒 PASS（`maop_verify.py` 的向后兼容合同），闸门从未生效。修复：`Plan` 新增 `dry_run` 字段，`MAOP_DRY_RUN_ENFORCE=1` 时三条路由置 True，未设置保持历史行为。路由口径实测：`config/agents.yaml` 路由表无 "deploy" 键，真实任务只命中 pipeline/fileops（deploy 分支仅能经 `routing_key` 覆盖触达）。新增 `py/tests/test_dry_run_gate_wiring.py`（接线 5 例 + gate 行为 3 例，8 passed）。已知限制：enforce 为 fail-closed，且当前执行器不产出 dry-run 信号（全库无产出方），开启后 pipeline/fileops 任务会卡在 verify，须先接执行器信号产出。`MAOP_DRY_RUN_ENFORCE` 已同步 `.env.example` / `README.md` / `docs/configuration.md`。
- **委派指标双计数修复（success/total 告警分母膨胀）**：`MAOP_DELEGATIONS_TOTAL` 在 `maop_plan.py`（建 plan）与 `dispatch_core.py`（派发）各 +1，而 success/failed 只在派发侧计数 → 纯环路流量下 total:success ≈ 2:1，`monitoring/alerts.yml` 的 success/total < 0.8 与 `slo-alerts.yml` 的 burn rate 会在健康系统上误报，`prometheus-alerts.yml` 的 failed/total 被稀释一半（漏报）；`MAOP_DELEGATION_DURATION` 亦混入 plan 构建耗时（稀释 P95 延迟告警）。修复：移除 plan 侧两处指标写入（计数/耗时唯一在派发侧），`py/maop/monitoring.py` 注释同步更正。回归测试 `py/tests/test_maop_plan.py::TestPlanMetricsSideEffects`（变异验证：重加 `.inc()` 即红）。
- **RouteScorer 单例污染修复（config 路由进程级失效）**：`get_route_scorer()` 的热重载守卫要求 `_instance.config is not None`，单例若被无 config 调用先初始化（生产真实路径：dashboard 冷却查询端点 `routing_service.get_route_cooldowns()` 早于任何路由请求裸调），之后所有真实 config 被永久忽略，config 路由静默退化为默认 chat。修复：`_instance.config is None` 时接受首个真实 config 替换单例（冷却状态跨替换保留），identity / 版本热重载语义不变。确定性复现：裸 `get_route_scorer()` 后 `maop_plan("run the ci pipeline", config=load_config())` 修复前 `chat` / 修复后 `pipeline`。回归测试 `py/tests/test_route_scorer.py::TestGetRouteScorer::test_config_recovers_configless_singleton`（含冷却保留断言）与 `test_configless_call_does_not_displace_real_config`。
- **日志轮转链式再轮转 bug（Windows `WinError 123` 根因）**：`rotate_logs()` 扫描目录内全部 `.log/.jsonl/.json` 超限文件，但从不跳过**已轮转的备份**（`_ROTATED_RE` 此前只用于清理旧备份），备份自身也超限 → 被再次轮转成 `<name>_<ts1>_<ts2>...` 链式文件名，每次还会把源文件重建为空；链条长度增长到触发 Windows 路径上限后 `os.replace` 抛 `WinError 123`。实测残留：`data/degradation_*.log` 39 个链式文件、`logs/MAOP-structured_*.log` 19 个，均已移出仓库至本地暂存区（可再生成）。修复：轮转循环对命中 `_ROTATED_RE` 的文件直接跳过（`log_rotate.py`）；新增回归测试 2 条（超限备份不再轮转 / 连续两次轮转不产生链式名，冻结 `datetime` 断言文件名集合），`tests/test_log_rotate.py` 8 passed。
- **relay_platform 写端点鉴权补齐**：`dashboard/routers/relay_platform.py` 的 `POST /api/relay-platforms`（注册体含 `api_key`）、`DELETE /{name}`、`POST /compare` 三个写端点此前**没有任何守卫** —— 文件未导入 `require_admin`，docstring 却声称本 router 负责"权限检查"，而其同级模块 model_gateway / hooks 的写端点全部逐行调用 `require_admin(request)`。后果是未授权请求即可写入出网凭据 + 目标 base_url（等于把后续 LLM 流量与密钥导向攻击者指定端点）或删除平台。修复：三个端点首行加 `require_admin(request)`，docstring 声明与实际一致。回归测试 `py/tests/test_relay_platform.py` 新增 3 条（非 admin → 403 且 `code=HTTP_403`；被拒的注册不落库；GET 端点口径不变），全文件 30 passed。取证：`scripts/check_admin_coverage.py` 的发现数由 17 降至 14，剩余项均已分类为用户级端点或外部回调。
- **`config/settings.yaml` 幽灵加载层**：`docs/configuration.md` 的"配置加载优先级"与 `maop/config/settings.py` 的模块 docstring 都列了 `config/settings.yaml` 这一层，但全仓无任何代码读取它（grep 只命中这两处声明与一篇归档报告；`settings.py` 是 Pydantic `BaseSettings`，只有 env + `.env` 两个来源）。属"文档与注释承诺了代码没有的能力"，已按实核更正为 环境变量 > `.env` > Field defaults，并在 docstring 显式写明不存在 YAML 层，防止再次被当成事实引用。
- **影响面判定脚本在非 UTF-8 控制台崩溃（Windows 四条 pytest 腿全红）**：`ci_path_scope.py` 的中文摘要含全角括号 `（`，Windows 上 `sys.stdout` 按**区域代码页**编码，en-US runner 是 cp1252 → `UnicodeEncodeError` 让脚本以 1 退出；zh-CN 本机是 cp936、能编码这些字符，所以本地怎么跑都绿。由两个既有的子进程用例如实抓到（`test_cli_writes_github_output` / `test_cli_mixed_changeset_reports_code_true`）。修法在脚本侧固定 UTF-8 输出（异常时降级不失败），不给调用方塞 `PYTHONIOENCODING` —— 真实 CI 步骤不会设它，设了也只是把缺陷留在原地。新增可证伪用例 `test_cli_survives_non_utf8_console`（显式 `PYTHONIOENCODING=cp1252` 起子进程，因此在任何平台都能复现）；变异验证：去掉 `reconfigure` → 该用例即红；还原后 `test_ci_path_scope.py` 34 passed。 **但第一版只修了写侧**：下一条 push 的 CI 实测 Windows 仍红 1 条 —— 子进程已固定输出 UTF-8，而用例用 `text=True` 按**父进程区域编码**解码，en-US runner 上变成 `UnicodeDecodeError: 'charmap' codec can't decode byte 0x90`（崩溃只是从写侧搬到读侧）。补法：两处读侧显式 `encoding="utf-8"`；另加跨平台确定的字节契约用例 `test_cli_stdout_is_valid_utf8_bytes`（不用 `text=True`，取原始字节严格按 UTF-8 解）。为什么必须有它：zh-CN 本机 cp936 会把 UTF-8 字节吞成乱码且不报错 —— 实测去掉读侧 `encoding` 后本机仍 passed，即"父进程解码失败"这类问题本机不可测，只有 en-US runner 会暴露，故把契约钉在字节层。变异验证（去掉 reconfigure + 子进程强制 cp1252）→ 该用例判红；最终 `test_ci_path_scope.py` 35 passed、ruff clean。
- **Nightly 的规模判据踩在刀刃上（假红）**：`tests/e2e/test_boundary_conditions.py::test_large_batch_register_1000` 同机自校准上限是"基线 × 条数 × 3"，Nightly run `36395972637` 连跑 3 次里第 3 次报"耗时 11.0s / 上限 10.6s"—— 实测比值 **2.97 对 3.00**，只差 1%。上浮本身不是退化（register 每条单独写 SQLite、无批量事务，表从 100 行涨到 1100 行时 per-op 自然变贵），而它要抓的复杂度退化量级是几百倍。上限放到 5×：本机实测比值 1.17（余量 4.3×），对 Nightly 最坏观测仍有 1.68× 余量；判据仍咬得住 —— 变异验证把上限临时收到 1.05（低于实测 1.17）用例立即失败并报出实测/上限两个数。

### Added

- **required checks 与 `ci.yml` 作业形状的三方一致性守卫**：master 的 required 上下文此前只活在两处 —— GitHub 设置（仓库里看不见）与 `docs/ci-gates.md` §7.2 的散文。新增机器可读清单 `.github/ci-required-checks.json`（5 条，各带 workflow/作业名/kind），配套 `py/tests/test_ci_required_checks.py` 6 条断言把**清单 ↔ `ci.yml` 作业形状 ↔ §7.2 散文**互相对账：context 必须唯一对应一个作业名（改名或删作业会让 required 永不上报、所有 PR 卡 `blocked`，实测）；required 作业必须在 `pull_request` 上**沿 needs 链递归可达**；kind 必须与作业真实 `if:` 一致；不许把矩阵作业列进清单（矩阵腿在 docs-only 时上报的是未展开的字面量名）；文档与清单不得各说各话。两个失败方向不对称：作业改名立刻可见，而"required 作业变成 PR 上恒不产出"是**静默假门禁** —— 所以可达性判据必须递归：`container-scan` 自己没有 `if:`，却 `needs: docker`（push-only），只查作业自身条件抓不到。变异验证 8 个破坏各点亮对应守卫；反证 —— 把递归摘掉后"祖先 push-only"这一例直接漏网。同时把 `.github/ci-required-checks.json` 加进 `ci_path_scope.py` 的 `CODE_FILES`：否则"只改清单"的 PR 被判 docs-only、这条守卫**永不执行**（正是本轮反复在治的"配了不跑"）。
- **`docs/ci-gates.md` §7.5：一次误判的证伪记录**：据"主干某次 push 只跑 18 秒、13 条 skipped"曾判定 job 级 scope 把 trunk 兜底验证裁掉了，实测两条推翻 —— ① `#52` 之前 `on.push.paths` 与 `on.pull_request.paths` 是同一份白名单且不含 `docs/**`、`CHANGELOG.md`，docs-only 合入主干本来就**完全不触发 workflow**（0 条 check），现状是"3 条真跑 + 可见的 skipped"，覆盖面不变、可见性变好；② 把旧白名单每一项喂给发布版分类器，16 条路径全部仍判 code=true、零漏判，且分类器另算 `deploy/`、`monitoring/`、`alertmanager/`、`nginx*.conf` ⇒ 净增。方法论：**比较两次 run 的时长必须同内容类型**；判"是否丢了覆盖率"要拿新旧判定面各自回放同一批输入。
- **`check_api_contract.py` 增补 README `/api/*` 引用检查**：README 的 curl 示例指向已不存在端点时，照抄会拿到 SPA 兜底 `200 + HTML`。新增 `parse_readme()`（完整 URL / 内联代码 / 散文裸路径三式提取）与 `readme_path_covered()`（方法无关、接受前缀族引用），复用同一份后端路由集判定；当前 README 4 处引用全部命中。能力并入自根 `scripts/doc_reconcile.py` —— 它用朴素子串匹配、看不见 FastAPI `prefix` 组合（`/api/cost/summary` 被误报不存在），且 CI lint job 的 `working-directory: py` 使其从未被执行；原文件已删除（CI 实际运行的是 `py/scripts/doc_reconcile.py`，与本次无关）。
- **`check_config_drift.py` 增补版本同步检查**：`maop/__init__.py` 的 `__version__` 与 `pyproject.toml` 的 `version` 必须一致（不一致 → FAIL；任一侧不可解析 → 显式打印告警，避免"没跑"与"全绿"同形）。同样并入自根 `scripts/doc_reconcile.py`。三项负例已实测（不一致 / 一致 / 不可解析）。
- **文档一致性检查接入 CI（新 `docs-gate` 作业）**：`py/scripts/check_docs_consistency.py` 此前"零引用、1400+ 条发现无人消费"，本轮治掉假阳性后接进 `ci.yml`。范围由 `docs/README.md` 自己声明的口径决定（第 1–6 章"当前权威文档"，第 7 章归档不算），历史快照/设计文档需文件头 `<!-- docs-gate: exempt=<理由> -->`、单行需 `skip=<理由>`，**理由不得留空且会被逐条打印**。作业**不挂 `if`**：只改文档会写进不存在的路径，只删代码会让文档里的旧路径失效，两边都必须跑（脚本只用标准库，本机约 3 秒）。新增守卫 `py/tests/test_docs_consistency_gate.py` 22 条：后缀简写放行边界（`core/worker_pool.py` 不得因 `core/reliability/worker_pool.py` 而放过）、跨仓/占位符/排版记号分型、gitignore 深层目录匹配、计数口径、假仓库端到端"注入死路径必红 / 空理由豁免不生效"、以及 `docs-gate` 作业不许被加 `if` 的结构断言。

### Changed

- **master 开启 required checks（带外配置，故在此留痕）**：job 级 scope 落地后按 `docs/ci-gates.md` §7 复测 skipped 语义 —— 以 docs-only PR 为夹具，required 只放一条 success 作业 → `clean`；换成一条**永不上报**的上下文 → 8 秒内翻 `blocked`（正对照，证明判定在算而非配置空转）；换成一条 **skipped** 作业 → 回到 `clean`。结论：**上报为 skipped 即满足 required，从未上报则永久卡住**。据此 required 设 5 条：`CI scope (code vs docs-only)`、`Secret Scan (gitleaks)`、`Docs consistency gate`（恒存在）+ `Lint (ruff + mypy)`、`Frontend Build`（docs-only 时跳过即满足，代码 PR 上必须真过）；`strict: false`、`enforce_admins: false`（保留 owner 逃生门；这也解释了早前"保护形同虚设"的观察 —— admin 绕过的是强制，不是判定）。**pytest 矩阵腿明确不纳入**：docs-only 时那条 skipped check 的名字是未展开的字面量，与代码 PR 上 9 条展开后的名字不同（两侧均实测），按精确名设会把文档 PR 永久卡住；真要覆盖 pytest 应加一条名字稳定的聚合作业读 `needs.*.result`，而不是折腾上下文匹配。同理排除 `Docker build` / `Container Scan (trivy)` / `Compose Smoke` / `Publish to PyPI`：它们在 PR 上恒 skipped 或不产生，设 required 等于"永远空满足"，是假门禁。
- **并入 origin/master（PR #33–#51）后新增门禁首跑即绿**：合并 vitest 3→5、node 20→24、前端覆盖率门禁接入、codecov 吞错步骤删除等 41 个远端提交，冲突 4 处（`ci.yml` / `CHANGELOG.md` / `package.json` / `package-lock.json`）的解决依据见合并提交 `a25b06a`。CI 实测：`Docs consistency gate`（本轮新接的作业）在首条 push 上 **success**，`Lint (ruff + mypy)`、`Frontend Build`、`Playwright E2E`、`SAST`、`Alembic migrations` 与 ubuntu/macos 各 Python 腿全绿；Windows 腿的红源自下面 Fixed 里的控制台编码问题。版本口径随合并更正：`docs/product-consolidation.md` 的"vitest 3.2.7（实测 lock）"→ 5.0.2，并注明当年那条"ci.yml 假称 vitest@5"的更正记录关系已反转（现在真是 5）。
- **人工审计脚本硬化**：`scripts/check_admin_coverage.py` 修复同一函数挂多个写方法装饰器导致的重复报告（`register_platform` 报两次，18 → 17 条），docstring 补记盲区（router 级 `dependencies=[...]` 守护看不见、硬编码 PUBLIC_ENDPOINTS）与逐条分类。它当时报出的 relay_platform 无鉴权问题**已于同日补齐**（见 Fixed）。`py/scripts/check_docs_consistency.py` 已从"人工工具"升级为 CI 门禁（见 Added），两类工具的定位差异在各自 docstring 里写明。
- **docs/archive 的 emoji 清零（本轮补完上轮未做的部分）**：15 个文件 / 476 处 emoji（357 行）按上一批同一口径处理 —— 正文/标题里的删除（严重度词如"高/中/低风险""已修复"本就在上下文里），表格中"仅 emoji"的单元格依表头换成文字（`状态`→已修复/未修复、`是否导出`→是/否、`新（MAOP）`→已改、依赖列→已加），`✓/✗/○` 保留（排版记号）。`design-system-legacy.md` 豁免：该文件把 emoji 当作设计规范对象描述（`🔷 Emoji 中文 English Name`），删除会破坏内容本身。改动做过结构性核对：15 文件逐行行数一致、每行 `|` 数量不变、且只动原本含 emoji 的行（0 异常）。**但当时声明的"`✓/✗/○` 保留"未成立** —— 清理脚本的字符类把 U+2713 `✓` 一并纳入，实际吞掉了 2 个 `✓`（同句仍有"一致""不依赖"文字，语义未受损），已由下一批恢复并改正字符类，见下条。
- **deliverables 的 emoji 清零 + 一次自纠**：4 个被跟踪的交付文档按同批口径清理（正文删除；纯 emoji 单元格按表头换文字：`判定`/`是否通过`→通过，`个人版`/`企业版`→是/否；`✓` 作线框图勾选记号保留）。核对过程中发现并修正上条所述偏差：恢复 `docs/archive` 被误删的 2 个 `✓`，清理用字符类改为显式跳过 U+2713/U+2717。注：`deliverables/` 整目录在 `.gitignore:174` 内，仅历史被跟踪的 9 个文件可入库，`Delivery-Plan-v5.1.0-Dual-Repo.md` 属未跟踪文件（本地副本一并清理）。
- **当前权威文档的路径订正（由新门禁扫出）**：`performance-benchmarks.md` 引用 7 个重构前的扁平模块路径（`py/maop/core/worker_pool.py` 等 → `core/reliability|security|monitoring/` 下的真实位置，两个同名模块按内容判定归属）；`contributing.md` 的 `core/api_key_vault.py`、`core/sandbox.py` → `core/security/api_key_vault.py`、`core/agent/plugins_hooks/plugin_sandbox.py`；`troubleshooting.md` 把 `system.py` 更正为 `dashboard/services/system_service.py`，并删掉不存在的 `config/sso.yaml`（SSO IdP 参数实际存在 provider 记录的 `config` 字段里，经 `POST /api/sso/providers` 写入）；`user-guide.md` 删掉不存在的 `config/tenants.yaml`（租户由 TenantManager 经 `/api/tenants/*` 管理）；`privacy-policy.md` 的 `data/.api-key` 更正为代码实际使用的 `data/.enc_key`；`cla.md` 移除仓库内不存在的 `docs/cla-employers.md` 承诺；`technical-whitepaper.md` 两处表格乱码（`| 列 |' |`、`| Art. 28 |" |` 多出的游离引号导致列数错位）已修。

### Removed

- **架构级死重清理（安全网 tag `archive-v4.0-before-removal`）**：`git rm -r archive/`（107 文件：js-dashboard 17 / ps-legacy 86 / legacy 4；零运行时引用，方案见 `docs/audits/archive-cleanup-plan.md`，2026-08-16 已成文未执行）；删除根 `otel-collector-config.yaml`（P2-M-02 起 compose 挂载 `deploy/otel-collector.yaml`，见 `docker-compose.yml:263`）、`scripts/migrate_bridge_to_proxy.py`（2026-07-26 重命名迁移已完成的一次性脚本）、`scripts/smoke_test_agents.py`（首 import 即断，功能已被 pytest 覆盖）、根 `scripts/doc_reconcile.py`（能力并入上述两脚本）、空目录 `plugins/`（`PluginManager` 启动自建，`plugin_manager.py:113`）。相关引用同步：README、`docs/DESIGN_RULES.md`、`docs/contributing.md`、`config/agents.yaml`、`maop.ps1`、`dashboard/server.py` 注释、`plugin_manager.py` 注释。
- **README 模块表死引用修正**：`core/security/tenant.py` 已于 2026-09-25 删除（严格子集并入 `core/tenant/`，见 `core/tenant/manager.py:14`），表项更新为 `core/tenant/manager.py`（由 `check_docs_consistency.py` 扫出，工具价值验证）。

## [Unreleased]（PR #33–#51 合并批次：依赖、CI 与探针更正）

### websockets 版本范围改判（下界 12.0 → 14.0，上界 <15 → <17）

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

### requirements.lock 与 pyproject 的镜像关系加上机械强制

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

### 类型门禁改为显式 CI 步骤（拿到 `workflow` scope 后的收尾）

#23 当时因为 gh token 无 `workflow` scope（GitHub 对改 `.github/workflows/*` 的写入直接拒绝），
只能把 `vue-tsc --noEmit` 借道 npm 的 `pretest` 生命周期生效。权限到位后按原计划改为显式形态：

- `.github/workflows/ci.yml` 的 frontend job 新增 `- name: Type check (vue-tsc)`（紧跟 `Lint frontend`）。
- `package.json` 删掉 `pretest`：门禁要在 CI 里**看得见、失败能归因到具体步骤**，不藏在 `npm test` 背后；
  本地 `npm test` 也回到只跑测试。`typecheck` 脚本保留。

### 主干 flaky 的 CI 侧观测探针（只观测，不改行为）

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

### 撤回 #39 的"真实泄漏"结论，并把探针改成可被证伪的

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

### 依赖与 CI 批次补记（2026-09-27：#40 #41 #42 与 dependabot 6 条）

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

### dependabot：`docker/*` 也成批，并让"该不该成批"变成机器判的

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

### 前端覆盖率门禁：接进 CI、去掉会把半程运行吞成绿灯的开关

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

### 从弃用分支捞回 K8s Operator 一致性测试，并改掉"整模块 slow"这种永不执行的结构

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

### 删掉一个"每次都失败、却从不报错"的 codecov 上传步骤

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

### 容器构建对 PyPI 镜像加 3 次退避重试（抗瞬时空索引）

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

## [Unreleased] - 2026-09-26（前端类型门禁）
### 前端补 `vue-tsc` 类型门禁

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

## [Unreleased] - 2026-09-26

### 文档与依赖口径更正（配合 MAOS 5.2.2）

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

### 依赖批次（dependabot）与 MCP WebSocket 存活判定修复

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

### SQLite「损坏即删除重建」范围收紧 —— 主干 CI 崩溃根因（实测）

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

### 并发用例的无界 `t.join()` 收口（32 处 / 19 文件）

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

## [Unreleased]

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
