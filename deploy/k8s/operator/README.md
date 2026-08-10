# MAOP Kubernetes Operator — Helm Chart

Helm chart for the MAOP Kubernetes Operator, which reconciles `MaopAgent`
custom resources into running agent workloads with multi-tenant isolation,
plugin loading, and RLS-aware data access.

## Layout

```
deploy/k8s/operator/
├── Chart.yaml              # chart metadata (v0.3.0, appVersion 4.5.0)
├── values.yaml             # default configuration
├── crds/
│   └── maopagent.yaml      # MaopAgent CRD (v1alpha1)
└── templates/
    ├── _helpers.tpl        # name/label helpers
    ├── deployment.yaml     # controller Deployment
    ├── service.yaml        # webhook + metrics Service
    ├── serviceaccount.yaml # RBAC ServiceAccount
    ├── role.yaml           # ClusterRole / Role
    ├── rolebinding.yaml    # ClusterRoleBinding / RoleBinding
    ├── configmap.yaml      # controller runtime config
    ├── webhook.yaml        # ValidatingWebhookConfiguration
    └── servicemonitor.yaml # Prometheus ServiceMonitor (optional)
```

## Install

```bash
# Install CRDs first (helm-hooks avoided to support --apply for GitOps)
kubectl apply -f deploy/k8s/operator/crds/

# Install the operator release
helm install maop-operator deploy/k8s/operator/ \
  --namespace maop-system --create-namespace
```

## Multi-tenant isolation

When `controller.multiTenant.enabled=true` (default), the operator:

1. Reads `spec.tenant` on each `MaopAgent` CR.
2. Enforces per-tenant RLS scoping on all data access.
3. Applies default quotas from `controller.multiTenant.defaultQuotas` unless
   overridden by `spec.quotas`.
4. Writes an audit entry to the tenant audit log on every reconcile.

## Plugin system

When `controller.plugins.enabled=true`, the controller loads plugins declared
in `spec.plugins`. Set `controller.plugins.strictApi=true` to reject plugins
whose declared `api_version` is incompatible with the host.

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

| Key | Default | Description |
|-----|---------|-------------|
| `controller.replicas` | `1` | Controller pods; >1 enables leader election |
| `controller.multiTenant.enabled` | `true` | RLS + quotas + audit |
| `controller.plugins.enabled` | `true` | Plugin loading |
| `controller.plugins.strictApi` | `true` | Reject incompatible plugin api_version |
| `rbac.clusterScope` | `true` | Cluster-scoped RBAC (needed for all-namespace watch) |
| `webhook.enabled` | `true` | Validating webhook for MaopAgent CRs |
| `crds.keep` | `true` | Keep CRDs on uninstall |