---
name: grafana-self-metrics
description: >   Use when diagnosing Grafana server health — HTTP latency, datasource proxy   errors, unified alerting performance, database connection issues, dashboard   rendering problems, or Go runtime pressure. Covers grafana_http_request_duration_seconds*,   grafana_stat_totals_*, grafana_datasource_request_total, grafana_alerting_*,   grafana_database_*, plus Go runtime (go_*, process_*) metrics emitted by   Grafana ≥13.0. Grounded on Grafana 13.1.0 deployed via kube-prometheus-stack   87.2.1 (grafana subchart 12.7.1).
keywords: [grafana, go]
---
# Grafana Self-Metrics — Environment-Anchored Reference

Backend health metrics for the **Grafana server** itself.

**Question answered**: "Is Grafana healthy? Are dashboards loading fast? Are
datasource queries failing? Is unified alerting keeping up?"

**Deployed version**: Grafana **13.1.0** via kube-prometheus-stack chart 87.2.1
(Grafana subchart 12.7.1). 5 replicas, PostgreSQL backend, HA unified alerting
enabled.

**Pipeline**: Grafana `:3000/metrics` → vmagent scrape → VictoriaMetrics.

Metrics enabled via `grafana.ini`:
```ini
[metrics]
enabled = true                      # default
disable_total_stats = false         # emits grafana_stat_totals_*
```

> ⚠️ **Go runtime metrics** (`go_*`, `process_*`) follow the same names as
> documented in the `go-apm-metrics` skill — not duplicated here; see that skill
> for goroutines, GC, memory, scheduler, mutex wait.

---

## 1. HTTP Request RED (Primary Health Signal)

Grafana instruments every HTTP handler. These are the **first metrics to check**
when users report "Grafana is slow" or "dashboards timeout."

| Metric Name | Type | What It Measures | Troubleshooting Use | Key Labels |
|---|---|---|---|---|
| `grafana_http_request_duration_seconds_bucket` | Histogram | Server-side latency distribution for all HTTP routes | RED method: p50/p95/p99 per handler; identify slow routes | `handler`, `method`, `status_code`, `le` |
| `grafana_http_request_duration_seconds_count` | Counter | Total HTTP requests served | Request rate per route (R of RED) | `handler`, `method`, `status_code` |
| `grafana_http_request_duration_seconds_sum` | Counter | Cumulative latency seconds | Average latency = sum/count | `handler`, `method`, `status_code` |

**Key `handler` values** (most diagnostic):
- `/api/datasources/proxy/:id/*` — datasource proxy calls
- `/api/ds/query` — unified datasource query API (Grafana 8+)
- `/api/dashboards/uid/:uid` — dashboard load
- `/api/search` — dashboard search
- `/api/alertmanager/*` — alerting API
- `/render/*` — image rendering (if renderer installed)
- `/api/annotations` — annotation queries

> ℹ️ Grafana 13.x also exposes native histograms alongside classic buckets by
> default (`classic_http_histogram_enabled = true`). VictoriaMetrics scrapes the
> classic buckets.

---

## 2. Stat Totals (Inventory Metrics)

Periodically collected gauges showing total object counts in the Grafana database.
Useful for capacity planning and detecting runaway provisioning.

| Metric Name | Type | What It Measures | Troubleshooting Use | Key Labels |
|---|---|---|---|---|
| `grafana_stat_totals_dashboard` | Gauge | Total number of dashboards | Sudden spike = runaway provisioning or sidecar loop | — |
| `grafana_stat_totals_datasource` | Gauge | Total number of datasources | Cross-check with expected count | — |
| `grafana_stat_totals_user` | Gauge | Total registered users | Capacity/licensing check | — |
| `grafana_stat_totals_org` | Gauge | Total organizations | Multi-org awareness | — |
| `grafana_stat_totals_playlist` | Gauge | Total playlists | — | — |
| `grafana_stat_totals_alert_rule` | Gauge | Total alert rules (unified alerting) | Rule count growth = evaluation load growth | — |
| `grafana_stat_totals_folder` | Gauge | Total folders | — | — |
| `grafana_stat_total_service_accounts` | Gauge | Total service accounts | Security audit | — |

> These metrics are disabled if `disable_total_stats = true` in `grafana.ini`.
> Deployed config has it `false` (enabled).

---

## 3. Datasource Proxy / Query Metrics

These measure Grafana's outbound calls TO datasources (Prometheus, Loki, Tempo, etc.).

| Metric Name | Type | What It Measures | Troubleshooting Use | Key Labels |
|---|---|---|---|---|
| `grafana_datasource_request_total` | Counter | Total datasource proxy/query requests | Error rate per datasource; if `code=5xx` rising → backend issue | `datasource`, `code`, `method` |
| `grafana_datasource_request_duration_seconds_bucket` | Histogram | Latency of datasource requests | Identify slow backends; compare across datasource types | `datasource`, `le` |
| `grafana_datasource_request_duration_seconds_count` | Counter | Count of datasource requests (histogram observations) | Request throughput per datasource | `datasource` |
| `grafana_datasource_request_duration_seconds_sum` | Counter | Cumulative time spent on datasource requests | Average per-request time to backend | `datasource` |
| `grafana_datasource_request_in_flight` | Gauge | Currently in-flight datasource requests | Saturation signal; high = connection pool exhaustion possible | `datasource` |

