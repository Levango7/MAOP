# MAOP Kubernetes Operator — Helm Chart

## Status

**PARTIALLY IMPLEMENTED / 部分实现（M0/M1，2026-10-30）—— 尚不可作为平台部署。**

- Controller: `py/maop/operator/` ships a kopf-based MaopTask controller
  (spec validation / reconcile state machine / status patches / event layer),
  covered by mock-chain tests (`py/tests/test_operator_m0.py`,
  `test_operator_m1.py`). Real-cluster (kind) validation is not done yet
  (deferred by charter).
- Image build source: `deploy/k8s/operator/Dockerfile` builds
  `ghcr.io/maop/operator` (CI docker job, build-only; pushing to a registry
  remains a release decision).
- `MaopAgent` is resolved by name for `MaopTask.spec.agent` (M1: when present,
  the executing agent is pinned via `maop run --agent`). Reconciling it into
  workloads (Deployment/Service/RLS, replicas, quotas) is still **planned**;
  `MaopWorkflow` is not implemented; multi-tenant isolation, plugin loading and
  RLS-aware data access remain **planned** behavior.
- Turn budget: `MaopTask.spec.maxTurns` rides along as `maop run --max-turns`
  and the consumed turns are reported back into `status.turns` (M2; one turn =
  one dispatch attempt). Token usage is reported back into `status.tokensUsed`
  / `status.tokensSource` (M3.3; `measured` for LLM-direct usage, `estimated`
  for CLI drivers — estimates are never presented as measured).
- Tests: `py/tests/test_k8s_operator.py` remains the structure/layering suite
  (with skip placeholders); M0/M1 add mock-chain and guard tests (see above).

The following sections describe the **intended** design, not fully shipped features.

## Overview (planned)

Once implemented, the chart would reconcile `MaopAgent` custom resources into
running agent workloads with multi-tenant isolation, plugin loading, and
RLS-aware data access.

## Layout

```
deploy/k8s/operator/
├── Chart.yaml              # chart metadata (v0.3.0, appVersion 5.2.0 — image not built)
├── values.yaml             # default configuration
├── crds/
│   └── maopagent.yaml      # MaopAgent CRD (v1alpha1)
└── templates/
    ├── _helpers.tpl        # name/label helpers
    ├── deployment.yaml     # controller Deployment (planned)
    ├── service.yaml        # webhook + metrics Service
    ├── serviceaccount.yaml # RBAC ServiceAccount
    ├── role.yaml           # ClusterRole / Role
    ├── rolebinding.yaml    # ClusterRoleBinding / RoleBinding
    ├── configmap.yaml      # controller runtime config
    ├── webhook.yaml        # ValidatingWebhookConfiguration
    └── servicemonitor.yaml # Prometheus ServiceMonitor (optional)
```

## Install (planned — not runnable today)

```bash
# Install CRDs first (helm-hooks avoided to support --apply for GitOps)
kubectl apply -f deploy/k8s/operator/crds/

# Install the operator release
helm install maop-operator deploy/k8s/operator/ \
  --namespace maop-system --create-namespace
```

（以上命令描述的是目标形态；当前执行不会得到可工作的 operator。）

## Multi-tenant isolation (planned — not implemented)

When `controller.multiTenant.enabled=true` (default), the operator is *planned*
to:

1. Read `spec.tenant` on each `MaopAgent` CR.
2. Enforce per-tenant RLS scoping on all data access.
3. Apply default quotas from `controller.multiTenant.defaultQuotas` unless
   overridden by `spec.quotas`.
4. Write an audit entry to the tenant audit log on every reconcile.

None of the above is implemented today.

## Plugin system (planned — not implemented)

When `controller.plugins.enabled=true`, the controller is *planned* to load
plugins declared in `spec.plugins`. Set `controller.plugins.strictApi=true` to
reject plugins whose declared `api_version` is incompatible with the host.

## Example MaopAgent CR

```yaml
apiVersion: maop.io/v1alpha1
kind: MaopAgent
metadata:
  name: support-bot
spec:
  model: gpt-4o
  tenant: acme
  replicas: 2
  maxTurns: 15
  tools:
    - mcp.search
    - mcp.knowledge
  plugins:
    - greeter
    - audit-enhancer
  quotas:
    maxTokensPerDay: 50000
    maxConcurrentTasks: 4
```

## Configuration highlights

Values are the *planned* API surface; none of them takes effect today.

| Key | Default | Description |
|-----|---------|-------------|
| `controller.replicas` | `1` | Controller pods; >1 enables leader election |
| `controller.multiTenant.enabled` | `true` | Planned: RLS + quotas + audit (not implemented) |
| `controller.plugins.enabled` | `true` | Planned: plugin loading (not implemented) |
| `controller.plugins.strictApi` | `true` | Planned: reject incompatible plugin api_version |
| `rbac.clusterScope` | `true` | Cluster-scoped RBAC (needed for all-namespace watch) |
| `webhook.enabled` | `true` | Validating webhook for MaopAgent CRs |
| `crds.keep` | `true` | Keep CRDs on uninstall |