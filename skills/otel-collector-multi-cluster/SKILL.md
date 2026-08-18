---
name: otel-collector-multi-cluster
description: "OTel Collector multi-cluster topology at <org>. Use when troubleshooting cross-cluster trace routing, k8sattributes processor issues, gateway loadbalancing, or designing collector pipelines. Covers agent \u2192 gateway \u2192 OTLP collector chain, RBAC requirements, hostname matching for GRPCRoutes."
keywords: [otel-collector-multi-cluster, otel, collector, multi, cluster, "otel collector", "collector multi", "multi cluster", otlp, rbac]
---
# OTel Collector Multi-Cluster Topology

The validated production topology for OpenTelemetry collection at <org>.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│ App (.NET / Python / Go SDK)                                │
│ → OTLP gRPC :4317 (or HTTP :4318, or HTTPS :443 via TLS)    │
└────────────────────────┬────────────────────────────────────┘
                         ↓
┌─────────────────────────────────────────────────────────────┐
│ otel-agent-collector (DaemonSet)                            │
│ Service: otel-agent-collector.monitoring:4317               │
│ ├── k8sattributes (k8s.namespace.name, k8s.pod.name, etc.)  │
│ ├── attributes (telemetry.source, eks_cluster)              │
│ ├── batch                                                   │
│ ├── loadbalancing/traces → gateway per-instance services    │
│ └── otlp (logs/metrics) → gateway service                   │
└────────────────────────┬────────────────────────────────────┘
                         ↓
┌─────────────────────────────────────────────────────────────┐
│ otel-gateway-collector (StatefulSet, 3 replicas)            │
│ Services: otel-gateway-collector-{0,1,2}.monitoring:4317    │
│ ├── tail_sampling (errors, high-latency, env-based prob)    │
│ ├── transform/strip-external-debug                          │
│ └── loadbalancing → otel-otlp-collector (k8s resolver)      │
└────────────────────────┬────────────────────────────────────┘
                         ↓
┌─────────────────────────────────────────────────────────────┐
│ opentelemetry-otlp-collector (Deployment, 3 replicas)       │
│ └── routing → Tempo / VictoriaMetrics / Loki / Pyroscope    │
└─────────────────────────────────────────────────────────────┘
```

## Service DNS reference

| Component | DNS | Port |
|-----------|-----|------|
| Agent Collector | `otel-agent-collector.monitoring` | 4317 |
| Gateway (main service) | `otel-gateway-collector.monitoring` | 4317 |
| Gateway (per-instance, StatefulSet) | `otel-gateway-collector-{0,1,2}.monitoring` | 4317 |
| OTLP Collector | `opentelemetry-otlp-collector.monitoring` | 4317 |
| Tempo Gateway | `tempo-gateway.monitoring` | 80 |

## Cross-cluster routing (DEV/PRD → core-devops)

When telemetry crosses clusters:

```
┌─────────────────────────────────────────────────────────────┐
│ DEV Cluster                                                 │
│                                                             │
│ App (HTTPS) → otelcollector-prd.<old-internal-domain>:443            │
│     ↓ (ServiceEntry + use-waypoint: none)                   │
│ otel-agent-collector:443 (TLS terminate)                    │
│     ↓ (loadbalancing/traces exporter)                       │
│ otel-gateway-{0,1,2}.<org-domain>:4317 (NLB → core-devops)    │
└─────────────────────────────────────────────────────────────┘
                         ↓
