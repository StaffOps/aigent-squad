---
name: kubescape-metrics
description: "Use when understanding Kubescape operator Prometheus metrics availability and posture-monitoring potential. Covers kubescape_controls_*, kubescape_vulnerabilities_*, node_agent_* metric families. IMPORTANT: the Prometheus exporter is DISABLED in the deployed config (chart v1.27.7) — only standard Go runtime metrics (go_*, process_*) from the kubescape component are scraped via ServiceMonitor. This skill documents what IS and what COULD BE exposed."
keywords: [kubescape, go, prometheus, could]
---
# Kubescape Operator Metrics

**Chart**: `kubescape/kubescape-operator` v1.27.7 (Helm repo `https://kubescape.github.io/helm-charts`).
**Deployed on**: `devops-core` cluster, namespace `kubescape`.

---

## ⚠️ CRITICAL: Prometheus Exporter is DISABLED in Deployed Config

The deployed values set:

```yaml
capabilities:
  prometheusExporter: disable   # ← DISABLED

nodeAgent:
  enabled: false                # ← node-agent not deployed
```

**Consequence**: the dedicated `prometheus-exporter` sidecar (which exposes `kubescape_controls_*` and `kubescape_vulnerabilities_*` scan-result metrics) is **NOT running**. The node-agent (which exposes `node_agent_*` eBPF metrics) is also **NOT deployed**.

**What IS scraped**: only the `kubescape` component's own `/metrics` endpoint via ServiceMonitor (`kubescape.serviceMonitor.enabled: true`). This exposes standard **Go runtime metrics** (`go_*`, `process_*`) from `client_golang` — nothing security-posture-specific.

---

## Scrape Pipeline (Current — Minimal)

```
kubescape pod (:8080/metrics, Go runtime only)
  → ServiceMonitor (kubescape namespace)
    → vmagent scrape
      → VictoriaMetrics
```

The ServiceMonitor is created because `kubescape.serviceMonitor.enabled: true`, but it only scrapes Go process metrics from the main kubescape scanner component.

---

## Currently Scraped Metrics (Go Runtime Only)

Since only the kubescape Go binary's `/metrics` endpoint is scraped, the available metrics are the standard `client_golang` set:

| Metric Name | Type | What It Measures | Troubleshooting Use |
|---|---|---|---|
| `go_goroutines` | Gauge | Live goroutines in kubescape process | Goroutine leak during long scans |
| `go_memstats_alloc_bytes` | Gauge | Current heap allocation | Memory pressure during vuln scanning |
| `go_memstats_sys_bytes` | Gauge | Total memory obtained from OS | Capacity ceiling |
| `go_gc_duration_seconds` | Summary | GC pause durations | GC pressure during large cluster scans |
| `process_cpu_seconds_total` | Counter | Total CPU time consumed | CPU cost of scans |
| `process_resident_memory_bytes` | Gauge | RSS memory | Compare against resource limits (512Mi) |
| `process_open_fds` | Gauge | Open file descriptors | FD exhaustion during SBOM generation |

> For full Go runtime metrics reference, see **`go-apm-metrics`** skill.

---

## Metrics Available IF `prometheusExporter` Were Enabled

If `capabilities.prometheusExporter=enable` is set, Kubescape deploys a dedicated `prometheus-exporter` sidecar that reads Kubescape CRDs (`WorkloadConfigurationScanSummary`, `VulnerabilityManifestSummary`, `VulnerabilitySummary`, `ConfigurationScanSummary`) and exposes aggregated security posture gauges.

