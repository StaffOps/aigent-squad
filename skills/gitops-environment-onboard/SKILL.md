---
name: gitops-environment-onboard
description: "Use when onboarding a new service into <ORG> GitOps pipeline. Covers the 4-step workflow from ApplicationSet registration through environment values, CI/CD pipeline, and domain catalog. Includes validation checklist and common pitfalls."
keywords: [gitops-environment-onboard, gitops, environment, onboard, "gitops environment", "environment onboard", pipeline, ci, cd]
---
# GitOps Environment Onboarding

## Overview

Onboarding a new service into <ORG>'s GitOps pipeline requires changes across 3-4 repositories. This workflow ensures the service is properly registered, configured per environment, and validated before first deployment.

**Reference implementation**: DPM domain (`dpm-applicationsets`, `dpm-environments-{dev,prd,btc}`)

## Prerequisites

Before starting:
- [ ] Service code exists in its own Git repo
- [ ] Dockerfile builds successfully (multi-arch: amd64 + arm64)
- [ ] Image pushed to Harbor (`harbor.<org>.com/<org>-images/<service>`)
- [ ] Image signed with cosign
- [ ] CostCenter identified (from official `tags.md`)
- [ ] Namespace exists or will be created

## Step 1: Register in ApplicationSet

Add the service to the domain's `*-applicationsets/` repo.

### If using existing ApplicationSet (most common)

The Git directory generator auto-discovers new services. **No changes needed** in the applicationsets repo — just create the directory in the environments repo (Step 2).

### If creating a new ApplicationSet category

Edit `applications/values.yaml.gotmpl` in the domain's applicationsets repo:

```yaml
# dpm-applicationsets/applications/values.yaml.gotmpl
apiVersion: argoproj.io/v1alpha1
kind: ApplicationSet
metadata:
  name: dpm-applications-{{ .Values.environment }}
  namespace: argo
spec:
  generators:
    - git:
        repoURL: {{ .Values.environmentsRepo }}
        revision: HEAD
        directories:
          - path: "{{ .Values.environment }}/*"
  template:
    metadata:
      name: "dpm-{{`{{path.basename}}`}}-{{ .Values.environment }}"
    spec:
      project: dpm
      sources:
        - repoURL: {{ .Values.chartRepo }}
          chart: app
          targetRevision: {{ .Values.chartVersion }}
          helm:
            valueFiles:
              - "$values/{{`{{path}}`}}/values.yaml"
        - repoURL: {{ .Values.environmentsRepo }}
          targetRevision: HEAD
          ref: values
      destination:
        server: {{ .Values.clusterServer }}
        namespace: dpm
      syncPolicy:
        automated:
          prune: true
          selfHeal: true
        retry:
          limit: 5
          backoff:
            duration: 5s
            factor: 2
            maxDuration: 3m
```

Then render via helmfile:

```bash
cd dpm-applicationsets/
helmfile -e prd template   # Validate output
helmfile -e prd diff       # Check against live
# helmfile -e prd apply    # After approval
```

## Step 2: Create Environment Values

Create service directory in the `*-environments/` repo for each target environment.

### Directory structure

```
dpm-environments/
├── dev/
│   └── my-new-service/
│       └── values.yaml
├── prd/
│   └── my-new-service/
│       └── values.yaml
└── btc/                    # Only if batch workload
    └── my-new-service/
        └── values.yaml
```

### Minimal values.yaml (API service)