┌─────────────────────────────────────────────────────────────┐
│ CORE-DEVOPS Cluster                                         │
│                                                             │
│ istio-olly-internal Gateway (TLS terminate on 4317/4318)    │
│     ↓ (GRPCRoute)                                           │
│ otel-gateway-collector (StatefulSet)                        │
│     ↓                                                       │
│ Tempo / VictoriaMetrics / Loki                              │
└─────────────────────────────────────────────────────────────┘
```

## Responsibility per layer

| Layer | Responsibilities |
|-------|------------------|
| **Cluster Collector (Agent)** | K8s enrichment via k8sattributesprocessor, static resource attrs (cluster.name, cloud.provider, cloud.region), batching |
| **Gateway Collector** | Tail-based sampling (traces only), OTTL transformations, metric aggregation |
| **OTLP Collector → Backend** | Routing: traces→Tempo, metrics→VictoriaMetrics, logs→Loki, profiles→Pyroscope |

## Why sampling MUST be at Gateway (not Agent)

Tail-based sampling requires the COMPLETE trace to decide. If sampled at the Cluster (Agent) Collector, each instance only sees its own spans — distributed traces across pods/clusters get inconsistent sampling decisions, breaking traces.

## Why k8s enrichment MUST be at Cluster (Agent) Collector

The `k8sattributesprocessor` needs access to the local cluster's Kubernetes API. The Gateway Collector in the core-devops cluster cannot access the K8s API of DEV/HML/PRD clusters.

## Critical RBAC: gateway loadbalancing exporter

Gateway uses `loadbalancing` exporter with `k8s` resolver to discover OTLP Collector pods. Required RBAC:

```yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: otel-gateway-collector
rules:
  - apiGroups: ["discovery.k8s.io"]
    resources: ["endpointslices"]
    verbs: ["list", "watch", "get"]
  - apiGroups: [""]
    resources: ["endpoints"]
    verbs: ["list", "watch", "get"]
```

**Without this**: Gateway enters error loop (`endpointslices forbidden`), rejects all incoming connections, entire pipeline blocked.

## Critical: hostname matching for GRPCRoute

The `loadbalancing` exporter hostnames MUST exactly match the GRPCRoute hostnames in the destination cluster.

**Correct:**
```yaml
exporters:
  loadbalancing/traces:
    resolver:
      static:
        hostnames:
          - otel-gateway-0.<org-domain>:4317
          - otel-gateway-1.<org-domain>:4317
          - otel-gateway-2.<org-domain>:4317
```

GRPCRoutes:
```
otel-gateway-0.<org-domain>:4317 → otel-gateway-collector-0
otel-gateway-1.<org-domain>:4317 → otel-gateway-collector-1
otel-gateway-2.<org-domain>:4317 → otel-gateway-collector-2
```

**Wrong:** `otel-gateway-collector-0.<org-domain>:4317` (returns `Unimplemented`)

## k8sattributesprocessor field validation

The processor validates metadata field names at startup. Common mistake:

```yaml
# ❌ WRONG — pod crashes on startup
k8sattributes:
  extract:
    metadata:
      - service.namespace
```

Error: `invalid configuration: processors::k8sattributes: "service.namespace" is not a supported metadata field`

```yaml
# ✅ CORRECT
k8sattributes:
  extract:
    metadata:
      - k8s.namespace.name
      - k8s.pod.name
      - k8s.deployment.name
      - k8s.statefulset.name
      - k8s.node.name
```

## k8sattributes filter — pod must have `CostCenter` label

The processor at <org> is configured to enrich only pods with the `CostCenter` label (cost tracking requirement). Pods without it won't get enriched.

For test pods:
```bash
kubectl run test --rm -i --restart=Never \
  --labels="Environment=dev,CostCenter=test" \
  --image=curlimages/curl:latest -- ...
```

## MDT (Multi-Destination Telemetry) Pipeline

Agent has secondary export path for external MDT collector:
- Endpoint: `https://otel-mdt.<old-internal-domain>:443`
- Signals: traces, metrics, logs (via `traces/mdt`, `metrics/mdt`, `logs/mdt` pipelines)
- Processor: `attributes/mdt_origin` adds `telemetry.origin=eks`
- Does NOT run k8sattributes or tail_sampling

## Receivers on Agent

