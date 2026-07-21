---
name: helmfile-k8s-addon
description: Use when managing cluster add-ons via helmfile in 02-KUBE/00-CONFIG/k8s-setup/. Covers multi-environment helmfile patterns, bootstrapping order, diff workflow, environment-specific values, and the bedag/raw companion release pattern.
keywords: [helmfile-k8s-addon, helmfile, k8s, addon, "helmfile k8s", "k8s addon", kube]
---
# Helmfile K8s Add-on Pattern

## Overview

Every cluster add-on at <org> is managed via helmfile in `02-KUBE/00-CONFIG/k8s-setup/`. Each add-on has its own directory with a `helmfile.yaml.gotmpl` that supports multi-environment deployment across all 3 EKS clusters.

- **Location**: `<workspace>/02-KUBE/00-CONFIG/k8s-setup/`
- **Pattern**: one directory per add-on, one helmfile per directory
- **Environments**: `default` (core-devops), `dev`, `prd`

## Directory Structure

```
k8s-setup/
├── argo/                    # ArgoCD, Rollouts, Workflows
├── cert-manager/            # TLS certificates (AWS PCA)
├── external-dns/            # DNS sync (public + private)
├── external-secrets/        # AWS Secrets Manager → K8s
├── istio/                   # Service mesh (ambient)
├── karpenter/               # Node autoscaling
├── keda/                    # Event-driven pod autoscaling
├── kyverno/                 # Policy engine
├── monitoring/              # Full observability stack
├── nginx/                   # Per-domain ingress controllers
├── gateways/                # Istio Gateway API resources
├── scaleops/                # Resource optimization
├── velero/                  # Backup/restore
├── harbor/                  # Container registry
├── gitlab-runner/           # CI runners
├── metrics-server/          # Resource metrics
└── ...                      # 30+ add-ons total
```

## Helmfile Structure (per add-on)

Each add-on follows a consistent pattern:

```
<addon>/
├── helmfile.yaml.gotmpl           # Main helmfile (environments + releases)
├── <release-name>/
│   ├── values.yaml.gotmpl         # Default values (all envs)
│   └── values-<env>.yaml.gotmpl   # Per-environment overrides (optional)
└── <release-name>-raw/
    ├── values.yaml.gotmpl         # bedag/raw companion (CRDs, extra resources)
    └── values-<env>.yaml.gotmpl   # Per-environment raw overrides
```

## Core Pattern: helmfile.yaml.gotmpl

Every add-on helmfile has 3 sections:

### 1. Repositories + Templates

```yaml
repositories:
  - name: karpenter
    url: public.ecr.aws/karpenter
    oci: true
  - name: bedag
    url: https://bedag.github.io/helm-charts

templates:
  default-release: &default-release
    namespace: kube-system
```

### 2. Environments (cluster targeting)

```yaml
environments:
  default:
    kubeContext: arn:aws:eks:us-east-1:<ACCOUNT_ID>:cluster/<org>-eks-prd
    values:
      - cluster_name: <org>-eks-prd
        environment: PRD
        role_arn: arn:aws:iam::<ACCOUNT_ID>:role/SomeRole-<org>-eks-prd
      - karpenter:
          enabled: true
          version: 1.8.3
          <<: *default-release
      - karpenter-raw:
          enabled: true
          version: 2.0.0
          <<: *default-release
  dev:
    kubeContext: arn:aws:eks:us-east-1:<ACCOUNT_ID>:cluster/<org>-workloads-dev-nv
    values:
      - karpenter:
          enabled: true
          version: 1.8.3
          <<: *default-release
      - karpenter-raw:
          enabled: true
          version: ''
          <<: *default-release
  prd:
    kubeContext: arn:aws:eks:us-east-1:<ACCOUNT_ID>:cluster/<org>-workloads-prd-nv
    values:
      - karpenter:
          enabled: true
          version: 1.8.3
          <<: *default-release
      - karpenter-raw:
          enabled: true
          version: ''
          <<: *default-release
```

