---
name: otel-ebpf-instrumentation
description: "OpenTelemetry eBPF Instrumentation (OBI) configuration at <org>. Use when configuring auto-instrumentation for apps without SDK, network metrics, context propagation, service discovery, or tuning eBPF performance. Covers DaemonSet deployment, discovery by namespace, network inter-zone (FinOps), context propagation, routes/filters, and cardinality control."
keywords: [otel-ebpf-instrumentation, otel, ebpf, instrumentation, "otel ebpf", "ebpf instrumentation", opentelemetry, obi, sdk]
---
# OTel eBPF Instrumentation (OBI)

Auto-instrumentation via eBPF for apps without OTel SDK. Generates HTTP/gRPC/SQL/Redis traces and metrics without code changes.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│ App (no OTel SDK)                                           │
└────────────────────────┬────────────────────────────────────┘
                         │ (eBPF hooks in the kernel)
                         ↓
┌─────────────────────────────────────────────────────────────┐
│ otel-ebpf-instrumentation (DaemonSet, privileged)           │
│ Image: ghcr.io/open-telemetry/opentelemetry-ebpf-           │
│        instrumentation/ebpf-instrument:v0.9.0               │
│ ├── Generates HTTP/gRPC/SQL/Redis traces                    │
│ ├── Generates application + network metrics                 │
│ └── Exports OTLP → otel-agent-collector.monitoring:4317     │
└────────────────────────┬────────────────────────────────────┘
                         ↓
┌─────────────────────────────────────────────────────────────┐
│ otel-agent → gateway → process → Tempo + VictoriaMetrics    │
└─────────────────────────────────────────────────────────────┘
```

## Key behavior

- **Ignores apps with OTel SDK** by default (`exclude_otel_instrumented_services: true`)
- OBI traces pass through the agent's tail sampling — same rules (10% PRD, 100% errors/high-latency)
- Traces feed the span_metrics connector in otel-process (service graph)
- Config via YAML file mounted as ConfigMap (not env vars)
- Not an OTel Collector — standalone binary with proprietary config

## Deployment (<org> pattern)

Raw manifest in `monitoring/opentelemetry-collector/obi/`:
- `collector.yaml` — DaemonSet + ServiceAccount + ClusterRole + ClusterRoleBinding + ConfigMap
- `config.yaml` — OBI config (injected via `tpl(readFile(...))`)

Follows the same organizational pattern as `profile/`, `agent/`, `gateway/`, `process/`.

## Discovery — Allow-list by team namespace

```yaml
discovery:
  exclude_instrument:
    - exe_path: '{*ebpf-instrument*,*otelcol*}'
  instrument:
    - k8s_namespace: 'ai*'
    - k8s_namespace: 'acum*'
    - k8s_namespace: 'apps*'
    - k8s_namespace: 'bm*'
    - k8s_namespace: 'ctp*'
    - k8s_namespace: 'dcp*'
    - k8s_namespace: 'deng*'
    - k8s_namespace: 'devops*'
    - k8s_namespace: 'dpm*'
    - k8s_namespace: 'mdt*'
    - k8s_namespace: 'plg*'
    - k8s_namespace: 'qua*'
```

New team → add here. Glob accepts `*` as wildcard (e.g., `dpm*` matches `dpm`, `dpm-people`, `dpm-benefits`).

### Additional available filters (not used by default)

| Filter | Example |
|--------|---------|
| `k8s_deployment_name` | `'my-deploy*'` |
| `k8s_pod_labels` | `{instrument: obi}` |
| `k8s_pod_annotations` | `{obi.instrument: 'true'}` |
| `open_ports` | `'8080,8443'` |
| `languages` | `'go'`, `'java'` |
| `containers_only` | `true` |

## Context propagation

```yaml
ebpf:
  context_propagation: headers
```

Injects `traceparent` into HTTP/1.1 requests leaving apps without SDK. Creates distributed E2E traces between legacy apps and SDK-instrumented apps.

- `headers` mode: HTTP headers only, no `hostNetwork` or `CAP_NET_ADMIN` required
- `tcp` mode: also works with HTTPS (injects at TCP level), requires `hostNetwork` + `CAP_NET_ADMIN`
- gRPC and HTTP/2: **not supported** in `tcp` mode

## Network metrics

```yaml
metrics:
  features: ['application', 'network_inter_zone']
network:
  enable: true
  allowed_attributes:
    - k8s.src.owner.name
    - k8s.src.namespace
    - k8s.dst.owner.name
    - k8s.dst.namespace
    - k8s.src.owner.type
    - k8s.dst.owner.type
  cidrs:
    - cidr: 172.30.0.0/16
      name: 'vpc-nv'
    - cidr: 172.25.0.0/16
      name: 'vpc-oh'
    - cidr: 172.28.0.0/16
      name: 'vpc-sp'
    - cidr: 10.0.0.0/16
      name: 'k8s-services'
    - cidr: 169.254.0.0/16
      name: 'aws-link-local'
    - cidr: 0.0.0.0/0
      name: 'external'
