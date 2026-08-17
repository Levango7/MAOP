# MAOP v5.1.0 修复计划书（Remediation Plan）

- **状态**: Approved
- **日期**: 2026-08-17
- **范围**: CI/CD、依赖安全、预算守卫、死代码、文档、部署、引擎行为、命名、产品定位
- **证据来源**:
  1. GitHub Actions API 实拉的最近 5 次 CI 运行记录（run 31994116189 及前 4 次，master 连续失败）
  2. 对 `py/requirements.lock` 本地实跑 pip-audit（去重后）
  3. `model/budget.py` 与 `core/budget_guard.py` 数据源源码级核验

---

## 0. 背景结论

v5.1.0 的测试、文档品类、CI 流水线设计均高于平均水准，但存在两类系统性问题：

1. **闭环未打通**：master 分支 CI 连续全红（Docker 登录失败 + pip-audit 失败），最近提交未经过自设质量门禁即推进。
2. **静默失效与失真**：预算守卫读一个已无人写入的账本（功能失效）；三份文档与代码现状脱节；约 1600 行被同名包遮蔽的死代码。

本计划不重写任何架构，全部沿代码中既有的注释意图与基础设施收口。

---

## 1. 问题清单与修复方案

### P0-1 CI Docker 构建失败：仓库凭据从未配置

**证据**：master 最近 5 次 CI 全部 `failure`；失败步骤 `Login to Container Registry`（job id 95284983292）。`.github/workflows/ci.yml` 的 docker job 在 `push` 事件下无条件执行登录，而 `REGISTRY_URL/USER/PASS` 三个 secrets 为空，登录必挂；连带 `container-scan` 因 `needs: docker` 被跳过。

**修复**：
```yaml
# docker job 顶层增加
env:
  REGISTRY_URL: ${{ secrets.REGISTRY_URL }}

# Login 步骤条件改为
if: github.event_name != 'pull_request' && env.REGISTRY_URL != ''

# build-push 步骤
push: ${{ github.event_name == 'push' && env.REGISTRY_URL != '' }}
```
同时 `container-scan` job 移除 `needs: docker`（该 job 本地 `docker build` 自有镜像，不依赖推送结果）。

**风险**：低。无 secrets 时变为"只构建不推送"，与现状等价但不再红。
**验收**：push master 后 CI 全绿；container-scan 独立产出 trivy 报告。

> **进度更新（2026-08-17）**：已由 c1eabdd 落地。注意：GitHub Actions 的 `if:` 表达式**禁止引用 secrets context**（5e3174b 曾因此 workflow 解析失败、0 job）。最终方案为"探测步骤 + steps context"：新增 `Check registry configured` 步骤（env 注入 `secrets.REGISTRY_URL` → 输出 `configured=true/false`），Login 的 `if:` 与 build 的 `push:` 均引用 `steps.reg.outputs.configured`。container-scan 的 `needs: docker` 处理见 P1-3 相关跟进。

### P0-2 依赖链：77 个已知漏洞 + lock 文件畸形

**证据**（pip-audit 实跑输出汇总）：

| 包 | 当前 | 修复版本 | 漏洞数 |
|---|---|---|---|
| aiohttp | 3.10.0 | ≥3.14.3 | ~40 |
| pyjwt | 2.8.0 | ≥2.13.0 | 9（认证核心，最急） |
| cryptography | 44.0 | ≥46.0.6（多条分散至 50.0） | 7 |
| starlette | 0.40.0 | ≥1.3.1（跨大版本） | 8 |
| python-multipart | 0.0.12 | ≥0.0.31 | 7 |
| lxml | 5.0.0 | ≥6.1.0 | 2 |
| idna | 3.10 | ≥3.15 | 2 |
| h11 | 0.14.0 | ≥0.16.0 | 1 |
| python-dotenv | 1.0.1 | ≥1.2.2 | 1 |
| pytest | 8.0 | ≥9.0.3 | 1 |

`requirements.lock` 另有结构缺陷：混用精确 pin 与版本范围（pip-audit 直接拒收范围项）；两条互相矛盾的重复项（`python-multipart` 0.0.9/0.0.12、`websockets` 12.0/13.1）。

**修复（两步走）**：
- **第 1 步（低风险）**：升 pyjwt、python-multipart、cryptography、lxml、idna、h11、python-dotenv 至修复版本；清除重复项。
- **第 2 步（独立 PR）**：aiohttp→3.14、starlette→1.3，fastapi 同步升至兼容 starlette 1.x 的版本；用 pip-tools 从 pyproject 重新生成真 lock（全精确 pin + hash）；`pip-audit -r requirements.lock` 保持硬门禁。