Source: [`kubescape/prometheus-exporter` metrics.go](https://github.com/kubescape/prometheus-exporter/blob/main/metrics/metrics.go) (v0.2.22, Apache-2.0).

### Compliance Controls (Configuration Scan Results)

| Metric Name | Type | What It Measures | Labels | Scope |
|---|---|---|---|---|
| `kubescape_controls_total_workload_critical` | Gauge | Critical control failures per workload | `namespace`, `workload`, `workload_kind` | Workload¹ |
| `kubescape_controls_total_workload_high` | Gauge | High control failures per workload | `namespace`, `workload`, `workload_kind` | Workload¹ |
| `kubescape_controls_total_workload_medium` | Gauge | Medium control failures per workload | `namespace`, `workload`, `workload_kind` | Workload¹ |
| `kubescape_controls_total_workload_low` | Gauge | Low control failures per workload | `namespace`, `workload`, `workload_kind` | Workload¹ |
| `kubescape_controls_total_workload_unknown` | Gauge | Unknown-severity control failures per workload | `namespace`, `workload`, `workload_kind` | Workload¹ |
| `kubescape_controls_total_namespace_critical` | Gauge | Critical control failures in namespace | `namespace` | Namespace |
| `kubescape_controls_total_namespace_high` | Gauge | High control failures in namespace | `namespace` | Namespace |
| `kubescape_controls_total_namespace_medium` | Gauge | Medium control failures in namespace | `namespace` | Namespace |
| `kubescape_controls_total_namespace_low` | Gauge | Low control failures in namespace | `namespace` | Namespace |
| `kubescape_controls_total_namespace_unknown` | Gauge | Unknown-severity control failures in namespace | `namespace` | Namespace |
| `kubescape_controls_total_cluster_critical` | Gauge | Critical control failures cluster-wide | — | Cluster |
| `kubescape_controls_total_cluster_high` | Gauge | High control failures cluster-wide | — | Cluster |
| `kubescape_controls_total_cluster_medium` | Gauge | Medium control failures cluster-wide | — | Cluster |
| `kubescape_controls_total_cluster_low` | Gauge | Low control failures cluster-wide | — | Cluster |
| `kubescape_controls_total_cluster_unknown` | Gauge | Unknown-severity control failures cluster-wide | — | Cluster |

¹ Workload-level metrics require `ENABLE_WORKLOAD_METRICS=true` env var on the exporter pod.

### Vulnerability Scan Results (Total)

| Metric Name | Type | What It Measures | Labels | Scope |
|---|---|---|---|---|
| `kubescape_vulnerabilities_total_workload_critical` | Gauge | Critical CVEs per workload container | `namespace`, `workload`, `workload_kind`, `workload_container_name` | Workload¹ |
| `kubescape_vulnerabilities_total_workload_high` | Gauge | High CVEs per workload container | `namespace`, `workload`, `workload_kind`, `workload_container_name` | Workload¹ |
| `kubescape_vulnerabilities_total_workload_medium` | Gauge | Medium CVEs per workload container | `namespace`, `workload`, `workload_kind`, `workload_container_name` | Workload¹ |
| `kubescape_vulnerabilities_total_workload_low` | Gauge | Low CVEs per workload container | `namespace`, `workload`, `workload_kind`, `workload_container_name` | Workload¹ |
| `kubescape_vulnerabilities_total_workload_unknown` | Gauge | Unknown-severity CVEs per workload container | `namespace`, `workload`, `workload_kind`, `workload_container_name` | Workload¹ |
| `kubescape_vulnerabilities_total_namespace_critical` | Gauge | Critical CVEs aggregated per namespace | `namespace` | Namespace |
| `kubescape_vulnerabilities_total_namespace_high` | Gauge | High CVEs aggregated per namespace | `namespace` | Namespace |
| `kubescape_vulnerabilities_total_namespace_medium` | Gauge | Medium CVEs aggregated per namespace | `namespace` | Namespace |
| `kubescape_vulnerabilities_total_namespace_low` | Gauge | Low CVEs aggregated per namespace | `namespace` | Namespace |
| `kubescape_vulnerabilities_total_namespace_unknown` | Gauge | Unknown-severity CVEs aggregated per namespace | `namespace` | Namespace |
| `kubescape_vulnerabilities_total_cluster_critical` | Gauge | Critical CVEs cluster-wide | — | Cluster |
| `kubescape_vulnerabilities_total_cluster_high` | Gauge | High CVEs cluster-wide | — | Cluster |
| `kubescape_vulnerabilities_total_cluster_medium` | Gauge | Medium CVEs cluster-wide | — | Cluster |
| `kubescape_vulnerabilities_total_cluster_low` | Gauge | Low CVEs cluster-wide | — | Cluster |
| `kubescape_vulnerabilities_total_cluster_unknown` | Gauge | Unknown-severity CVEs cluster-wide | — | Cluster |

### Vulnerability Scan Results (Relevant — Runtime-Filtered)

Only populated when `capabilities.relevancy=enable` (currently **disabled**).

| Metric Name | Type | What It Measures | Labels | Scope |
|---|---|---|---|---|
| `kubescape_vulnerabilities_relevant_workload_critical` | Gauge | Critical CVEs actually loaded in runtime per workload | `namespace`, `workload`, `workload_kind`, `workload_container_name` | Workload¹ |
| `kubescape_vulnerabilities_relevant_workload_high` | Gauge | High CVEs actually loaded in runtime per workload | (same) | Workload¹ |
| `kubescape_vulnerabilities_relevant_workload_medium` | Gauge | Medium CVEs actually loaded in runtime per workload | (same) | Workload¹ |
| `kubescape_vulnerabilities_relevant_workload_low` | Gauge | Low CVEs actually loaded in runtime per workload | (same) | Workload¹ |
| `kubescape_vulnerabilities_relevant_workload_unknown` | Gauge | Unknown-severity CVEs actually loaded in runtime | (same) | Workload¹ |
| `kubescape_vulnerabilities_relevant_namespace_critical` | Gauge | Relevant critical CVEs per namespace | `namespace` | Namespace |
| `kubescape_vulnerabilities_relevant_namespace_high` | Gauge | Relevant high CVEs per namespace | `namespace` | Namespace |
| `kubescape_vulnerabilities_relevant_namespace_medium` | Gauge | Relevant medium CVEs per namespace | `namespace` | Namespace |
| `kubescape_vulnerabilities_relevant_namespace_low` | Gauge | Relevant low CVEs per namespace | `namespace` | Namespace |
| `kubescape_vulnerabilities_relevant_namespace_unknown` | Gauge | Relevant unknown-severity CVEs per namespace | `namespace` | Namespace |
| `kubescape_vulnerabilities_relevant_cluster_critical` | Gauge | Relevant critical CVEs cluster-wide | — | Cluster |
| `kubescape_vulnerabilities_relevant_cluster_high` | Gauge | Relevant high CVEs cluster-wide | — | Cluster |
| `kubescape_vulnerabilities_relevant_cluster_medium` | Gauge | Relevant medium CVEs cluster-wide | — | Cluster |
| `kubescape_vulnerabilities_relevant_cluster_low` | Gauge | Relevant low CVEs cluster-wide | — | Cluster |
| `kubescape_vulnerabilities_relevant_cluster_unknown` | Gauge | Relevant unknown-severity CVEs cluster-wide | — | Cluster |

---

## Node-Agent Metrics (NOT Deployed)

The node-agent is **disabled** (`nodeAgent.enabled: false`). If it were enabled with `runtimeDetection=enable` and `nodeAgent.config.prometheusExporter=enable`, it would expose on `:8080/metrics`:

### eBPF Event Counters

| Metric Name | Type | What It Measures |
|---|---|---|
| `node_agent_exec_counter` | Counter | Total exec events from eBPF probe |
| `node_agent_open_counter` | Counter | Total open events from eBPF probe |
| `node_agent_network_counter` | Counter | Total network events from eBPF probe |
| `node_agent_dns_counter` | Counter | Total DNS events from eBPF probe |
| `node_agent_syscall_counter` | Counter | Total syscall events from eBPF probe |
| `node_agent_capability_counter` | Counter | Total capability events from eBPF probe |
| `node_agent_randomx_counter` | Counter | Total randomx (crypto-mining) events from eBPF probe |
| `node_agent_ebpf_event_failure_counter` | Counter | Total failed events from eBPF probe |
| `node_agent_symlink_counter` | Counter | Total symlink events from eBPF probe |
| `node_agent_hardlink_counter` | Counter | Total hardlink events from eBPF probe |
| `node_agent_ssh_counter` | Counter | Total SSH events from eBPF probe |
| `node_agent_http_counter` | Counter | Total HTTP events from eBPF probe |
| `node_agent_ptrace_counter` | Counter | Total ptrace events from eBPF probe |
| `node_agent_iouring_counter` | Counter | Total io_uring events from eBPF probe |

### Rule Engine & Containers

| Metric Name | Type | What It Measures | Labels |
|---|---|---|---|
| `node_agent_rule_counter` | Counter | Rules processed by engine | `rule_id` |
| `node_agent_alert_counter` | Counter | Alerts sent by engine | `rule_id` |
| `node_agent_container_start_counter` | Counter | Container start events | — |
| `node_agent_container_stop_counter` | Counter | Container stop events | — |

### eBPF Program Performance

| Metric Name | Type | What It Measures | Labels |
|---|---|---|---|
| `node_agent_program_current_runtime` | Gauge | Current runtime of eBPF programs | `program_type`, `program_name` |
| `node_agent_program_current_run_count` | Gauge | Current run count of eBPF programs | `program_type`, `program_name` |
| `node_agent_program_total_runtime` | Counter | Total runtime of eBPF programs | `program_type`, `program_name` |
| `node_agent_program_total_run_count` | Counter | Total run count of eBPF programs | `program_type`, `program_name` |
| `node_agent_program_map_memory` | Gauge | Map memory usage per eBPF program | `program_type`, `program_name` |
| `node_agent_program_map_count` | Gauge | Map count per eBPF program | `program_type`, `program_name` |
| `node_agent_program_total_cpu_usage` | Gauge | Total CPU usage per eBPF program | `program_type`, `program_name` |
| `node_agent_program_per_cpu_usage` | Gauge | Per-CPU usage per eBPF program | `program_type`, `program_name` |

---

## Troubleshooting Quick-Reference

| Symptom | What to Check | Current Limitation |
|---------|---------------|-------------------|
| Want vulnerability count trends over time | `kubescape_vulnerabilities_total_cluster_*` | **Not available** — exporter disabled |
| Want compliance drift alerting | `kubescape_controls_total_cluster_*` | **Not available** — exporter disabled |
| Kubescape pod OOMKilling | `process_resident_memory_bytes` vs 512Mi limit | ✅ Available (Go runtime metrics) |
| Long scan durations | `go_goroutines` spikes, `process_cpu_seconds_total` rate | ✅ Available |
| Want to monitor eBPF events for threat detection | `node_agent_*` counters | **Not available** — node-agent disabled |

### How to Enable the Prometheus Exporter

To activate full security-posture metrics, update the helmfile values:

```yaml
capabilities:
  prometheusExporter: enable    # Deploy prometheus-exporter sidecar

# Optional: enable workload-level granularity (higher cardinality)
# Set ENABLE_WORKLOAD_METRICS=true on the exporter pod

# Optional: enable relevancy-filtered metrics
# capabilities:
#   relevancy: enable
```

After enabling, the exporter pod will scrape Kubescape CRDs and expose `kubescape_controls_*` + `kubescape_vulnerabilities_*` gauges on its `/metrics` endpoint. A ServiceMonitor will need to be created or the existing one extended to scrape it.

---

## Cardinality Warning

If workload-level metrics are enabled (`ENABLE_WORKLOAD_METRICS=true`), cardinality scales as:

```
(namespaces × workloads × workload_kinds) × 5 severities × 2 domains (controls + vulns)
+ (namespaces × workloads × containers) × 5 severities × 2 types (total + relevant)
```

For a cluster with 200 workloads across 20 namespaces, this produces ~4000+ time series from the exporter alone. Consider keeping workload-level disabled and using namespace/cluster aggregates unless per-workload alerting is needed.

---

## Complements

- **`go-apm-metrics`** — full Go runtime metrics reference (what the kubescape ServiceMonitor currently scrapes)
- **`k8s-workload-metrics`** — container resource usage for kubescape pods (CPU/memory/restarts)
- **`kyverno-metrics`** — related security posture tool with active Prometheus metrics in this environment

## Sources

- Deployed chart: `kubescape/kubescape-operator` v1.27.7 (helmfile at `02-KUBE/00-CONFIG/k8s-setup/kubescape/`)
- Prometheus exporter source: [github.com/kubescape/prometheus-exporter](https://github.com/kubescape/prometheus-exporter) v0.2.22 (`metrics/metrics.go`)
- Official integration docs: [kubescape.io/docs/operator/prometheus-integration](https://kubescape.io/docs/operator/prometheus-integration/)
- Node-agent metrics: [kubescape.io/docs/operator/prometheus-integration/#node-agent-metrics](https://kubescape.io/docs/operator/prometheus-integration/#node-agent-metrics)
