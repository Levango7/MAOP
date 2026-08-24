# MAOP 项目只读审计报告（2026-08-24 修正版）

**审计方式声明**：全程未做任何写操作。以下每条结论均注明证据（文件：行 / 命令实测输出）。我明确区分三个可信度等级：**[实证]**=我亲自运行/读码验证；**[抽查]**=抽样验证、未全覆盖；**[声明]**=仅项目文档自称、我未能独立证实。

**修正声明**：本报告基于 2026-08-23 初版审计报告，经 2026-08-24 逐条核对验证后修正。修正项标注 `[修正]`，补充项标注 `[补充]`，删除项标注 `[删除]`。

---

## 〇、最严重发现：发布纪律与门禁信任已断裂（P0 级系统性问题）

**这不是单个 bug，是流程性失效，优先级高于一切技术问题。**

1. **主干 CI 连续 8 次全红**（GitHub Actions 真实数据 `[实证]`）：
   - run #140（2026-08-19）最后一次绿；#141~#148（08-19 ~ 08-23，含今天）全部 `failure`
   - 最新 run #148 的失败步骤：**Lint 的 `mypy` 步** + **Frontend Build 的 `npm run lint` 步**；由于这两个门禁挂了，**12 平台测试矩阵、e2e、Playwright、Docker、Compose 冒烟全部被 skipped**——即最近 8 次推送的代码从未通过完整验证链路。

2. **提交声明与事实不符**（`[实证]` 交叉验证）：
   - 提交 `eefceb4` 自述"验证： ruff 0 error, 全量测试 7250 passed 0 failed"
   - 但同一提交在 CI 里 mypy 挂了；我在本地用 **与 CI 完全相同的命令**复现：`mypy maop/ --ignore-missing-imports --no-warn-unused-ignores` → **5 个真实错误，3 个文件**（实测输出）：
     ```
     maop\dashboard\routers\evolve_insights.py:84: "LoopReport" has no attribute "id"
     maop\dashboard\routers\evolve_insights.py:101: "LoopReport" has no attribute "report_json"
     maop\dashboard\routers\evolve_insights.py:93: "heatmap" already defined on line 53
     maop\engine.py:673: "result" already defined on line 646
     maop\loop_executor.py:103: AnalysisResult | None → expected AnalysisResult
     ```

3. **其中两处是会真出事的运行时 Bug，不只是类型报错**（`[实证]`，我对照读了 `core/evolution/evolution_loop_types.py:78-94` 的 `LoopReport` 定义确认）：
   - **`evolve_insights.py:84`**：`h.id` —— `LoopReport` 只有 `cycle_id`，没有 `id`。`/api/evolve/metrics` 端点只要有演化历史记录就会 **AttributeError → 500**。
   - **`evolve_insights.py:101`**：`h.report_json` —— 同样不存在。虽然外层有 `contextlib.suppress(Exception)`（第 100 行）不会炸接口，但每轮循环都会吞错 → **heatmap 功能永远输出空数组，静默失效**。新功能昨天刚提交，等于带病上线。
   - `loop_executor.py:103`：核实 `_should_execute_parallel`（`loop_executor.py:42`）有 `analysis is not None` 守卫，**运行时安全**，仅类型层面问题，降级为 P3。

---

## 一、产品设计与定位

**评价：定位清晰、商业模式设计合理。**

- ✅ `[实证]` 双版定位明确（README.md:28-135）：MAOP=个人版/MIT 免费开源，MAOS=企业版/Commercial/私有仓库（ADR-017 物理隔离），能力对比表完整，Edition 检测优先级清晰（4 级），License 用 Ed25519 + honor-system 降级 + 7 天宽限期，巨完整。
- ✅ `[实证]` 核心定位表述克制诚实："MAOP 是编排**外部 CLI agent** 的治理层，非自研 agent 运行时"（README.md:6）——避免了与 LangChain/LangGraph 的正面撞车，差异化合理。
- ✅ `[抽查]` "31 个第三方 CLI 适配器"声明：`config/agents.yaml` 实际数出 31 个命名 agent（含 MAOP 自指），声明基本属实。
- ⚠️ **PRD 成功指标与实现脱节**（`[实证]`）：`deliverables/PRD-...md:1.3` 承诺个人版"pip install 依赖数 < 15"；实际 `py/pyproject.toml:8-36` 直接依赖 **18 个**（lock 解析后 44 个）。指标无人追踪验收，属于 PRD 与工程两张皮。
- ⚠️ 发布节奏前科（`[实证]`）：CHANGELOG 自承 2026-08-11~14 四天发了 4 个版本（5.0.0→5.1.0），事后补了《发布节奏规范》。约束刚立，尚无执行记录支撑。

