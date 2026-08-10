# MAOP Operator Helm Chart — Packaging Guide

This directory documents how to package and distribute the MAOP operator Helm
chart. The chart source lives in the parent directory (`deploy/k8s/operator/`);
this `helm/` folder holds packaging artifacts and release notes.

## Chart location

```
deploy/k8s/operator/          ← chart root (Chart.yaml, values.yaml, templates/, crds/)
├── Chart.yaml
├── values.yaml
├── README.md
├── crds/
│   ├── maopagent.yaml
│   ├── maoptask.yaml
│   └── maopworkflow.yaml
├── templates/
│   ├── _helpers.tpl
│   ├── deployment.yaml
│   ├── service.yaml
│   ├── serviceaccount.yaml
│   ├── role.yaml
│   ├── rolebinding.yaml
│   ├── configmap.yaml
│   ├── webhook.yaml
│   └── servicemonitor.yaml
├── crd.yaml                   ← combined CRDs (kubectl apply -f)
├── controller.yaml            ← static deployment (kubectl apply -f, no Helm)
└── helm/                      ← this directory: packaging artifacts
    ├── README.md
    └── release-notes.md
```

## Build a package

```bash
# From the repository root:
helm package deploy/k8s/operator --destination deploy/k8s/operator/helm/

# Produces: deploy/k8s/operator/helm/maop-operator-0.3.0.tgz
```

## Lint before release

```bash
helm lint deploy/k8s/operator
```

## Install from package

```bash
# Install the packaged tarball:
helm install maop deploy/k8s/operator/helm/maop-operator-0.3.0.tgz \
  --namespace maop-system --create-namespace

# Or install directly from the chart source:
helm install maop deploy/k8s/operator \
  --namespace maop-system --create-namespace
```

## Upgrade

```bash
helm upgrade maop deploy/k8s/operator \
  --namespace maop-system \
  --set controller.replicas=2 \
  --set controller.multiTenant.enabled=true
```

## Multi-tenant + plugins (production override)

```bash
helm upgrade maop deploy/k8s/operator --namespace maop-system \
  --set controller.replicas=2 \
  --set controller.multiTenant.enabled=true \
  --set controller.multiTenant.defaultQuotas.maxAgents=50 \
  --set controller.plugins.enabled=true \
  --set controller.plugins.strictApi=true \
  --set controller.plugins.allowlist="{greeter,audit-logger}"
```

## CRDs

Three custom resources are defined:

| CRD          | Purpose                                                  |
|--------------|----------------------------------------------------------|
| `MaopAgent`  | Long-running agent workload (Deployment + Service).       |
| `MaopTask`   | Bounded single-task execution against an agent.           |
| `MaopWorkflow` | Multi-step DAG of tasks with dependency resolution.      |

Apply CRDs out-of-band (recommended for production to avoid upgrade conflicts):

```bash
kubectl apply -f deploy/k8s/operator/crd.yaml
```

Or let Helm manage them (`crds.install=true` is the default in `values.yaml`).

## Static install (no Helm)

For environments without Helm, use the combined static manifests:

```bash
kubectl apply -f deploy/k8s/operator/crd.yaml
kubectl apply -f deploy/k8s/operator/controller.yaml
```

## Release process

1. Bump `version` (chart) and `appVersion` (operator image) in `Chart.yaml`.
2. Update `helm/release-notes.md`.
3. `helm lint deploy/k8s/operator` — must pass.
4. `helm package deploy/k8s/operator --destination deploy/k8s/operator/helm/`.
5. Optionally push to an OCI registry:
   `helm push deploy/k8s/operator/helm/maop-operator-0.3.0.tgz oci://ghcr.io/maop/charts`.