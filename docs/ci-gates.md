# CI 门禁与触发面

> 面向改 MAOP 的人：哪些检查一定会跑、哪些会被跳过、以及**怎么分辨"跳过了"和"根本没跑"**。
> 最后更新：2026-10-06（publish 的 tag 触发由"注释认错"改为真打通、版本站点守卫补齐
> Dockerfile 与 package.json、部署工件纳入分类面、镜像 node 对齐）。

## 1. 触发模型

`MAOP CI`（`.github/workflows/ci.yml`）对**每个 PR 和每次 trunk push 都会启动**，然后由
一个无条件运行的作业 `CI scope (code vs docs-only)` 决定哪些重活跑：

```
scope ──┬─→ lint ─→ test(9 平台矩阵) ─→ audit / sbom
        │         └→ perf-smoke / migrations / sast
        ├─→ frontend ─→ e2e
        └─→ docs-gate（依赖 scope，但无条件跑）
secret-scan（无条件，永远跑）
docker / container-scan / compose-smoke（仅 trunk push，见 §4）
publish（仅 tag push `v*`；2026-10-06 才补上 tag 触发，见 §3 第 3 条）
```

- `docs-gate` 跑 `py/scripts/check_docs_consistency.py --gate`（README + docs/ 的路径存在性、
  模块计数、表格列数、**CHANGELOG 结构**——单一 Unreleased / SemVer 降序 / 合法版本号 /
  归档段双向豁免）。它**不跟着 `code=false` 跳过**，理由是这个检查的两类事故分别落在
  两侧：只改文档会写进不存在的路径，只删代码会让文档里的旧路径失效——两边都必须跑。
  脚本只用标准库、本机约 3 秒，不属"重活"。判定范围由 `docs/README.md` 第 1–6 章
  （"当前权威文档"）决定，历史快照/设计文档要在文件头写理由才豁免，脚本会逐条打印。

- 同一 job 另跑两条 ADR-023 门禁（2026-10-12 复评处置，纯 stdlib、不新增 job 名）：
  - `py/scripts/check_silent_exceptions.py`：静默 `except … pass/continue/...` 必须有
    `# silent-ok: <闭集类别> — <理由>`（或按 ADR-018 记日志）；存量 81 条+47 处
    `contextlib.suppress` 进基线，**双向棘轮**（新增红、基线条目失效也红，只许减）。
  - `py/scripts/check_file_size.py`：单文件 800 行上限，`migrations/` 整体豁免，
    存量 7 个文件 allowlist 带拆分排期、双向一致（拆完必须移除条目）。
  两侧的守卫在 `py/tests/test_quality_gates.py`（变异自证 + 双向一致性 + ci.yml 接线断言）。

- `scope` 把变更集交给 `py/scripts/ci_path_scope.py` 分类，输出 `code=true|false`。
- `code=false`（docs-only）时，`lint` 与 `frontend` 及它们的下游作业被**跳过**（skipped）。
- 分类面 = `py/`、`config/`、`dashboard/`、`dashboard-enterprise/`、`.github/workflows/`、
  `deploy/`、`monitoring/`、`alertmanager/`、compose 两件、`.dockerignore`，外加根文件
  `README.md` / `ROADMAP.md` / `py/Dockerfile` / `py/requirements.lock|txt` /
  `nginx.conf` / `nginx.prod.conf` / `alertmanager.yml`。
  其中 `deploy/`、`alertmanager/`、`monitoring/` 与三个根 conf 是 2026-09-29 补进来的
  （原 `on.*.paths` 白名单漏了它们）：`deploy/` 与 `alertmanager.*` 有测试直接读
  （Helm chart、otel/grafana 配置、告警模板），`monitoring/` 与 nginx conf 是 compose
  栈的挂载源 —— 都属"改了会影响 CI 结论"，不是 docs。
- **拿不准时一律按"跑全量"处理**（基线算不出、变更集为空 → `code=true`）。宁可多跑，不可静默少跑。
- push 腿解析 base 时**不回退 `head~1`**：新分支首推可能带多个提交，只看最后一个会把
  "前面有代码改动"的 push 误判成 docs-only（2026-09-29 移除该回退，宁走"基线未知 → 全量"）。

## 2. 为什么不用 `on.pull_request.paths` 白名单

白名单不匹配时**整个 workflow 不启动**，于是一条 check 都不会产生。后果不是"少跑一点"，而是
**"CI 全绿"与"CI 没跑"在 API 上完全同形**：`pending=0 && fail=0` 对两者同时成立。
本仓真的据此合掉过一个只改 `CHANGELOG.md` 的 PR。job 级判定把"没跑"变成看得见的 `skipped`，
并且 `scope` / `secret-scan` 永远在，所以永远不会有"零 check 的绿"。