## 二、文档 / UI / 接口规范

- ✅ 文档体系庞大且分层（73 篇 md：45 顶层 + 28 归档；PRD/HLD/ADR×17/SLA/隐私政策/迁移指南/运维手册俱全），`docs/README.md` 有元索引，README/ROADMAP/CHANGELOG 治理分工明确。
- ✅ `[实证]` `/api/v1` 别名机制真实存在（`_register_routes.py:592-623`）；统一错误格式 `ErrorSchema` 有文档且有 `handle_api_errors` 装饰器落地（50 个 router 文件在用）。
- ⚠️ **API 文档过度承诺**（`[实证]`）：`docs/api-reference.md` 开头自称"覆盖全部 HTTP 与 WebSocket 端点"，但代码里路由装饰器共 **468 处**，文档 651 行、`/api/` 提及 340 次（含叙述文本）——实际覆盖显著不全，建议改成"核心端点"口径。
- ⚠️ **`[修正]` 9 个 router 未接入统一错误格式**（`[实证]`，Python 脚本逐一扫描验证）：`alerts.py / api_keys.py / auth.py / control.py / dag.py / data.py / n8n.py / observability.py / routing_preview.py`。原报告称 10 个（含 `state.py`），实际 `state.py` 已不在未装饰列表中；原报告遗漏了 `n8n.py`。部分（auth/observability）可辩解，但 CHANGELOG"统一错误响应"的表述应按"已装饰端点"口径写，不要隐含全覆盖。
- ⚠️ 文档一致性小错（`[实证]`）：`docs/quickstart.md:9` 要求 Python 3.11+，`py/pyproject.toml` 声明 `>=3.10` 且 CI 测到 3.10；ROADMAP v5.0.0 验收清单有错别字"API 积极移除"（第 83 行，应为"积极移除"）。
- ✅ UI 侧：`frontend-style-guide.md` 存在且有设计 tokens 分层；34 个 i18n 视图文件对 31 个视图，覆盖充分；但**未实际渲染验证视觉效果**（本审计为静态+命令行维度）。

## 三、架构、模块与代码质量（含 Bug 清单）

**Bug 实证清单（除前述 mypy 3 处外）：**

| # | 级别 | 证据 | 问题 |
|---|------|------|------|
| B1 | P0 | `py/maop/dashboard/routers/control.py:67`、`routers/system/workflow.py:73` | `asyncio.create_task(proc.communicate())` **未持有任务引用**：可能被 GC 提前回收（"Task was destroyed but it is pending"），其异常永远不会被检索。注释意图是防管道死锁，正确，但实现方式有坑 |
| B2 | P1 | `evolve_insights.py:84` | `h.id` → 运行时 AttributeError，`/api/evolve/metrics` 有数据时 500（上文详述） |
| B3 | P1 | `evolve_insights.py:101` | `h.report_json` 不存在 + 被 `suppress` 吞 → heatmap 静默恒空（上文详述） |
| B4 | P0(流程) | GitHub Actions run #145-148 | 见第〇节：mypy 门禁红 8 次未拦 |

**`[删除]` 原报告 B4"59 处 `except: pass`"条目**：经实测验证，当前代码库中 `except: pass` 模式为 **0 处**，bare `except:` 也为 **0 处**（ruff E722 全通过）。原报告此条严重失实，可能引用了旧版本数据或使用了不同的搜索模式。**此条已删除。**

**`[补充]` 异常处理质量**：当前代码库在异常处理方面表现优秀——无 bare except、无 `except: pass`、ruff E722 全通过。经典雷区已系统性清扫。

**架构质量：**

- ✅ 五层架构清晰，ADR 17 篇决策链完整；"10 个上帝模块拆分"（v5.x）的技术债偿还动作真实可见。
- ⚠️ **拆分后仍有第二轮大文件**（`[实证]`）：`core/scheduling/supervisor.py` 1421 行/13 类/61KB；`engine.py` 42KB；`dispatch_core.py`/`dispatch_debate.py` 各 ~35KB；另有 meta/monitoring/backends/vector_store/message_queue 5 个 30KB+ 文件。拆分范式（re-export shim）正确，建议列入 5.2 计划。
- ✅ 经典雷区已系统性清扫（`[实证]` grep 全库）：无裸 `except:`、无可变默认参数、`eval()` 已换 AST safe_eval（maop_plan.py:333 有修复记录）、`shell=True` 已全部替换为 list 形式（残留均为注释）、SQL f-string 仅存在于迁移脚本的受控标识符拼接、`lrucache`/pickle 类豁免在 `.bandit` 显式管理。
- ✅ `ruff check` 本地实测 **0 error**；测试收集 **7622 条 / 0 收集错误**。
- ⚠️ 生产代码残留 10 处 `assert`（含 1 处在 `tool_signing.py` 的 docstring 示例中，无害），其余 9 处为内部不变量断言，`python -O` 下会被剥离——Docker 默认不用 `-O`，当前无实际风险，建议长期改为显式异常。

