# Agent Coordination（多代理工作区协调）

> 2026-10-10 建立。本仓库（F:\Nexus\MAOP）曾有多个 Agent 会话并行工作。
> **2026-10-10 更新：WorkBuddy 已关闭，本仓现由 ZCode（本会话）全权负责**——
> 防撞纪律保留备用（将来若再开并行会话，照此执行）。

## 领地声明（按目录/文件粒度）

| Agent | 状态 | 领地（文件/目录） |
|---|---|---|
| ZCode（本会话） | **全权**（2026-10-10 起） | 全仓 |
| WorkBuddy | **已关闭** | — |

## 防撞纪律（双方遵守）

1. **动工前**：`git fetch && git log --oneline origin/master -3` + `git status --short`——
   发现对方在跟踪文件里的未提交改动，**不要动那些文件**，在本文件追加条目等对方划掉。
2. **提交只加显式路径**（`git add <路径>`，禁 `git add -A`/`git add .`）——
   避免把对方的未跟踪文件（WS-1/ 等杂物）卷进提交。
3. **禁 amend / 禁 force-push 已有 PR 的分支**（rebase 解决冲突后 `--force-with-lease` 除外，
   且在 PR 里注明）。
4. **先开分支再提交**：合完 PR 后 master 是检出状态，直接 commit 会落到 master 上
   （2026-10-10 本会话踩过——commit 后才发现 push 报 refspec 不匹配；
   处置=git branch <新分支> 打锚点 + `git reset --hard origin/master` 把 master 送回，
   再切分支 push，零损失）。提交前 `git branch --show-current` 确认不是 master。
5. **CHANGELOG [Unreleased] 段序**：双方都往同一位置插段会冲突——后合并的一方
   rebase 时保留双方段落（先合前者、后者 rebase 解决）。
6. 每轮动手前刷新本表；发现对方条目与自己冲突，先沟通再动。
7. **gh api -f body= 里禁反引号**（bash 会当命令替换吞掉正文——踩过三次）；
   用 `--input <json 文件>` 传 PR/Issue 正文。

## 当前已知并发风险点

- 前端 E2E 目录（`e2e/`）——若双方都在扩 E2E，**各用各的 spec 文件名**，
  不要往同一个 spec 文件里加用例。
- `py/maop/core/scheduling/`、`py/maop/core/reliability/`、`py/maop/engine_utils.py`——
  本会话近几轮的主战场（批 B/C + 批量化），如需动这些文件先看本表上方条目。

## 已完成的领地操作（2026-10-27）

- **chat_service.py 拆分**（1152 → 482 行）：§2 Stream → `services/chat/streaming.py`（251 行），
  §5 Feedback → `services/chat/feedback.py`（503 行）。父模块保留 §1 Chat + §3 React + §4 Debate
  + 转发 import（`__all__` 不变）。allowlist 条目移除（7→6），nosec inventory 条目更新
  （`chat_service.py:B608:10` → `chat/feedback.py:B608:10`）。门禁全绿：ruff / mypy /
  file-size / silent-exceptions / config-drift / api-contract / wiring / docs-consistency /
  bandit nosec。目标测试 50 passed。
- **data_service.py 拆分**（1185 → 509 行，PR #171 已合并）：§2 Logs → `services/data/logs.py`，
  §3 Analysis → `services/data/analysis.py`。详见 PR #171。
- **agent_service.py 拆分**（843 → 479 行）：§1 Catalog → `services/agent/catalog.py`（243 行），
  §3-4 Proxy/Gateway → `services/agent/gateway.py`（277 行），`services/agent/__init__.py` 聚合。
  父模块保留 §2 Router + §5 Collaboration + §6 SubAgent + 转发 import（`__all__` 不变）
  + PEP 562 `__getattr__`（代理子模块模块级变量 `_agent_proxy` 等）。allowlist 移除（6→4）。
  门禁全绿：ruff / mypy / file-size / silent-exceptions / config-drift / api-contract /
  wiring / docs-consistency / coverage-ratchet / bandit nosec。agent 测试 396 passed（16 文件）。
- **evolution_service.py 拆分**（806 → 601 行）：F2-01 Evolution Loop AC-07 → `services/evolution/loop.py`（277 行），
  `services/evolution/__init__.py` 聚合。父模块保留 evolution_experiment + evolve_insights + 转发 import。
  allowlist 移除（6→4）。门禁全绿。evolution 测试 223 passed（14 文件）。
- **distributed_scheduler.py 拆分**（950 → 6 文件子包）：`_models.py`（常量/数据类/异常）、
  `_worker_selection.py`（_WorkerSelectionMixin）、`_failure.py`（_FailureDetectionMixin）、
  `_results.py`（_ResultsMixin）、`_scheduler.py`（DistributedScheduler 组合 + DAG 执行）、
  `__init__.py`（公开 API 转发）。外部导入路径不变。门禁全绿：ruff / mypy / 117 tests。
  commit `89d2603`。Mimosa 拦 f-string format specifier（`!r`/`:.0f`）于 raise 语句 →
  改用 plain interpolation（无 format specifier），错误消息由 logger.error 承载详情。