**`datasource` label values** correspond to datasource type strings: `prometheus`,
`loki`, `tempo`, `postgres`, `victoriametrics-datasource`, etc.

---

## 4. Unified Alerting (grafana_alerting_*)

Grafana 13 uses **unified alerting** exclusively (legacy alerting removed). These
metrics track rule evaluation and notification health.

| Metric Name | Type | What It Measures | Troubleshooting Use | Key Labels |
|---|---|---|---|---|
| `grafana_alerting_rule_evaluations_total` | Counter | Total alert rule evaluations | Evaluation throughput; drop = scheduler stalled | `org` |
| `grafana_alerting_rule_evaluation_failures_total` | Counter | Failed evaluations (query error, timeout) | Non-zero rate = alerts blind (not evaluating) | `org` |
| `grafana_alerting_rule_evaluation_duration_seconds_bucket` | Histogram | Time to evaluate a single rule | Slow evaluations → delayed alerts; tune query or interval | `org`, `le` |
| `grafana_alerting_rule_group_rules` | Gauge | Number of rules per group | Load balancing across evaluation groups | `rule_group`, `org` |
| `grafana_alerting_rule_send_alerts_duration_seconds_bucket` | Histogram | Time to send alerts to notification channels | Notification pipeline latency | `org`, `le` |
| `grafana_alerting_notifications_total` | Counter | Total notifications sent | Volume tracking | `type`, `org` |
| `grafana_alerting_notifications_failed_total` | Counter | Failed notification deliveries | Non-zero = Slack/email/webhook delivery broken | `type`, `org` |
| `grafana_alerting_schedule_behind_seconds` | Gauge | How far behind the evaluation schedule is | >0 = evaluator can't keep up with rule count × interval | — |
| `grafana_alerting_active_configurations` | Gauge | Active alerting configurations (HA) | Should equal replica count in HA mode | — |

### HA Alerting (Gossip Ring)

With 5 replicas and HA enabled, Grafana uses gossip for deduplication:

| Metric Name | Type | What It Measures | Troubleshooting Use | Key Labels |
|---|---|---|---|---|
| `grafana_alerting_cluster_members` | Gauge | Number of peers in the HA cluster ring | Should equal replica count (5); lower = split-brain risk | — |
| `grafana_alerting_cluster_messages_received_total` | Counter | Gossip messages received | Ring communication health | — |
| `grafana_alerting_cluster_messages_sent_total` | Counter | Gossip messages sent | Ring communication health | — |
| `grafana_alerting_cluster_peer_info` | Gauge | Per-peer ring membership info | Identify which peer is missing from the ring | `peer` |

---

## 5. Database Metrics (grafana_database_*)

Grafana uses PostgreSQL (this deployment). These metrics reflect the Go `database/sql`
pool stats for the Grafana→PostgreSQL connection.

| Metric Name | Type | What It Measures | Troubleshooting Use | Key Labels |
|---|---|---|---|---|
| `grafana_database_conn_max_open` | Gauge | Max open connections configured | Pool ceiling | — |
| `grafana_database_conn_open` | Gauge | Currently open connections (in-use + idle) | Near max_open → exhaustion risk | `state` (`inuse`, `idle`) |
| `grafana_database_conn_max_idle_closed_total` | Counter | Connections closed due to max idle limit | Frequent closing = max_idle too low | — |
| `grafana_database_conn_max_lifetime_closed_total` | Counter | Connections closed due to max lifetime | Expected rotation behavior | — |
| `grafana_database_conn_wait_count_total` | Counter | Times a connection was waited for (pool exhausted) | **Rate > 0 = requests queuing for DB connection** | — |
| `grafana_database_conn_wait_duration_seconds_total` | Counter | Total time spent waiting for connections | High rate = DB pool bottleneck | — |
| `grafana_database_conn_max_idle_time_closed_total` | Counter | Connections closed due to idle timeout | Normal lifecycle | — |

---

## 6. API Login / Auth Metrics

| Metric Name | Type | What It Measures | Troubleshooting Use | Key Labels |
|---|---|---|---|---|
| `grafana_api_login_oauth_total` | Counter | OAuth login attempts | Auth volume | — |
| `grafana_api_login_post_total` | Counter | Password login attempts | Brute-force detection (correlate with `status_code`) | — |
| `grafana_api_response_status_total` | Counter | API responses by status | Global error rate; 5xx spike = server issue | `status_code` |

---

## 7. Build & Instance Info

| Metric Name | Type | What It Measures | Troubleshooting Use | Key Labels |
|---|---|---|---|---|
| `grafana_build_info` | Gauge (const 1) | Grafana build metadata | Identify running version across replicas | `branch`, `edition`, `goversion`, `revision`, `version` |
| `grafana_instance_start_total` | Counter | Grafana instance start count | Restart tracking (CrashLoopBackOff detection) | — |

---

## 8. Process / Runtime (shared with all Go services)

