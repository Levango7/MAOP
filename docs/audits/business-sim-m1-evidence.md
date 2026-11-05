# M-SIM M1 证据（kind 腿 / Temporal 腿 / 48h soak）

> 章程：ROADMAP「M-SIM 模拟业务场景预生产演练」M1 另批——kind operator CR 腿
> + Temporal 后端腿 + 48h soak 串联。M0 证据见
> `business-sim-evidence-2026-11-04.md`（四缺口 GAP-1..4 的发现与收口）。
> 本文件按腿落证据；soak 段在 48h 跑满后补终态。

## 腿一：kind operator CR 腿（2026-11-05，本地 kind v0.31.0 / kubectl v1.36.1 / docker 29.9.2）

**形态**：`MaopTask` CR 承载工单分诊剧本三节点（triage → resolve → receipt），
operator（kopf）reconcile 后子进程执行 `maop run --agent <spec.agent> --task
<spec.prompt>`，与生产 k8s 部署同一路径。

### 结果：3/3 CR Succeeded，token 计量为实测

| CR | agent | phase | turns | tokensUsed | tokensSource |
|---|---|---|---|---|---|
| sim-triage-1 | sim-triage | Succeeded | 1 | 976 | **measured** |
| sim-resolve-1 | sim-worker | Succeeded | 2 | 1348 | **measured** |
| sim-receipt-1 | sim-reporter | Succeeded | 1 | 639 | **measured** |

`tokensSource=measured` 是 GAP-2 方案 A（#219：协议行→`MaopResult.usage`
回填）在集群形态的直接验证——M-T3.3 kind 验收（2026-11-01）当时为
`turns=1 / tokensUsed=0 / tokensSource=estimated`。sim-resolve-1 的 turns=2
= 一次 dispatch 失败重试后成功（turn 语义=一次 dispatch 尝试，如实记账）。

### 过程发现的三条真缺口（kind 腿的价值所在）

1. **chart webhook Secret 缺失**：`webhook.enabled=true`（默认）时 deployment
   挂 `webhook-certs`→`secret/maop-operator-webhook-tls`，该 Secret 不由 chart
   创建 ⇒ Pod 永久 `ContainerCreating`（`FailedMount`）。烟测/无证书部署须
   `--set webhook.enabled=false`（或预建 TLS Secret）。**建议：chart 补
   cert-manager 依赖声明或 values 说明**。
2. **RBAC 缺 CRD 读权限**：kopf 启动要 list `customresourcedefinitions`
   （apiextensions.k8s.io），ClusterRole 未授权 ⇒ 403 重试（9 次）；
   现网若开严格 RBAC 同样命中。**补丁**：ClusterRole 增
   `apiGroups:[apiextensions.k8s.io], resources:[customresourcedefinitions],
   verbs:[get,list,watch]`（本腿以 live patch 验证；chart 修复随收口 PR）。
3. **sim stub 不在镜像内**：`.dockerignore` 按设计排除 `py/scripts/`
   （"build/dev scripts not needed at runtime"），而业务剧本的确定性 stub
   就在该目录。**处置**：stub 经 ConfigMap 挂 `/data/sim/`（不污染生产镜像；
   agents.yaml 的 cli_args 指向挂载路径），K8s 原生且可复用。

### 复现配方（本腿实跑序）

```bash
docker build -f deploy/k8s/operator/Dockerfile -t maop-operator:m1 .
kind create cluster --name maop-m1 --wait 120s
kind load docker-image maop-operator:m1 --name maop-m1
kubectl apply -f deploy/k8s/operator/crds/maopagent.yaml,deploy/k8s/operator/crds/maoptask.yaml
helm template maop-operator deploy/k8s/operator --set image.repository=maop-operator \
  --set image.tag=m1 --set webhook.enabled=false | kubectl apply -f -
kubectl create configmap maop-sim-agents --from-file=agents.yaml=<sim-agents.yaml>
kubectl create configmap maop-sim-stub --from-file=stub_business_agent.py=<repo>/py/scripts/business_sim/stub_business_agent.py
# deployment 追加 subPath 挂载 /data/config/agents.yaml 与 /data/sim/stub_business_agent.py
kubectl apply -f <MaopTask CRs>
```

（Windows 本机注意 `MSYS_NO_PATHCONV=1`，容器内绝对路径会被 MSYS 改写。）