```

### Generated metrics

| Metric | What it measures |
|--------|-----------------|
| `obi_network_flow_bytes_total` | Bytes between endpoints with src/dst owner and namespace |
| `obi_network_inter_zone_bytes_total` | Cross-AZ bytes (AWS cost ~$0.01-0.02/GB) |

### Cardinality control

- `allowed_attributes`: aggregate by **owner** (Deployment), not by individual pod
- `cidrs`: classify traffic into known categories (vpc, services, aws, external)

### Network filter — Allow-list by namespace

```yaml
filter:
  network:
    k8s_dst_namespace:
      match: '{ai*,acum*,apps*,bm*,ctp*,dcp*,deng*,devops*,dpm*,mdt*,plg*,qua*}'
    k8s_src_namespace:
      match: '{ai*,acum*,apps*,bm*,ctp*,dcp*,deng*,devops*,dpm*,mdt*,plg*,qua*}'
```

Uses `match` (allow-list) instead of `not_match` (deny-list) — infra is automatically ignored without maintenance.

## Routes — URL cardinality control

```yaml
routes:
  ignored_patterns:
    - /healthz
    - /ready
    - /metrics
    - /health
    - /live
    - /ping
  unmatched: heuristic
```

- `ignored_patterns`: drops traces/metrics from healthchecks (reduces volume 30-50%)
- `patterns`: defines templates to group URLs (e.g., `/api/v1/users/{id}`)
- `unmatched: heuristic`: tries to automatically group unmapped URLs

## Performance tuning

```yaml
ebpf:
  http_request_timeout: 30s   # requests without response → status 408
  high_request_volume: true   # prevents event drops under high load
  # wakeup_len: 1000          # reduces CPU under high load (default: 500)
```

### When to tune further

| Symptom | Action |
|---------|--------|
| High OBI CPU | `wakeup_len: 1000-2000` |
| Event drops | `high_request_volume: true` (already active) |
| Too many series | `attributes.select` to exclude labels |
| High trace volume | `otel_traces_export.sampler` with ratio |
| Irrelevant protocols causing overhead | `instrumentations: ['http', 'grpc']` |

## Available metrics features

| Feature | Description | <org> uses? |
|---------|-------------|-------------|
| `application` | http/grpc/sql/redis duration | ✅ Yes |
| `network_inter_zone` | Cross-AZ bytes | ✅ Yes |
| `network` | Flow bytes (L4) | Via `network.enable` |
| `application_service_graph` | Who calls whom | ❌ Redundant (already exists via spanmetrics connector) |
| `application_span` | Legacy spanmetrics | ❌ Redundant |
| `application_span_otel` | Spanmetrics OTel format | ❌ Redundant |
| `application_host` | Per-host metrics | ❌ Irrelevant in K8s |
| `application_span_sizes` | Request/response body sizes | Optional (future) |

## Kubernetes metadata

```yaml
attributes:
  kubernetes:
    enable: true
    meta_restrict_local_node: true  # each obi pod only stores metadata from its own node
```

Automatically decorated labels: `k8s.namespace.name`, `k8s.deployment.name`, `k8s.pod.name`, `k8s.node.name`, `k8s.container.name`, etc.

## Supported instrumentation

| Protocol | Versions |
|----------|----------|
| HTTP | 1.0/1.1 (context propagation), 2.0 (no TCP propagation) |
| gRPC | 1.0+ |
| PostgreSQL | All |
| MySQL | All |
| Redis | All |
| MongoDB | 5.0+ |
| Kafka | All |
| AWS S3/SQS | All |

## Relationship with <org> Telemetry Helper (corporate lib)

- Apps **with SDK** (via helper): OBI **ignores** automatically (no duplicate traces)
- Apps **without SDK**: OBI generates traces + metrics via eBPF
- Metrics export interval: lib uses 60s (OTel default), OBI uses 30s (configurable)
- Both export to the same endpoint: `otel-agent-collector.monitoring:4317`

## Anti-patterns

- ❌ Using `k8s_namespace: '*'` — instruments infra unnecessarily
- ❌ Deny-list in network filter — hard to maintain, prefer allow-list by namespace
- ❌ `application_service_graph` when spanmetrics connector already exists — duplicates metrics
- ❌ Sampling in OBI when tail sampling in agent already covers — double cut
- ❌ Not using `meta_restrict_local_node` in large clusters — memory waste
- ❌ Network without `allowed_attributes` — cardinality explodes (aggregates by pod)
- ❌ Not filtering healthchecks in `routes.ignored_patterns` — useless volume

## Local docs

Full documentation at:
```
01-DEVOPS/EXTERNAL-DOCS/opentelemetry.io/content/en/docs/zero-code/obi/
├── configure/    # All config options
├── setup/        # Kubernetes, Docker, standalone
├── metrics.md    # Emitted metrics
├── network/      # Network observability
└── distributed-traces.md
```