这条约束不是靠约定维持的，由 `py/tests/test_ci_path_scope.py` 里 4 条结构断言钉住：
PR 触发器不许再出现 `paths`、`scope` 必须是无条件根作业、`lint`/`frontend` 必须依赖 `scope`、
必须至少存在一条无条件作业。

## 3. 判读结果时的三个坑

1. **注册数**：刚 push 完 `check-runs` 可能是空的，`pending=0` 是**假绿**。判"跑完"要同时要求
   注册数达到预期（PR 腿 ≥18，master push 腿 ≥22）。
2. **`Container Scan (trivy)` 红不一定是漏洞**：镜像构建走第三方 PyPI 源，偶发返回空候选集会报
   `... (from versions: none)`；同一 commit 的 `Docker build` / `Compose Smoke` 若都绿，
   单独重跑即恢复。已给该层加 3 次退避重试。
3. **`Publish to PyPI`：触发面 2026-10-06 才打通，此前恒不产生**。该 job 的条件是
   `refs/tags/v*`，而 `on.push` 原先只声明 branches（GitHub 的 branches 过滤会排除所有 tag
   push）—— 于是它连一条 check 都不创建，"发布管线已就位"是假的。现已补 `tags: ['v*']`，
   并确认依赖链（scope→lint→test / frontend / e2e）在 tag push 上都会真跑：tag push 拿不到
   diff 基线，`ci_path_scope` 按"空变更集 = 全量"的保守分支判 `code=true`。
   守卫：`py/tests/test_ci_publish_reachable.py`（含"删掉 tags 触发就变红"的反向对照），
   发布前还有 `py/scripts/check_release_tag.py` 核对 tag 与 `__version__`。
   **仍缺的是外部前置**：在 PyPI 项目 `maop-orchestrator` 把本仓库登记为 trusted publisher
   （Owner=Levango7 / Repository=MAOP / Workflow=ci.yml，该 job 走 OIDC、无 token secret）。
   未配置前第一条真 tag 会在 `Publish maop` 步骤 401/403 红 —— 该红就该红。
   实测口径：PyPI 上 `maop-orchestrator` / `maop-enterprise` / `maos` 目前均 404
   （`maop` 这个名字是他人的 0.0.0 占位包），所以不要对外称"已可 pip install"。

## 4. 容器作业的特别提示（改 `py/Dockerfile` 前必读）

`docker` / `container-scan` / `compose-smoke` 只带 `if: github.event_name == 'push' && ref == main|master|develop`
一类的条件，**PR 腿上看不到它们**；而且它们在 `needs: [test, frontend, e2e]` 下游，
矩阵没跑完之前连 check 都不会创建。所以：**改 Dockerfile 拿不到 PR 级 CI 证明**，
请在本地用同一基础镜像把那条 `RUN` 单独跑一遍再提 PR，别把验证交给不会执行的 gate。

镜像内的前端构建（`frontend-builder` 阶段的 `npm ci` + `npm run build`）**是 CI 里唯一执行
生产前端构建的地方** —— 前端 job 只跑 lint/typecheck/test/coverage，从不跑 `vite build`。
所以 2026-09-29 把 `NODE_IMAGE` 默认值从 `node:20-alpine` 对齐到 `node:24-alpine`
（与 `env.NODE_VERSION` 一致）：镜内 `npm ci` 读的是同一个 lockfile，jsdom@30.1.1 的
engines 是 `^22.22.2 || ^24.15.0 || >=26.0.0`，node 20 不在区间内。
本地验证方式（已实测通过，node v24.21.0，`✓ built in 29.62s`）：
`docker build -f py/Dockerfile --target frontend-builder --build-arg NODE_IMAGE=node:24-alpine -t maop-fe-smoke .`

## 5. 前端覆盖率门禁

`Frontend Build` 里有显式一步 `Frontend coverage gate`（`npm run test:coverage`）。
阈值是 2026-09-27 按**实测值留 0.5pp 余量**定的下限（statements 60.5 / branches 45.5 /
functions 55.5 / lines 63.5），**只许往上抬**；要降必须连带改
`py/tests/test_frontend_coverage_gate.py` 里的 `FLOORS` 并说明理由。
它同时兼作"测试是否都跑了"的哨兵：文件漏跑必然把总数打穿下限（历史上冷启动 worker
起不来时，一次只跑了 25/56 个文件）。

## 6. 守卫清单（谁钉住什么）

