---
name: fluent-bit-vs-otel-logs
description: "Comparison between Fluent Bit and OTel Collector filelog receiver for log collection. Use when planning migration, debugging dual log pipelines (OTLP + stdout), or understanding why naming conventions diverge between logs and traces/metrics."
keywords: [fluent-bit-vs-otel-logs, fluent, bit, vs, otel, logs, "fluent bit", "bit vs", "vs otel", "otel logs", collector, otlp]
---
# Fluent Bit vs OTel Collector for Logs

Comparison and migration considerations for log collection at <org>.

## Current state: dual log pipeline

Applications at <org> have TWO parallel log paths:

| Path | Pipeline | Format | Trace correlation |
|------|----------|--------|-------------------|
| **OTLP** | App SDK → OTel Collector → Loki | Structured, single-line | ✅ Automatic (traceId/spanId) |
| **stdout/stderr** | Container → CRI → Fluent Bit → Loki | Raw/JSON, may be multiline | ❌ Only if app emits traceId in log |

The OTel path is **authoritative** for .NET / Python apps using <org> OTel Helper.

## Why dual pipeline?

The Fluent Bit path captures what OTLP cannot:
- **Bootstrap/startup logs** (before OTel SDK initializes)
- **Crash logs** (after OTel SDK shuts down)
- **Non-instrumented apps** (don't use OTel)
- **Infrastructure components** (argo, nginx, kyverno, ingress, etc.)
- **Sidecars and init containers**

Even with full OTel adoption, you'd still need Fluent Bit (or filelog) for these.

## Naming convention mismatch

**Problem**: Fluent Bit uses different label names than OTel:

| OTel attribute | Fluent Bit field | Status |
|----------------|------------------|--------|
| `k8s.namespace.name` | `kubernetes.namespace_name` | Mismatch |
| `k8s.pod.name` | `kubernetes.pod_name` | Mismatch |
| `k8s.pod.uid` | `kubernetes.pod_id` | Mismatch |
| `k8s.node.name` | `kubernetes.host` | Mismatch |
| `k8s.deployment.name` | (not native) | Missing in Fluent Bit |

**Impact**:
- Logs in Loki use Fluent Bit naming (`kubernetes.pod_name`)
- Traces and metrics use OTel naming (`k8s.pod.name`)
- Cross-signal correlation requires manual mapping in Grafana

## Workaround: Loki label normalization at <org>

<org>'s Fluent Bit config promotes specific keys to Loki labels with normalized names:

```yaml
labels:
  service_namespace: $kubernetes['namespace_name']  # was: kubernetes.namespace_name
  k8s_pod_name: $kubernetes['pod_name']             # was: kubernetes.pod_name
  service_workload: $kubernetes['labels']['app.kubernetes.io/name']
```

This makes Loki queries consistent with metric label names (`service_namespace`, `service_workload`).

## OTel Collector filelog receiver — the alternative

```yaml
receivers:
  filelog:
    include:
      - /var/log/containers/*.log
    operators:
      - type: container
        # ... CRI parsing
      - type: kubernetes_metadata
        # ... k8s enrichment

processors:
  k8sattributes:
    extract:
      metadata:
        - k8s.namespace.name
        - k8s.pod.name
        - k8s.deployment.name
```

### Benefits
- ✅ Same naming as traces/metrics (`k8s.namespace.name`)
- ✅ Same `k8sattributesprocessor` enrichment
- ✅ Single tool to maintain (OTel Collector everywhere)
- ✅ Native multiline support
- ✅ Direct OTLP export to Loki (or Tempo for log-trace links)

### Trade-offs
- ❌ More resource-heavy than Fluent Bit (Go vs C)
- ❌ Less mature for log-specific features (some Fluent Bit filters not yet in filelog)
- ❌ Migration effort for existing pipelines
- ❌ Multiline parsing config differs from Fluent Bit
- ❌ Need to update all Loki dashboards/queries that use Fluent Bit label names

## Performance comparison (rough)

| Metric | Fluent Bit | OTel filelog |
|--------|------------|--------------|
| Memory per node | ~50-100MB | ~150-300MB |
| CPU per 1k logs/sec | low | medium |
| Throughput | very high | high |
| Configuration complexity | medium | medium-high |

For most <org> workloads, both are fine. Performance is rarely the deciding factor.

## Migration plan (if/when adopted)

### Phase 1: Side-by-side
- Keep Fluent Bit running
- Deploy OTel filelog DaemonSet
- Send to a different Loki tenant or with different labels
- Validate parity

### Phase 2: Cutover one namespace at a time
- Disable Fluent Bit for namespace X
- Validate logs flowing via OTel
- Update Loki dashboards to use OTel label names
- Rollback if issues

### Phase 3: Full migration
- Remove Fluent Bit DaemonSet
- Update all dashboards/alerts to OTel naming

### Phase 4: Cleanup
- Remove `service_namespace` workaround labels
- Use `k8s.namespace.name` everywhere

## When to migrate

**Migrate when:**
- You want unified k8s metadata across all signals
- Adopting more advanced k8sattributes features (annotations, owner refs)
- Standardizing on OTel Collector for everything
- Correlation issues between logs and traces/metrics become painful

**Don't migrate when:**
- Current pipeline works fine
- Resource pressure on agent nodes (Fluent Bit is lighter)
- Critical Fluent Bit features not yet in filelog
- No bandwidth for migration

## Decision at <org> (May 2026)

**Status**: Stay with Fluent Bit. OTel filelog migration on roadmap but not active.

**Reasoning**: Current pipeline is stable, naming workarounds in place, no urgent driver. Will revisit when Loki query inconsistency becomes a real pain point or when we need k8sattributes features Fluent Bit doesn't provide.

## Reference

- Fluent Bit Loki output: `fluent-bit-docs/pipeline/outputs/loki.md`
- OTel filelog receiver: `opentelemetry.io/content/en/docs/collector/configuration.md`
- k8sattributesprocessor: `data/collector/processors/k8sattributes.yml`
- Related skill: `fluent-bit-loki-pipeline`
