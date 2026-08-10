# MAOP Operator Helm Chart — Release Notes

## v0.3.0 (appVersion 4.5.0)

### Added
- **MaopTask CRD** (`crds/maoptask.yaml`) — bounded single-task execution
  against a MaopAgent, with per-task tools/plugins, timeout, retry policy,
  and token/turn tracking in status.
- **MaopWorkflow CRD** (`crds/maopworkflow.yaml`) — multi-step DAG orchestration
  with `dependsOn` edges, `failureStrategy` (failFast/continue/retry), and
  per-step status aggregation.
- **Combined `crd.yaml`** — multi-document manifest of all three CRDs for
  `kubectl apply -f` without Helm.
- **Static `controller.yaml`** — Helm-free deployment manifest (Namespace,
  ServiceAccount, ClusterRole/Binding, ConfigMap, Deployment, Service,
  Mutating/Validating webhooks) rendered from default `values.yaml`.
- **`helm/` packaging directory** — documentation for `helm package`, lint,
  install, upgrade, and OCI registry push.

### Changed
- ClusterRole now grants reconcile verbs for `maoptasks` and `maopworkflows`
  (and their `/status`, `/finalizers` subresources), not just `maopagents`.
- Admission webhooks now cover all three CR kinds (`maopagents`, `maoptasks`,
  `maopworkflows`).

### Multi-tenant & plugins
- `controller.multiTenant.enabled=true` (default) wires RLS scoping, per-tenant
  quotas (`maxTokensPerDay`, `maxRequestsPerDay`, `maxAgents`,
  `maxConcurrentTasks`), and audit logging into every reconciled workload.
- `controller.plugins.enabled=true` (default) loads the typed plugin system
  (`maop.core.plugins`) with `strictApi` enforcement.

### Compatibility
- CRD group: `maop.io`, version: `v1alpha1`, scope: `Namespaced`.
- All CRDs are `served: true, storage: true` for `v1alpha1`.
- No breaking changes to existing `MaopAgent` CR schema; new fields are
  additive.