| 测试 | 钉住的东西 |
|------|---|
| `py/tests/test_ci_path_scope.py` | 变更集分类 + 触发面结构（白名单不许回来） |
| `py/tests/test_ci_workflow_hygiene.py` | 不许有吞自身失败的步骤（`fail_ci_if_error: false`、未登记的 `continue-on-error`） |
| `py/tests/test_frontend_coverage_gate.py` | 覆盖率门禁在 CI 里、阈值不低于实测、禁全局忽略未处理错误 |
| `py/tests/test_dependabot_config.py` | dependabot 配置形状 + 同前缀 ≥2 个包的 action 必须成批 |
| `py/tests/test_requirements_lock_sync.py` | `requirements.lock` 的直依赖段必须逐条镜像 `pyproject.toml` |
| `py/tests/test_docs_consistency_gate.py` | 文档一致性门禁：范围只来自索引当前章节、豁免必须写理由、注入死路径必须判红、`docs-gate` 作业不许挂 `if` |
| `py/tests/test_ci_required_checks.py` | required 清单（`.github/ci-required-checks.json`）↔ `ci.yml` 作业形状 ↔ §7.2 散文 三方一致：不许改名/删作业导致上下文永不上报，不许 required 作业在 PR 上恒不产出（沿 needs 链递归查），不许矩阵名当 required |
| `py/tests/test_ci_merge_gate.py` | 聚合守卫的两面：判定脚本的策略单测（白名单式 + fail closed + docs-only 的 skipped 必须放行），以及 ci.yml 里 gate 的形状（name 稳定、`if: always()` 不许掉、needs 盖住全部重活、不许纳 push-only 作业） |
| `py/tests/test_nightly_flaky_coverage.py` | nightly `flaky-detection` 的**覆盖面**：必须覆盖实测出过问题的平台（macos/3.13、windows/3.12）、每条腿 `--reruns=0`、复刻 ci.yml 的并发配置（Linux/macOS `-n 2`、Windows `-n 0`）、矩阵值渲染后是合法 bash 且不留占位符、每条腿都得是 ci.yml 真跑过的组合；另有 `TestSlowMarkerCoverage` 核对**每个含 `slow` 的文件都被某条腿真跑到**（见 §9） |
| `py/tests/test_env_example_drift.py` | `.env.example` ↔ 代码实读的 `MAOP_*` 变量双向一致（两条来源都要算：`os.getenv` 直读 + `MAOPSettings` 的 `env_prefix`/`AliasChoices` 映射）；反向不许留下无说明的"没人认的开关"；企业版变量走 `ENTERPRISE_SIDE_VARS` 逐条登记 |


## 7. master 的 required checks：已实测语义 + 现行配置

触发面改成 job 级之后，"哪些 check 在什么条件下存在"有了确定性，才敢给 master 加保护。
选 required 作业的规则只有一条：**被设为 required 的作业，必须在它想拦的那类 PR 上真的存在。**

### 7.1 实测结论（2026-09-29，本 PR 为夹具）

做法：临时给 master 加保护，每轮换一组 required 上下文，读本 PR 的 `mergeable_state`；
其中"不存在的上下文"是**正对照**，用来证明这组判定确实在算而不是配置空转。

| 轮 | required 上下文 | #53 判定 |
|----|---|---|
| 基线 | 无 | `clean` |
| A | `CI scope (code vs docs-only)`（success） | `clean` |
| B | A + `exp53-nonexistent-required-check`（永不上报） | `blocked`（8 次采样稳定） |
| C | A + `Lint (ruff + mypy)`（**skipped**） | `clean`（7 次采样一致） |
| 撤除 | 无 | `clean` |

由此确定的三条语义：

1. **上报为 `skipped` 的检查满足 required**（job 被 `if:` 跳过时 Actions 仍会产出一条
   check run，status `completed` 而 conclusion `skipped`）。所以把 docs-only 时会跳过的重活设为 required，
   不会卡住文档 PR，却能在代码 PR 上真拦一道。
2. **从未上报的上下文不满足 required**，会把 PR 永久卡在 `blocked`。B 轮就是这条的证据。
3. 保护对 **admin 不生效但判定照常计算**：`enforce_admins: false` 时 owner 仍能直推 master，
   而 `mergeable_state` 依然按 required 集合算。早前"保护形同虚设"的观察属于这条，
   不是配置无效 —— B 轮 8 秒内翻成 `blocked` 证明配置是活的。

### 7.2 现行配置（已生效，非待合并态）

