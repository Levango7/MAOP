# M-SIM business-scenario rehearsal evidence (M0)

- generated: 2026-11-04 06:56:18
- exec path: python -m maop.cli run (production entry, same shape as operator/Temporal executor)
- driver: deterministic stub, no external LLM/network, MAOP_ROOT_DIR=F:\Nexus\MAOP-op\py\.sim-final\sim-home

## scenario results

| scenario | nodes | rounds | exec | success | failed |
|---|---|---|---|---|---|
| ticket_triage | 3 | 1 | 3 | 3 | 0 |
| daily_report | 4 | 1 | 4 | 4 | 0 |
| code_review | 4 | 1 | 4 | 4 | 0 |

## node details

| scenario | node | round | agent | status | turns | tokens | source | ms |
|---|---|---|---|---|---|---|---|---|
| ticket_triage | triage | 1 | sim-triage | success | 1 | 563 | estimated | 1675 |
| ticket_triage | resolve | 1 | sim-worker | success | 1 | 419 | estimated | 1521 |
| ticket_triage | receipt | 1 | sim-worker | success | 1 | 743 | estimated | 1658 |
| daily_report | collect | 1 | sim-worker | success | 1 | 520 | estimated | 1561 |
| daily_report | aggregate | 1 | sim-worker | success | 1 | 528 | estimated | 1572 |
| daily_report | draft | 1 | sim-reporter | success | 1 | 322 | estimated | 1507 |
| daily_report | verify | 1 | sim-reviewer | success | 1 | 784 | estimated | 1524 |
| code_review | lint | 1 | sim-worker | success | 1 | 583 | estimated | 1524 |
| code_review | scan | 1 | sim-worker | success | 1 | 711 | estimated | 1575 |
| code_review | review | 1 | sim-reviewer | success | 1 | 788 | estimated | 1593 |
| code_review | summarize | 1 | sim-reporter | success | 1 | 963 | estimated | 1698 |

## cost reconciliation (M-X3)

```json
{
  "db_path": "F:\\Nexus\\MAOP-op\\py\\.sim-final\\sim-home\\data\\maop.db",
  "exists": true,
  "tables": [
    "circuit_breaker_state",
    "failover_chains",
    "breaker_events",
    "sqlite_sequence",
    "memory_entries",
    "memory_traces",
    "memory_trajectory",
    "memory_fts",
    "memory_fts_data",
    "memory_fts_idx",
    "memory_fts_docsize",
    "memory_fts_config",
    "hooks",
    "hook_logs",
    "ts_raw",
    "ts_5min",
    "ts_1hour",
    "queue_messages",
    "queue_dead_letters",
    "queue_idempotent",
    "kv_store",
    "prompt_templates",
    "prompt_versions",
    "_migrations",
    "run_events",
    "agent_catalog",
    "cost_entries",
    "routing_decisions",
    "agent_performance"
  ],
  "attribution_total": {
    "cost_usd": 0.0,
    "calls": 11,
    "tokens": 3185
  }
}
```


## alert channel (alert_ingest)

```json
{
  "count_before": 0,
  "count_after": 1,
  "persisted": true
}
```


## backup and restore (db_backup)

```json
{
  "backups": [
    "db_name='maop.db' backup_path='F:\\\\Nexus\\\\MAOP-op\\\\py\\\\.sim-final\\\\sim-home\\\\data\\\\backups\\\\maop.db.2026-11-03_225619087900.bak' size_bytes=430080 created_at='2026-11-03T22:56:19.107993+00:00' duration_ms=19"
  ],
  "integrity_checks": [
    {
      "file": "maop.db.2026-11-03_225619087900.bak",
      "integrity": "ok"
    }
  ]
}
```

## exposed gaps

- GAP-1 (code-confirmed): wiring.run_workflow legacy backend lands in maop_plan.execute_workflow which ONLY plans and never executes any agent (maop_plan.py:429 docstring admits the name is a misnomer). Real execution exists only via Dispatcher.dispatch / maop run / Temporal backend; the cross-backend contract gap must be closed in E2-b.
- GAP-2 (measured): M-X3 attribution tokens=3185 != protocol-line tokens=6924 -- cost attribution vs execution-path metering mismatch.
- GAP-3 (environment, tooling): 子进程解析 maop 的两颗地雷——(a) py/scripts/business_sim/run_sim.py 曾用 parents[1] 取 py 目录（差一层到 scripts/），(b) 本机 site-packages 存在把 F:\Nexus\MAOP\py（主工作树旧分支）注入 sys.path 的 .pth。两者叠加时子进程解析到旧分支 cli（无 --agent），报 unrecognized arguments。已在 harness 修为 parents[2] 并以 runpy 包装确定性启动；**任何按 repo 约定从脚本目录spawn maop 子进程的工具都有同款风险**。

## GAP-2 根因诊断（人工复核，2026-11-04）

**两层计量、两套 schema、互不相认**：

1. **协议行层**（M3.1/M3.3 合同）：CLI 驱动回填 `[USAGE] turns=N tokens=T source=estimated`
   ——单一 `tokens=` 总量，被 operator reconcile 与 CLI 出口解析进
   `status.tokensUsed`（CRD 用量会计的权威值）。
