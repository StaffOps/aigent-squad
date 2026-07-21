---
name: pyroscope-self-metrics
description: "Use when operating or troubleshooting the Grafana Pyroscope backend itself \u2014 ingestion failures, flush bottlenecks, query latency, series pressure, storage health. Covers pyroscope_distributor_*, pyroscope_tsdb_head_*, pyroscope_head_*, pyroscope_request_duration_seconds, pyroscopedb_*, and Go runtime. This is the SELF-TELEMETRY catalog for operating the Pyroscope server; for profiling usage patterns (pprof types, trace correlation, client SDK), see observability/pyroscope-profiling-patterns."
keywords: [pyroscope, go, grafana, sdk]
---
# Pyroscope Backend Self-Metrics

Operational metrics for monitoring the **Grafana Pyroscope** continuous profiling
backend itself (server health, not client profiling data).

**Deployed version**: Helm chart `pyroscope` **2.1.0**, appVersion **2.1.0**
(from `grafana/helm-charts`, microservices mode).

**Architecture deployed**: distributor (5 replicas) → ingester (5 replicas,
RF=2) → compactor (1) + store-gateway (5, RF=3) + querier (3) +
query-frontend (3) + query-scheduler (3). S3 backend storage.

**Pipeline**: Pyroscope component `:4040/metrics` → vmagent ServiceMonitor scrape
→ VictoriaMetrics. All components use the `pyroscope` Prometheus namespace
(via dskit `MetricsNamespace`).

> **Source grounding**: Metric names verified from Pyroscope v2.1.0 source code:
> `pkg/distributor/metrics.go`, `pkg/phlaredb/metrics.go`, and
> `pkg/phlare/server_metrics.go` (dskit middleware). Names are the **exact**
> Prometheus-scraped form stored in VictoriaMetrics.

---

## 1. HTTP/gRPC Server RED (all components)

All components expose dskit middleware metrics under the `pyroscope` namespace.

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `pyroscope_request_duration_seconds_bucket` | Histogram | Latency of all HTTP/gRPC requests served | RED method: p50/p95/p99 per route; identify slow endpoints | `method`, `route`, `status_code`, `ws`, `le` |
| `pyroscope_request_duration_seconds_count` | Counter | Total request count | Request rate per route/status; error rate = status_code >= 400 | `method`, `route`, `status_code`, `ws` |
| `pyroscope_request_message_bytes_bucket` | Histogram | Inbound request body size | Detect oversized push payloads overwhelming distributor | `method`, `route`, `le` |
| `pyroscope_response_message_bytes_bucket` | Histogram | Outbound response body size | Detect expensive query responses (large profile results) | `method`, `route`, `le` |
| `pyroscope_inflight_requests` | Gauge | Currently in-flight requests | Saturation signal; high value = concurrency exhaustion risk | `method`, `route` |
| `pyroscope_tcp_connections` | Gauge | Active TCP connections | Connection pool pressure | `protocol` |
| `pyroscope_tcp_connections_limit` | Gauge | Max allowed TCP connections (0=unlimited) | Know the ceiling for connection saturation | `protocol` |

**Key routes** (filter `route` label):
- `/push.v1.PusherService/Push` — profile push (gRPC)
- `/ingest` — legacy push (HTTP)
- `/querier.v1.QuerierService/SelectMergeProfile` — profile query
- `/querier.v1.QuerierService/SelectMergeStacktraces` — stacktrace query
- `/querier.v1.QuerierService/Series` — series query

---

## 2. Distributor Metrics (Ingest Path)

Emitted by the distributor component. Critical for detecting ingest failures and
rate-limit rejections.

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `pyroscope_distributor_received_compressed_bytes_bucket` | Histogram | Compressed bytes per profile received | Baseline ingest volume; sudden drop = upstream stopped sending | `type`, `tenant` |
| `pyroscope_distributor_received_decompressed_bytes_total_bucket` | Histogram | Decompressed bytes per profile at each processing stage | Track loss at each stage: `received` → `sampled` → `normalized`. Gap between stages = rejection | `tenant`, `stage` |
| `pyroscope_distributor_received_samples_bucket` | Histogram | Samples per profile received | Detect anomalously large profiles (excessive stacks) | `type`, `tenant` |
| `pyroscope_distributor_received_samples_bytes_bucket` | Histogram | Size of samples (without symbols) per request | Sample payload sizing; correlates with ingester memory | `type`, `tenant` |
| `pyroscope_distributor_received_symbols_bytes_bucket` | Histogram | Size of symbols per request | Symbol deduplication efficiency; large = wasted bandwidth | `type`, `tenant` |
| `pyroscope_distributor_parse_duration_seconds_bucket` | Histogram | Duration of profile parsing (JFR/pprof) per ingest | Slow parsing = CPU bottleneck in distributor; check profile format | `type`, `tenant` |
| `pyroscope_distributor_push_batch_series` | Histogram | Number of series per batched push (PushBatch call) | Detect high-cardinality tenants sending many series per push | `tenant` |
| `pyroscope_distributor_replication_factor` | Gauge | Configured replication factor | Confirm RF=2 matches deployment expectation | — |

