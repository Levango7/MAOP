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
