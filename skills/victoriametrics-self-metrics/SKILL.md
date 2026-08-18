---
name: victoriametrics-self-metrics
description: >
  Use when diagnosing VictoriaMetrics cluster health, vmagent scrape/remote-write
  pipeline, or vmalert rule evaluation. Covers self-telemetry metric prefixes:
  vm_rows_inserted_total, vm_rpc_*, vm_cache_*, vm_http_requests_total,
  vm_slow_row_inserts_total, vm_rows_ignored_total, vm_concurrent_*,
  vmagent_remotewrite_*, vm_promscrape_*, vmalert_iteration_total,
  vmalert_alerting_rules_*, vmalert_recording_rules_*, vmalert_alerts_*.
  Grounded on victoria-metrics-cluster chart 0.44.0 (AppVersion v1.145.0),
  vm-operator chart 0.63.1.
keywords:
  - victoriametrics
  - victoria metrics
  - vm_rows
  - vminsert
  - vmselect
  - vmstorage
  - vmagent
  - vmalert
  - vm_rpc
  - vm_cache
  - remote_write
  - scrape
  - cardinality
  - slow_row
  - rows_ignored
  - ingestion
  - vm cluster
---

# VictoriaMetrics Self-Metrics Catalog

Self-telemetry metrics emitted by VictoriaMetrics cluster components (vminsert,
vmselect, vmstorage), vmagent, and vmalert.

**Question answered**: "Is the metrics backend itself healthy — ingesting, storing,
querying, and alerting correctly?"

**Scope**: Internal `vm_*`, `vmagent_*`, and `vmalert_*` metrics exposed on each
component's `/metrics` endpoint. This is a **metrics catalog** — for operational
tuning guidance, troubleshooting playbooks, and cardinality management, see the
linked ops-focused skills.

---

## Deployed Configuration (Grounding)

| Component | Chart | Version | AppVersion | Port |
|-----------|-------|---------|------------|------|
| vminsert / vmselect / vmstorage | `vm/victoria-metrics-cluster` | `0.44.0` | `v1.145.0` | 8480 / 8481 / 8482 |
| vmagent | vm-operator CRD `VMAgent` | operator `0.63.1` | v1.145.0 (operator-managed) | 8429 |
| vmalert | vm-operator CRD `VMAlert` | operator `0.63.1` | v1.145.0 (operator-managed) | 8880 |

**Pipeline**: Component `/metrics` → vmagent (VMAgent `scrape-cluster`, `disableSelfServiceScrape: false`) → vminsert → vmstorage → VictoriaMetrics.

ServiceMonitors are enabled for all cluster components (`serviceMonitor.enabled: true`).

---

## 1. vminsert — Ingestion Layer

vminsert receives samples from vmagent/remote_write clients, replicates (RF=2),
and forwards to vmstorage nodes via RPC.

| Metric Name | Type | What It Measures | Troubleshooting Use | Key Labels |
|---|---|---|---|---|
| `vm_rows_inserted_total` | Counter | Total rows (samples) successfully inserted | Baseline ingest rate; drop = upstream problem or vminsert saturation | `type` (prometheus, influx, etc.) |
| `vm_http_requests_total` | Counter | HTTP requests received by vminsert | Request rate per path; spikes indicate traffic surge | `path`, `code` |
| `vm_http_request_duration_seconds` | Summary/Histogram | HTTP request latency | Slow writes = downstream vmstorage pressure | `path` |
| `vm_concurrent_insert_current` | Gauge | Currently active concurrent inserts | Approaching `maxConcurrentInserts` (48 in this env) = saturation | — |
| `vm_concurrent_insert_limit` | Gauge | Maximum concurrent inserts allowed | Reference ceiling for saturation calculation | — |
| `vm_rpc_send_duration_seconds_total` | Counter | Cumulative time spent sending RPC data to vmstorage | Rate increase = vmstorage backpressure / slow network | `addr` (storage node) |
| `vm_rpc_rows_sent_total` | Counter | Rows sent via RPC to each vmstorage node | Even distribution expected; skew = unhealthy storage node | `addr` |
| `vm_rpc_rows_lost_total` | Counter | **KEY — rows permanently lost** because all vmstorage replicas for a row were unavailable | Non-zero = **data loss**. Requires immediate investigation. | `addr` |
| `vm_rpc_connection_errors_total` | Counter | RPC connection failures to vmstorage nodes | Spikes = network issue or vmstorage restarts | `addr` |
| `vm_rpc_buf_pending_bytes` | Gauge | Pending bytes in RPC write buffer | Growing = vmstorage not draining fast enough | `addr` |
| `vm_slow_row_inserts_total` | Counter | Rows that hit slow-path (TSID cache miss → index lookup) | High rate = new time series storm / cardinality explosion | — |
| `vm_rows_ignored_total` | Counter | **KEY — rows rejected** due to validation failures | Non-zero rate = schema/limit violations being silently dropped | `reason` |
| `vm_tcplistener_conns` | Gauge | Active TCP connections on the listen socket | Connection saturation signal | `addr` |
| `vm_tcplistener_accepts_total` | Counter | Total accepted TCP connections | Baseline connection rate | `addr` |