下列 4 条就是 GitHub 分支保护**当前生效**的集合——2026-10-13 实测
`gh api repos/Levango7/MAOP/branches/master/protection` 与本小节逐字一致。原标题写的
"本 PR 合并时生效"是错的：合并不改分支保护，同步是 §7.4 那个手工动作，早已执行完毕。
带外配置仓库内看不见，所以 §7.6 之后补了 nightly 的
`Required checks drift (manifest vs GitHub)` 作业（三态 MATCH / DRIFT / UNVERIFIED）。
⚠️ **但截至 2026-10-13 这条作业处于 UNVERIFIED**：nightly run `38224542974` 的注解是
`未配置 secret REQUIRED_CHECKS_TOKEN`，脚本在没有 token 时只发 warning 并以 0 退出，
作业照常绿。也就是说**带外漂移现在实际没人守**——"仓库内三方对账 + nightly 全绿"
并不等于 GitHub 侧一致，本小节能对上的唯一原因是我手工跑了 `gh api`。
"给默认 `GITHUB_TOKEN` 开 `administration: read`"这条捷径**已实测不存在**：workflow 的
`permissions:` 块不接受 `administration` 这个键（GitHub 返 422，那条 run 连作业都没生成，
见 run `38287020335`），而默认 token 直接读分支保护返回 **403 Resource not accessible by
integration**（run `38287261844`，反例读 secrets 同样 403，证明不是"其实都读得到"）。
所以要让它真跑起来只有一条路：**配一个只读 PAT 到 `REQUIRED_CHECKS_TOKEN`**。
在那之前，这条守卫的绿应当被读作 UNVERIFIED，而不是"一致"。
不建 PAT 时的替代路径见 §7.7：PR 侧金丝雀（默认 token）+ 本机定时对账（`gh auth token`）。

**唯一权威清单是 `.github/ci-required-checks.json`**（机器可读，含每条对应的 workflow/作业/
kind），由 `py/tests/test_ci_required_checks.py` 与 `ci.yml` 的作业形状、以及本小节散文
**三方互相核对**。改保护时：先改清单 → 再按 §7.4 把 GitHub 侧同步 → 守卫用例会在改名、
删除、或把 required 作业改成"PR 上不产出"时判红。

required 上下文 4 条，其余保护项全关：

- `CI scope (code vs docs-only)`、`Secret Scan (gitleaks)`、`Docs consistency gate`
  —— 永远存在，任何 PR 都跑。
- `CI merge gate` —— `if: always()` 的聚合作业：任何 PR 都会产出这一条 check，它把上游
  lint / 9 平台 pytest / 前端 / e2e / 迁移 / pip-audit / bandit / SBOM / perf-smoke /
  lock-drift / **wheel 安装冒烟**（`wheel-install`：把构建出的 wheel 全依赖装进干净 venv 再断言，
  覆盖 editable 结构上看不见的"少打包一个模块 / 包元数据有误"）的结论
  折进自己的红绿（判定脚本 `py/scripts/ci_merge_gate.py`，规则见 §7.3 末段）。
  这一条替代了过去单独把 lint、前端设为 required 的做法，覆盖面更大。
- `strict: false`（不要求分支领先，避免每次主干合并把在跑的 PR 全部判过期）、
  `enforce_admins: false`（保留 owner 逃生门；并行会话的直推路径不受影响）、
  无 review 门槛、不要求 linear history / conversation resolution。

### 7.3 required 的取舍：谁进、谁不进、为什么

- **矩阵作业不能按名字进 required**：docs-only 时那条 skipped check 的**名字是未展开的字面量**
  `pytest (${{ matrix.os }}, Python ${{ matrix.python-version }})`（实测），而代码 PR 上是
  9 条展开后的名字。按精确名设 → docs-only 永远 `blocked`；按通配设则代码面匹配行为尚无实测
  证据。正解就是现在这条聚合作业：一条 name 稳定的 check 覆盖整个矩阵，不必折腾上下文匹配。
- **聚合作业的判定规则**（`ci_merge_gate.py`，白名单式、fail closed）：
  ① 任何上游结论不是 `success` 且不是（在 scope 明确判 `code=false` 时的）`skipped` → 红；
  ② scope 判 `code=true` 却有该跑的作业是 `skipped` → 红（"分类说改了代码但门禁没跑"正是最该拦的）；
  ③ scope 没输出、needs 为空、缺 `result` 字段、出现未知的新状态 → 一律红。
  它**必须**挂 `if: always()`：否则上游一红它自己就不产出，required 变成"从未上报"，
  所有 PR 永久卡在 `blocked`（§7.1 的 B 轮语义）。这条由结构守卫钉住，不许改坏。
  - 规则②的**例外**（2026-10-04 修）：同轮里已存在硬失败时，`skipped` 是 `needs:` 链上的
    **级联跳过**，不再列为不通过原因，改由 `cascade_skips()` 输出 `::notice::`。
    起因是 run 476：`test` 失败后 `needs: test` 的 `audit`/`sbom` 被连同报成"该跑的作业没跑"，
    注解里真正的红点反被淹没 —— **判定没错但归错了因**。有硬失败时红点必然已由那个失败给出，
    所以这不改变红绿，只修措辞。
- **`Lint (ruff + mypy)` 与 `Frontend Build` 不再单列 required**：它们已在聚合作业的 needs 里，
  失败或被跳过都会把 `CI merge gate` 判红。单列只是把同一件事说两遍。