Grafana emits standard Go `process_*` metrics (from prometheus/client_golang):

| Metric Name | Type | What It Measures | Troubleshooting Use | Key Labels |
|---|---|---|---|---|
| `process_resident_memory_bytes` | Gauge | RSS memory | Compare with container limit for OOMKill proximity | — |
| `process_cpu_seconds_total` | Counter | CPU time consumed | CPU saturation per replica | — |
| `process_open_fds` | Gauge | Open file descriptors | Approaching `process_max_fds` = socket/file leak | — |
| `process_max_fds` | Gauge | FD limit (ulimit) | Ceiling | — |

> For full Go runtime metrics (`go_goroutines`, `go_memstats_*`, `go_gc_*`,
> `go_sched_*`) see skill **`go-apm-metrics`** — Grafana exposes the same set.

---

## Symptom → Metric Quick-Reference

| Symptom | First Query | Follow-up |
|---------|-------------|-----------|
| Dashboards load slowly | `histogram_quantile(0.99, sum by (le,handler) (rate(grafana_http_request_duration_seconds_bucket{handler=~"/api/ds/query\|/api/datasources/proxy.*"}[5m])))` | Check `grafana_datasource_request_duration_seconds` per backend |
| Datasource errors (panels show "Error") | `sum by (datasource,code) (rate(grafana_datasource_request_total{code=~"5.."}[5m]))` | Cross-reference with backend health (VM/Loki/Tempo status) |
| Alerts not firing | `rate(grafana_alerting_rule_evaluation_failures_total[5m]) > 0` | Check `grafana_alerting_schedule_behind_seconds`; if >0, reduce rules or increase interval |
| Grafana OOMKilled | `process_resident_memory_bytes` + `go_memstats_heap_inuse_bytes` | Check `grafana_datasource_request_in_flight` (connection accumulation) |
| DB connection pool exhaustion | `rate(grafana_database_conn_wait_count_total[5m]) > 0` | Check `grafana_database_conn_open{state="inuse"}` vs `grafana_database_conn_max_open` |
| Alerting HA split-brain | `grafana_alerting_cluster_members < 5` | Verify all pods can reach `:9094` gossip port |
| Login failures spike | `rate(grafana_api_login_oauth_total[5m])` + check `grafana_http_request_duration_seconds_count{handler="/login",status_code=~"4.."}` | Verify Keycloak reachable |
| Dashboard count explosion | `grafana_stat_totals_dashboard` growing unexpectedly | Check sidecar provisioning loop (`grafana_dashboard` label selector) |
| Notification delivery failures | `rate(grafana_alerting_notifications_failed_total[5m]) > 0` | Check destination (Slack webhook, SMTP); correlate with `grafana_http_request_duration_seconds{handler="/api/alertmanager/*"}` |

---

## MetricsQL Examples (Copy-Paste)

### HTTP request p99 latency by handler (top 10 slowest)

```promql
topk(10,
  histogram_quantile(0.99,
    sum by (le, handler) (
      rate(grafana_http_request_duration_seconds_bucket[5m])
    )
  )
)
```

### Datasource error rate (5xx) by datasource type

```promql
sum by (datasource) (
  rate(grafana_datasource_request_total{code=~"5.."}[5m])
)
```

### Alert evaluation failure rate

```promql
sum(rate(grafana_alerting_rule_evaluation_failures_total[5m]))
/
sum(rate(grafana_alerting_rule_evaluations_total[5m]))
```

### DB connection pool saturation

```promql
grafana_database_conn_open{state="inuse"}
/
grafana_database_conn_max_open
```

### HA ring health (expect 5 members)

```promql
grafana_alerting_cluster_members
```

### Datasource in-flight saturation

```promql
sum by (datasource) (grafana_datasource_request_in_flight)
```

---

## Complements

- **`go-apm-metrics`** — full Go runtime metrics (GC, scheduler, goroutines, mutex) emitted by Grafana
- **`observability/grafana-cross-signal-correlation`** — datasource configuration for trace↔metric↔log correlation (do NOT duplicate here)
- **`collector-internal-metrics`** — OTel Collector pipeline health (upstream of Grafana's datasource queries)
- **`loki-tempo-self-metrics`** — backend health of Loki/Tempo (correlated when datasource errors appear)
- **`victoriametrics-troubleshooting`** — VictoriaMetrics backend health (correlated with slow Prometheus queries in Grafana)
- **`sre/alerting-strategy`** — alerting design and Alertmanager routing context

---

## Sources

- Grafana official docs: [Set up Grafana monitoring](https://grafana.com/docs/grafana/latest/setup-grafana/set-up-grafana-monitoring/) (metrics endpoint, stat_totals, native histograms)
- Grafana source: `pkg/infra/metrics/metrics.go` (metric registry definitions)
- Grafana source: `pkg/services/ngalert/metrics/` (unified alerting metrics)
- Deployed chart: kube-prometheus-stack 87.2.1 → grafana subchart 12.7.1 → appVersion **13.1.0**
- Deployed `grafana.ini`: unified_alerting HA enabled, PostgreSQL backend, 5 replicas