**Stage values** (for `pyroscope_distributor_received_decompressed_bytes_total`):
- `received` — earliest; before rate-limit/sampling checks
- `sampled` — after rate-limit/sampling acceptance
- `normalized` — after validation and normalization (ready for ingester)

---

## 3. Ingester / Head Block Metrics (Storage Write Path)

Emitted by the ingester's phlaredb head block. Critical for series pressure,
flush health, and OOM prevention.

### Series & Profile Tracking

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `pyroscope_tsdb_head_series` | Gauge | Total active series in the head block | **KEY capacity signal**. Compare vs `max_global_series_per_tenant` (60000). Approaching limit = rejections imminent | — |
| `pyroscope_tsdb_head_series_created_total` | Counter | Cumulative series created | Rate = series churn; high churn → high-cardinality problem (pod/node labels) | `profile_name` |
| `pyroscope_head_profiles` | Gauge | Total profiles in the head block | Memory pressure proxy; profiles × avg size ≈ memory usage | — |
| `pyroscope_head_profiles_created_total` | Counter | Cumulative profiles created | Ingest rate per profile type | `profile_name` |
| `pyroscope_head_samples` | Gauge | Sample values currently in head | Memory correlation; large = potential OOM on flush | — |
| `pyroscope_head_ingested_sample_values_total` | Counter | Sample values ingested into head per profile type | Per-type throughput; drop = upstream stopped or was rejected | `profile_name` |
| `pyroscope_head_received_sample_values_total` | Counter | Sample values received (before dedup/merge) | Compare with `ingested` to see rejection/dedup ratio | `profile_name` |
| `pyroscope_head_size_bytes` | Gauge | In-memory size of head block stores | Direct memory pressure indicator; compare vs pod memory limit | `type` |

### Flush Health

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `pyroscope_head_flushed_blocks_total` | Counter | Total blocks flushed (success/failure) | `status=failed` → data at risk, storage write problem | `status` |
| `pyroscope_head_flushed_reason_total` | Counter | Why a block was flushed | Unexpected reasons (e.g. `force`) may indicate pressure | `reason` |
| `pyroscope_head_flushed_block_size_bytes` | Histogram | Size of flushed blocks | Baseline block sizing; too-large blocks = slow compaction | — |
| `pyroscope_head_flushed_block_duration_seconds` | Histogram | Time to flush a block to storage | High flush time = S3/storage latency or large block | — |
| `pyroscope_head_flushed_block_series` | Histogram | Series count per flushed block | Cardinality per flush cycle | — |
| `pyroscope_head_flushed_block_samples` | Histogram | Sample count per flushed block | Volume per flush cycle | — |
| `pyroscope_head_flushed_block_profiles` | Histogram | Profile count per flushed block | Volume per flush cycle | — |
| `pyroscope_head_flushed_table_size_bytes_bucket` | Histogram | Size of individual flushed parquet tables | Identify which table type is largest | `name`, `le` |
| `pyroscope_head_block_duration_seconds` | Histogram | Duration (time range) covered by a block | Should match configured block range; deviations = early flush | — |
| `pyroscope_head_written_profile_segments_total` | Counter | Profile row-group segments written | `status=failed` → write path broken | `status` |
| `pyroscope_head_written_profile_segments_size_bytes` | Histogram | Size of written profile segments | Segment sizing health | — |

### Rows Written (Parquet)

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `pyroscope_rows_written` | Counter | Rows written to parquet tables | Per-type write throughput | `type` |

---

## 4. Block Storage Metrics (Store-Gateway / Querier)

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `pyroscopedb_block_opening_duration` | Histogram | Latency of opening a block from object storage | Slow opens = S3 latency or cold cache | — |
| `pyroscopedb_blocks_currently_open` | Gauge | Number of blocks currently held open | Memory pressure on store-gateway; correlate with RSS | — |
| `pyroscopedb_block_profile_table_accesses_total` | Counter | Profile table access count | Hot-table identification; high access = cache candidate | `table` |

---

## 5. Ring / Memberlist Metrics

Pyroscope uses Mimir-style hash rings (via dskit) for distributor→ingester
sharding and store-gateway ring.

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `pyroscope_ring_members` | Gauge | Ring member count by state | `state=Unhealthy` or `Leaving` = instance problem | `name`, `state` |
| `pyroscope_ring_oldest_member_timestamp` | Gauge | Timestamp of oldest ring member | Detect stale members (not leaving cleanly) | `name` |
| `cortex_ring_tokens_total` | Gauge | Tokens held per ring | ⚠️ Uses `cortex_` prefix (dskit heritage). Uneven tokens = hot shards | `name` |