- **只在 trunk push 才存在的作业**：`Docker build` / `Container Scan (trivy)` /
  `Compose Smoke` / `Publish to PyPI`。它们在 PR 上要么根本不产生、要么恒为 skipped，
  设成 required 等于"永远空满足"，是假门禁；也**不**把它们放进聚合作业的 needs ——
  否则严格面下它们在 PR 上永远是 skipped，会把 gate 恒判红。容器面仍靠主干 push 验证。

### 7.4 复测 / 撤销

改配置前先复测语义（平台会变）：`PUT /repos/Levango7/MAOP/branches/master/protection`
带 `required_status_checks.contexts`，读任一 PR 的 `mergeable_state`；**测完立刻
`DELETE` 同一路径**。逃生门：`DELETE .../protection` 一键清空，或临时
`POST .../protection/enforce_admins` 的逆操作。

复测要带**正对照**：只放一条永不上报的上下文，确认判定真的会翻成 `blocked`。少了这一步，
"配置没生效"和"语义宽松"会长得一模一样（§7.1 的 B 轮就是干这个的）。

### 7.5 docs-only 合入主干：#52 前后各跑什么（一次误判的证伪记录）

有人看到"主干某次 push 只跑了 18 秒、13 条 skipped"就判定 job 级 scope 把主干兜底验证裁掉了。
两条实测把它证伪，省得重复争：

1. **改动前更糟，不是更好**：`#52` 之前 `ci.yml` 的 `on.push.paths` 与 `on.pull_request.paths`
   是同一份白名单，其中**不含** `docs/**` 与 `CHANGELOG.md` —— 只改文档合入主干时整个 workflow
   **不触发、一条 check 都不产生**。现在至少有 3 条真跑（`CI scope (code vs docs-only)`、
   `Secret Scan (gitleaks)`、`Docs consistency gate`），其余是**看得见的** skipped。
   覆盖面不变，可见性严格变好。
2. **分类器没有漏判**：把旧白名单的每一项喂给发布版 `py/scripts/ci_path_scope.py`，
   `py/`、`config/`、`dashboard/`、`dashboard-enterprise/`、`.github/workflows/`、`README.md`、
   `ROADMAP.md`、`py/Dockerfile`、`.dockerignore`、`docker-compose.yml`、
   `docker-compose.prod.yml` 共 16 条路径**全部判 code=true、零漏判**；而分类器另外把
   `deploy/`、`monitoring/`、`alertmanager/`、`nginx*.conf` 也算代码（旧白名单没有）⇒ 净增覆盖。
   空变更集按 code=true 处理，方向是保守的。

方法论一条：**比较两次 run 的时长必须同内容类型**。docs-only 的快跑看着就像"测试被裁"；
要判"某次改动是否丢了覆盖率"，正解是拿新旧两套判定面**各自回放同一批输入**，
而不是看单次 run 的结果。

### 7.6 一次真实漂移：清单说 4 条，GitHub 上是 5 条（2026-10-01 已修）

§7.2 那套"清单 ↔ ci.yml ↔ 文档散文"三方对账有个**结构性盲区**：三方**全在仓库里**，
而 GitHub 分支保护是**带外配置**（Settings 里点的）。仓库里改任何东西都看不见它。

2026-10-01 实测抓到一次真实漂移：

| 来源 | required 上下文 |
|---|---|
| `.github/ci-required-checks.json` | 4 条，含 `CI merge gate` |
| 本文 §7.2 散文 | 同样 4 条，明写"Lint / Frontend Build 不再单列" |
| **GitHub 实际配置** | **5 条：`Lint` + `Frontend Build` 单列，且没有 `CI merge gate`** |

即 PR #56 设计的"一条 gate 覆盖 lint + 9 平台 pytest + 前端 + e2e + 迁移 + 审计"**从未在
平台侧生效**。真正在拦的仍只是 lint 与前端构建；三方守卫对此**结构性地看不见**——它们
互相一致，漂移在第四处。

**为什么三方守卫抓不到**：`test_ci_required_checks.py` 只能读仓库文件；GitHub 那份配置
不在仓库里，任何仓库内测试都够不着它。这不是"漏写了一条断言"，是覆盖面本身的边界。

**修法**（两步）：

1. 按 §7.4 把 GitHub 侧同步为清单的 4 条（2026-10-01 执行；同步前先用真实 `needs`
   输入跑过 `ci_merge_gate.py` 三种情形，确认 docs-only 绿、code+failure 红、fail-closed 生效）。
2. 补上第四处对账：`py/scripts/check_required_checks_drift.py` + `nightly.yml` 的
   `required-checks-drift` 作业 + `py/tests/test_ci_required_checks_drift.py`（13 例）。

**这个守卫刻意三态，而不是"绿/红"两态**：

