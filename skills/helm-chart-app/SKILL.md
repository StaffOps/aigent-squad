---
name: helm-chart-app
description: "<ORG> corporate app Helm chart. Use when deploying services to EKS, configuring Rollouts/Deployments/StatefulSets, KEDA autoscaling, Istio routing, ExternalSecrets, or mandatory labels. Covers all values.yaml options, deployment types, strategies, and common patterns."
keywords: [helm-chart-app, helm, chart, app, "helm chart", "chart app", eks, istio, keda]
---
# <ORG> Corporate App Helm Chart

## Overview

The `app/` chart is the **most used Helm chart at <ORG>**. It standardizes application deployments across all EKS clusters.

- **Location**: `02-KUBE/00-CONFIG/helm-charts/app/`
- **Chart version**: `0.2.0-alpha`
- **Chart type**: `application`
- **Dependencies**: None (self-contained with raw templates)

## Deployment Types

| `deploymentType` | Use case | Default strategy |
|------------------|----------|-----------------|
| `Rollout` | APIs, web services (default) | Canary |
| `StatefulSet` | Services needing persistent volumes | RollingUpdate |
| `Deployment` | Standard workloads without progressive delivery | RollingUpdate |

### Strategy per deployment type

```yaml
# Rollout strategies
deploymentType: Rollout
strategy: Canary       # or BlueGreen

# StatefulSet strategies
deploymentType: StatefulSet
strategy: RollingUpdate  # or OnDelete

# Deployment strategies
deploymentType: Deployment
strategy: RollingUpdate  # or Recreate
```

## Mandatory Labels

Every deployment MUST include these labels. Kyverno rejects pods without them.

```yaml
area: "dpm"                          # Team sigla (lowercase)
costCenter: "Program-DataPlatform"   # Official CostCenter from tags.md
costScope: "API"                     # CostScope enum value
costProject: "PEOPLE"                # UPPERCASE with dashes
environment: "PRD"                   # PRD | HML | DEV | BTC
```

Without `costCenter`, the OTel Collector `k8sattributesprocessor` will NOT enrich telemetry.

## Image Configuration

```yaml
image:
  repository: harbor.<org>.com/<org>-images/dpm-people-api
  tag: "a1b2c3d"          # Immutable SHA tag from CI
  pullPolicy: IfNotPresent
```

Never use `latest` in production manifests. Kyverno warns on mutable tags.

## Service

```yaml
service:
  port: 8080   # Used by ingress, probes, and service definition
```

The port value propagates to Service, Ingress backend, and probe targets.

## Resources

**<ORG> convention: requests MUST equal limits.** This ensures QoS class `Guaranteed` and predictable scheduling.

```yaml
resources:
  requests:
    cpu: "250m"
    memory: "256Mi"
  limits:
    cpu: "250m"
    memory: "256Mi"
  resizePolicy:
    - resourceName: cpu
      restartPolicy: NotRequired
    - resourceName: memory
      restartPolicy: NotRequired
```

`resizePolicy` enables in-place pod vertical scaling (ScaleOps integration) without pod restart.

## Autoscaling (KEDA)

```yaml
autoscaling:
  enabled: true
  offHoursScaleDown: true    # Scale to 0 outside business hours (DEV only)
  minReplicas: 2
  maxReplicas: 10
  pollingInterval: 30
  triggers:
    - type: cpu
      metadata:
        type: Utilization
        value: "70"
  advanced:
    horizontalPodAutoscalerConfig:
      behavior:
        scaleDown:
          stabilizationWindowSeconds: 300
          policies:
            - type: Percent
              value: 25
              periodSeconds: 60
        scaleUp:
          stabilizationWindowSeconds: 0
          policies:
            - type: Percent
              value: 100
              periodSeconds: 15
```

`offHoursScaleDown` creates a cron trigger that scales to 0 replicas outside 08:00-20:00 BRT (DEV only).

## Probes

```yaml
livenessProbe:
  httpGet:
    path: /healthz
    port: http
  initialDelaySeconds: 5
  periodSeconds: 10
  failureThreshold: 3

readinessProbe:
  httpGet:
    path: /ready
    port: http
  initialDelaySeconds: 5
  periodSeconds: 5
  failureThreshold: 3
```

For gRPC services:

```yaml
livenessProbe:
  grpc:
    port: 5100
readinessProbe:
  grpc:
    port: 5100
```

## PodDisruptionBudget

Always enabled. Ensures availability during node drains and rollouts.

```yaml
pdb:
  maxUnavailable: "25%"
```

## Node Configuration

```yaml
node:
  arch: amd64              # amd64 | arm64
  capacity_type: on-demand # on-demand | spot
  azs: "us-east-1a,us-east-1b"
```

Karpenter uses these as scheduling constraints via `nodeSelector` and `topologySpreadConstraints`.

## ConfigMap

Non-sensitive environment variables:

```yaml
configMap:
  SERVICE_NAME: "dpm-people-api"
  ENVIRONMENT: "PRD"
  OTEL_EXPORTER_OTLP_ENDPOINT: "http://otel-agent-collector.monitoring:4317"
  <ORG>_OTEL_EXTRA_INSTRUMENTATION: "SQL,AWS,REDIS"
```

## ExternalSecret

AWS Secrets Manager integration via External Secrets Operator:

```yaml
externalSecret:
  refreshInterval: "1h"
  data:
    - secretKey: DATABASE_URL
      remoteRef:
        key: dpm/people-api/prd
        property: database_url
    - secretKey: REDIS_CONNECTION
      remoteRef:
        key: dpm/people-api/prd
        property: redis_connection
```