### `vm_rows_ignored_total` reason values (v1.145.0)

- `too_many_labels` — row exceeds `maxLabelsPerTimeseries` (100 in this env)
- `too_long_label_name` — label name exceeds limit
- `too_long_label_value` — label value exceeds limit
- `duplicate_labels` — duplicate label names in a single series
- `negative_timestamp` — timestamp is negative

---

## 2. vmstorage — Storage Layer

vmstorage handles data persistence, indexing, caching, and serves queries from vmselect.

| Metric Name | Type | What It Measures | Troubleshooting Use | Key Labels |
|---|---|---|---|---|
| `vm_rows_inserted_total` | Counter | Rows accepted into storage | Compare with vminsert's sent rows; gap = loss | — |
| `vm_slow_row_inserts_total` | Counter | Rows requiring new TSID creation (index miss) | **Primary cardinality signal**. High rate = new series storm. | — |
| `vm_rows_ignored_total` | Counter | Rows rejected at storage level | Same reasons as vminsert; double-check here | `reason` |
| `vm_cache_entries` | Gauge | Entries in internal caches | Capacity check per cache type | `type` (indexdb/tagFilters, indexdb/dataBlocks, storage/tsid, etc.) |
| `vm_cache_size_bytes` | Gauge | Size of each cache in bytes | Approaching configured limits = eviction pressure | `type` |
| `vm_cache_requests_total` | Counter | Total cache lookups | Denominator for hit rate | `type` |
| `vm_cache_misses_total` | Counter | Cache misses | `misses / requests` = miss rate. High miss rate → slow_row_inserts | `type` |
| `vm_cache_size_max_bytes` | Gauge | Configured maximum cache size | Reference ceiling for `vm_cache_size_bytes` | `type` |
| `vm_concurrent_search_requests_current` | Gauge | In-flight search (query) requests | Saturation signal for vmselect→vmstorage RPC | — |
| `vm_concurrent_search_requests_limit` | Gauge | Max concurrent search requests (`search.maxConcurrentRequests`, 40 here) | Ceiling for saturation calc | — |
| `vm_http_requests_total` | Counter | HTTP requests to vmstorage | Health/status endpoint activity | `path`, `code` |
| `vm_merge_need` | Gauge | Pending merge operations | Merge backlog; growing indefinitely = compaction cannot keep up | — |
| `vm_parts` | Gauge | Number of data parts (pre-merge) | Growing = merge backlog or insufficient I/O | `type` (indexdb, storage) |
| `vm_data_size_bytes` | Gauge | On-disk data size | Capacity planning | `type` |
| `vm_free_disk_space_bytes` | Gauge | Free disk space | Below `storage.minFreeDiskSpaceBytes` (10GB here) = **readonly mode imminent** | — |
| `vm_storage_deduplicate_interval_seconds` | Gauge | Configured dedup interval (if dedup enabled) | Understand dedup behavior | — |

### Key cache types in this environment

- `indexdb/tagFilters` — configured at 256MB (`storage.cacheSizeIndexDBTagFilters`)
- `indexdb/dataBlocks` — configured at 3072MB
- `indexdb/indexBlocks` — configured at 512MB
- `storage/tsid` — configured at 3072MB

---

## 3. vmselect — Query Layer

vmselect handles PromQL/MetricsQL queries, fans out to all vmstorage nodes, deduplicates, and returns results.