**风险**：第 2 步中高——starlette 1.x 中间件/异常行为有变化。**缓解**：独立 PR，跑全 12 平台矩阵 + Playwright E2E，重点回归登录 / 文件上传 / SSE 三条路径。
**验收**：`pip-audit` 退出码 0；lock 全精确 pin、无重复键。

> **进度更新（2026-08-17）**：已由 5e3174b 部分落地（fastapi 0.115.0→`>=0.141.1,<0.142`、starlette→`>=1.3.1`、cryptography→`<51`、python-dotenv→`>=1.2.2`，三文件对齐），本地 `pip-audit -r requirements.lock` 实测 **No known vulnerabilities found**。剩余项：aiohttp/pyjwt/python-multipart/lxml/idna/h11/pytest 在 lock 中均为范围约束、解析到最新版无漏洞；第 2 步"pip-tools 生成全 pin + hash lock"仍可独立立项。

### P0-3 BudgetGuard 双实现，准入拦截读死账本 ⚠️（本计划最严重项）

**证据**：
- `model/budget.py` BudgetGuard（**JSON 账本** `data/budget_ledger.json`）← `delegate/dispatcher.py:687` 的 `can_spend` 准入拦截用它；
- `core/budget_guard.py` BudgetGuard（**SQLite** `budget_daily` 表）← dashboard 预算页用它；
- `model/budget.py` docstring 自述：JSON 账本写入路径已被废弃（主循环改写 CostTracker SQLite）。

**结论**：预算准入拦截读的账本无任何写入方，预算守卫**实际永远放行**，对外宣称的核心治理功能静默失效。

**修复**：
1. **止血**：dispatcher 预算检查切换到 SQLite 实现（或直接用 CostTracker 聚合当日/当月花费对比限额）。
2. **observe 模式一天**：超预算只告警不拦截，确认阈值配置等价（两实现配置源不同：pydantic `BudgetConfig` vs SQLite `budget_config` 表）。
3. **开启硬拦截** + 删除 JSON 实现，`model/budget.BudgetGuard` 改为 deprecated 再导出。
4. **补契约测试**：写入一笔成本 → `can_spend` 必须读到；防止账本再次断写。

**风险**：中。拦截阈值可能因配置源切换而变化。**缓解**：切换前做配置默认值等价性对比测试。
**验收**：契约测试通过；构造超预算用例时 dispatch 返回 `exit_code=-6`。

### P0-4 死代码：被同名包遮蔽的 router

**证据**：`dashboard/routers/agents.py`（940 行）与 `routers/agents/` 包、`routers/info.py`（674 行）与 `routers/info/` 包并存。Python 导入规则包优先于模块，server.py 的 `from maop.dashboard.routers import agents` 实际导入包——两个 `.py` 文件永不被加载。

**修复**：删除两个 `.py` 文件；删除前 diff 其与包内实现的差异确认无孤儿逻辑；修正 `test_data_proxy_coverage.py` 中提及 `agents.py` 的过时注释。已核验：测试引用的 `routers.agents._deps` 属包内部，不受影响。

**风险**：低（删除前先 diff）。
**验收**：`import maop.dashboard.server` 正常，全量测试绿。

### P1-1 文档三处失真

| 失真点 | 现状 | 修复 |
|---|---|---|
| `docs/api-reference.md` | 标版本 4.3.0（代码 5.1.0） | 从 `/openapi.json` 半自动重新生成；CI 加 drift 检查（路由计数 vs 文档行数） |
| API Changelog v4.4.0 条目 | 宣称错误格式 `{"error":{code,message}}` | 改为实际的扁平 `ErrorSchema {status,error,code,detail,request_id}`（改文档不改代码） |
| `docs/design-system.md` | 描述已归档的 JS 仪表盘（色值/命名与 Vue 版冲突） | 加归档声明移入 `docs/archive/`，权威规范指向 `DESIGN_RULES.md` |

**风险**：低，纯文档 + 只读脚本。
**验收**：三份文档版本号一致；新贡献者照文档可跑通登录 + 一次调用。

### P1-2 SBOM 与 pip-audit 输入不一致

**证据**：SBOM job 用 `requirements.txt`（audit job 注释承认该文件含已移除依赖 etcd3）。
**修复**：SBOM 输入改为 P0-2 修复后的真 lock；`requirements.txt` 删除或降级为指引；同步 Makefile `install` 目标。
**风险**：低。**验收**：sbom.json 覆盖 lock 全部依赖。