2. **成本账层**（P2-1 成本双写统一，`maop/maop_loop_phases.py:385-420`）：
   `CostTracker.record_actual_cost` 读 `usage.prompt_tokens / completion_tokens`
   ——CLI 驱动路径没有 provider，恒为 0 → 永远落到启发式估算
   （task 文本 CJK×2 + len//4 与 stdout 同式），账面记的是**文本长度启发式**。

同一 11 次执行：CRD 侧（协议行）= 6924 tokens，成本账侧（启发式）= 3185 tokens，
两者都标 estimated、互不引用。**后果**：配额门（M3.2 QuotaChecker「终态后回报
用量」）与 CRD 报表如果各取一侧，配额裁决与用量报表将系统性分歧。

**修复选项（需裁决）**：
- **A（推荐）**：协议行 schema 升级为 `prompt_tokens=/completion_tokens=`
  （或 `tokens=` + 约定拆分规则），成本账层按「provider usage → 协议行 →
  启发式」优先级链取值并标注来源——三层单一真相，改动面 = 协议行 +
  loop 记账器 + stub/测试，operator reconcile 兼容旧行。
- B：成本账保持启发式作为对协议行声明的**独立审计源**，两数并存并在
  聚合视图标注双源——零代码改动，但看板永远有两个数。
- C：归因行加 `tokens_source` 维度，聚合可按源拆分——折中，改动 M-X3。

**推荐 A**：消除而非解释分歧；M-SIM harness 的对账断言（usage 合计 ==
attribution 合计）落地后即为该修复的验收门。

## GAP-4 Windows 编码缺口（CI 复测发现，已修）

四条 windows pytest 腿（3.10–3.13）首跑全红，根因非配额取消而是**真实跨平台
缺口**：``maop/core/agent/adapters/cli_adapter.py`` 的 ``subprocess.run(...,
text=True)`` 未指定 encoding，Windows 上管道走 locale 编码（cp1252），

- stub 子进程 ``print()`` 含中文 task 文本 → 子进程侧 ``UnicodeEncodeError``
  退出码 1（``test_stub_is_deterministic`` 断言 returncode==0 失败）；
- 经 ``maop.cli run`` 的三节点剧本 3/3 全失败（``test_scenario_end_to_end``
  ``failed == 3``）。

**影响面**：任何输出非 ASCII 的外部 CLI agent 在 Windows 部署上都会复现——
这不是演练工具的边角，而是生产路径的跨平台缺陷，恰是 M-SIM 预生产演练
要抓的那类问题（本机 Windows 控制台默认 UTF-8，故本地全绿、CI 才暴露）。

**修复**（同笔提交）：
1. ``cli_adapter.py`` subprocess.run 增 ``encoding="utf-8", errors="replace"``
   （POSIX 本就 UTF-8，无行为变化；Windows 使能非 ASCII 输出）；
2. ``stub_business_agent.py`` 入口对 stdout/stderr 做 UTF-8 reconfigure，
   保证被未指定 encoding 的父进程调用时也不炸（防御纵深）。

回归：``tests/test_business_sim.py`` 4/4 本地通过，CI 四条 windows 腿复跑
即为跨平台验收。

### GAP-4 第二层（复跑定位）：maop.cli 自身打印中文崩溃

首层修复（cli_adapter UTF-8 + stub reconfigure）使 stub 确定性测试转绿，但
windows 四腿仅剩 ``test_scenario_end_to_end`` 仍红（3/3 节点失败）。本机以
``PYTHONIOENCODING=cp1252``（本机全局 PYTHONUTF8=1 PYTHONIOENCODING=utf-8
正是「本地全绿、CI 全红」的成因）复现并打印节点证据，定位到**第二层**：

- ``run_sim.run_node`` 派生的 ``maop.cli`` 子进程自身 stdout 在 Windows 管道
  走 cp1252；loop 打印含中文的运行/失败信息（``[FAIL] run 失败: ...``）时
  UnicodeEncodeError——**连异常报告路径自身都连带崩溃**（stderr 里
  ``[FAIL] run 失败: 'charmap' codec can't encode...`` 即其自证）。
- 影响面：任何 Windows 部署上 maop CLI 路径输出中文运行信息即中断，属
  生产入口级跨平台缺陷，比第一层更靠核心。

**修复**（同笔）：
1. ``maop/cli.py`` ``main()`` 入口对 stdout/stderr 做 UTF-8 reconfigure
   （errors="replace"，与 cli_adapter 解码侧对称；POSIX 无行为变化）；
2. ``run_sim.child_env`` 增 ``PYTHONUTF8=1``，覆盖整个子进程树。

**验证**：``PYTHONIOENCODING=cp1252``（比 CI 更严：CI 无此变量、PYTHONUTF8
会生效；此模拟下 PYTHONIOENCODING 压过 UTF-8 模式，仅靠入口 reconfigure 撑）
全 4 例通过——即修复在「环境变量全被压制」的最坏情形下依然成立。
