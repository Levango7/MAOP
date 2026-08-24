# MAOP 只读审查报告（2026-08-24 · AtomCode 独立审查）

> **审查方式声明**：全程只读，未做任何写操作（本报告文件除外，为应项目方要求存档）。以下每条结论标注证据等级：**[实证]**=亲自读码/实测命令验证；**[抽查]**=抽样验证；**[声明]**=项目文档自称、未能独立证实。
>
> **重要前提**：审查对象为**当前工作区**（含 95 个已修改 + 25 个未跟踪文件，`git status` 实测，约 4167 行新增），与 `git log` 最新提交（21135ee）不一致，也与仓库内 `docs/audit-report-2026-08-24-corrected.md`（基于更早状态）存在差异。所有"已修复"结论均指**工作区代码**，不代表已提交。

---

## 〇、先纠正一份"过时审计报告"

仓库自带 `docs/audit-report-2026-08-24-corrected.md` 的**大部分 P0/P1 结论在当前工作区已不成立**，逐条实测如下：

| 审计报告原结论 | 当前实测结果 |
|---|---|
| mypy 5 错：`evolve_insights.py:84 h.id`、`:101 h.report_json`、`:93 heatmap 重定义`、`engine.py:673 result 重定义`、`loop_executor.py:103` 类型 | **[实证]** 全量 `mypy maop/ --ignore-missing-imports --no-warn-unused-ignores`（与 CI 同命令）实测 **Success: no issues found in 360 source files**；`evolve_insights.py` 全文件 grep 已无 `.id` / `report_json` 引用 |
| P0：`control.py:67`、`workflow.py:73` 孤儿 `create_task` | **[实证]** 已修复：两处均 `_bg_tasks.add(_t)` + `add_done_callback(_bg_tasks.discard)`（control.py:76-78、workflow.py:79-81） |
| 9 个 router 未接入统一错误格式 | **[实证]** 只剩 **auth.py**（8 端点，handle_api_errors 引用数=0）；alerts/api_keys/control/dag/data/n8n/observability/routing_preview 均已接入 |
| 前端 lint 2 warning（Docs.test.js `vi`、useMarkdown.js `codeLang`） | **[实证]** 这两处已修复；现为 **0 error / 4 warning**（见第三章） |
| 缺 `.gitattributes` | **[实证]** 已存在（LF 归一化，含二进制声明） |
| quickstart 要求 3.11 与 pyproject 3.10 冲突 | **[实证]** 已统一：quickstart.md:9 与 pyproject.toml:6 均为 3.10+ |
| PRD 承诺依赖 <15 实际 18 | **[实证]** PRD 已更新为 ≤18（PRD 第 38 行），与 pyproject 实测 18 个直接依赖一致 |
| lock 含 ML 依赖撑大镜像 | **[实证]** requirements.lock:83 已将 sentence-transformers 注释为可选 |

> 结论：审计报告基于 2026-08-23 前状态，其核心 bug 清单已被后续修复。**但这些修复尚未提交**——上线前第一件事（见第六章）。

---

## 一、产品设计与定位（第 1 问）

**结论：定位清晰、差异化成立，但有 3 处可改良。**

✅ 合理之处：
- **[实证]** "编排外部 CLI agent 的治理层、非自研运行时"（README.md:6）——避开与 LangChain/LangGraph 正面竞争，差异化真实。31 个 CLI 适配器声明基本属实（config/agents.yaml 实测 31 个命名 agent + 若干能力型内置 agent）。
- **[实证]** 双版架构（MAOP 个人版 MIT / MAOS 企业版 Commercial + ADR-017 物理隔离）+ Ed25519 License + honor-system 降级 + 7 天宽限期，商业模式完整（README.md:28-135）。
- **[实证]** 版本治理成熟：ROADMAP/CHANGELOG 分工、发布节奏规范（patch 每周≤1 / minor 两周≤1 / major 季度≤1）、发布前 checklist 齐备。