| 结论 | 退出码 | 含义 |
|---|---|---|
| `MATCH` | 0 | 实况与清单逐条一致 |
| `DRIFT` | 1 | 不一致，并**点名**多出/缺失的具体条目 |
| `UNVERIFIED` | 3 | **读不到实况**（无 token / 无 admin 作用域 / 网络失败） |

`UNVERIFIED` 必须与 `MATCH` 分开：读分支保护要 admin 作用域，默认 `GITHUB_TOKEN` 没有
（必然 403）。**若把"验不到"判成通过，这个守卫就会变成本仓最鄙视的那种"永远绿的门禁"
——比没有门禁更坏，因为它让人以为门禁在。** 脚本读到失败会打 `::warning` 并明说
"本次没能读到实况，不等于一致"；nightly 在缺 `REQUIRED_CHECKS_TOKEN` secret 时同样
**显式说明并跳过**，不静默变绿。

启用真对账：建一个 PAT（`read:repo` 即可）存为仓库 secret `REQUIRED_CHECKS_TOKEN`。
在此之前 nightly 会把实况打印出来供人工核对。

本地自查（已 `gh auth login`）::

    cd py && python scripts/check_required_checks_drift.py
    # 退出码 0/1/3 见上表；3 = 没 token，不是"一致"

### 7.7 PR 侧金丝雀：用默认 token 读"平台到底在拦什么"（2026-10-13）

§7.6 那条 nightly 对账能读实况，但要 admin PAT；而本项目**不建 PAT**（见 §7.2 的
`REQUIRED_CHECKS_TOKEN` 说明），于是带外漂移实际长期无人守。2026-10-13 把可行性又摸了一遍，
结论是：**默认 `GITHUB_TOKEN` 读不到"平台要求什么"，但读得到"平台认为这 PR 能不能合"。**
把这两件事分开，就有了一条不需要任何 secret 的 PR 侧金丝雀。

**读不到的**（都实测过，别再试）：`StatusCheckRollupContext.isRequired` 字段虽在 schema 里，
但解析器要一个 pull request ID —— 在 PR 级 / commit 级、merged / open 各态、admin 与默认 token
下**一律**返回 `A pull request ID or pull request number is required.` ⇒ 不可用；
`GET /branches/master/protection` 用默认 token 是 **403**；给 workflow 的 `permissions:` 加
`administration: read` 会让整个 workflow 失效（0 作业），`metadata: read` 同样（均为 2026-10-13 实测）。

**读得到的**：PR 级 GraphQL 的 `mergeStateStatus`（`CLEAN` / `UNSTABLE` / `BLOCKED` / …）与
`statusCheckRollup.contexts`（每条 check 的名字 + 结论）。默认 token 加
`pull-requests: read` + `checks: read` 即可，**不需要 admin**。

于是新增作业 **`Required checks satisfiability canary`**（`ci.yml`，**非 required** 的诊断作业）。
它 `needs: [scope, secret-scan, docs-gate, gate]`（覆盖清单全部 4 条）后再读 PR 汇总，判定：

| 情形 | 结论 | 退出码 |
|---|---|---|
| 清单 4 条全部 success/skipped，且 `mergeStateStatus` 可合 | `MATCH` | 0 |
| 清单里某条**在汇总里根本没出现**（作业被改名/删除）且平台判 `BLOCKED` | `DRIFT` | 1 |
| 清单 4 条全绿、平台却仍判 `BLOCKED`（存在清单未列出的 required） | `DRIFT` | 1 |
| 清单内某条自己没过/未完成，或 PR 是草稿 | `NOT-APPLICABLE`（不表态） | 0 |
| **归因不成立**：本轮 run 的清单作业没全 success，或本轮 head 已不是 PR 现 head | `NOT-APPLICABLE`（弃权） | 0 |
| 读不到实况 / `mergeStateStatus=UNKNOWN` 且重试仍未知 | `UNVERIFIED` | 3 |
| `NEEDS_JSON` / `PR_HEAD_SHA` 缺失或坏 JSON（接线本身断了，归因无从做起） | `UNVERIFIED` | 3 |

要点四条：

1. **`UNVERIFIED` 非零退出**：与 §7.4 / §7.6 同一条原则 —— "验不到"绝不判绿。金丝雀读不到
   汇总时作业真红，而不是静默通过。
2. **非 required 是刻意选择**：金丝雀自己在跑时的 pending 会让 `mergeStateStatus` 变
   `BLOCKED`（required 未满足）。把它设成 required ⇒ 它永远读到自己造成的 `BLOCKED`，恒假红。
   它作为诊断作业的价值在于：一旦"平台要求得比清单多"，PR **本就被平台拦着**，金丝雀的红
   只是把病因（哪条 required 缺席 / 存在清单外条目）点出来。