> **Note**: Some ring metrics retain the `cortex_` prefix from dskit. This is
> expected and not a misconfiguration.

---

## 6. Go Runtime (all components)

All Pyroscope components expose standard `client_golang` runtime metrics.
See skill `go-apm-metrics` for the full catalog. Key ones for Pyroscope ops:

| Metric Name | Type | Troubleshooting Use |
|---|---|---|
| `go_memstats_heap_inuse_bytes` | Gauge | Ingester memory — compare vs pod limit (4Gi) |
| `go_goroutines` | Gauge | Goroutine count; leak detection |
| `go_gc_duration_seconds` | Summary | GC pause; high = latency spikes |
| `process_resident_memory_bytes` | Gauge | RSS — the "real" memory; compare vs k8s limit |

---

## Troubleshooting Quick Reference

| Symptom | First Metrics to Check |
|---------|------------------------|
| Profiles being rejected ("Maximum active series limit exceeded") | `pyroscope_tsdb_head_series` vs configured limit (60000); `pyroscope_distributor_received_decompressed_bytes_total{stage="received"}` vs `{stage="normalized"}` (gap = rejection) |
| Ingester OOMKilled | `pyroscope_head_size_bytes`, `process_resident_memory_bytes`, `pyroscope_head_samples` |
| Push rate limit exceeded | `pyroscope_distributor_received_decompressed_bytes_total{stage="received"}` rate vs `{stage="sampled"}` rate (gap = rate-limited) |
| Slow profile queries | `pyroscope_request_duration_seconds{route=~".*SelectMerge.*"}` p99; `pyroscopedb_block_opening_duration` |
| Block flush failures (data loss risk) | `pyroscope_head_flushed_blocks_total{status="failed"}`; `pyroscope_head_written_profile_segments_total{status="failed"}` |
| High series churn (cardinality explosion) | `rate(pyroscope_tsdb_head_series_created_total)` — if high, check ingestion_relabeling_rules for high-cardinality labels being promoted |
| Distributor rejecting large profiles | `pyroscope_distributor_received_compressed_bytes` p99; `pyroscope_distributor_parse_duration_seconds` |
| Store-gateway slow to serve | `pyroscopedb_block_opening_duration` p99; `pyroscopedb_blocks_currently_open` vs memory |
| Ring unhealthy members | `pyroscope_ring_members{state="Unhealthy"}` > 0 |
| S3 storage write latency | `pyroscope_head_flushed_block_duration_seconds` p99 |

---

## Deployment-Specific Notes (this environment)

- **Series limit**: `max_global_series_per_tenant: 60000` (raised from default 5000 due to ~167 services profiled)
- **Ingestion rate**: `ingestion_rate_mb: 16`, `ingestion_burst_size_mb: 8`
- **Replication factor**: 2 (distributor → 2 ingesters per profile)
- **Ingester memory limit**: 4Gi (raised to accommodate ~34k head_series per ingester)
- **Self-profiling**: disabled (`disableSelfProfile: true`, `self_profiling.disable_push: true`)
- **High-cardinality guard**: `k8s.pod.name` and `k8s.node.name` are NOT promoted to labels (only `service_name`, `namespace`, `eks_cluster`, `cluster`, `workload`)

---

## Complements

- `observability/pyroscope-profiling-patterns` — client-side profiling usage, pprof types, trace-to-profile correlation, Pyroscope architecture overview
- `apm-metrics/go-apm-metrics` — full Go runtime metrics catalog (goroutines, GC, scheduler, memory classes)
- `apm-metrics/collector-internal-metrics` — OTel Collector pipeline health (profile collector that pushes to Pyroscope)
- `apm-metrics/victoriametrics-self-metrics` — VictoriaMetrics health (where these metrics are stored)

---

## Sources

- Pyroscope v2.1.0 source: `pkg/distributor/metrics.go` — distributor metric definitions
- Pyroscope v2.1.0 source: `pkg/phlaredb/metrics.go` — head/ingester/block metric definitions
- Pyroscope v2.1.0 source: `pkg/phlare/server_metrics.go` — dskit HTTP/gRPC middleware metrics
- Helm chart: `grafana/pyroscope` v2.1.0 (appVersion 2.1.0) from `https://grafana.github.io/helm-charts`
- Deployed config: `02-KUBE/00-CONFIG/k8s-setup/monitoring/pyroscope/values.yaml.gotmpl`
- dskit server metrics: `github.com/grafana/dskit/server` (provides `pyroscope_request_duration_seconds` etc.)
