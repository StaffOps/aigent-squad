---
name: argocd-patterns
description: Use when configuring ArgoCD ApplicationSets, sync policies, hooks, multi-cluster deployments, or troubleshooting sync failures. Covers generators, sync waves, health checks, sharding, and <org> multi-cluster topology.
keywords: [argocd]
---
# ArgoCD Patterns

## Overview

ArgoCD is the **sole deployment mechanism** for PRD/HML/BTC at <org>. It runs on the `core-devops` cluster (`<org>-eks-prd`) and manages applications across all 3 EKS clusters.

- **Namespace**: `argo` (core-devops cluster)
- **Chart**: `argo/argo-cd` v9.5.14
- **UI**: internal (via ingress)
- **Notifications**: `#eks-notifications-argo` Slack channel

## ApplicationSet Generators

### Git Directory Generator (<org> primary pattern)

Scans a Git repo for directories and creates one Application per directory:

```yaml
apiVersion: argoproj.io/v1alpha1
kind: ApplicationSet
metadata:
  name: dpm-applications
  namespace: argo
spec:
  generators:
    - git:
        repoURL: https://gitlab.internal/dpm/dpm-environments.git
        revision: HEAD
        directories:
          - path: "prd/*"
          - path: "dev/*"
            exclude: true    # Optional: exclude specific paths
  template:
    metadata:
      name: "dpm-{{path.basename}}-{{path[0]}}"
    spec:
      project: dpm
      source:
        repoURL: https://gitlab.internal/devops/helm-charts.git
        chart: app
        targetRevision: HEAD
        helm:
          valueFiles:
            - "$values/{{path}}/values.yaml"
      sources:
        - repoURL: https://gitlab.internal/dpm/dpm-environments.git
          targetRevision: HEAD
          ref: values
      destination:
        server: https://kubernetes.default.svc
        namespace: "dpm"
      syncPolicy:
        automated:
          prune: true
          selfHeal: true
```

### List Generator

Static list of targets — useful for bootstrap resources:

```yaml
generators:
  - list:
      elements:
        - cluster: dev
          url: https://dev-cluster-api.internal
          namespace: dpm
        - cluster: prd
          url: https://prd-cluster-api.internal
          namespace: dpm
```

### Matrix Generator

Combines two generators (cartesian product):

```yaml
generators:
  - matrix:
      generators:
        - git:
            repoURL: https://gitlab.internal/dpm/dpm-environments.git
            directories:
              - path: "*/*"
        - list:
            elements:
              - env: dev
                cluster: https://dev-api.internal
              - env: prd
                cluster: https://prd-api.internal
```

### Merge Generator

Combines generators with override logic:

```yaml
generators:
  - merge:
      mergeKeys:
        - service
      generators:
        - git:
            # Base: all services from directory
            directories:
              - path: "prd/*"
        - list:
            # Override: specific services get custom config
            elements:
              - service: people-api
                replicas: 5
```

## <org> Pattern: Git Directory → Environments Repos

```
*-applicationsets/ repo          *-environments/ repo
┌─────────────────────┐         ┌──────────────────────┐
│ applications/       │         │ dev/                 │
│   values.yaml.gotmpl│────────▶│   people-api/        │
│                     │  scans  │     values.yaml      │
│ crons/              │         │   kyc-api/           │
│   values.yaml.gotmpl│         │     values.yaml      │
└─────────────────────┘         │ prd/                 │
                                │   people-api/        │
                                │     values.yaml      │
                                └──────────────────────┘
```

8 domain repos follow this pattern: `dpm`, `dcp`, `mdt`, `bm`, `acum`, `apps`, `ai`, `ctp`.

## Sync Waves

Order resource creation within an Application using annotations:

```yaml
metadata:
  annotations:
    argocd.argoproj.io/sync-wave: "0"    # Lower = earlier
```

Typical ordering:

| Wave | Resources |
|------|-----------|
| `-5` | Namespaces, CRDs |
| `-3` | RBAC (ServiceAccount, Role, RoleBinding) |
| `-1` | ExternalSecrets, ConfigMaps |
| `0` | Deployments/Rollouts (default) |
| `1` | Services, Ingress |
| `3` | ScaledObjects (KEDA) |
| `5` | Post-deploy jobs (migrations) |

## Hooks

Execute resources at specific sync phases:

```yaml
metadata:
  annotations:
    argocd.argoproj.io/hook: PreSync
    argocd.argoproj.io/hook-delete-policy: HookSucceeded
```

| Hook | When | Use case |
|------|------|----------|
| `PreSync` | Before sync | DB migrations, schema changes |
| `Sync` | During sync | Main resources (rarely used explicitly) |
| `PostSync` | After sync | Smoke tests, notifications |
| `SyncFail` | On sync failure | Cleanup, alert escalation |

Delete policies:
- `HookSucceeded` — delete after success (most common)
- `HookFailed` — delete after failure
- `BeforeHookCreation` — delete previous hook before creating new one

## Sync Policies

### Automated sync (PRD standard)

```yaml
syncPolicy:
  automated:
    prune: true        # Delete resources removed from Git
    selfHeal: true     # Revert manual changes (drift correction)
  syncOptions:
    - CreateNamespace=true
    - PrunePropagationPolicy=foreground
    - PruneLast=true
  retry:
    limit: 5
    backoff:
      duration: 5s
      factor: 2
      maxDuration: 3m
```

### Manual sync (special cases)

