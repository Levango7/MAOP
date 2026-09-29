# CI 门禁与触发面

> 面向改 MAOP 的人：哪些检查一定会跑、哪些会被跳过、以及**怎么分辨"跳过了"和"根本没跑"**。
> 最后更新：2026-09-29（部署工件纳入分类面、publish 死代码注释、镜像 node 对齐后）。

## 1. 触发模型

`MAOP CI`（`.github/workflows/ci.yml`）对**每个 PR 和每次 trunk push 都会启动**，然后由
一个无条件运行的作业 `CI scope (code vs docs-only)` 决定哪些重活跑：

```
scope ──┬─→ lint ─→ test(9 平台矩阵) ─→ audit / sbom
        │         └→ perf-smoke / migrations / sast
        ├─→ frontend ─→ e2e
        └─→ docs-gate（依赖 scope，但无条件跑）
secret-scan（无条件，永远跑）
docker / container-scan / compose-smoke / publish（仅 trunk push，见 §4）
```

- `docs-gate` 跑 `py/scripts/check_docs_consistency.py --gate`（README + docs/ 的路径存在性、
  模块计数、表格列数）。它**不跟着 `code=false` 跳过**，理由是这个检查的两类事故分别落在
  两侧：只改文档会写进不存在的路径，只删代码会让文档里的旧路径失效——两边都必须跑。
  脚本只用标准库、本机约 3 秒，不属"重活"。判定范围由 `docs/README.md` 第 1–6 章
  （"当前权威文档"）决定，历史快照/设计文档要在文件头写理由才豁免，脚本会逐条打印。

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
3. **`Publish to PyPI` 永远不跑，不是失败、也不是"偶尔 skipped"**。`on.push` 只声明了
   branches，GitHub 的 branches 过滤排除所有 tag push，而该 job 的条件是
   `refs/tags/v*` —— 条件永远不成立。PyPI 侧也从未发布过（`CHANGELOG.md:245`：包名实测 404）。
   要让发布真正发生需两步：`on.push` 补 `tags: ['v*']` + 在 PyPI 配置 trusted publisher
   （该 job 走 OIDC，无 token secret）。在此之前不要对外称"已支持 PyPI 发布"。

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

### 7.2 现行配置（本 PR 合并时生效）

required 上下文 5 条，其余保护项全关：

- `CI scope (code vs docs-only)`、`Secret Scan (gitleaks)`、`Docs consistency gate`
  —— 永远存在，任何 PR 都跑。
- `Lint (ruff + mypy)`、`Frontend Build` —— docs-only 时跳过（按 §7.1 第 1 条即满足），
  代码 PR 上必须真过。
- `strict: false`（不要求分支领先，避免每次主干合并把在跑的 PR 全部判过期）、
  `enforce_admins: false`（保留 owner 逃生门；并行会话的直推路径不受影响）、
  无 review 门槛、不要求 linear history / conversation resolution。

### 7.3 明确不进 required 的，以及原因

- **`pytest (…)` 矩阵作业**：docs-only 时那条 skipped check 的**名字是未展开的字面量**
  `pytest (${{ matrix.os }}, Python ${{ matrix.python-version }})`（实测），而代码 PR 上是
  9 条展开后的名字。按精确名设 required → docs-only 永远 `blocked`；按通配设则代码面匹配
  行为尚无实测证据（当前无代码 PR 可对照），所以先不猜。要把 pytest 也纳入 required，
  正解是加一条名字稳定的聚合作业（`needs: [scope, pytest, …]` + `if: always()`，自己读
  `needs.*.result` 判成败），而不是折腾上下文匹配。
- **只在 trunk push 才存在的作业**：`Docker build` / `Container Scan (trivy)` /
  `Compose Smoke` / `Publish to PyPI`。它们在 PR 上要么根本不产生、要么恒为 skipped，
  设成 required 等于"永远空满足"，是假门禁。

### 7.4 复测 / 撤销

改配置前先复测语义（平台会变）：`PUT /repos/Levango7/MAOP/branches/master/protection`
带 `required_status_checks.contexts`，读任一 PR 的 `mergeable_state`；**测完立刻
`DELETE` 同一路径**。逃生门：`DELETE .../protection` 一键清空，或临时
`POST .../protection/enforce_admins` 的逆操作。
