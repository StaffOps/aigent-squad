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
| **APM - Service Overview** | `apm-svc-overview` | "my service is slow / throwing errors?" — service-level RED, latency quantiles, errors by status, dependency latency, service graph | OBI eBPF `http.server.*` (VictoriaMetrics) + Tempo service graph |
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

## Kubernetes folder (`bfqu5w5hje9s0a`, under DevOps-GenericMonitoring) — 3 subfolders

**Comprehensive coverage — do NOT build new K8s dashboards; point here.** (Corrected
2026-07-23: this folder was previously mis-catalogued as empty. It holds 3 subfolders.)

### `Argo` (`bfqucadu1zy0wf`)
| Dashboard | uid | Use it when… |
|-----------|-----|--------------|
| **Argo Rollouts - Overview** | `argo-rollouts-overview` | canary/blue-green progress, rollout health, pause/abort/analysis |
| **ArgoCD - Application Overview** | `dcfqusckg2t81sd` | app sync status, drift, reconciliation health |

### `EKS` (`cfqucch8u1rlsc`) — workload health (the go-to for "is service X healthy?")
| Dashboard | uid | Use it when… |
|-----------|-----|--------------|
| **Cluster - Global Overview** | `defqu7doew581sd` | cluster-wide CPU/mem/pod capacity |
| **Cluster - Namespaces Overview** | `ddfqu7g7fly58gb` | per-namespace resource usage ranking |
| **Compute Resources - Namespace (Pods)** | `dffqu6t0zi27eoa` | pods in a namespace: CPU/mem vs requests |
| **Compute Resources - Namespace (Workloads)** | `dffqu6u5y41ou8d` | workloads (deploy/sts/ds) in a namespace |
| **Compute Resources - Node (Pods)** | `ddfqu6uihxr3lsc` | node pressure, pods per node |
| **Compute Resources - Pod** | `ddfqu6uumcfv9cf` | single-pod deep-dive: CPU/mem/restarts/throttling |
| **Compute Resources - Workload** | `dcfqu6v1p4og74f` | a deployment/sts CPU/mem over time |
| **Kubernetes - App Issue View** | `dffqu7i03wqcxsf` | triage an app's issues (restarts, OOM, errors) |
| **Kubernetes - App Workload** | `dcfqu7htmk5af4c` | an app's workload health |
| **Persistent Volumes** | `dafqu6z2bq0em8b` | PVC usage/capacity, volume pressure |

### `Istio` (`bfqucb8lq8iyoe`) — service mesh
| Dashboard | uid | Use it when… |
|-----------|-----|--------------|
| **Istio - RED** | `dcfqu73q4pgzcwc` | rate/errors/duration per service (mesh RED) |
| **Istio - Traffic per Pod** | `dffqu6z75fvl6of` | per-pod mesh traffic |

> A full **kubernetes-mixin** set (DVPS-tagged) also lives in **DevOps-Default/Kubernetes-Default**
> (`7030053e-2ebb`) — API server, Kubelet, Networking, Proxy, Compute Resources, PV. Prefer the
> **EKS** subfolder above for workload-health; use the mixin for control-plane/kubelet/networking depth.

## When nothing fits → offer to build

If no existing dashboard/panel/PromQL answers the question, do NOT fall back to a shell
command. Offer to help build:
- a **dashboard** (propose panels + the PromQL/LogQL behind them),
- a **single panel/PromQL** the user can add,
- or the **change** to an existing dashboard (which one + what to add).
Dashboards here are git-synced (`managedBy: repo`), so changes land via the provisioning
repository, not the Grafana UI.