3. **方向性局限**：非 admin 只能证明"平台要求得**多**"，**证不了"要求得**少**"**（保护被放松
   或删除）。后者是更危险的静默方向，**本作业看不见**。
4. **判定前必须先归因（2026-10-15 的一次真实假红换来的）**：`statusCheckRollup` 是跟着 PR 的
   **现 head** 走的，而金丝雀是在**某一个 workflow run** 里执行的 —— 两者可以错位。PR #110 的
   旧 head `cb67725`（run `38729427212`）被同 ref 的新 push 抢占取消后，`Docs consistency gate`
   结论 `cancelled`、`CI merge gate` 结论 `failure`，而带 `if: always()` 的金丝雀 3.5 小时后
   （18:56Z）仍被调度起来：它读到的是"新 head 上一条 check 都还没产出"的汇总 ⇒ 四条 required
   全 `<缺席>` + `BLOCKED` ⇒ 判出 `DRIFT: 平台侧的 required 与仓库清单不符（4 处）`。同一个 PR
   四分钟后在真 head（run 632）上判的是 `MATCH`。**这条红是假的，但机制是真的**：清单与保护
   真脱节时也是"缺席 + BLOCKED"，两种成因在汇总里长得一模一样。
   因此判定前加了两道**独立**归因（`py/scripts/check_required_checks_satisfiability.py` 的
   `attribution()`）：① `${{ toJSON(needs) }}` —— 本轮 run 里清单作业没全 success，就没资格
   说"缺席是平台造成的"；② `pullRequest.headRefOid` 对比本轮 run 的 head sha —— 不等即过期 run，
   弃权。两道任一为空（env 没接上）判 `UNVERIFIED` 而不是静默退回旧行为。
   `py/tests/test_required_checks_satisfiability.py` 里同时钉住**两个方向**：过期 run 的复刻用例
   必须不出现 `DRIFT`，而"needs 全成功 + head 未变 + 一条 required 缺席 + BLOCKED"必须**仍是**
   `DRIFT` —— 否则这道守卫就只是一个把金丝雀永久变成弃权的新式自我失明。

**证不了的方向由带外对账兜底**（第二条线，工具已入库 `tools/drift-audit/`）：
`bash tools/drift-audit/run.sh` 用调用者自己的凭据（`gh auth token`，`repo` 作用域就够读分支保护；
**不要求新建 PAT，也不要求仓库配 secret**）跑 §7.6 的 `check_required_checks_drift.py` 做逐条
**双向**对账，退出码 0/1/3 连同结论落进日志。默认就地取当前检出的清单；把检出固定在 master 上、
每轮 `fetch + --ff-only` 再跑（`MAOP_DRIFT_UPDATE=1`）是推荐姿势。它**不**碰你的工作树，除非你显式
要求 —— 脚本不假设自己有权改别人的树。

日志除了三态结论，还有一道**缺勤可见性**自检：漏跑的那些轮次不会在日志里留任何痕迹，而这份日志
是该守卫对外的唯一凭据，所以每轮开头比较"距上一轮多久"，超过 `MAOP_DRIFT_GAP_H`（默认 36h）就补
一行 `GAP(...)`；`GAP` 还细分 `NO-PRIOR-RUN`（没历史/日志被删）、`PARSE-FAILED`（时间戳解不开）、
`SCAN-FAILED`（扫描工具本身缺失）三态 —— 因为"我扫不动"和"确实没历史"是两个结论，混起来会让缺勤
重新变隐形。`bash tools/drift-audit/selftest.sh`（11 探针）把这三态连同新鲜时的**负对照**一起钉住。

它与金丝雀互补：金丝雀只在 PR 上跑，覆盖"清单外的 required"与"required 从未上报"；带外对账覆盖
仓库外的**双向**漂移，含"保护被删"。两者都刻意区分"不一致"与"验不到" —— 任何一处的绿都不代表
另一处也绿。

## 8. nightly 的 flaky 检测：覆盖面本身也要被钉住（2026-10-04）

`Flaky Test Detection` 自称"暴露时好时坏的不稳定测试"，但原实现只在
**ubuntu-latest + 3.13** 上串行跑 3 遍。而实测出过问题的两家凶
（`tests/test_tool_manager.py::TestCallSyncFallback`）出现在
**macos-latest/3.13** 与 **windows-latest/3.12** —— 探针采样平台与症状出现平台
**不相交**，结构上就不可能红。这是本仓反复出现的那一类"假门禁"，只是这次伪装成
"我们已经在做 nightly flaky 检测了"。

修正后的覆盖（由 `py/tests/test_nightly_flaky_coverage.py` 钉住）：

