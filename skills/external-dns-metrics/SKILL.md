---
name: external-dns-metrics
description: "Use when diagnosing ExternalDNS health \u2014 DNS record sync failures, source/registry endpoint drift, provider API errors, reconciliation staleness, or split-horizon (private/public) divergence. Covers external_dns_controller_*, external_dns_registry_*, external_dns_source_*, external_dns_provider_*, external_dns_http_*, plus go_* and process_* runtime. Grounded on Helm chart external-dns/external-dns 1.21.1 (appVersion v0.21.0)."
keywords: [external-dns, external, dns, "external dns"]
---
# ExternalDNS Prometheus Metrics Catalog

Health and operational metrics for **ExternalDNS** — the controller that synchronizes
Kubernetes Service/Ingress/Gateway resources to DNS providers (AWS Route53 in this environment).

**Deployed version**: Helm chart `external-dns/external-dns` **1.21.1** (appVersion **v0.21.0**).
**Source**: [kubernetes-sigs/external-dns](https://github.com/kubernetes-sigs/external-dns) official metrics docs.

---

## Scrape Pipeline

```
ExternalDNS pod (:7979/metrics) → vmagent (ServiceMonitor) → VictoriaMetrics
```

- **Port**: 7979 (default metrics port)
- **ServiceMonitor**: enabled via `serviceMonitor.enabled: true` in Helm values
- **Two instances deployed per cluster**:
  - `external-dns-private` — manages private zones (`bdc.app.br`, `bdc.api.br`, `bdc.internal`), `--aws-zone-type=private`
  - `external-dns-public` — manages public zones (`bigdatacorp.com.br`, `bigdatacorp.info`), `--aws-zone-type=public`
- **Namespace**: `external-dns`
- **Sync interval**: `1m` (configured via `interval: 1m`)
- **Policy**: `upsert-only` (never deletes records)
- **Registry**: TXT (ownership via `txtOwnerId` = cluster name)

Both instances use the same IRSA role (`ExternalDNSRole-<cluster>`) and the same annotation filter
(`external-dns.alpha.kubernetes.io/hostname`).

---

## 1. Controller Metrics (Reconciliation Health)

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `external_dns_controller_last_sync_timestamp_seconds` | Gauge | Timestamp of last **successful** sync with DNS provider | Stale value (now - value > 2×interval) = sync loop stuck or failing | — |
| `external_dns_controller_last_reconcile_timestamp_seconds` | Gauge | Timestamp of last **attempted** reconciliation (success or fail) | If this advances but `last_sync` doesn't → provider-side errors | — |
| `external_dns_controller_verified_records` | Gauge | Records existing in both source AND registry (i.e., confirmed in DNS) | Drop = records disappearing from DNS; compare to `source_endpoints_total` | `record_type` |
| `external_dns_controller_no_op_runs_total` | Counter | Reconcile loops that found nothing to change | High rate = healthy steady-state; sudden drop = changes started happening | — |
| `external_dns_controller_consecutive_soft_errors` | Gauge | Consecutive soft errors in reconciliation loop | >0 sustained = transient provider/source failures accumulating; investigate before hard failure | — |

---

## 2. Registry Metrics (TXT Ownership Records)

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `external_dns_registry_endpoints_total` | Gauge | Number of DNS endpoints tracked in the TXT registry | Baseline count of managed records; unexpected drop = ownership records lost | — |
| `external_dns_registry_errors_total` | Counter | Errors reading/writing the TXT registry | Non-zero rate = Route53 API failures on TXT records or permission issues | — |
| `external_dns_registry_records` | Gauge | Registry records partitioned by type | Per-type breakdown of managed DNS records | `record_type` |
| `external_dns_registry_skipped_records_owner_mismatch_per_sync` | Gauge | Records skipped because another owner ID claims them | Non-zero = multi-cluster collision — another cluster's ExternalDNS owns the record | `record_type`, `owner`, `foreign_owner`, `domain` |

---

## 3. Source Metrics (Kubernetes Resource Discovery)

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `external_dns_source_endpoints_total` | Gauge | Number of DNS endpoints discovered from K8s sources (Service, Ingress, Gateway) | Drift vs `registry_endpoints_total` = records not synced yet or being filtered | — |
| `external_dns_source_errors_total` | Counter | Errors reading Kubernetes sources (API server failures) | Non-zero rate = RBAC issue, API server overloaded, or CRD not installed | — |
| `external_dns_source_records` | Gauge | Source records partitioned by type | Per-type count from K8s; compare to registry for drift detection | `record_type` |
| `external_dns_source_deduplicated_endpoints` | Gauge | Endpoints removed as duplicates | High value = multiple K8s resources producing same FQDN | `record_type`, `source_type` |
| `external_dns_source_invalid_endpoints` | Gauge | Endpoints rejected due to invalid config | Non-zero = misconfigured annotations on Services/Ingresses | `record_type`, `source_type` |

---

## 4. Provider Cache Metrics (Route53 API Efficiency)

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `external_dns_provider_cache_records_calls` | Counter | Calls to provider cache for listing records | High rate with `from_cache=false` = frequent Route53 API calls (throttling risk) | `from_cache` |
| `external_dns_provider_cache_apply_changes_calls` | Counter | Calls to provider cache for applying changes | Rate = actual mutation frequency against Route53 | — |

---

## 5. HTTP Request Metrics (ExternalDNS API Client)

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `external_dns_http_request_duration_seconds` | Summary | Latency of HTTP requests made by ExternalDNS (e.g., webhook providers) | High quantiles = slow provider responses; timeout risk | `handler`, `scheme`, `host`, `path`, `method`, `status` |

> **Note**: For the AWS Route53 provider (native, not webhook), API call metrics go through the AWS SDK
> and are NOT exposed as `external_dns_http_*`. Route53 throttling manifests as `registry_errors_total`
> or `controller_consecutive_soft_errors` rising.

---

## 6. Build Info

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `external_dns_build_info` | Gauge (constant=1) | Version metadata of the running binary | Confirm expected version after upgrade | `arch`, `go_version`, `os`, `revision`, `version` |

---

## 7. Go Runtime & Process Metrics

Standard `go_*` and `process_*` metrics from `client_golang`. See **go-apm-metrics** skill for full catalog.

Key ones for ExternalDNS:

| Metric Name | Type | What It Measures | Troubleshooting Use |
|---|---|---|---|
| `go_goroutines` | Gauge | Live goroutines | Leak = goroutine count growing monotonically |
| `go_memstats_heap_inuse_bytes` | Gauge | Heap memory in use | OOM risk if approaching container memory limit |
| `process_resident_memory_bytes` | Gauge | RSS of the process | Compare to resource requests/limits |
| `process_cpu_seconds_total` | Counter | CPU time consumed | Unexpected spike = heavy reconciliation loop |

---

## Troubleshooting Quick Reference

| Symptom | First Metrics to Check | Likely Cause |
|---------|------------------------|--------------|
| DNS records not appearing | `source_endpoints_total` vs `registry_endpoints_total` + `controller_last_sync_timestamp_seconds` | Source not discovering endpoints (annotation missing) or sync stuck |
| Records disappearing | `registry_skipped_records_owner_mismatch_per_sync` | Another cluster's ExternalDNS claiming ownership (txtOwnerId collision) |
| Sync loop stuck | `controller_last_reconcile_timestamp_seconds` vs `controller_last_sync_timestamp_seconds` | Provider errors (throttling, IAM), check `registry_errors_total` |
| Route53 throttling | `registry_errors_total` rate + `controller_consecutive_soft_errors` | Too many hosted zones or records; increase `interval`, check IAM limits |
| Wrong zone updated | Compare `external-dns-private` vs `external-dns-public` `registry_endpoints_total` | Misconfigured `domainFilters` or `--aws-zone-type` |
| Source errors | `source_errors_total` rate | RBAC (ClusterRole missing Gateway API resources), API server pressure |
| Memory growing | `go_memstats_heap_inuse_bytes` trend | Large number of endpoints, or goroutine leak on API watch |
| Records exist in source but not DNS | `source_endpoints_total` > `controller_verified_records` | Pending sync (wait 1 interval) or `upsert-only` policy blocking |
| Owner mismatch warnings | `registry_skipped_records_owner_mismatch_per_sync` > 0 | Multi-cluster: `txtPrefix` collision between clusters |

---

## Split-Horizon Debugging (Private vs Public)

Both instances share the same namespace. Differentiate by release name label in metrics:

```promql
# Private instance: records in registry
external_dns_registry_endpoints_total{app_kubernetes_io_instance="external-dns-private"}

# Public instance: records in registry
external_dns_registry_endpoints_total{app_kubernetes_io_instance="external-dns-public"}

# Compare source vs registry per instance (drift detection)
external_dns_source_endpoints_total{app_kubernetes_io_instance="external-dns-private"}
  - external_dns_registry_endpoints_total{app_kubernetes_io_instance="external-dns-private"}
```

Common split-horizon issues:
- Private record in public zone → wrong `domainFilters`
- Both instances claiming same record → `txtOwnerId` must differ (here: uses cluster name = same, but zone-type filter prevents collision)

---

## Key Queries

```promql
# Sync freshness (seconds since last successful sync)
time() - external_dns_controller_last_sync_timestamp_seconds

# Source→Registry drift (should be ~0 in steady state)
external_dns_source_endpoints_total - external_dns_registry_endpoints_total

# Error rate (5m window)
rate(external_dns_registry_errors_total[5m])
rate(external_dns_source_errors_total[5m])

# Ownership collisions per sync
external_dns_registry_skipped_records_owner_mismatch_per_sync > 0

# No-op ratio (healthy = high)
rate(external_dns_controller_no_op_runs_total[10m])
```

---

## Complements

- **go-apm-metrics** — full Go runtime metrics catalog (`go_*`, `process_*`)
- **route53-patterns** (skill) — AWS Route53 hosted zone management, health checks, External-DNS integration
- **eks-management** (skill) — IRSA configuration for ExternalDNS ServiceAccount
- **k8s-workload-metrics** — container resource metrics for the ExternalDNS pods

---

## Sources

- [ExternalDNS Official Metrics Documentation](https://kubernetes-sigs.github.io/external-dns/latest/docs/monitoring/metrics/) (v0.21.0)
- Helm chart `external-dns/external-dns` v1.21.1 — `k8s-setup/external-dns/helmfile.yaml.gotmpl`
- Values: `external-dns-private/values.yaml.gotmpl`, `external-dns-public/values.yaml.gotmpl`
- Metric naming: `external_dns_<subsystem>_<name>` (subsystems: controller, registry, source, provider, http, webhook_provider)