```yaml
receivers:
  otlp:
    protocols:
      grpc:
        endpoint: 0.0.0.0:4317
      http:
        endpoint: 0.0.0.0:4318

  otlp/tls:
    protocols:
      http:
        endpoint: 0.0.0.0:443
        tls:
          cert_file: /certs/tls.crt
          key_file: /certs/tls.key

  host_metrics:
    collection_interval: 30s
    scrapers:
      cpu: {}
      memory: {}
      network: {}
```

## Cluster contexts at <org>

| Context | Cluster | Region | Purpose |
|---------|---------|--------|---------|
| `dev` | <org>-eks-dev | us-east-1 | Development |
| `prd-nv` | <org>-eks-prd-nv | us-east-1 | Production |
| `core-devops` | <org>-eks-core | us-east-1 | Observability backends |
| (arn) | <org>-eks-prd | us-east-1 | Production |

## Reference

- OTel Collector docs: https://opentelemetry.io/docs/collector/
- Local cache: `<workspace>/01-DEVOPS/EXTERNAL-DOCS/opentelemetry.io/content/en/docs/collector/`
- Config: `<workspace>/02-KUBE/00-CONFIG/k8s-setup/monitoring/opentelemetry-collector/`
- Related skills: `istio-ambient-otel`, `istio-ambient-debugging`, `multicluster-label-strategy`

## otel-process — Spanmetrics per team

The otel-process StatefulSet (5 replicas) generates span metrics from traces. Metrics are split per team using filter processors and separate connectors:

```yaml
# Filter by service.namespace prefix (glob match)
filter/dpm:
  traces:
    span:
      - 'not IsMatch(resource.attributes["service.namespace"], "dpm.*")'

filter/apps:
  traces:
    span:
      - 'not IsMatch(resource.attributes["service.namespace"], "apps.*")'

filter/general:
  traces:
    span:
      - 'IsMatch(resource.attributes["service.namespace"], "dpm.*")'
      - 'IsMatch(resource.attributes["service.namespace"], "apps.*")'
```

Each team gets its own `span_metrics/<team>` connector with potentially different dimensions. All share namespace `spanmetrics.apm` — team separation is via `service.namespace` in `resource_metrics_key_attributes`.

### Why per-team connectors

- Teams can index custom dimensions (ex: `requesting.dataset` for dpm, custom attrs for apps)
- `general` connector uses fewer dimensions → lower cardinality for teams that don't need extras
- Adding a new team = new filter + connector + pipeline, no impact on others

### Pipeline structure

```
traces pipeline → Tempo (all spans, unfiltered)
traces/spanmetrics_dpm → filter/dpm → span_metrics/dpm
traces/spanmetrics_apps → filter/apps → span_metrics/apps
traces/spanmetrics_general → filter/general → span_metrics/general
metrics/spanmetrics → [all 3 connectors] → prometheusremotewrite
```

## otel-agent — Promoting `pod` label on metrics

The k8sattributes processor adds `k8s.pod.name` as resource attribute. To make it appear as a **metric label** (for runtime metrics like `process_runtime_dotnet_thread_pool_queue_length`), promote it in the `context: datapoint` transform:

```yaml
transform/promote_labels:
  metric_statements:
    - context: datapoint
      statements:
        - set(attributes["pod"], resource.attributes["k8s.pod.name"]) where resource.attributes["k8s.pod.name"] != nil
```

### Cardinality note

This multiplies series for **all OTLP metrics** by number of pods. Acceptable for runtime metrics (few series per pod). If cardinality becomes a problem, add a condition:

```yaml
# Only for runtime metrics (future refinement)
- set(attributes["pod"], resource.attributes["k8s.pod.name"]) where resource.attributes["k8s.pod.name"] != nil and IsMatch(metric.name, "process_runtime_*")
```

### Why on the agent (not gateway)

- Agent runs on every cluster and has k8sattributes with local K8s API access
- Gateway (core-devops only) doesn't have K8s API access to workload clusters
- If not promoted at agent level, the info is lost before reaching the gateway