⚠️ 改良建议：
1. **产品面太宽**（[实证] nav.js 实测 30+ 路由、enterprise 6 大功能 + v5.1.0 6 大新功能集中在两周内发布）——个人版核心叙事被稀释。RFC-001 自己承认"功能仓库"认知成本 5-8 分钟（JetBrains 标准 30 秒）。建议按 RFC-001 的"工作台"信息架构（已部分落地，nav.js 可见 Run/Evolve 合并）持续推进，并砍掉低频视图（如独立 EvolutionHistory 已合并进 Evolve）。
2. **成功指标无验收闭环**（[实证] PRD 1.3 有磁盘占用/冷启动/依赖数指标，但 CI 无对应门禁，仅依赖数被实际跟踪）——建议把"冷启动 <3s、磁盘 <50MB"加入性能 job。
3. **RFC-001 仍是 Draft 状态**（[实证] product-design-rfc-001.md:3），但其迭代 A 已在 nav.js 落地——"设计文档与实现脱节"（文档未更新状态）。

---

## 二、文档、UI、接口规范（第 2 问）

**结论：文档体系在同类开源项目里属上乘，但存在若干实测不一致。**

✅ **[实证]** 73+ 篇文档分层完整（PRD/HLD/ADR×17/SLA/隐私政策/迁移指南/runbook）；`docs/README.md` 有元索引；ErrorSchema 有权威定义（error_handler.py:29-46）且 50 个 router 中 49 个已接入统一错误格式。

❌ 实测问题：

| # | 级别 | 证据 | 问题 |
|---|------|------|------|
| D1 | P2 | **[实证]** `README.md:305-317` 的 agents.yaml 示例是**旧 list 格式**（`- name: coder, driver: openai, model: gpt-4`），而实际 `config/agents.yaml` 是 **dict 格式**（`agents: MAOP: {cli_args: -m maop.cli run...}`）——用户照 README 写配置必然报错 | README 配置示例过时 |
| D2 | P2 | **[实证]** `docs/README.md` 称"346 个端点"；实测路由装饰器 479 处、去重路径 384 个（含 /api/v1 别名） | 端点计数与文档不符 |
| D3 | P2 | **[实证]** `auth.py`（8 个端点）未用 ErrorSchema，`/api/auth/login` 等返回手写 `JSONResponse({"status","error"})`，缺 `code/detail/request_id` 字段——与 CHANGELOG"统一错误响应"表述不符 | 错误格式未全覆盖 |
| D4 | P3 | **[实证]** `docker-compose.yml:338` 与 `docker-compose.prod.yml:346` 的 redis-server 均用 `${MAOP_REDIS_PASSWORD:?}` **强制**密码；但 prod 应用侧传参 `docker-compose.prod.yml:97,161` 用 `${MAOP_REDIS_PASSWORD:-}` 默认空——服务端密码强制、应用侧传参策略不一致（redis 无鉴权启动的风险**不成立**，属一致性小问题） | 配置语法风格不一致 |

✅ UI 侧：**[实证]** 32 个视图全部有 i18n 覆盖（36 个 i18n 文件，含共享命名 view-skills/view-sso/view-tlmemory/view-vector/view-workflow/view-apikeys，逐一映射核验）；DESIGN_RULES.md（色彩/边框/布局 token）与 frontend-style-guide.md（ListPageLayout/DetailDrawer 契约）存在且与代码吻合。

---

## 三、架构、模块、前端、后端、测试、CICD、部署（第 3 问）

**结论：架构设计成熟度中上（ADR 17 篇、分层清晰），质量门禁设计认真，但存在 1 个流程风险 + 若干遗留项。**

### 架构 ✅
- **[实证]** 五层架构（Entry→Orchestration→Dispatch→Infrastructure→Data）与代码结构一致；"10 个上帝模块拆分"真实落地（server.py/llm_provider.py/dispatcher.py 等已拆为 re-export shim + 实现）。
- ⚠️ **第二轮大文件**（[实证] wc -l 实测）：`supervisor.py` **1571 行**、`engine.py` 998、`memory/manager.py` 949、`migrations/pg/versions/001_initial_schema.py` 938、`dispatch_debate.py` 895、`vector_store.py` 848、`dispatch_core.py` 840——拆分范式正确但第二轮收敛未完。
- ⚠️ **[实证]** 4 处 `loop.create_task` / `ensure_future` 未持有引用（`cost_tracker.py:268`、`maop_loop_phases.py:178/498`、`a2a.py:283`）——fire-and-forget 有注释与 `try/except RuntimeError` 兜底，风险低，但与该库自己刚修掉的孤儿任务模式同类，建议统一持有。

