---
name: helm-chart-cronworkflow
description: "Use when deploying scheduled batch jobs via Argo CronWorkflows on <ORG> EKS clusters. Covers the corporate cronworkflow Helm chart, schedule/concurrency config, IRSA, ExternalSecrets, mandatory labels, multi-step workflows, and <ORG> cron namespaces."
keywords: [helm-chart-cronworkflow, helm, chart, cronworkflow, "helm chart", "chart cronworkflow", argo, eks, irsa]
---
# <ORG> Corporate CronWorkflow Helm Chart

## Overview

The `cronworkflow/` chart standardizes Argo CronWorkflow deployments across <ORG> EKS clusters. It enforces mandatory labels, resource limits, retry strategies, and integrates with External Secrets Operator for AWS credentials.

- **Location**: `02-KUBE/00-CONFIG/helm-charts/cronworkflow/`
- **Chart version**: `0.5.0`
- **Chart type**: `application`
- **CRD**: `argoproj.io/v1alpha1 CronWorkflow`

## Core Concepts

### Argo CronWorkflow

A CronWorkflow is a K8s CRD that creates Workflow resources on a cron schedule. Each Workflow spawns pods that execute steps sequentially or in parallel.

```
CronWorkflow (schedule) → Workflow (instance) → Pod(s) (execution)
```

### Key fields

| Field | Purpose | <ORG> default |
|-------|---------|-------------|
| `schedule` | Cron expression (5-field) | Required |
| `timezone` | IANA timezone | `UTC` |
| `concurrencyPolicy` | How to handle overlapping runs | `Forbid` |
| `suspend` | Disable without deleting | `false` |
| `successfulJobsHistoryLimit` | Completed runs to keep | `1` |
| `failedJobsHistoryLimit` | Failed runs to keep | `1` |

### Concurrency policies

| Policy | Behavior | Use when |
|--------|----------|----------|
| `Forbid` | Skip new run if previous still running | Default — prevents resource stacking |
| `Allow` | Run concurrently | Independent idempotent jobs |
| `Replace` | Kill running, start new | Latest data matters more than completion |

## Chart Structure

```
cronworkflow/
├── Chart.yaml
├── values.yaml
├── templates/
│   ├── _helpers.tpl          # Labels, annotations, selectors
│   ├── cronworkflow.yaml     # Main CronWorkflow resource
│   └── external-secret.yaml  # Optional ESO integration
└── examples/
    ├── single-step.yaml
    └── multi-step.yaml
```

## Mandatory Labels

The chart propagates labels to CronWorkflow metadata, workflowMetadata, AND podMetadata:

```yaml
costCenter: "Program-DataPlatform"    # Official CostCenter (required)
costScope: "PROCESS"                  # CostScope enum (required)
costProject: "PEOPLE-BATCH"           # UPPERCASE with dashes (required)
# team: "DPM"                         # Optional sub-team
```

Without `CostCenter`, the OTel Collector `k8sattributesprocessor` will NOT enrich telemetry for workflow pods.

## Values Reference

### Identification & Schedule

```yaml
name: dpm-people-batch-sync
description: "Sync people data from external sources"
costCenter: "Program-DataPlatform"
costScope: "PROCESS"
costProject: "PEOPLE-BATCH"
schedule: '0 3 * * *'          # Daily at 03:00 UTC
timezone: UTC
concurrencyPolicy: Forbid
suspend: false
successfulJobsHistoryLimit: 1
failedJobsHistoryLimit: 1
```

### ServiceAccount (IRSA)

```yaml
serviceAccountName: argo-workflow    # Must have IRSA annotation for AWS access
```

The ServiceAccount is NOT created by this chart — it must exist in the namespace with proper IRSA annotation:

```yaml
# Created separately (bootstrap or external-secrets-raw)
apiVersion: v1
kind: ServiceAccount
metadata:
  name: argo-workflow
  annotations:
    eks.amazonaws.com/role-arn: arn:aws:iam::524040971621:role/ArgoWorkflowsAccessRole-<org>-eks-prd
```

### Node Configuration

```yaml
nodeConfig:
  arch: amd64              # amd64 | arm64
  capacity_type: on-demand # on-demand | spot
  nodepool: default        # Karpenter nodepool
  azs:                     # Empty = any AZ
    - us-east-1a
    - us-east-1b
```

### Ephemeral Storage (PVC)

```yaml
ephemeralStorage:
  enabled: true
  storageClassName: gp3
  storage: 10Gi
  mountPath: /mnt/
```

PVC is auto-cleaned via `volumeClaimGC.strategy: OnWorkflowCompletion`. Tags are propagated via `k8s-pvc-tagger`.

### External Secrets

```yaml
externalSecret:
  enabled: true
  refreshInterval: 10m
  data:
    - secretKey: DATABASE_PASSWORD
      remoteRef:
        key: dpm/people-batch/prd
        property: password
    - secretKey: API_KEY
      remoteRef:
        key: dpm/people-batch/prd
        property: api_key
```

When enabled, creates an `ExternalSecret` resource and injects all keys via `envFrom.secretRef`.

### Retry Strategy

```yaml
retryStrategy:
  limit: 3    # Retries per step on failure
```

### Steps (Container Definitions)