### P1-3 deploy-staging 名不副实

**证据**：job 在 GH runner 上 `docker compose up` + curl，runner 销毁即消失——是冒烟测试不是部署。
**修复（推荐方案 A，立即执行）**：job 改名 `compose-smoke`，文档注明部署能力待建。方案 B（真 staging：SSH deploy 或 `deploy/k8s` manifests + kubectl apply + 健康检查回滚）待基础设施就绪后单独立项。
**风险**：A 无；B 涉及主机凭据管理，需配套 secret 轮换。
**验收**：A——job 名与行为一致。

### P2-1 Engine 无执行器时返回假成功（Breaking）

**证据**：`engine.py` AGENT/DAG 步骤未注入 `step_executor` 时返回 `status=SUCCESS` + 占位文本。
**修复**：改为返回 `FAILED` + `error="No step_executor configured"`，构造时打 warning。同步修正直接构造 `Engine()` 的测试文件（`test_engine.py` / `test_integration.py` / `reliability/test_pev_pipeline.py` 等 5 处），改为注入 mock executor。CHANGELOG 标 **Breaking**。
**风险**：中——依赖旧行为的下游会被打破，此即修复目的。
**验收**：未注入执行器执行 AGENT 步骤得到 FAILED；测试矩阵全绿。

### P2-2 evolve / evolution 同名双概念

**证据**：`/api/evolve/*`（演化分析建议）与 `/api/evolution/*`（AB 实验 + 部署晋升）并存。
**修复（不改 API 路径，避免 breaking）**：文件改名 `evolve.py → evolve_insights.py`、`evolution.py → evolution_experiment.py`；api-reference 与两个前端视图的 PageHeader 各写一段概念区分。
**风险**：低。**验收**：新人 5 分钟内分清两个 API 族职责。

### P2-3 产品命名分裂与定位文档缺失

**证据**：包名 MAOP，但 HLD/LLD 用 "Nexus 统一编排平台"；无目标用户/目标场景文档。
**修复**：① 拍板单一产品名（建议代码包名保持 `maop`，只统一文档口径，改包名成本极高无收益）；② 补 1–2 页《定位与目标场景》写入 README 首屏：核心场景（多 agent 成本管控 / 跨 agent 审计合规）、反场景（单任务直接用单个 CLI agent）。
**风险**：低。

### P2-4 type-check 形同虚设

**证据**：`package.json` 声明 `type-check: vue-tsc`，但 `src/` 唯一 `.ts` 是 `env.d.ts`，34k 行前端全 JS。
**修复**：方案 A（立即）移除该 script 停止声明 TS 能力；方案 B（排期末尾）开 `allowJs + checkJs` 渐进迁移，新文件强制 `.ts`。
**风险**：B 初期噪音大，需约定清零节奏。

---

## 2. 执行排期

| 冲刺 | 目标 | 包含项 | 预估 |
|---|---|---|---|
| **S1（本周）** | CI 回绿 + 止血 | P0-1、P0-2 第 1 步、P0-3 止血、P0-4、P1-2、P1-3A | 2–3 人天，全部低风险 |
| **S2（下周）** | 中风险攻坚 | P0-2 第 2 步（独立 PR）、P0-3 收敛+契约测试、P1-1 | 4–5 人天 |
| **S3（后续）** | 重构与定位 | P2-1、P2-2、P2-3、P2-4 | 按需 |

**顺序硬约束**：P0-3 的 observe 模式必须跨一个自然日再开硬拦截；P0-2 第 2 步必须晚于第 1 步合入（避免一次 PR 混入两类风险）。

## 3. 完成定义（Definition of Done）

- [ ] master 连续 3 次 CI 全绿（含 container-scan 独立产出）
- [ ] `pip-audit -r requirements.lock` 退出码 0，lock 全 pin 无重复
- [ ] 超预算构造用例被拦截（exit_code=-6），契约测试在库
- [ ] 全库 `grep -rn "budget_ledger.json"` 无生产读写路径残留
- [ ] 三份失真文档修正，openapi drift 检查进 CI
- [ ] CHANGELOG 记录 Engine Breaking 变更

---

## 附：执行日志

- **2026-08-17**：计划书入库 docs/remediation-plan-v5.1.0.md。P0-1 已由 c1eabdd 落地（探测步骤方案，规避 secrets-in-if 限制）；P0-2 第 1 步主体已由 5e3174b 落地（pip-audit 0 漏洞，本地验证通过）。P0-3 核验与止血进行中。
