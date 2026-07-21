---
name: pyroscope-profiling-patterns
description: Use when querying Pyroscope, designing continuous profiling pipelines, or correlating profiles with traces. Covers pprof profile types, eBPF vs SDK profiling, Pyroscope architecture, trace-to-profile correlation, and <org>-specific status.
keywords: [pyroscope-profiling, pyroscope, profiling, "pyroscope profiling", sdk]
---
# Pyroscope Continuous Profiling Patterns

Continuous profiling with Grafana Pyroscope at <org> — architecture, profile types, correlation with traces, and operational patterns.

## <org> Status (May 2026)

> **⚠️ STAND-BY**: Pyroscope SDK packages were removed from <org> OTel Helper libs in April 2026 due to compatibility issues with OTel SDK 1.15.3. The `IProfilingProvider` interface is preserved as abstraction.

| Item | Status |
|------|--------|
| Pyroscope backend | ✅ Running on core-devops cluster |
| Service DNS | `pyroscope-query-frontend.monitoring:4040` |
| Grafana datasource | ✅ Configured (UID: `pyroscope`) |
| SDK integration (.NET/Python) | ❌ Removed (stand-by) |
| eBPF agent | ❌ Not deployed |
| OTel native Profiles signal | Alpha (Mar 2026) — waiting for Beta/GA |

**Re-enablement plan**: when OTel Profiles signal stabilizes OR Pyroscope SDK packages update for OTel 1.15.3+.

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    Ingestion paths                        │
│                                                          │
│  eBPF Agent ──┐                                          │
│               ├──→ Distributor → Ingester → Storage (S3) │
│  SDK Push ────┘                                          │
│                                                          │
│  OTel Collector ──→ pprof receiver → Distributor         │
│  (future)                                                │
└─────────────────────────────────────────────────────────┘

Query path:
  Grafana → Query Frontend → Querier → Storage (S3)
```

| Component | Role |
|-----------|------|
| **Distributor** | Receives profiles, validates, routes to ingesters |
| **Ingester** | Buffers in memory, flushes to object storage |
| **Storage** | S3 (long-term), local disk (WAL) |
| **Query Frontend** | Query splitting, caching, deduplication |
| **Querier** | Reads from storage + ingesters |

## Profile Types (pprof)

| Type | Metric | Unit | What it measures |
|------|--------|------|------------------|
| `cpu` | `process_cpu` | nanoseconds | CPU time consumed |
| `alloc_objects` | `memory` | count | Total allocations (cumulative) |
| `alloc_space` | `memory` | bytes | Total bytes allocated (cumulative) |
| `inuse_objects` | `memory` | count | Live objects in heap |
| `inuse_space` | `memory` | bytes | Live bytes in heap |
| `goroutine` | `goroutine` | count | Active goroutines (Go only) |
| `mutex` | `mutex` | nanoseconds | Time blocked on mutexes |
| `block` | `block` | nanoseconds | Time blocked on synchronization |

### Profile type IDs (Grafana query)

Format: `<name>:<sample_type>:<sample_unit>:<period_type>:<period_unit>`

```
process_cpu:cpu:nanoseconds:cpu:nanoseconds
memory:alloc_objects:count:space:bytes
memory:alloc_space:bytes:space:bytes
memory:inuse_objects:count:space:bytes
memory:inuse_space:bytes:space:bytes
goroutine:goroutine:count:goroutine:count
mutex:contentions:count:count:count
block:delay:nanoseconds:count:count
```

## eBPF vs SDK Profiling

| Aspect | eBPF (agent) | SDK (in-process) |
|--------|--------------|------------------|
| Code changes | None | Requires SDK integration |
| Granularity | Function-level (kernel symbols) | Line-level + runtime metadata |
| Languages | Any compiled language | Language-specific SDK |
| Overhead | ~1-3% CPU | ~2-5% CPU |
| Trace correlation | ❌ No (no span context) | ✅ Yes (span tags) |
| Deployment | DaemonSet on nodes | Per-application |
| Profile types | CPU only | CPU, memory, goroutine, mutex, block |

**Recommendation**: Use eBPF for broad visibility (all pods, no code change). Use SDK for deep analysis + trace correlation on critical services.

## Trace-to-Profile Correlation

When SDK profiling is active, profiles are tagged with span context:

```
pyroscope.profile.id → links profile session to trace
span_id → specific span being profiled
trace_id → parent trace
```

### OTel Collector integration (future)

```yaml
# OTel Collector config — pprof receiver
receivers:
  pyroscope:
    protocols:
      http:
        endpoint: 0.0.0.0:4040

processors:
  attributes:
    actions:
      - key: service.name
        from_attribute: service.name
        action: upsert