```yaml
steps:
  - name: sync-data
    container:
      image: harbor.<org>.com/<org>-images/dpm-people-batch:a1b2c3d
      imagePullPolicy: IfNotPresent
      command: ["python"]
      args: ["/app/sync.py", "--full"]
      env:
        - name: ENVIRONMENT
          value: "PRD"
        - name: SERVICE_NAME
          value: "dpm-people-batch"
        - name: OTEL_EXPORTER_OTLP_ENDPOINT
          value: "http://otel-agent-collector.monitoring:4317"
      resources:
        requests:
          cpu: 500m
          memory: 1Gi
        limits:
          cpu: 500m
          memory: 1Gi
```

### Multi-Step with Conditional Execution

```yaml
steps:
  - name: extract
    container:
      image: harbor.<org>.com/<org>-images/dpm-etl:a1b2c3d
      command: ["python", "/app/extract.py"]
      resources:
        requests: { cpu: 250m, memory: 512Mi }
        limits: { cpu: 250m, memory: 512Mi }

  - name: transform
    when: "{{steps.extract.status}} == Succeeded"
    container:
      image: harbor.<org>.com/<org>-images/dpm-etl:a1b2c3d
      command: ["python", "/app/transform.py"]
      resources:
        requests: { cpu: 1000m, memory: 2Gi }
        limits: { cpu: 1000m, memory: 2Gi }
```

Steps execute sequentially. The `when` field uses Argo Workflows expression syntax.

## <ORG> Cron Namespaces

| Namespace | Cluster | Domain | Purpose |
|-----------|---------|--------|---------|
| `dpm-crons` | core-devops | DataPlatform | Data sync, ETL, batch processing |
| `dcp-crons` | core-devops | DataCapture | Capture scheduling, crawlers |
| `devops-crons` | core-devops | Infrastructure | Maintenance, cleanup, backups |

Each namespace has:
- Dedicated `argo-workflow` ServiceAccount with IRSA
- `ClusterSecretStore` reference for ESO
- Kyverno policies enforcing mandatory labels

## ApplicationSet Integration

CronWorkflows are managed via the `crons/values.yaml.gotmpl` in each domain's `*-applicationsets/` repo:

```yaml
# dpm-applicationsets/crons/values.yaml.gotmpl
apiVersion: argoproj.io/v1alpha1
kind: ApplicationSet
metadata:
  name: dpm-crons
  namespace: argo
spec:
  generators:
    - git:
        repoURL: https://gitlab.internal/dpm/dpm-environments.git
        revision: HEAD
        directories:
          - path: "btc/*"
  template:
    spec:
      source:
        repoURL: https://gitlab.internal/devops/helm-charts.git
        path: cronworkflow
        helm:
          valueFiles:
            - "{{`{{path}}`}}/values.yaml"
      destination:
        namespace: dpm-crons
```

## Complete Example: Production Batch Job

```yaml
name: dpm-people-daily-sync
description: "Daily sync of people records from external APIs"
costCenter: "Program-DataPlatform"
costScope: "PROCESS"
costProject: "PEOPLE-BATCH"
schedule: '0 4 * * *'
timezone: America/Sao_Paulo
concurrencyPolicy: Forbid
suspend: false
successfulJobsHistoryLimit: 3
failedJobsHistoryLimit: 5
serviceAccountName: argo-workflow

nodeConfig:
  arch: arm64
  capacity_type: on-demand
  nodepool: default
  azs: []

ephemeralStorage:
  enabled: true
  storageClassName: gp3
  storage: 20Gi
  mountPath: /mnt/data

externalSecret:
  enabled: true
  refreshInterval: 1h
  data:
    - secretKey: DB_CONNECTION
      remoteRef:
        key: dpm/people-batch/prd
        property: db_connection
    - secretKey: API_TOKEN
      remoteRef:
        key: dpm/people-batch/prd
        property: api_token

retryStrategy:
  limit: 2

steps:
  - name: sync-people
    container:
      image: harbor.<org>.com/<org>-images/dpm-people-batch:f4e5d6c
      imagePullPolicy: IfNotPresent
      command: ["dotnet", "DPM.PeopleBatch.dll"]
      args: ["--mode", "full-sync"]
      env:
        - name: ENVIRONMENT
          value: "BTC"
        - name: SERVICE_NAME
          value: "dpm-people-batch"
        - name: OTEL_EXPORTER_OTLP_ENDPOINT
          value: "http://otel-agent-collector.monitoring:4317"
      resources:
        requests:
          cpu: 1000m
          memory: 2Gi
        limits:
          cpu: 1000m
          memory: 2Gi
```

## Anti-patterns

- ❌ Missing `concurrencyPolicy` (defaults to `Allow` in Argo — jobs stack up, OOM nodes)
- ❌ No `resources.requests` / `resources.limits` (unschedulable or noisy-neighbor)
- ❌ Using `latest` image tag (non-reproducible, breaks rollback)
- ❌ Hardcoded secrets in `env` values (use `externalSecret`)
- ❌ No retry strategy (transient failures kill the entire pipeline)
- ❌ Missing mandatory labels (Kyverno rejects, OTel enrichment fails)
- ❌ `successfulJobsHistoryLimit: 0` (no audit trail for debugging)
- ❌ No alerting on cron failure (silent data staleness)
- ❌ `concurrencyPolicy: Allow` without idempotency guarantee
- ❌ Pulling images from Docker Hub directly (use Harbor proxy)
- ❌ Missing `serviceAccountName` (no AWS access via IRSA)
- ❌ Single-arch image on `arm64` nodeConfig (scheduling failure)

## Related

- `helmfile-applicationset` skill — how CronWorkflows are managed via ApplicationSets
- `helm-chart-app` skill — long-running service chart (complementary)
- `external-secrets-aws-sm` skill — ESO integration details
- `aws-tag-policies` steering — mandatory tag values