### 后端代码质量 ✅
- **[实证]** 经典雷区系统性清扫：`except: pass` = **0**、裸 `except:` = **0**（全库 grep 实测）、`shell=True` 全为注释、`eval()` 已换 AST safe_eval（maop_plan.py:333-337）、无可变默认参数、ruff 0 error、mypy 全量 0 error。
- **[实证]** 安全细节扎实：login 限流含用户名锁定 + IP 维度双限流 + 防整表 clear 绕过（auth.py:380-415）；Engine 无执行器时返回 FAILED 而非假成功（engine.py:662-667）；auth 关闭时降级 guest 非 admin（security/auth.py:540-546）。

### 前端（第 3 问之 UI）⚠️
- **[实证]** `npx eslint src/` 实测 **0 error / 4 warning**：
  - `OnboardingWizard.vue:12` 属性换行（可 --fix）
  - `useDagProgress.js:73` `_getToken` 未使用（死代码）
  - `EvolutionHistory.vue:128,278` 属性顺序 ×2（可 --fix）
- ⚠️ **[实证]** Playwright e2e 仅 **4 个 spec**（backend-health/core/enterprise-route-guard/knowledge-graph）对 32 个视图——UI 回归网薄，与后端 7323 条测试的量级不匹配。
- ✅ **[实证]** 前端调用的关键端点均有后端实现（`/api/agents/presets`→crud.py:172、`/api/dag/auto-split`→dag.py:47、`/api/control/maintain`→control.py:221、`/api/evolution/approve`→evolution_experiment.py:243 逐一核验）。

### 测试（第 3 问之测试）✅
- **[实证]** 本地 `pytest --collect-only` **7323 条收集、0 收集错误**；266 个测试文件；CI 门禁设计认真（coverage ratchet FLOOR=80、`--timeout=60`、`--reruns=3`、契约/性能/可靠性独立 job、alembic 升降级 + PG 集成、12 平台矩阵）。
- ⚠️ [声明] 覆盖率 82% 的说法源自仓库文档（pyproject.toml:229 注释 + ratchet baseline），未独立跑全量覆盖率（耗时长且会写 .coverage，违反只读约定）。

### CI/CD 与部署（第 3 问之 CICD/部署）⚠️
- ✅ **[实证]** 链路完整：lint → 12 平台测试 → 前端 build → Playwright → 迁移 → Docker → trivy/bandit/CodeQL/gitleaks/SBOM/pip-audit → PyPI 发布 → compose 冒烟 → staging 部署 → 失败自动回滚（ci.yml 全 982 行通读）。`rollback` job 用 push before-SHA + gh api 查询最近成功 deployment 双路兜底（ci.yml:880-898）。
- ✅ **[实证]** Docker 安全基线扎实：多阶段构建（含前端构建阶段）、非 root、tini、requirements.lock 锁定、JWT_SECRET 缺失拒启动。
- ❌ **P0 级流程风险（[声明]+[实证] 混合）**：仓库内审计报告称 **CI 连续 8 次全红**（run #141-148，mypy + 前端 lint 门禁挂导致测试矩阵全 skip）。无法用 `gh` 验证 GitHub Actions 历史（无凭证，审计报告自己标注"本地无 gh CLI 无法独立验证"），但审计报告至少证明 **CI 曾经红灯且未被拦截**。而当前工作区实测 mypy/ruff/lint 全绿——**说明修复都在本地，尚未提交，CI 未重新验证**。这正是发布前最需要闭环的一环。

---

## 四、Bug 专项排查（第 4 问：细节 + 出处）

经逐条核验，**当前工作区未发现会立即崩溃的 P0 运行时 bug**（审计报告点名的均已修复）。发现的问题按级别列出：

