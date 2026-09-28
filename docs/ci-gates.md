# CI 门禁与触发面

> 面向改 MAOP 的人：哪些检查一定会跑、哪些会被跳过、以及**怎么分辨"跳过了"和"根本没跑"**。
> 最后更新：2026-09-28（job 级 scope 判定落地后）。

## 1. 触发模型

`MAOP CI`（`.github/workflows/ci.yml`）对**每个 PR 和每次 trunk push 都会启动**，然后由
一个无条件运行的作业 `CI scope (code vs docs-only)` 决定哪些重活跑：

```
scope ──┬─→ lint ─→ test(9 平台矩阵) ─→ audit / sbom
        │         └→ perf-smoke / migrations / sast
        └─→ frontend ─→ e2e
secret-scan（无条件，永远跑）
docker / container-scan / compose-smoke / publish（仅 trunk push，见 §4）
```

- `scope` 把变更集交给 `py/scripts/ci_path_scope.py` 分类，输出 `code=true|false`。
- `code=false`（docs-only）时，`lint` 与 `frontend` 及它们的下游作业被**跳过**（skipped）。
- 分类面与历史 `on.*.paths` 白名单等价：`py/`、`config/`、`dashboard/`、`dashboard-enterprise/`、
  `.github/workflows/`、`py/Dockerfile`、`requirements.lock|txt`、compose、`.dockerignore`，
  外加根目录 `README.md` / `ROADMAP.md`（lint job 的 Doc↔Code reconcile 门禁会读它们）。
- **拿不准时一律按"跑全量"处理**（基线算不出、变更集为空 → `code=true`）。宁可多跑，不可静默少跑。

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
3. **`Publish to PyPI` 在多数情况下是 skipped**，不是失败。

## 4. 容器作业的特别提示（改 `py/Dockerfile` 前必读）

`docker` / `container-scan` / `compose-smoke` 只带 `if: github.event_name == 'push' && ref == main|master|develop`
一类的条件，**PR 腿上看不到它们**；而且它们在 `needs: [test, frontend, e2e]` 下游，
矩阵没跑完之前连 check 都不会创建。所以：**改 Dockerfile 拿不到 PR 级 CI 证明**，
请在本地用同一基础镜像把那条 `RUN` 单独跑一遍再提 PR，别把验证交给不会执行的 gate。

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