Key points:
- `default` = core-devops cluster (`<org>-eks-prd`)
- Each release has `enabled`, `version`, `namespace` per environment
- YAML anchors (`&default-release`) reduce repetition
- `version: ''` for bedag/raw means "use chart default" (raw chart doesn't need pinning)

### 3. Releases (with dynamic version/namespace)

```yaml
---
templates:
  default: &default
    version: '{{`{{ .Values | get .Release.Name | get "version" }}`}}'
    namespace: '{{`{{ .Values | get .Release.Name | get "namespace" }}`}}'
    values:
      - ./{{`{{ .Release.Name }}`}}/values.yaml.gotmpl
      # {{ if ne .Environment.Name "default" }}
      - ./{{`{{ .Release.Name }}`}}/values-{{`{{ .Environment.Name }}`}}.yaml.gotmpl
      # {{ end }}

releases:
  - chart: karpenter/karpenter
    name: karpenter
    condition: karpenter.enabled
    <<: *default
    wait: true
  - chart: bedag/raw
    name: karpenter-raw
    condition: karpenter-raw.enabled
    <<: *default
    needs:
      - karpenter
```

The `---` separator creates a second YAML document where `.Values` from environments are available.

## bedag/raw Companion Pattern

Most add-ons have a `-raw` release that deploys additional resources not covered by the upstream chart:

| Add-on | Raw release deploys |
|--------|---------------------|
| `karpenter-raw` | NodePool, EC2NodeClass CRDs |
| `external-secrets-raw` | ClusterSecretStore |
| `argo-rollouts-raw` | AnalysisTemplate, Notification triggers |
| `argo-workflows-raw` | ClusterWorkflowTemplate, RBAC |
| `kyverno-policies` | ClusterPolicy resources |

The raw release always `needs` the main release (ensures CRDs exist before instances).

## Values Files (.gotmpl)

Values files use helmfile templating to access environment values:

```yaml
# karpenter/values.yaml.gotmpl
settings:
  clusterName: {{ .Values.cluster_name | default "<org>-eks-prd" }}
  interruptionQueue: {{ .Values.cluster_name | default "<org>-eks-prd" }}
serviceAccount:
  annotations:
    eks.amazonaws.com/role-arn: {{ .Values.karpenter_controller_role_arn }}
replicas: 2
```

Per-environment overrides:

```yaml
# karpenter/values-dev.yaml.gotmpl
replicas: 1
settings:
  clusterName: <org>-workloads-dev-nv
```

## Bootstrapping Order

Add-ons have dependencies. Install order matters:

```
1. cert-manager          # TLS certs (needed by everything with TLS)
2. external-secrets      # Secrets sync (needed by apps that consume secrets)
3. kyverno               # Policies (must exist before workloads)
4. karpenter             # Node provisioning (needed for scheduling)
5. istio                 # Service mesh (base → cni → istiod → ztunnel)
6. nginx / gateways      # Ingress (needs certs, mesh)
7. monitoring            # Observability stack
8. argo                  # GitOps (CD, Rollouts, Workflows)
9. Application charts    # Actual workloads
```

Within a single helmfile, use `needs` for ordering:

```yaml
releases:
  - name: external-secrets
    <<: *default
    wait: true
  - name: external-secrets-raw
    <<: *default
    needs:
      - external-secrets    # CRDs must exist first
```

## Operational Workflow

### Preview changes (mandatory before apply)

```bash
# Diff against live cluster for specific environment
helmfile -e dev diff

# Diff for core-devops (default env)
helmfile diff

# Template only (no cluster access needed)
helmfile -e prd template
```

### Apply changes

```bash
# Apply to dev cluster
helmfile -e dev apply

# Apply to production (requires explicit approval)
helmfile -e prd apply
```

### Target specific release

```bash
# Only diff karpenter release
helmfile -e dev -l name=karpenter diff

# Only apply external-secrets
helmfile -e prd -l name=external-secrets apply
```

## Environment-Specific Patterns

### Disabling add-ons per cluster

```yaml
# ArgoCD only runs on core-devops, not on workload clusters
environments:
  default:
    values:
      - argo-cd:
          enabled: true       # core-devops: YES
  dev:
    values:
      - argo-cd:
          enabled: false      # dev cluster: NO
  prd:
    values:
      - argo-cd:
          enabled: false      # prd cluster: NO
```

### Different versions per cluster

```yaml
environments:
  default:
    values:
      - keda:
          version: 2.16.1    # core-devops: latest
  dev:
    values:
      - keda:
          version: 2.16.1    # dev: same
  prd:
    values:
      - keda:
          version: 2.15.0    # prd: conservative
```

## Real Example: external-secrets

```yaml
repositories:
  - name: external-secrets
    url: https://charts.external-secrets.io
  - name: bedag
    url: https://bedag.github.io/helm-charts

templates:
  default-release: &default-release
    namespace: external-secrets

environments:
  default:
    kubeContext: arn:aws:eks:us-east-1:<ACCOUNT_ID>:cluster/<org>-eks-prd
    values:
      - external_secrets_role_arn: arn:aws:iam::<ACCOUNT_ID>:role/ExternalSecretsAccessRole-<org>-eks-prd
        region: us-east-1
      - external-secrets:
          enabled: true
          version: 0.17.0
          <<: *default-release
      - external-secrets-raw:
          enabled: true
          version: ''
          <<: *default-release
  dev:
    kubeContext: arn:aws:eks:us-east-1:<ACCOUNT_ID>:cluster/<org>-workloads-dev-nv
    values:
      - external-secrets:
          enabled: true
          version: 0.17.0
          <<: *default-release
      - external-secrets-raw:
          enabled: true
          version: ''
          <<: *default-release
  prd:
    kubeContext: arn:aws:eks:us-east-1:<ACCOUNT_ID>:cluster/<org>-workloads-prd-nv
    values:
      - external-secrets:
          enabled: true
          version: 0.17.0
          <<: *default-release
      - external-secrets-raw:
          enabled: true
          version: ''
          <<: *default-release
---
templates:
  default: &default
    version: '{{`{{ .Values | get .Release.Name | get "version" }}`}}'
    namespace: '{{`{{ .Values | get .Release.Name | get "namespace" }}`}}'
    values:
      - ./{{`{{ .Release.Name }}`}}/values.yaml.gotmpl
releases:
  - chart: external-secrets/external-secrets
    name: external-secrets
    condition: external-secrets.enabled
    <<: *default
    wait: true
  - chart: bedag/raw
    name: external-secrets-raw
    condition: external-secrets-raw.enabled
    <<: *default
    needs:
      - external-secrets
```

## Anti-patterns

- ❌ `helmfile apply` in production without `helmfile diff` first (blind changes)
- ❌ Hardcoded chart versions without upgrade plan (version rot)
- ❌ Skipping `wait: true` on CRD-providing releases (race conditions)
- ❌ Missing `needs` between raw and main release (CRD not found errors)
- ❌ Editing live resources via `kubectl edit` (drift from helmfile state)
- ❌ Same version across all envs without testing in dev first (untested upgrades)
- ❌ Missing `condition` field (release always installed even when disabled)
- ❌ Putting all add-ons in one giant helmfile (blast radius too large)
- ❌ Not using `kubeContext` per environment (wrong cluster targeted)
- ❌ Storing secrets in values files (use External Secrets Operator)
- ❌ Upgrading multiple add-ons simultaneously in production (isolate changes)

## Related

- `helmfile-templating` skill — escaping gotchas in `.gotmpl` files
- `helmfile-applicationset` skill — application-level helmfile (different pattern)
- `k8s-best-practices-bdc` steering — resource requirements, labels
- `k8s-safety` steering — read-only by default, approval gates
