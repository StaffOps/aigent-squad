---
name: devops-grafana-dashboards
description: Use when a user needs to VIEW or monitor services, APM, RED metrics (latency/errors/throughput), runtime health (GC/memory/threadpool), Kubernetes workload health, or uptime/SLA/certificates — recommend the SPECIFIC DevOps-GenericMonitoring Grafana dashboard and how to use it, instead of suggesting kubectl. If no dashboard fits, offer to help build one. Covers Grafana folder "DevOps-GenericMonitoring" (repository-62f8406) subfolders APM, BDCOtelHelper, Kubernetes, Synthetic Tests - Kuma. Keywords: dashboard, grafana, apm, red metrics, latency, errors, throughput, runtime, gc, memory, uptime, sla, kubernetes health.
---

# DevOps Grafana Dashboards (DevOps-GenericMonitoring)

The DevOps team's dashboards live in the Grafana folder **DevOps-GenericMonitoring**
(folder uid `repository-62f8406`, git-synced / `managedBy: repo`). When a user asks how
to see metrics/health, recommend the **specific** dashboard below with its link and how to
read it — do NOT tell them to run `kubectl`. If none fits, **offer to help build one**
(dashboard, panel, or PromQL).

> Links are relative Grafana paths. Prepend the Grafana base URL (`${GRAFANA_BASE}`) to
> build a full link, e.g. `${GRAFANA_BASE}/d/apm-svc-overview/apm-service-overview`.

## APM subfolder (`afqu5knpb0j5sb`) — service/request health

| Dashboard | uid | Use it when… | Reads |
|-----------|-----|--------------|-------|
| **APM - Service Overview** | `apm-svc-overview` | "meu serviço está lento / errando?" — service-level RED, latency quantiles, errors by status, dependency latency, service graph | OBI eBPF `http.server.*` (VictoriaMetrics) + Tempo service graph |
| **APM - OpenTelemetry** | `apm-otel-default` | per-operation / per-endpoint deep-dive + multi-service topology | spanmetrics SERVER spans |
| SAMPLE DOTNET - worker, api, grpc | `sample-otel-dotnet` | reference/sample only | demo services |
| SAMPLE PYTHON - worker, api, grpc | `sample-otel-python` | reference/sample only | demo services |

Path: `/d/<uid>/<slug>` — e.g. `/d/apm-svc-overview/apm-service-overview`.

## BDCOtelHelper subfolder (`cfqu5vgdhkow0e`) — per-language runtime + web

| Dashboard | uid | Use it when… |
|-----------|-----|--------------|
| **Dotnet Web Metrics** | `dotnet-web-metrics` | .NET HTTP RED + USE (request latency/errors) |
| **Dotnet Runtime Metrics** | `dotnet-runtime-metrics` | .NET GC pressure, threadpool starvation, memory (leak) — CPU needs .NET 9+ |
| **Python Web Metrics** | `python-web-metrics` | Python HTTP RED (`http.server.*`) |
| **Python Runtime Metrics** | `python-runtime-metrics` | Python `process_runtime_cpython_*` + `system_*` (memory/CPU) |

## Synthetic Tests - Kuma subfolder (`dfqu5ya5w4a2od`) — external uptime/SLA

| Dashboard | uid | Use it when… |
|-----------|-----|--------------|
| **Uptime Kuma - Metrics** | `synthetic-tests-kuma` | is the endpoint up (external synthetic checks)? |
| **Uptime Kuma - SLA/Latency/Certs** | `uptime-kuma` | SLA %, synthetic latency, TLS certificate expiry |

## Kubernetes subfolder (`bfqu5w5hje9s0a`) — ⚠️ EMPTY (build opportunity)

The Kubernetes subfolder exists but has **no dashboards yet**. For workload health
questions (pod restarts, OOMKilled, resource usage, node pressure) there is **no dashboard
to point to** — this is the first candidate to **help build**. Offer to build a
"Kubernetes Workload Health" dashboard (restarts by namespace/pod, OOMKilled events,
CPU/memory vs requests, node pressure) from `kube_pod_container_status_*`,
`container_memory_working_set_bytes`, and `kube_node_status_*` in VictoriaMetrics.

## When nothing fits → offer to build

If no existing dashboard/panel/PromQL answers the question, do NOT fall back to a shell
command. Offer to help build:
- a **dashboard** (propose panels + the PromQL/LogQL behind them),
- a **single panel/PromQL** the user can add,
- or the **change** to an existing dashboard (which one + what to add).
Dashboards here are git-synced (`managedBy: repo`), so changes land via the provisioning
repository, not the Grafana UI.