## PersistentVolumeClaim

For `StatefulSet` deployments only:

```yaml
persistentVolumeClaim:
  storageClassName: gp3    # gp3 (SSD) | st1 (HDD, throughput)
  accessModes:
    - ReadWriteOnce
  storage: "10Gi"
```

## ServiceAccount

IRSA annotation for pod-level AWS access:

```yaml
serviceAccount:
  create: true
  annotations:
    eks.amazonaws.com/role-arn: "arn:aws:iam::524040971621:role/dpm-people-api-role"
```

## Ingress

```yaml
ingress:
  enabled: true
  ingressClassName: "nginx-dpm-internal"  # nginx-<team>-<internal|external>
  annotations:
    cert-manager.io/cluster-issuer: "aws-privateca-issuer"
    external-dns.alpha.kubernetes.io/hostname: "people-api.<org>.internal"
  hosts:
    - host: people-api.<org>.internal
      paths:
        - path: /
          pathType: Prefix
  tls:
    - secretName: people-api-tls
      hosts:
        - people-api.<org>.internal
```

## Route (Gateway API)

Recommended for new deployments (replacing Ingress):

```yaml
route:
  enabled: true
  parentRef:
    name: <org>-gateway
    namespace: istio-gateway
    sectionName: https
  hostnames:
    - "people-api.<org>.internal"
  matches:
    - path:
        type: PathPrefix
        value: /
  httpsRedirect: true
```

## Istio Traffic Policy

```yaml
istioTrafficPolicy:
  loadBalancer:
    simple: LEAST_REQUEST
```

Applied via `DestinationRule`. `LEAST_REQUEST` distributes traffic to the pod with fewest active requests.

## ServiceMonitor

```yaml
serviceMonitor:
  enabled: true
  port: 8081
  interval: "15s"
  path: /metrics
```

Port 8081 is the standard metrics port (separate from application port).

## Templates Included

| Template | Purpose |
|----------|---------|
| `_pod.tpl` | Pod spec (containers, volumes, security context) |
| `_helpers.tpl` | Name/label/selector helpers |
| `_validations.tpl` | Pre-render validation (mandatory labels, resources) |
| `rollout.yaml` | Argo Rollout resource |
| `deployment.yaml` | Standard Deployment |
| `statefulset.yaml` | StatefulSet with volumeClaimTemplates |
| `service.yaml` | ClusterIP Service |
| `ingress.yaml` | NGINX Ingress |
| `route.yaml` | Gateway API HTTPRoute |
| `virtualservice.yaml` | Istio VirtualService (legacy) |
| `destinationrule.yaml` | Istio DestinationRule |
| `scaledobject.yaml` | KEDA ScaledObject |
| `externalsecret.yaml` | External Secrets Operator |
| `configmap.yaml` | ConfigMap from values |
| `pdb.yaml` | PodDisruptionBudget |
| `serviceaccount.yaml` | ServiceAccount with IRSA |
| `servicemonitor.yaml` | Prometheus ServiceMonitor |
| `triggerauthentication.yaml` | KEDA TriggerAuthentication |

## Common Patterns

### API Deployment (Rollout + Canary)

```yaml
deploymentType: Rollout
strategy: Canary
image:
  repository: harbor.<org>.com/<org>-images/dpm-people-api
  tag: "a1b2c3d"
service:
  port: 8080
area: "dpm"
costCenter: "Program-DataPlatform"
costScope: "API"
costProject: "PEOPLE"
environment: "PRD"
resources:
  requests: { cpu: "500m", memory: "512Mi" }
  limits: { cpu: "500m", memory: "512Mi" }
autoscaling:
  enabled: true
  minReplicas: 3
  maxReplicas: 15
```

### Background Process

```yaml
deploymentType: Deployment
strategy: RollingUpdate
service:
  port: 8080
costScope: "PROCESS"
autoscaling:
  enabled: true
  triggers:
    - type: aws-sqs-queue
      metadata:
        queueURL: "https://sqs.us-east-1.amazonaws.com/524040971621/dpm-queue"
        queueLength: "5"
```

### StatefulSet with PVC

```yaml
deploymentType: StatefulSet
strategy: RollingUpdate
persistentVolumeClaim:
  storageClassName: gp3
  storage: "50Gi"
autoscaling:
  enabled: false
```

## Anti-patterns

- Missing mandatory labels (Kyverno rejects, OTel enrichment fails)
- `resources.requests != resources.limits` (breaks Guaranteed QoS)
- No PDB (pods evicted without protection during node drain)
- `image.tag: latest` (mutable, non-reproducible)
- No probes (pods never restart on deadlock, never removed from LB)
- Using `Deployment` for APIs (no progressive delivery, no canary)
- Hardcoded secrets in `configMap` (use `externalSecret`)
- Single replica without PDB exception documented

---

## Related skills

- `helmfile-applicationset` — how the corporate `app` chart is wired into ApplicationSets via Helmfile + bedag/raw pattern
- `helmfile-k8s-addon` — same pattern for cluster add-ons (alternative chart strategy)
- `helmfile-templating` — gotmpl + Sprig + tpl escaping for advanced values
- `argocd-patterns` — sync waves, hooks, health checks for app deploys
- `kyverno-policies` — mandatory labels enforced on every chart-rendered resource
- `helm-chart-cronworkflow` — companion chart for batch/scheduled jobs
- `telemetry-standard` — how telemetry env vars are wired into the chart
- `external-secrets-aws-sm` — `externalSecret` block in this chart