```yaml
# dpm-environments/dev/my-new-service/values.yaml
deploymentType: Rollout
strategy: Canary

image:
  repository: harbor.<org>.com/<org>-images/dpm-my-new-service
  tag: "initial"    # CI pipeline will update this

service:
  port: 8080

# Mandatory labels
area: "dpm"
costCenter: "Program-DataPlatform"
costScope: "API"
costProject: "MY-PROJECT"
environment: "DEV"

# Resources (ScaleOps will optimize over time)
resources:
  requests:
    cpu: "250m"
    memory: "256Mi"
  limits:
    cpu: "250m"
    memory: "256Mi"

# Health checks
livenessProbe:
  httpGet:
    path: /healthz
    port: http
  initialDelaySeconds: 10
  periodSeconds: 10
readinessProbe:
  httpGet:
    path: /ready
    port: http
  initialDelaySeconds: 5
  periodSeconds: 5

# Autoscaling
autoscaling:
  enabled: true
  minReplicas: 1
  maxReplicas: 5
  triggers:
    - type: cpu
      metadata:
        type: Utilization
        value: "70"

# OTel configuration
configMap:
  SERVICE_NAME: "dpm-my-new-service"
  ENVIRONMENT: "DEV"
  OTEL_EXPORTER_OTLP_ENDPOINT: "http://otel-agent-collector.monitoring:4317"

# Secrets from AWS Secrets Manager
externalSecret:
  refreshInterval: "1h"
  data:
    - secretKey: DATABASE_URL
      remoteRef:
        key: dpm/my-new-service/dev
        property: database_url

# ServiceAccount with IRSA
serviceAccount:
  create: true
  annotations:
    eks.amazonaws.com/role-arn: "arn:aws:iam::524040971621:role/dpm-my-new-service-dev"

# Node scheduling
node:
  arch: amd64
  capacity_type: on-demand
```

### PRD values (differences only)

```yaml
# dpm-environments/prd/my-new-service/values.yaml
deploymentType: Rollout
strategy: Canary

image:
  repository: harbor.<org>.com/<org>-images/dpm-my-new-service
  tag: "initial"

service:
  port: 8080

area: "dpm"
costCenter: "Program-DataPlatform"
costScope: "API"
costProject: "MY-PROJECT"
environment: "PRD"

resources:
  requests:
    cpu: "500m"
    memory: "512Mi"
  limits:
    cpu: "500m"
    memory: "512Mi"

livenessProbe:
  httpGet:
    path: /healthz
    port: http
readinessProbe:
  httpGet:
    path: /ready
    port: http

autoscaling:
  enabled: true
  minReplicas: 3
  maxReplicas: 20
  triggers:
    - type: cpu
      metadata:
        type: Utilization
        value: "70"

configMap:
  SERVICE_NAME: "dpm-my-new-service"
  ENVIRONMENT: "PRD"
  OTEL_EXPORTER_OTLP_ENDPOINT: "http://otel-agent-collector.monitoring:4317"

externalSecret:
  refreshInterval: "1h"
  data:
    - secretKey: DATABASE_URL
      remoteRef:
        key: dpm/my-new-service/prd
        property: database_url

serviceAccount:
  create: true
  annotations:
    eks.amazonaws.com/role-arn: "arn:aws:iam::524040971621:role/dpm-my-new-service-prd"

node:
  arch: arm64
  capacity_type: on-demand
```

## Step 3: Configure CI/CD Pipeline

The GitLab CI pipeline must:
1. Build + push image to Harbor (both architectures)
2. Sign image with cosign
3. Update image tag in the environments repo

### Pipeline image tag update (deploy stage)

```yaml
# .gitlab-ci.yml (service repo)
deploy:dev:
  stage: deploy
  script:
    - |
      git clone https://gitlab.internal/dpm/dpm-environments.git
      cd dpm-environments
      yq -i '.image.tag = "'${CI_COMMIT_SHORT_SHA}'"' dev/my-new-service/values.yaml
      git add .
      git commit -m "chore(deploy): update my-new-service to ${CI_COMMIT_SHORT_SHA}"
      git push
  rules:
    - if: $CI_COMMIT_BRANCH == "development"
```

ArgoCD detects the commit and syncs automatically.

## Step 4: Register in Domain Catalog

Update domain tracking (for FinOps and service discovery):

| Field | Value |
|-------|-------|
| Service name | `dpm-my-new-service` |
| CostCenter | `Program-DataPlatform` |
| CostScope | `API` |
| CostProject | `MY-PROJECT` |
| Helm chart | `app` (corporate) |
| Environments | DEV, PRD |
| Namespace | `dpm` |
| Team | DPM |

## Validation Checklist

After onboarding, verify:

### Mandatory (blocks deployment)

