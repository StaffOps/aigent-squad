---
name: backing-services-metrics
description: "Use when troubleshooting backing-service health (Redis, PostgreSQL, CoreDNS), correlating app-side latency with backend saturation, diagnosing cache misses, replication lag, connection exhaustion, or DNS resolution delays. All metrics confirmed present in live VictoriaMetrics inventory (2026-07-06)."
keywords: [backing-services, backing, services, "backing services", coredns, dns, redis, victoriametrics]
---
# Backing Services Metrics Reference

All metrics in this document are **confirmed present in the organization's live VictoriaMetrics inventory (2026-07-06)**. Metric names are in their Prometheus/underscore form as scraped.

**Backends**: VictoriaMetrics (MetricsQL/PromQL), Tempo, Loki, Grafana.
**Apps**: OTel SDK → Collector with tail sampling at gateway.

---

## 1. Redis (oliver006/redis_exporter)

Source: [oliver006/redis_exporter](https://github.com/oliver006/redis_exporter) — metrics derived from Redis `INFO` command sections.

### Metric Reference

| Metric | Type | Unit | What It Measures | Troubleshooting Use | Key Labels |
|--------|------|------|------------------|--------------------| -----------|
| `redis_uptime_in_seconds` | Gauge | seconds | Time since Redis server start | Detect recent restarts (value drops to 0) | `addr` |
| `redis_exporter_last_scrape_error` | Gauge | boolean (0/1) | Whether last scrape had errors | Scrape health — value >0 means exporter can't reach Redis | `addr`, `err` |
| `redis_keyspace_hits_total` | Counter | ops | Successful key lookups from keyspace | Hit ratio numerator | `addr` |
| `redis_keyspace_misses_total` | Counter | ops | Failed key lookups (key didn't exist) | Hit ratio denominator — rising misses = cache thrash | `addr` |
| `redis_evicted_keys_total` | Counter | keys | Keys evicted due to `maxmemory` policy | **KEY METRIC** — any non-zero rate means memory pressure is forcing data loss | `addr` |
| `redis_expired_keys_total` | Counter | keys | Keys removed by TTL expiration | Normal lifecycle; sudden spikes may indicate bulk TTL alignment | `addr` |
| `redis_blocked_clients` | Gauge | clients | Clients blocked on BLPOP/BRPOP/WAIT | Consumer starvation or slow producers | `addr` |
| `redis_connections_received_total` | Counter | connections | Total connections accepted | Connection churn — high rate means short-lived connections (missing pooling) | `addr` |
| `redis_commands_processed_total` | Counter | commands | Total commands executed | Throughput baseline; correlate drops with app errors | `addr` |
| `redis_memory_used_bytes` | Gauge | bytes | Total memory allocated by Redis | Capacity planning; compare with `redis_memory_max_bytes` | `addr` |
| `redis_memory_max_bytes` | Gauge | bytes | `maxmemory` configuration limit | Saturation ceiling — 0 means no limit set | `addr` |
| `redis_mem_fragmentation_ratio` | Gauge | ratio | RSS / used_memory ratio | >1.5 = excessive fragmentation (restart or defrag needed); <1 = swapping (critical) | `addr` |
| `redis_db_keys` | Gauge | keys | Number of keys in each database | Capacity monitoring per logical DB | `addr`, `db` |
| `redis_rdb_last_bgsave_status` | Gauge | boolean (1=ok, 0=err) | Status of last RDB background save | Persistence health — 0 means backup is failing | `addr` |
| `redis_rdb_changes_since_last_save` | Gauge | changes | Unflushed writes since last RDB save | Data-at-risk gauge; rising = longer recovery point | `addr` |
| `redis_repl_backlog_is_active` | Gauge | boolean (0/1) | Whether replication backlog is active | 0 on a replica = replication not configured | `addr` |
| `redis_master_repl_offset` | Gauge | bytes | Master's replication offset | Compare master vs replica offset for lag detection | `addr` |
| `redis_cpu_sys_seconds_total` | Counter | seconds | System CPU consumed by Redis | CPU saturation; correlate with command rate | `addr` |
| `redis_cpu_user_seconds_total` | Counter | seconds | User CPU consumed by Redis | CPU saturation from Lua scripts or complex commands | `addr` |
| `redis_slowlog_length` | Gauge | entries | Number of entries in slowlog | Rising = more commands exceeding slowlog threshold | `addr` |
| `redis_latest_fork_seconds` | Gauge | seconds | Duration of last fork (bgsave/rewrite) | Long forks (>1s) cause latency spikes for all clients | `addr` |

### Formulas

**Cache Hit Ratio:**
```promql
rate(redis_keyspace_hits_total[5m])
/
(rate(redis_keyspace_hits_total[5m]) + rate(redis_keyspace_misses_total[5m]))
```
Target: >95%. Below 90% indicates application is requesting keys that don't exist (wrong TTLs, cold cache, or logic bug).

**Memory Saturation:**
```promql
redis_memory_used_bytes / redis_memory_max_bytes
```
Alert at >85%. At 100% evictions begin (controlled by `maxmemory-policy`).

**Eviction Rate:**
```promql
rate(redis_evicted_keys_total[5m]) > 0
```
Any sustained eviction = data being discarded. Immediate action required.

**Replication Lag (bytes behind):**
```promql
redis_master_repl_offset - on(addr) group_right() redis_slave_repl_offset
```
Or use `redis_connected_slave_lag_seconds` if available.

### ⚠️ High-Cardinality Labels

| Label | Risk | Mitigation |
|-------|------|------------|
| `db` (on `redis_db_keys`) | Low (bounded 0-15) | Safe |
| `cmd` (on `redis_commands_duration_seconds_total`) | Medium — bounded by command set but ~200 values | Aggregate or use `topk()` |
| `key` (on `redis_key_size`, `redis_key_value`) | **DANGEROUS** — unbounded if `check-keys` glob is wide | Restrict `--check-keys` patterns |

---

## 2. PostgreSQL (prometheus-community/postgres_exporter)

Source: [postgres_exporter](https://github.com/prometheus-community/postgres_exporter) — built-in collectors query `pg_stat_*` views.

**Note**: Citus/distributed PostgreSQL is present in the environment; some metrics may carry additional `datname` values for distributed databases.

### Metric Reference

| Metric | Type | Unit | What It Measures | Troubleshooting Use | Key Labels |
|--------|------|------|------------------|--------------------| -----------|
| `pg_up` | Gauge | boolean (0/1) | Whether exporter can connect to PostgreSQL | Basic reachability health check | `server` |
| `pg_stat_database_xact_commit` | Counter | transactions | Committed transactions | Throughput baseline | `datid`, `datname` |
| `pg_stat_database_xact_rollback` | Counter | transactions | Rolled-back transactions | Rising rollback ratio = app errors or deadlocks | `datid`, `datname` |
| `pg_stat_database_blks_hit` | Counter | blocks | Buffer cache hits (shared_buffers) | Cache efficiency numerator | `datid`, `datname` |
| `pg_stat_database_blks_read` | Counter | blocks | Disk reads (cache misses) | Cache efficiency denominator — rising = cold cache or undersized `shared_buffers` | `datid`, `datname` |
| `pg_stat_database_tup_fetched` | Counter | rows | Rows fetched (by index scan) | Index usage efficiency | `datid`, `datname` |
| `pg_stat_database_tup_returned` | Counter | rows | Rows returned (incl. seq scans) | High returned/fetched ratio = missing indexes (seq scans) | `datid`, `datname` |
| `pg_stat_database_tup_inserted` | Counter | rows | Rows inserted | Write throughput | `datid`, `datname` |
| `pg_stat_database_tup_updated` | Counter | rows | Rows updated | Write throughput (heavier than inserts due to MVCC) | `datid`, `datname` |
| `pg_stat_database_tup_deleted` | Counter | rows | Rows deleted | Dead tuple generation rate | `datid`, `datname` |
| `pg_stat_activity_count` | Gauge | connections | Active backend count by state | Connection saturation when approaching `max_connections` | `server`, `datname`, `state` |
| `pg_stat_activity_max_tx_duration` | Gauge | seconds | Duration of longest running transaction | Long transactions block vacuum and cause bloat | `server`, `datname`, `state` |
| `pg_locks_count` | Gauge | locks | Number of locks by mode | Lock contention — high `ExclusiveLock` blocks writes | `datname`, `mode` |
| `pg_replication_last_replay_seconds` | Gauge | seconds | **KEY METRIC** — replication lag in seconds | Replica freshness; >30s for read replicas serving live traffic = stale reads | `server` |
| `pg_replication_is_replica` | Gauge | boolean (0/1) | Whether this instance is a replica | Topology identification | `server` |
| `pg_database_size_bytes` | Gauge | bytes | Total size of database on disk | Capacity planning; sudden growth = bloat or data explosion | `datname` |
| `pg_stat_user_tables_n_dead_tup` | Gauge | tuples | Dead tuples not yet vacuumed | Table bloat indicator — dead/live ratio >10% needs vacuum | `datname`, `schemaname`, `relname` |
| `pg_stat_user_tables_n_live_tup` | Gauge | tuples | Live tuples in table | Denominator for bloat ratio | `datname`, `schemaname`, `relname` |
| `pg_stat_user_tables_last_autovacuum` | Gauge | timestamp (unix) | When autovacuum last ran | Stale timestamps = vacuum not running (blocked by long tx?) | `datname`, `schemaname`, `relname` |
| `pg_settings_max_connections` | Gauge | connections | Configured `max_connections` | Saturation ceiling for `pg_stat_activity_count` | `server` |
| `pg_wal_size_bytes` | Gauge | bytes | WAL segment total size | WAL growth indicates write amplification or archival lag | `server` |

### Formulas

**Buffer Cache Hit Ratio:**
```promql
rate(pg_stat_database_blks_hit{datname="mydb"}[5m])
/
(rate(pg_stat_database_blks_hit{datname="mydb"}[5m]) + rate(pg_stat_database_blks_read{datname="mydb"}[5m]))
```
Target: >99%. Below 95% = severe disk pressure, increase `shared_buffers` or investigate query patterns.

**Transaction Rollback Ratio:**
```promql
rate(pg_stat_database_xact_rollback{datname="mydb"}[5m])
/
(rate(pg_stat_database_xact_commit{datname="mydb"}[5m]) + rate(pg_stat_database_xact_rollback{datname="mydb"}[5m]))
```
Target: <1%. Above 5% indicates systemic application errors or deadlocks.

**Connection Saturation:**
```promql
pg_stat_activity_count{state="active"}
/
pg_settings_max_connections
```
Alert at >80%. At 100% new connections are refused (app sees "connection refused" or timeout).

**Table Bloat Ratio:**
```promql
pg_stat_user_tables_n_dead_tup
/
(pg_stat_user_tables_n_live_tup + pg_stat_user_tables_n_dead_tup)
```
Above 20% = vacuum is not keeping up. Check `pg_stat_activity_max_tx_duration` for blockers.

**Sequential Scan Ratio (missing indexes signal):**
```promql
rate(pg_stat_database_tup_returned{datname="mydb"}[5m])
/
rate(pg_stat_database_tup_fetched{datname="mydb"}[5m])
```
Ratio >10 suggests heavy sequential scans — likely missing indexes.

### ⚠️ High-Cardinality Labels

| Label | Risk | Mitigation |
|-------|------|------------|
| `datname` | Low-Medium (bounded by DB count) | Safe in most setups |
| `schemaname` + `relname` (on `pg_stat_user_tables_*`) | **Medium-High** — one series per table | Monitor total series; consider filtering to top-N tables |
| `state` (on `pg_stat_activity_count`) | Low (bounded: active, idle, idle in transaction, etc.) | Safe |
| `mode` (on `pg_locks_count`) | Low (bounded ~8 lock modes) | Safe |

---

## 3. CoreDNS (built-in prometheus/metrics plugin)

Source: [CoreDNS prometheus plugin](https://coredns.io/plugins/metrics/), [cache plugin](https://coredns.io/plugins/cache/), [forward plugin](https://coredns.io/plugins/forward/).

### Metric Reference

| Metric | Type | Unit | What It Measures | Troubleshooting Use | Key Labels |
|--------|------|------|------------------|--------------------| -----------|
| `coredns_dns_requests_total` | Counter | queries | Total DNS queries received | Throughput baseline; breakdown by type shows query mix | `server`, `zone`, `proto`, `family`, `type` |
| `coredns_dns_request_duration_seconds_bucket` | Histogram | seconds | **KEY METRIC** — DNS query processing latency | Directly correlates with app-side `dns.lookup.duration`; p99 >100ms degrades all HTTP clients | `server`, `zone`, `type` |
| `coredns_cache_hits_total` | Counter | hits | Cache hits by type (denial/success) | Cache efficiency numerator | `server`, `type`, `zones` |
| `coredns_cache_misses_total` | Counter | misses | Cache misses (deprecated — derive from hits/requests) | Rising misses = cold cache, TTL too low, or new domains | `server`, `zones` |
| `coredns_cache_entries` | Gauge | entries | Current cache size by type | Capacity monitoring (bounded by configured max) | `server`, `type`, `zones` |
| `coredns_forward_healthcheck_broken_total` | Counter | events | All upstreams unhealthy events | **Critical** — means CoreDNS has no healthy resolver and is spraying randomly | — |
| `coredns_forward_max_concurrent_rejects_total` | Counter | rejects | Queries rejected due to concurrency limit | DNS queries being dropped under load | — |
| `coredns_panics_total` | Counter | panics | CoreDNS process panics | Any increment = bug or resource exhaustion requiring restart | — |
| `coredns_proxy_request_duration_seconds_bucket` | Histogram | seconds | Forward proxy latency per upstream | Identifies slow upstream resolvers | `proxy_name`, `to`, `rcode` |

### Formulas

**Cache Hit Ratio:**
```promql
sum(rate(coredns_cache_hits_total[5m]))
/
(sum(rate(coredns_cache_hits_total[5m])) + sum(rate(coredns_cache_misses_total[5m])))
```
Target: >80% for internal services (predictable domains). Low ratio = excessive unique domain lookups or TTL too short.

**DNS Latency (p99):**
```promql
histogram_quantile(0.99, sum by(le) (rate(coredns_dns_request_duration_seconds_bucket[5m])))
```
Target: <50ms. Above 200ms visibly degrades all HTTP-based services (every new connection starts with DNS).

**Forward Error Rate:**
```promql
rate(coredns_forward_healthcheck_broken_total[5m]) > 0
```
Any sustained rate = all upstream resolvers failing. DNS resolution is degraded cluster-wide.

### ⚠️ High-Cardinality Labels

| Label | Risk | Mitigation |
|-------|------|------------|
| `type` (on `coredns_dns_requests_total`) | Low (~15 DNS types: A, AAAA, SRV, etc.) | Safe |
| `zone` | Low-Medium (bounded by Corefile zones) | Safe |
| `to` (on `coredns_proxy_request_duration_seconds`) | Low (bounded by configured forwarders) | Safe |
| `rcode` (on responses) | Low (~15 rcodes: NOERROR, NXDOMAIN, SERVFAIL, etc.) | Safe |

---

## How Metrics Interrelate (Cross-Signal Correlation)

### App → Backing Service Correlation Map

```
┌────────────────────────────────────────────────────────────────────┐
│ APPLICATION (OTel SDK metrics)                                      │
│                                                                     │
│  db.client.operation.duration ──────────┐                          │
│  http.client.request.duration ──────────┤                          │
│  dns.lookup.duration ───────────────────┤                          │
└─────────────────────────────────────────┼──────────────────────────┘
                                          │ correlates with
┌─────────────────────────────────────────┼──────────────────────────┐
│ BACKING SERVICES                        ▼                          │
│                                                                     │
│  PostgreSQL:                                                        │
│    pg_stat_database_blks_read ↑  → app sees high db.client latency │
│    pg_stat_activity_count saturated → app sees connection timeouts  │
│    pg_replication_last_replay_seconds ↑ → stale reads on replicas  │
│                                                                     │
│  Redis:                                                             │
│    redis_evicted_keys_total ↑ → app sees cache misses (hit ratio↓) │
│    redis_blocked_clients ↑ → app sees BLPOP timeouts               │
│    redis_mem_fragmentation_ratio <1 → swapping → all ops slow      │
│    redis_latest_fork_seconds ↑ → periodic latency spikes           │
│                                                                     │
│  CoreDNS:                                                           │
│    coredns_dns_request_duration_seconds p99 ↑ → app dns.lookup ↑   │
│    coredns_forward_healthcheck_broken ↑ → SERVFAIL → app conn fail │
│    coredns_forward_max_concurrent_rejects ↑ → DNS drops → timeouts │
└─────────────────────────────────────────────────────────────────────┘
```

### Correlation Patterns

| App-Side Symptom | First Backend Metric to Check | Correlation Logic |
|------------------|-------------------------------|-------------------|
| `db.client.operation.duration` spikes | `pg_stat_database_blks_read` rate | Cache miss → disk IO → slow queries |
| `db.client.operation.duration` spikes | `pg_locks_count{mode="ExclusiveLock"}` | Lock contention → query waiting |
| `db.client.operation.duration` spikes | `pg_stat_activity_count` vs `pg_settings_max_connections` | Connection exhaustion → queuing |
| Redis cache miss ratio in app | `redis_evicted_keys_total` rate | Evictions = forced cache invalidation |
| Redis operation timeout | `redis_blocked_clients` | Blocking commands starving pool |
| Redis operation timeout | `redis_latest_fork_seconds` | Fork (bgsave) blocking event loop |
| HTTP client timeouts | `coredns_dns_request_duration_seconds` p99 | DNS resolution delay before TCP connect |
| Intermittent connection failures | `coredns_forward_healthcheck_broken_total` | No healthy DNS upstream |
| Stale data on read replicas | `pg_replication_last_replay_seconds` | Replica behind master |

---

## Symptom → Metric Quick Reference

### Redis Symptoms

| Symptom | Query | Threshold |
|---------|-------|-----------|
| Cache thrashing | `rate(redis_evicted_keys_total[5m]) > 0` | Any sustained eviction |
| Memory pressure | `redis_memory_used_bytes / redis_memory_max_bytes > 0.85` | >85% |
| Low hit ratio | `rate(redis_keyspace_hits_total[5m]) / (rate(redis_keyspace_hits_total[5m]) + rate(redis_keyspace_misses_total[5m])) < 0.9` | <90% |
| Fragmentation | `redis_mem_fragmentation_ratio > 1.5 or redis_mem_fragmentation_ratio < 1` | >1.5 or <1 (swapping) |
| Fork latency spike | `redis_latest_fork_seconds > 1` | >1s (causes client-visible pause) |
| Backup failing | `redis_rdb_last_bgsave_status == 0` | 0 = error |
| Slow commands | `redis_slowlog_length > 10` | Growing = accumulating slow ops |
| Connection storm | `rate(redis_connections_received_total[1m]) > 100` | Context-dependent; high churn = missing pool |

### PostgreSQL Symptoms

| Symptom | Query | Threshold |
|---------|-------|-----------|
| Cache miss (disk IO) | `rate(pg_stat_database_blks_read[5m]) / (rate(pg_stat_database_blks_hit[5m]) + rate(pg_stat_database_blks_read[5m])) > 0.05` | >5% miss = poor caching |
| Connection saturation | `pg_stat_activity_count / pg_settings_max_connections > 0.8` | >80% |
| Replication lag | `pg_replication_last_replay_seconds > 30` | >30s for live read replicas |
| Long transactions | `pg_stat_activity_max_tx_duration > 300` | >5min blocks vacuum |
| Table bloat | `pg_stat_user_tables_n_dead_tup / (pg_stat_user_tables_n_live_tup + 1) > 0.2` | >20% dead tuples |
| High rollback rate | `rate(pg_stat_database_xact_rollback[5m]) / (rate(pg_stat_database_xact_commit[5m]) + rate(pg_stat_database_xact_rollback[5m])) > 0.05` | >5% |
| Lock contention | `pg_locks_count{mode=~"ExclusiveLock\|RowExclusiveLock"} > 50` | Context-dependent |
| WAL growth | `deriv(pg_wal_size_bytes[1h]) > 0` | Sustained growth = archival lag |

### CoreDNS Symptoms

| Symptom | Query | Threshold |
|---------|-------|-----------|
| DNS latency | `histogram_quantile(0.99, sum by(le) (rate(coredns_dns_request_duration_seconds_bucket[5m]))) > 0.1` | p99 > 100ms |
| Cache cold | `sum(rate(coredns_cache_misses_total[5m])) / (sum(rate(coredns_cache_hits_total[5m])) + sum(rate(coredns_cache_misses_total[5m]))) > 0.5` | >50% miss |
| Upstream failure | `rate(coredns_forward_healthcheck_broken_total[5m]) > 0` | Any increment |
| Overload drops | `rate(coredns_forward_max_concurrent_rejects_total[5m]) > 0` | Any increment = queries being dropped |
| Process instability | `rate(coredns_panics_total[5m]) > 0` | Any panic = critical |
| NXDOMAIN storm | `rate(coredns_dns_requests_total{rcode="NXDOMAIN"}[5m])` | Context-dependent spike |

---

## Verification Sources

| Exporter | Official Documentation |
|----------|----------------------|
| Redis | [oliver006/redis_exporter](https://github.com/oliver006/redis_exporter) — metrics from `INFO` command, mapped in `exporter.go` `metricMapGauges` / `metricMapCounters` |
| PostgreSQL | [prometheus-community/postgres_exporter](https://github.com/prometheus-community/postgres_exporter) — built-in collectors querying `pg_stat_database`, `pg_stat_activity`, `pg_stat_user_tables`, `pg_replication_*`, `pg_locks`, `pg_settings` |
| CoreDNS | [coredns.io/plugins/metrics](https://coredns.io/plugins/metrics/) (prometheus plugin), [coredns.io/plugins/cache](https://coredns.io/plugins/cache/) (cache plugin), [coredns.io/plugins/forward](https://coredns.io/plugins/forward/) (forward plugin) |

---

*All metrics confirmed present in live VictoriaMetrics inventory (2026-07-06).*