| Metric Name | Type | What It Measures | Troubleshooting Use | Key Labels |
|---|---|---|---|---|
| `vm_http_requests_total` | Counter | Query requests by path and status | RED method: rate + errors (5xx codes) | `path`, `code` |
| `vm_http_request_duration_seconds` | Summary/Histogram | Query latency | Slow query detection; correlate with `search.logSlowQueryDuration` (7s here) | `path` |
| `vm_concurrent_select_current` | Gauge | Currently active select (query) goroutines | Saturation signal | — |
| `vm_concurrent_select_limit` | Gauge | Max concurrent selects (`search.maxConcurrentRequests`, 32 here) | Ceiling | — |
| `vm_cache_entries` | Gauge | vmselect-side cache entries (rollup results, query cache) | Low entries + misses = cold cache after restart | `type` |
| `vm_cache_size_bytes` | Gauge | vmselect cache byte size | Eviction pressure | `type` |
| `vm_cache_misses_total` | Counter | Cache misses | High after restart (cold cache); should stabilize | `type` |
| `vm_rows_read_total` | Counter | Rows scanned across queries | Expensive query detection (high rows/query) | — |
| `vm_slow_queries_total` | Counter | Queries exceeding `search.logSlowQueryDuration` (7s) | Alert trigger; check `/api/v1/status/top_queries` | — |

---

## 4. vmagent — Scrape & Remote Write

vmagent scrapes targets and remote-writes to vminsert. Deployed as `VMAgent` CRD
(2 replicas, stateful mode, 32 remote write queues).

| Metric Name | Type | What It Measures | Troubleshooting Use | Key Labels |
|---|---|---|---|---|
| `vmagent_remotewrite_requests_total` | Counter | Total remote write requests sent | Baseline write traffic per destination | `url`, `status_code` |
| `vmagent_remotewrite_retries_total` | Counter | Remote write retry attempts | Non-zero rate = transient vminsert failures | `url` |
| `vmagent_remotewrite_errors_total` | Counter | **Failed** remote write requests (after retries exhausted) | **Data loss signal** — samples in failed requests are dropped | `url` |
| `vmagent_remotewrite_pending_data_bytes` | Gauge | **KEY** — bytes queued in persistent buffer awaiting send | Growing = vminsert can't keep up. Approaching disk limit = loss imminent. | `url` |
| `vmagent_remotewrite_send_duration_seconds_total` | Counter | Cumulative time spent sending | Rate = avg send latency; growing = network/vminsert saturation | `url` |
| `vmagent_remotewrite_conn_bytes_written_total` | Counter | Total bytes written over wire | Throughput per remote write target | `url` |
| `vmagent_remotewrite_queue_size` | Gauge | Number of pending blocks in remote write queue | Growing = queue saturation; compare with `remoteWrite.queues` (32) | `url` |
| `vm_promscrape_targets_total` | Gauge | Total discovered scrape targets | Target discovery health; drop = service discovery issue | `job`, `status` (up/down) |
| `vm_promscrape_scrapes_total` | Counter | Total scrape attempts | Scrape frequency baseline | `type` (regular, limit) |
| `vm_promscrape_scrape_duration_seconds` | Summary | Scrape request latency | Slow targets starving the scrape loop | `job` |
| `vm_promscrape_scrapes_failed_total` | Counter | Failed scrape attempts | Target unreachable / timeout | `job` |
| `vm_promscrape_targets_response_len_total` | Counter | Total bytes scraped | Throughput per job; unexplained growth = cardinality | `job` |
| `vm_promscrape_discovery_targets_total` | Gauge | Targets found per discovery config | Service discovery working correctly | `type` (kubernetes, static, etc.) |

---

## 5. vmalert — Rule Evaluation & Alerting

vmalert evaluates recording/alerting rules against vmselect and remote-writes
recording rule results back to vminsert.

Deployed: 1 replica, `evaluationInterval: 30s`, `selectAllByDefault: true`.

| Metric Name | Type | What It Measures | Troubleshooting Use | Key Labels |
|---|---|---|---|---|
| `vmalert_iteration_total` | Counter | Total rule group evaluation iterations | Rate should match `1 / evaluationInterval` per group | `group`, `file` |
| `vmalert_iteration_duration_seconds` | Summary/Histogram | Duration of each group evaluation | If duration > evaluationInterval → rule lag / missed evals | `group`, `file` |
| `vmalert_iteration_missed_total` | Counter | **KEY — evaluations that could not complete** within interval | Non-zero = rules running behind; data gaps in recording rules | `group`, `file` |
| `vmalert_alerting_rules_active` | Gauge | Currently active (firing) alerting rules | Alert volume monitoring | — |
| `vmalert_alerting_rules_error` | Gauge | **KEY — alerting rules in error state** | Non-zero = rules failing to evaluate (bad query, timeout, datasource down) | `group`, `alertname`, `file` |
| `vmalert_recording_rules_active` | Gauge | Active recording rules | Volume tracking | — |
| `vmalert_recording_rules_error` | Gauge | **KEY — recording rules in error state** | Non-zero = derived metrics not being produced | `group`, `recording`, `file` |
| `vmalert_alerts_fired_total` | Counter | Total alerts fired | Alert activity rate | `group`, `alertname` |
| `vmalert_alerts_pending` | Gauge | Alerts in pending state (for duration not yet met) | Expected during ramp-up; sustained high = noisy rules | — |
| `vmalert_alerts_sent_total` | Counter | Notifications sent to Alertmanager | Delivery health | `addr` |
| `vmalert_alerts_send_errors_total` | Counter | **Failed** notification sends to Alertmanager | Non-zero = alerts not reaching Slack/PD | `addr` |
| `vmalert_remotewrite_total` | Counter | Recording rule results written to vminsert | Baseline write rate from vmalert | — |
| `vmalert_remotewrite_errors_total` | Counter | **Failed** remote writes of recording rule results | Non-zero = recording rules producing results but not persisting them | — |
| `vmalert_config_last_reload_successful` | Gauge | 1 = last config reload succeeded, 0 = failed | Alert on 0 — rules not loading | — |