| # | 级别 | 证据（亲自读码） | 问题 |
|---|------|-----------------|------|
| B1 | P2 | **[实证]** `auth.py` 全文件无 `handle_api_errors`（8 端点，引用数=0），错误响应缺 `code/detail/request_id` | 唯一未接入统一错误格式的 router |
| B2 | P2 | **[实证]** `README.md:305-317` agents.yaml 示例为已废弃 list 格式 | 照文档配置必失败 |
| B3 | P3 | **[实证]** `useDagProgress.js:73` `_getToken` 死代码 + `OnboardingWizard.vue:12`、`EvolutionHistory.vue:128,278` 3 处可 --fix warning | 前端 4 warning 未清 |
| B4 | P3 | **[实证]** `supervisor.py` 1571 行 / `engine.py` 998 行 / `memory/manager.py` 949 行 | 大文件二轮拆分未完 |
| B5 | P3 | **[实证]** `cost_tracker.py:268`、`maop_loop_phases.py:178,498`、`a2a.py:283` fire-and-forget 任务未持引用 | 低风险，与已修模式同类 |
| B6 | P3 | **[实证]** `docs/README.md` "346 端点" vs 实测 384 个去重路径 | 文档计数失实 |
| B7 | P3 | **[实证]** `evolve_insights.py` 中 `agent_counts` 从 `phases` 里猜 `agent` 字段（`:98-101`），拿不到就 `continue` 静默跳过统计 | 演化指标聚合对数据形态脆弱，无数据时静默空 |
| B8 | P3 | **[实证]** `docker-compose.prod.yml:97,161` 应用侧 `REDIS_PASSWORD` 用 `:-` 默认空，与 redis-server `:?` 强制（:346）风格不一致 | 配置语法不一致（无实际无鉴权风险） |

**未发现**：[实证] 无 SQL 注入（迁移脚本外无 f-string SQL）、无命令注入（全 list 形式）、无硬编码密钥、无 `except: pass`、前后端端点抽样无失配。

---

## 五、证据分级与诚实声明（第 5 问）

- **[实证]**：所有代码行号、命令输出（mypy 360 文件 Success、ruff 0、pytest 收集 7323、eslint 4 warning、git status 120 文件、compose 密码语法、路由计数）均为本次会话亲自执行/阅读。
- **[抽查]**：31 个适配器只数了名字未逐一验证 driver 可用性；i18n 逐一映射核验过但未逐个渲染。
- **[声明]**：CI 8 连红历史（无 gh 凭证无法独立验证）；覆盖率 82%；Docker 镜像实际构建/K8s 实际部署/压测数值——均未实测。
- **未验证**：前端页面实际视觉效果（只做了静态+lint 审查）。

---

## 六、距离正式提交上线还有多少路？

**结论：代码质量本身已具备上线品相（当前工作区 lint/type/test 收集全绿），距离"正式提交上线"约 1.5~2 人日 + 1 个工作日 staging 验证，且必须先过一道流程关。**

| 优先级 | 事项 | 预估 |
|---|---|---|
| **P0 阻塞（必须先做）** | ① **提交当前 120 个未提交文件**（95 M + 25 ??，含全部修复），推送后跑 CI 确认全绿——修复全在本地，CI 从未验证过这批代码；② 若 CI 仍红，以 CI 日志为准修复（不要以提交消息为准） | 0.5 人日 |
| **P1 发布前应清** | ③ auth.py 8 端点接入 ErrorSchema（B1）；④ README agents.yaml 示例更新为 dict 格式（B2） | 0.5 人日 |
| **P2 本迭代** | ⑤ 前端 4 warning 清理（B3）；⑥ 端点计数文档修正（B6）；⑦ compose 密码语法统一（B8） | 0.5 人日 |
| **P3 下一周期** | ⑧ supervisor.py 等大文件二轮拆分（B4）；⑨ Playwright e2e 扩充（4→覆盖核心 10+ 视图）；⑩ fire-and-forget 任务统一持引用（B5）；⑪ RFC-001 状态更新 | 规划项 |

**最后一句必须直说**：当前最大风险不在代码，而在**验证闭环**——仓库内审计报告记录"CI 8 连红期间每条提交都声称 mypy 0 / 测试全过"，其中 mypy 声称与 CI 实测矛盾。代码质量门禁本身设计得很好，问题只是"结果没人核对"。正式上线前，请以 CI 实际输出为准做一道人工守门，并确保本次修复的 120 个文件真正提交、真正绿过一次 CI。

---

*本报告由 AtomCode 于 2026-08-24 以只读方式生成并应项目方要求存档。*