## 四、测试体系

- ✅ 数量与质量并存：7622 收集；`[抽查]` 两个"覆盖补充分"文件（`test_coverage_boost.py`、`test_routers_smoke_coverage.py`）**均为真实行为断言**（后者真的用 AsyncClient 按 admin 身份逐个打端点并校验响应结构），不是 import 冲数。其余 25 个 assume：`[声明]` 未逐一验证。
- ✅ CI 门禁设计认真：coverage ratchet 渐进式（FLOOR=80，实测 82% 有 pyproject:233 注释佐证）、`--timeout=60`、`--reruns=3`、契约测试独立目录、性能/可靠性测试独立 job。
- ⚠️ **前端 e2e 稀疏**（`[实证]`）：Playwright 仅 4 个 spec 对 30+ 视图，UI 回归网很薄。
- **`[修正]` 前端 lint 当前 0 error, 2 warning**（`[实证]` 本地复现）：
  - `src/__tests__/Docs.test.js:7:55` — `vi` is defined but never used（warning，死代码）
  - `src/composables/useMarkdown.js:73:9` — `codeLang` is assigned a value but never used（warning，死代码）
  - 原报告称"2 error"及提到 `afterEach` 未 import / `paraBuf prefer-const`，实际这些问题已不存在（可能已被修复）。当前仅为 2 个 warning 级别的死代码，不影响构建。

## 五、CI/CD 与部署

- ✅ CI 覆盖面在同规模开源项目里属上乘：lint/mypy/配置漂移审计/文档一致性脚本/12 平台矩阵/契约/e2e/Playwright/alembic 升降级/PG 服务容器/Docker 构建/compose 冒烟/trivy/bandit/gitleaks/SBOM/pip-audit/PyPI 可信发布。
- ⚠️ **但这些门禁当前全部失效于主分支**（第〇节 8 连红），等于"装了很贵的警报器但贴了消音纸"。
- **`[删除]` 原报告"Redis 默认密码"条目**：经实测验证，`docker-compose.yml:338` 实际使用 `${MAOP_REDIS_PASSWORD:?Set MAOP_REDIS_PASSWORD environment variable}`，是 `:?` 强制变量语法（与 PG 一致），**无默认密码**。`docker-compose.prod.yml:346` 同样使用 `:?` 强制。原报告称有 `:-maop_dev` 默认值，此条严重失实。**Redis 密码策略已对齐 PG，此条已删除。**
- **`[补充]` Redis 密码策略确认**：`docker-compose.yml` 和 `docker-compose.prod.yml` 均使用 `:?` 强制环境变量，与 PG/Vault 策略一致，安全基线扎实。
- ⚠️ **Docker 生产镜像依赖锁定有洞**（`[实证]`）：`py/Dockerfile:28` 用 `requirements.lock`，注释声明"锁定版本、与 CI audit 一致"；但 lock 含可选 ML 依赖 `sentence-transformers==2.7.0`（lock:80），其传递依赖 **torch/scikit-learn 均未入锁**（lock 全 80 行，查证 0 条 torch/scikit-learn）→ 构建时联网浮动解析 ~GB 级依赖，既违背锁定意图又显著撑大镜像。
- ✅ 容器安全基线扎实（`[实证]`）：非 root、tini、`cap_drop: ALL`、`no-new-privileges`、资源限额、观测端口绑 127.0.0.1、生产环境 JWT_SECRET 缺失即拒启动（compose 注释与代码一致）、`MAOP_AUTH=1` 容器默认值。
- ✅ K8s Operator Helm Chart 存在且 appVersion=5.1.0 与全库版本统一。

## 六、安全（专项）