| 腿 | 重复次数 | 并发 | 依据 |
|---|---|---|---|
| ubuntu-26.04 / 3.13 | 3 | `-n 2` | 基线腿（2026-10-09 随 ci.yml 由 `ubuntu-latest` 显式化） |
| macos-latest / 3.13 | 3 | `-n 2` | 症状出现过的平台（run 36792737281） |
| windows-latest / 3.12 | 2 | `-n 0` | 症状出现过的平台；CI 在 Windows 上因 xdist 竞态本就走串行 |

另加一步 `-m "not slow and serial" -n 0` 的单次运行：ci.yml 把用例拆成
`not slow and not serial` 与 `not slow and serial` 两步，而原 flaky 作业只跑了前者，
`serial` 那一族（共享全局状态 / 固定端口）从没在无重试条件下被观察过。

两条硬约束：

1. **全程 `--reruns=0`**。ci.yml 的 pytest 腿带 `--reruns=3`，会把 flaky 重试成绿；
   只有关掉重试，"时好时坏"才可见。守卫对每条腿都断言这一点。
2. **矩阵值必须真的插进脚本**。守卫把每条腿渲染一遍，断言没有残留 `${{ }}` 占位符，
   并对渲染结果跑 `bash -n` —— 插错位置（例如把 `-n 2` 塞进引号内）会让整条腿静默
   跑成别的东西，属于"看不见的失效"。

超时按实测定：run 477 的同一套 CI 腿为 ubuntu 6min / macos 3min / windows-3.12 14min
（且那是**含** `--reruns=3` 的耗时），最慢腿跑 2 遍约 28min ⇒ `timeout-minutes: 60`。

**仍未覆盖**：Windows 只跑 2 遍而不是 3 遍，是为了把墙钟压在 1 小时内；若日后再次
出现 Windows 侧的长尾 flaky，优先调这条腿的次数而不是放宽整个矩阵。

## 9. `slow` 标记那一族：曾经**没有任何一条腿跑它**（2026-10-06）

起因是一次口径核对：本机全量 collect 是 10339 条，而 CI 主腿只有 8766 个结果。
差额里有一类是真漏 —— 所有腿的标记表达式都排除 `slow`：

| 腿 | 标记表达式 | 对 slow 的处理 |
|---|---|---|
| ci.yml `test` 主腿 | `not slow and not serial` | 排除 |
| ci.yml `test` serial 步 | `not slow and serial` | 排除 |
| nightly `flaky-detection` | `not slow …` | 排除 |
| ci.yml `perf-smoke` | `tests/performance/ -m slow` | 只跑 performance 那 25 条 |
| ci.yml `perf-smoke` | `tests/reliability/ tests/stability/`（无 `-m`） | 顺带跑到 |

于是根级的 **47 条 slow 用例**（`test_ldap_real_env.py` 21、`test_stress.py 14`、
`test_k8s_operator.py 12`）在 CI 与 nightly 上**一次都不执行** —— 它们坏了主干照样全绿。
其中 12 条还是 2026-10 刚从弃用分支捞回来的 k8s 静态一致性用例：捞回来却没接上执行面，
等于白捞。

处理：nightly 新增 `Slow-marker suite` 作业，跑整个 `py/tests/ -m slow`
（`-n 0` 串行、`--timeout=300`、`--reruns=0`）。本机实测 76 条：**57 passed / 22 skipped /
0 failed / 2 分 44 秒**（skip 的那批需要真 LDAP 服务），所以放 nightly 不占 PR 时间，
也不写 `|| true`（本仓禁止吞失败的门禁，§6）。

> 上面这组数字是**进度型**的，截至 `684c15a`（2026-10-06）。现在要重测：
> `cd py && MAOP_ENV=test python -m pytest tests/ -q -m slow --timeout=300 -n 0 --no-cov`，
> 或只看族的大小 `python -m pytest tests/ -q --collect-only -m slow`。
> 各文件的条数同理（`-m slow --collect-only` 按文件分组数）。
> 这一族的**存在性**由守卫保证，数字变了不代表门禁失效 —— 别照抄旧数。

守卫在 `py/tests/test_nightly_flaky_coverage.py` 的 `TestSlowMarkerCoverage`：它扫出所有
含 `pytest.mark.slow` 的文件，要求**存在一条路径命中它、且标记表达式没排除 slow 的腿**，
否则点名报出未覆盖的文件。反向对照也写成了用例（把所有腿都改成 `not slow` 时必须报红），
因为这类"扫描器恒真"的失效正是本节的成因。变异验证：把 nightly 那条腿的 `-m slow`
改成 `-m "not slow"` ⇒ 2 条守卫红并精确列出那 3 个文件。

**判据（可复用）**：核对"CI 跑了多少用例"时，总数对不上不一定是跳过或口径差异，
必须逐条追到"哪一族、由哪条腿负责"。`-m` 表达式是选择器，**每条腿都可以合法地看不见一整族用例**。