- [ ] **CostCenter label** present and valid (without it, `k8sattributesprocessor` won't enrich telemetry)
- [ ] **Health endpoints** respond (`/healthz`, `/ready`)
- [ ] **Resources requests/limits** set (Kyverno rejects without them)
- [ ] **Image from Harbor** (Kyverno mutates, but verify it resolves)
- [ ] **Image signed** with cosign (Kyverno rejects unsigned in PRD/HML/BTC)
- [ ] **Multi-arch image** (amd64 + arm64) — single-arch breaks Graviton scheduling
- [ ] **ExternalSecret syncs** successfully (`kubectl get externalsecret -n <ns>`)
- [ ] **ServiceAccount** exists with correct IRSA annotation

### Recommended (best practice)

- [ ] **Telemetry flowing** — check traces in Tempo, metrics in VictoriaMetrics
- [ ] **PDB configured** (default in app chart, but verify)
- [ ] **Autoscaling** configured (KEDA ScaledObject)
- [ ] **Ingress/Route** configured for external access
- [ ] **Alerting rules** created for the service
- [ ] **Grafana dashboard** provisioned

### Verification commands

```bash
# Check ArgoCD sync status
argocd app get dpm-my-new-service-dev

# Check pod is running with correct labels
kubectl get pods -n dpm -l app.kubernetes.io/name=dpm-my-new-service --show-labels

# Check ExternalSecret sync
kubectl get externalsecret -n dpm dpm-my-new-service

# Check telemetry (trace test)
curl http://my-new-service.dpm.svc.cluster.local:8080/healthz
# Then query Tempo for service.name=dpm-my-new-service
```

## Common Pitfalls

| Pitfall | Symptom | Fix |
|---------|---------|-----|
| Missing `CostCenter` label | Telemetry not enriched, FinOps blind | Add to values.yaml |
| Single-arch image | Pod pending on Graviton nodes | Build for linux/amd64 + linux/arm64 |
| No health endpoints | Pod never becomes Ready, Rollout stuck | Implement `/healthz` and `/ready` |
| Wrong ExternalSecret key | Pod CrashLoopBackOff (missing env) | Verify secret exists in AWS SM |
| Missing IRSA annotation | AccessDenied on AWS API calls | Add `eks.amazonaws.com/role-arn` |
| `latest` image tag | Non-reproducible, ArgoCD can't detect changes | Use immutable SHA tags |
| No `resources.requests` | Kyverno rejects pod | Set CPU + memory requests |
| Unsigned image (PRD) | Kyverno rejects pod | Sign with cosign in CI |
| Wrong namespace in values | App deploys to wrong namespace | Match ApplicationSet destination |
| Missing `SERVICE_NAME` env | OTel reports as `my-service` (default) | Set in configMap |

## Reference: DPM Domain Structure

```
02-KUBE/ENVIRONMENTS/
├── dpm-environments-dev/
│   └── dev/
│       ├── people-api/values.yaml
│       ├── people-process/values.yaml
│       └── kyc-api/values.yaml
├── dpm-environments-prd/
│   └── prd/
│       ├── people-api/values.yaml
│       └── people-process/values.yaml
└── dpm-environments-btc/
    └── btc/
        └── people-batch/values.yaml
```

## Anti-patterns

- ❌ Deploying without going through environments repo (manual `kubectl apply`)
- ❌ Skipping DEV/HML and deploying directly to PRD
- ❌ Copy-pasting values.yaml without updating environment-specific fields
- ❌ Hardcoding image tags (CI pipeline should update them)
- ❌ Missing mandatory labels (silent failures in telemetry and cost allocation)
- ❌ Creating service without ExternalSecret (hardcoded secrets in configMap)
- ❌ No multi-arch build (breaks Graviton scheduling, increases costs)
- ❌ Skipping cosign signing (Kyverno blocks in PRD/HML/BTC)
- ❌ Not testing in HML before PRD ("it worked in dev")

## Related

- `helmfile-applicationset` skill — ApplicationSet rendering pattern
- `helm-chart-app` skill — all values.yaml options for the app chart
- `helm-chart-cronworkflow` skill — batch job onboarding
- `argocd-patterns` skill — sync policies, hooks, generators
- `ci-cd-conventions` steering — pipeline stages, image tagging
- `aws-tag-policies` steering — mandatory tag values