exporters:
  otlp/pyroscope:
    endpoint: pyroscope-distributor.monitoring:4040
    tls:
      insecure: true

service:
  pipelines:
    profiles:
      receivers: [pyroscope]
      processors: [attributes]
      exporters: [otlp/pyroscope]
```

### Grafana datasource correlation

```yaml
# Tempo datasource — tracesToProfiles
tracesToProfiles:
  datasourceUid: pyroscope
  tags:
    - key: service.name
      value: service_name
  profileTypeId: process_cpu:cpu:nanoseconds:cpu:nanoseconds
  customQuery: true
  query: '{service_name="$${__tags.service_name}"}'
```

This enables: click on a Tempo span → view CPU profile for that time window.

## <org> Datasource Configuration

| Field | Value |
|-------|-------|
| Name | Pyroscope |
| Type | `grafana-pyroscope-datasource` |
| UID | `pyroscope` |
| URL | `http://pyroscope-query-frontend.monitoring:4040` |
| Cluster | `<org>-eks-prd` (core-devops) |

### Querying Pyroscope directly

```bash
# From core-devops cluster
kubectl run pyro-q -n monitoring --rm -i --restart=Never \
  --image=curlimages/curl:latest -- -s \
  "http://pyroscope-query-frontend.monitoring:4040/pyroscope/render?query=process_cpu:cpu:nanoseconds:cpu:nanoseconds{service_name=\"my-service\"}&from=now-1h&until=now"
```

## When Re-enabled: Integration Pattern

### .NET (planned)

```csharp
// <org> OTel Helper — when profiling returns
services.AddBDCOtel(options => {
    options.EnableProfiling = true;  // IProfilingProvider interface
});
```

### Python (planned)

```python
from bdc_otel import setup_telemetry
setup_telemetry(enable_profiling=True)
```

### Correlation via tracestate + span attribute

When profiling is active, the <org> libs will:
1. Start profiling session per root span
2. Tag span with `pyroscope.profile.id`
3. Set `tracestate: pyroscope=<profile_id>`
4. Stop profiling when span ends
5. Push profile to Collector pprof receiver

## Common Queries (Grafana Explore)

### CPU profile for a service (last 1h)
```
process_cpu:cpu:nanoseconds:cpu:nanoseconds{service_name="dpm-people-api"}
```

### Memory allocations
```
memory:alloc_space:bytes:space:bytes{service_name="dpm-people-api"}
```

### Compare two time ranges (diff view)
Select "Comparison" mode in Grafana → pick baseline vs comparison range.

### Filter by label
```
process_cpu:cpu:nanoseconds:cpu:nanoseconds{service_name="my-svc", namespace="dpm"}
```

## Labels Strategy

Pyroscope labels come from the profiling agent/SDK. Required labels:

| Label | Source | Required |
|-------|--------|----------|
| `service_name` | `SERVICE_NAME` env var | ✅ Yes |
| `namespace` | K8s namespace (from agent) | ✅ Yes |
| `pod` | K8s pod name | Recommended |
| `node` | K8s node name | Optional |

Without `service_name`, profiles are unattributable — useless for debugging.

## Anti-patterns

- ❌ **Profiling all pods with SDK** — high overhead, cardinality explosion in storage. Use eBPF for broad coverage, SDK only for critical services.
- ❌ **Missing `service_name` label** — profiles without service attribution are noise. Always set via `SERVICE_NAME` env var.
- ❌ **No retention/rotation policy** — profiles are large. Without lifecycle rules on S3, storage grows unbounded.
- ❌ **CPU limits on profiled pods** — CPU profiling under throttling produces misleading flamegraphs (shows throttle waits, not real hotspots).
- ❌ **Profiling in BTC (batch) without sampling** — batch jobs produce massive profiles. Sample or profile only on error.
- ❌ **Direct Pyroscope SDK push bypassing Collector** — breaks the single-point-of-control principle. When re-enabled, profiles MUST flow through OTel Collector.
- ❌ **Comparing profiles across different code versions** — symbol names change between releases. Compare within same version.

## Reference

- Grafana Pyroscope docs: https://grafana.com/docs/pyroscope/latest/
- pprof format: https://github.com/google/pprof
- OTel Profiles signal (Alpha): https://opentelemetry.io/docs/specs/otel/profiles/
- Local docs: `<workspace>/01-DEVOPS/EXTERNAL-DOCS/pyroscope/docs`
- <org> profiling branch (not merged): `feat/pyroscope` commit `824171a` in bdctelemetryhelper
- Related skills: `grafana-cross-signal-correlation`, `monitoring-stack-overview`