```yaml
syncPolicy:
  syncOptions:
    - CreateNamespace=true
  # No `automated` block = manual sync required
```

Use manual sync for:
- Database-related resources (migrations need coordination)
- Infrastructure changes with high blast radius
- Resources that need human verification before apply

## Multi-Cluster Topology

```
┌─────────────────────────────────────────────┐
│ core-devops (<org>-eks-prd)                   │
│                                             │
│  ArgoCD ──┬── manages ──▶ core-devops apps  │
│           │                                 │
│           ├── manages ──▶ dev cluster apps   │
│           │              (<org>-workloads-dev) │
│           │                                 │
│           └── manages ──▶ prd cluster apps   │
│                          (<org>-workloads-prd) │
└─────────────────────────────────────────────┘
```

Cluster registration in ArgoCD:

```yaml
# Managed via argocd CLI or declarative cluster secret
apiVersion: v1
kind: Secret
metadata:
  name: <org>-workloads-dev-nv
  namespace: argo
  labels:
    argocd.argoproj.io/secret-type: cluster
data:
  name: dev
  server: https://XXXXX.gr7.us-east-1.eks.amazonaws.com
  config: ...  # Bearer token or exec config
```

## Health Checks (Custom Lua)

ArgoCD uses Lua scripts to determine resource health for CRDs:

```yaml
# argocd-cm ConfigMap
resource.customizations.health.argoproj.io_Rollout: |
  hs = {}
  if obj.status ~= nil then
    if obj.status.phase == "Healthy" then
      hs.status = "Healthy"
      hs.message = "Rollout is healthy"
    elseif obj.status.phase == "Degraded" then
      hs.status = "Degraded"
      hs.message = obj.status.message
    else
      hs.status = "Progressing"
      hs.message = "Rollout in progress"
    end
  end
  return hs
```

<org> custom health checks for:
- `Rollout` (Argo Rollouts)
- `ScaledObject` (KEDA)
- `ExternalSecret` (ESO)
- `CronWorkflow` (Argo Workflows)
- `VirtualService` (Istio)

## Sharding (>2k Applications)

When managing thousands of apps, shard the application-controller:

```yaml
# argo-cd values
controller:
  replicas: 3
  env:
    - name: ARGOCD_CONTROLLER_REPLICAS
      value: "3"
```

ArgoCD distributes apps across controller replicas using consistent hashing on app name.

## AppProject Isolation

Each domain gets its own AppProject with restricted permissions:

```yaml
apiVersion: argoproj.io/v1alpha1
kind: AppProject
metadata:
  name: dpm
  namespace: argo
spec:
  sourceRepos:
    - https://gitlab.internal/dpm/*
    - https://gitlab.internal/devops/helm-charts.git
  destinations:
    - namespace: dpm*
      server: '*'
    - namespace: dpm-crons
      server: '*'
  clusterResourceWhitelist:
    - group: ''
      kind: Namespace
```

## Notifications (Slack)

ArgoCD notifications route to domain-specific channels:

```yaml
# Notification trigger
trigger.on-sync-failed: |
  - when: app.status.sync.status == 'OutOfSync' and app.status.health.status == 'Degraded'
    send: [slack-sync-failed]

# Template
template.slack-sync-failed: |
  message: |
    🔴 Sync failed: {{.app.metadata.name}}
    Status: {{.app.status.sync.status}}
    Health: {{.app.status.health.status}}
```

Channels:
- `#eks-notifications-argo` — all sync issues
- `#eks-notifications-teams` — team-specific routing

## Anti-patterns

- ❌ `selfHeal: true` without understanding implications (reverts ALL manual changes including emergency fixes)
- ❌ Sync without `prune: true` (orphaned resources accumulate, cost waste)
- ❌ One ArgoCD Application per individual resource (management overhead, slow sync)
- ❌ `kubectl apply` in PRD/HML/BTC (breaks GitOps, no audit trail)
- ❌ Editing Applications in ArgoCD UI (ApplicationSet overwrites changes)
- ❌ Missing retry policy (transient failures cause permanent OutOfSync)
- ❌ `targetRevision: HEAD` on mutable branches without webhook (delayed sync)
- ❌ No AppProject isolation (one team can deploy to another's namespace)
- ❌ Disabling auto-sync without documented reason and re-enable timeline
- ❌ Ignoring `Degraded` health status (silent failures in production)
- ❌ Hardcoding cluster URLs in ApplicationSet templates (use environment layering)
- ❌ No sync wave ordering (secrets created after deployments that need them)

## Debugging Checklist

1. `argocd app get <app>` — sync status, health, last sync result
2. `argocd app diff <app>` — live vs desired state
3. `argocd app sync <app> --dry-run` — preview what would change
4. Check events: `kubectl get events -n <ns> --sort-by='.lastTimestamp'`
5. Check Kyverno: `kubectl get policyreport -n <ns>`
6. Check ExternalSecret: `kubectl get externalsecret -n <ns>`
7. Check image exists: `curl -s https://<harbor-registry>/v2/<project>/<image>/tags/list`

## Related

- `helmfile-applicationset` skill — how ApplicationSets are rendered via helmfile
- `helm-chart-app-bdc` skill — the app chart consumed by Applications
- `helm-chart-cronworkflow-bdc` skill — CronWorkflow chart for batch jobs
- `helmfile-k8s-addon` skill — cluster add-on management (including ArgoCD itself)