## 腿二：Temporal 后端腿（2026-11-05，temporal CLI v1.10.0 / Server 1.32.2，`server start-dev --headless`）

**形态**：三节点 DAG（triage → resolve → receipt，`depends_on` 链）经
`maop workflow run m1-ticket`（`MAOP_ORCHESTRATION_BACKEND=temporal`）提交，
`TemporalAdapter` 编译为 `MaopDagWorkflow` 在 dev server 执行，节点执行体 =
`python -m maop run` 子进程（生产同形）。

### 结果：3/3 节点 success + 成本台账三节点齐全（measured）

- workflow：`steps_planned=3 / steps_completed=3`，节点 status 全 `success`；
- Temporal 历史：`m1-ticket-*` × MaopDagWorkflow `Completed`（多次重放一致）；
- 成本台账（`cost_entries`）：三 agent 均有行，且 **prompt_tokens +
  completion_tokens == total_tokens 逐行成立**（协议行拆分经 GAP-2 链路入账）。

### GAP-5（新发现 → 已修复，2026-11-06）：特定任务文本触发 loop 并行分支后成本静默丢失

- **现象**：任务文本含英文 `and verify refresh chain` 时，该节点 `maop run`
  的 `returncode=0 / [OK]` 且节点 status=success，但
  `MaopResult.model=None, usage=None` ⇒ `_budget_reconciliation` 早退
  （`if not exec_result.model: return`）⇒ `run_events` 无 `run.cost`、成本表
  缺行。同 agent 换 M0 中文原文（含「校验刷新链」）则正常入账——
  **触发面是具体文本形态（英文关键词组合），不是 agent/breaker 状态**：
  全新 home（干净 breaker）复现；同文本换 agent 仍复现；去掉
  `and verify refresh chain` 后缀即恢复。
- **旁证**：该路径同时产生两个 `run.step` 事件（双 dispatch），
  kind 腿 `sim-resolve-1` 的 `turns=2` 即同一文本的同源现象
  （CR 里第 2 次 dispatch 成功，成本走 loop 层 `[USAGE]` 无碍，
  但成本表侧丢失）。
- **影响**：计费/成本归因在触发文本上静默缺行——属「静默」类缺陷，
  M-SIM 的增量对账（soak）用 M0 中文文本未触发，故此前未暴露。
- **建议修法（待收口批评估）**：`_budget_reconciliation` 的早退条件从
  「model 为空即放弃」改为「显式失败标记才放弃」；或在
  loop 计划/校验分支替换 `exec_result` 时透传原结果的 `model/usage`。
  验收判据 = 用本文件的英文触发文本跑 `run_sim` 同款增量对账为 match。

### 腿内附注（配置级，非产品缺陷）

- sim home 未配 `routing:` 表 ⇒ loop 打警告
  `Config routing did not match task, using default: rk=chat agent=codex`
  （forced_agent 仍生效，不影响执行路由）；生产 config 有 routing 段，属
  腿配置简化，如实记录。

## 腿三：48h soak

（本机 2026-11-05 04:56 点火，逐轮 JSONL `.sim-soak/soak-progress.jsonl`；
跑满后补终态统计与完整性检查汇总。）
### GAP-5 修复回执（2026-11-06）

根因精化：触发文本让 analyzer 拆出两个子任务（`reset session` + `verify
refresh chain`）走 **`_execute_parallel` 并行分支**（`[st-001]` 前缀的聚合
输出为证），聚合 `new_result` 未透传 model/usage ⇒ 对账早退。

**双层修复**：
1. `loop_executor._execute_parallel`：聚合结果 model 取首个非空子任务值、
   usage 按键求和（并累计进 loop 级 `_tokens_used_measured`，与串行路径
   口径一致；全子任务无实测时保持 None 不冒充）。
2. `maop_loop_phases._budget_reconciliation` 硬化：早退条件从「model 空
   即放弃」改为「model 与实测 usage 皆缺才放弃」（model 留空时按启发式
   路径同形以 "" 归档）。

**验收（判据即 M1 证据稿所立）**：英文触发文本跑 loop →
`exec_result.usage={808,540,1348}`、成本行在账（`808+540=1348`，与 kind 腿
`sim-resolve-1` 的 1348 同源互证）；单测 3/3
（`tests/test_gap5_parallel_usage.py`：聚合求和/无实测保 None/对账硬化）；
邻近回归 40/40；ruff/mypy 无问题。