---

## Troubleshooting Quick Reference

| Symptom | First Metrics to Check |
|---------|------------------------|
| Samples missing from queries | `vm_rows_inserted_total` (vminsert) vs `vm_rpc_rows_sent_total`; `vm_rpc_rows_lost_total` |
| Write latency spike | `vm_rpc_send_duration_seconds_total` rate; `vm_rpc_buf_pending_bytes`; `vm_concurrent_insert_current` vs `_limit` |
| Cardinality explosion / high churn | `vm_slow_row_inserts_total` rate (vmstorage); `vm_cache_misses_total{type="storage/tsid"}` |
| vmagent queue growing | `vmagent_remotewrite_pending_data_bytes`; `vmagent_remotewrite_errors_total` |
| Scrape targets disappearing | `vm_promscrape_targets_total{status="down"}`; `vm_promscrape_scrapes_failed_total` |
| Query timeouts / slow dashboards | `vm_slow_queries_total`; `vm_concurrent_select_current` vs `_limit`; `vm_http_request_duration_seconds` |
| vmstorage disk filling | `vm_free_disk_space_bytes` (below 10GB = readonly); `vm_data_size_bytes` |
| Alert rules not evaluating | `vmalert_alerting_rules_error`; `vmalert_iteration_missed_total`; `vmalert_iteration_duration_seconds` |
| Alerts not reaching Slack | `vmalert_alerts_send_errors_total`; check Alertmanager connectivity |
| Recording rules failing | `vmalert_recording_rules_error`; `vmalert_remotewrite_errors_total` |
| Rows being silently dropped | `vm_rows_ignored_total` by `reason`; check `maxLabelsPerTimeseries` (100) |
| Cache cold after restart | `vm_cache_misses_total` / `vm_cache_requests_total` — high miss rate is expected temporarily; `vm_slow_row_inserts_total` peaks during warm-up |
| RPC distribution skew | `vm_rpc_rows_sent_total` by `addr` — should be roughly even across 10 vmstorage nodes |

---

## Complements

- `observability/victoriametrics-troubleshooting` — operational troubleshooting playbooks, capacity planning, and remote_write backpressure diagnosis
- `observability/victoriametrics-tuning` — tuning flags (extraArgs), RPC saturation, cache warm-up, goroutine scheduling for vminsert/vmselect/vmstorage
- `observability/vm-cardinality-management` — detecting and fixing high-cardinality explosions, label removal patterns
- `observability/streaming-aggregation` — vmagent `streamAggrConfig` reducing cardinality before ingest
- `apm-metrics/collector-internal-metrics` — OTel Collector pipeline health (upstream of vmagent/VM)
- `observability/vmalert-configuration` — VMAlert CRD config, helmfile escaping, evalDelay

---

## Sources

- [VictoriaMetrics Cluster Monitoring docs](https://docs.victoriametrics.com/cluster-victoriametrics/#monitoring) — official self-monitoring guidance (v1.145.0)
- [vmagent Monitoring docs](https://docs.victoriametrics.com/vmagent/#monitoring) — vmagent self-metrics reference
- [vmalert Monitoring docs](https://docs.victoriametrics.com/vmalert/#monitoring) — vmalert self-metrics reference
- Helm chart changelog: `victoria-metrics-cluster` chart `0.44.0` → AppVersion `v1.145.0` (released 2026-06-08)
- Deployed config: `k8s-setup/monitoring/vm-cluster/values.yaml.gotmpl`, `vm-operator-raw/vmalert/vmalert-resource.yaml`, `vm-operator-raw/vmagent-scrape-cluster/vmagent-scrape-cluster-resource.yaml`
