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
