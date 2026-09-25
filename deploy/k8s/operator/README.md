# MAOP Kubernetes Operator — Helm Chart

## Status

**PLANNED — NOT IMPLEMENTED / 未实现、不可部署。**

This directory is only a *planned API shape*: a chart/metadata skeleton for an
operator that does not exist yet.

- No controller implementation: the repository contains no kopf /
  kubernetes-client / watch-based operator code.
- No image build source: `ghcr.io/maop/operator` has no Dockerfile and no build
  workflow in this repository — the image referenced by `controller.yaml` /
  `values.yaml` has never been built.
- Nothing reconciles `MaopAgent` / `MaopTask` / `MaopWorkflow` CRs; multi-tenant
  isolation, plugin loading and RLS-aware data access are **planned** behavior,
  not implemented behavior.
- Tests cover structure only: `py/tests/test_k8s_operator.py` asserts that
  `Chart.yaml` / `controller.yaml` / `crd.yaml` exist; multi-tenant / plugin /
  RLS checks are `pytest.skip` placeholders.

The following sections describe the **intended** design, not shipped features.

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