- ✅ `.env`/`.env.sandbox`/密钥/运行时 DB 被 `.gitignore` 完整覆盖（`git check-ignore` 实测通过）；全库无私钥入库（命中的 `-----BEGIN PRIVATE KEY-----` 均为 `guardrail.py:108`、`maop_verify.py:75` 的**检测正则**，非泄漏）。
- ✅ gitleaks + bandit(-ll 低危起步） + trivy + SBOM + pip-audit 全链路在 CI 里（当前因上游门禁挂掉被 skipped——又被第〇节连坐）。
- ✅ auth 关闭时降级为 guest 角色而非 admin（`security/auth.py:540-546`，fail-closed 设计正确）。

---

## 距离正式上线还有多少路？

**结论：底座已具备上线品相，但当前状态不满足项目自己定下的发布闸门。按项目自身的《发布前 checklist》（CHANGELOG 开头），第 1 条"CI 全绿"此刻不成立。**

| 优先级 | 事项 | 预估工作量 |
|---|---|---|
| **P0 阻塞（不修不能发）** | ① 修 mypy 5 错（核心是 `evolve_insights.py` 两处真 bug：`h.id`→`h.cycle_id`、`report_json` 字段缺失需补数据源或删功能；`heatmap` 重定义删旧声明；`engine.py` result 重定义改变量名；`loop_executor.py` 类型断言）；② 修 `create_task` 孤儿任务 ×2（存入模块级集合持有引用）；③ 推送修复并让 CI 全绿 | 0.5 人日 |
| **P1 发布前应清** | ④ 9 个 router 逐个添加 `handle_api_errors` 装饰器；⑤ `requirements.lock` 剥离/锁定 ML 传递依赖（或 Docker 用 `--no-deps` 显式装锁定集）；⑥ api-reference.md "全部端点"改为诚实口径或补全 | 1~2 人日 |
| **P2 本迭代内** | ⑦ 补 `.gitattributes`（行尾统一，根治 Windows 假脏）；⑧ quickstart Python 3.11 vs 3.10 口径统一；⑨ PRD 指标（依赖数 <15）与实际对齐或修订指标；⑩ ROADMAP 错别字；⑪ ESLint 2 warning（删除未使用的 `vi` import 和 `codeLang` 变量） | 0.5 人日 |
| **P3 下一周期** | ⑫ supervisor.py 等 9 个 30KB+ 二次拆分；⑬ Playwright 覆盖扩充；⑭ 生产 assert 替换 | 规划项 |

**总工期估算：修到"可发"约 1~2 人日 + 按项目自身规范要求的"1 个完整工作日 staging 验证"。**

**但必须直说的一点**：当前最大风险不在代码质量（代码质量在同类项目中是中上水平），而在**验证闭环的诚信度**——8 次 CI 全红期间，每条提交消息都写"测试 NNNN passed / ruff 0 / mypy 0"，其中 mypy 声称与 CI 实测直接矛盾，且昨天刚上线的新端点已被我实证带病。如果这些"验证记录"是 agent 工具自动生成而未经人工核对的，那么上线前需要补一道**人工守门**：以 CI 为准、不以提交消息为准。门禁的设计本身是好的，问题只在于"没人听它的"。

---

### 修正记录

| # | 原报告内容 | 修正后 | 修正理由 |
|---|-----------|--------|----------|
| 1 | "前端 lint 当前 2 error" | "0 error, 2 warning" | 实测 `npx eslint` 输出 0 error 2 warning；原报告提到的 afterEach/paraBuf 问题已不存在 |
| 2 | "全库扫描 59 处 `except: pass`" | "0 处" | 实测 Python 脚本精确搜索 0 处；ruff E722 全通过 |
| 3 | "Redis 默认密码 `:-maop_dev`" | "已用 `:?` 强制，无默认密码" | 实测 `docker-compose.yml:338` 使用 `${MAOP_REDIS_PASSWORD:?...}` |
| 4 | "10 个 router 未接入统一错误格式" | "9 个" | 实测 Python 脚本扫描 9 个；原列表含 `state.py`（实际已不在），遗漏 `n8n.py` |

### 诚实声明（未验证项）
- 7622 条测试**全量执行结果**未在本地实跑（耗时原因），采用 CI 证据 + 收集验证代替；鉴于近期提交声明与 CI 存在矛盾记录，该数字按 `[声明]` 对待。
- Docker 镜像实际构建、K8s 实际部署、k6/locust 压测数值、前端页面视觉效果：未验证。
- 27 个 `*_coverage*` 测试文件抽查了 2 个（均为真实断言），其余 25 个未逐一核对。
- CI 8 连红基于原报告声明，本地无 `gh` CLI 无法独立验证 GitHub Actions run 历史。

### 环境噪音备注（不计入项目问题，但影响你的日常工作流）
- 仓库**缺 `.gitattributes`**，叠加本机 `core.autocrlf=true`，导致 `git status` 显示近千文件"已修改"而 `git diff` 实际为空——这会让所有 Windows 贡献者的 diff/提交体验恶化，强烈建议 ⑦ 一并修。