---
name: scaleops-metrics
description: "Use when assessing ScaleOps platform observability. ScaleOps is a commercial closed-source Kubernetes optimization platform (chart scaleops/scaleops 1.31.1). Its Prometheus metric names are NOT publicly documented \u2014 no official metrics reference exists outside gated vendor documentation. This skill documents the deployed component topology, likely metric endpoints (go_*, controller-runtime workqueue_*), and points to k8s-workload-metrics for pod-level health. Do NOT invent scaleops_* metric names."
keywords: [scaleops, prometheus]
---
# ScaleOps Platform Metrics — Observability Status

**Commercial closed-source platform. Prometheus metric names NOT publicly documented.**

## Deployed Version

| Field | Value |
|-------|-------|
| Helm chart | `scaleops/scaleops` |
| Chart version | **1.31.1** |
| Registry | `registry.scaleops.com/charts` (authenticated) |
| Image registry | `registry.scaleops.com` |
| Namespace | `scaleops-system` |
| CRD API group | `analysis.scaleops.sh/v1alpha1` |

Grounded on: helmfile at `k8s-setup/scaleops/helmfile.yaml.gotmpl` (deployed to
`devops-core`, `applications-dev-nv`, `applications-prd-nv` clusters).

---

## Component Topology (from deployed values)

| Component | Purpose | Deployed |
|-----------|---------|----------|
| **Core controller/recommender** | Right-sizing engine — reads Prometheus/VM metrics, computes recommendations, applies patches | ✅ (implied by CRDs + automation config) |
| **Dashboard** | UI for viewing recommendations, savings, policies | ✅ (`dashboard.enabled: true`, 2 CPU / 4Gi) |
| **Network Monitor** | Network traffic observability, cost attribution | ✅ (`networkMonitor.enabled: true`, 500m / 768Mi) |
| **API Observability** | HTTP/gRPC call monitoring for cost breakdown | ✅ (`global.enableApiObservability: true`) |

Additional configuration:
- `workloadAutomation.excludeTypes: [argoworkflows]`
- `cloudBillingIntegration.aws.enabled: true` (reads CUR from S3)
- Parent URL (PRD): `https://bigdatacorp.scaleops.com` (multi-cluster SaaS control plane)

---

## Metrics Availability — HONEST Assessment

### What is NOT available (cannot be confirmed)

| Category | Status |
|----------|--------|
| Official Prometheus metrics reference | ❌ **Not publicly documented** — docs.scaleops.com is gated |
| `scaleops_*` custom metric names | ❌ **Cannot confirm existence or naming** |
| ServiceMonitor / PodMonitor in chart values | ❌ **Not present in deployed config** |
| Prometheus scrape annotations in values | ❌ **Not configured** |
| Metrics port configuration | ❌ **Not exposed in values** |

### What is LIKELY present (unconfirmed — based on architecture)

ScaleOps is written in Go (confirmed by Red Hat container catalog: Go binary,
operator pattern, `analysis.scaleops.sh` CRDs). Go controllers using
controller-runtime typically expose:

| Likely Metric Prefix | Reasoning | Confidence |
|---------------------|-----------|------------|
| `go_*` | Standard Go runtime metrics (client_golang) — virtually all Go controllers expose these | ⚠️ HIGH (architectural inference, not confirmed for this chart) |
| `workqueue_*` | controller-runtime workqueue metrics — standard for any operator | ⚠️ MEDIUM (operator pattern) |
| `controller_runtime_reconcile_*` | controller-runtime reconciliation metrics | ⚠️ MEDIUM (operator pattern) |
| `rest_client_requests_total` | Kubernetes API client metrics | ⚠️ MEDIUM (operator pattern) |
| `process_*` | Process-level metrics from client_golang | ⚠️ HIGH (architectural inference) |

**⚠️ CRITICAL: These are UNCONFIRMED inferences. Do NOT query these as if they
exist without first verifying via `kubectl get svc -n scaleops-system` + checking
for `/metrics` endpoints on ScaleOps pods.**

### What ScaleOps CONSUMES (confirmed)

ScaleOps **reads** metrics from VictoriaMetrics/Prometheus to make optimization
decisions. It is a **consumer** of the telemetry stack, not primarily an emitter.
From the CloudBolt/StormForge comparison (2026): "ScaleOps requires in-cluster
Prometheus for metrics collection and storage."

In this environment, ScaleOps reads from VictoriaMetrics (vmagent scrapes →
vminsert → vmstorage → vmselect, queried by ScaleOps at the vmselect endpoint).

---

## How to Discover Actual Metrics (verification steps)

If you need to confirm what ScaleOps actually exposes:

```bash
# 1. List ScaleOps pods and services
kubectl get pods -n scaleops-system -o wide
kubectl get svc -n scaleops-system

# 2. Check for /metrics endpoints on services
kubectl get svc -n scaleops-system -o yaml | grep -A5 "port"

# 3. Check pod annotations for prometheus scrape config
kubectl get pods -n scaleops-system -o jsonpath='{range .items[*]}{.metadata.name}: {.metadata.annotations}{"\n"}{end}'

# 4. If a metrics port is found, query it directly
kubectl run scaleops-q -n scaleops-system --rm -i --restart=Never \
  --image=curlimages/curl:latest -- -s "http://<service>:<port>/metrics" | head -50

# 5. Check if vmagent is already scraping ScaleOps
# Query VictoriaMetrics for any scaleops-related targets
# Look for job labels containing "scaleops"
```

---

## Troubleshooting ScaleOps Health (without custom metrics)

Since ScaleOps-specific metrics are not confirmed, use standard Kubernetes
workload telemetry:

| Symptom | What to check | Tool |
|---------|---------------|------|
| ScaleOps not applying recommendations | Pod status, logs, leader election | `kubectl logs -n scaleops-system`, `k8s-workload-metrics` |
| Dashboard unreachable | Dashboard pod health, service endpoints | `kubectl get pods -n scaleops-system -l app=scaleops-dashboard` |
| Network Monitor high resource usage | Container resource consumption | `kubectl top pods -n scaleops-system` |
| CRD reconciliation issues | Events, controller logs | `kubectl get events -n scaleops-system --sort-by='.lastTimestamp'` |
| ScaleOps not seeing workload metrics | VictoriaMetrics connectivity | Check ScaleOps pod logs for Prometheus/VM query errors |
| Recommendations stale or missing | Data pipeline from VM → ScaleOps | Verify vmselect endpoint reachable from scaleops-system namespace |

### Key log patterns to watch

```bash
# Controller/recommender logs
kubectl logs -n scaleops-system -l app.kubernetes.io/name=scaleops --tail=100

# Network monitor logs
kubectl logs -n scaleops-system -l app=scaleops-network-monitor --tail=50

# Dashboard logs (if API errors)
kubectl logs -n scaleops-system -l app=scaleops-dashboard --tail=50
```

---

## Deployed Custom Resources

The `scaleops-raw` bedag/raw release creates:

| CRD Kind | Name | Purpose |
|----------|------|---------|
| `CustomOwnerGrouping` | `cronworkflow` | Groups Argo CronWorkflow pods by workflow name for unified recommendations |
| `Policy` | `cronworkflow` | Defines optimization policy for CronWorkflow workloads (840h window, 93rd percentile, 10% CPU headroom, 5% memory headroom) |

---

## Complements

- **k8s-workload-metrics** — primary tool for ScaleOps pod health (container_*, kube_pod_*)
- **go-apm-metrics** — if ScaleOps Go runtime metrics are confirmed as scraped
- **karpenter-metrics** — ScaleOps interacts with Karpenter for node optimization
- **backing-services-metrics** — if ScaleOps uses Redis/PostgreSQL (not confirmed in this deployment)

---

## Sources

- Deployed helmfile: `k8s-setup/scaleops/helmfile.yaml.gotmpl` — chart `scaleops/scaleops` v1.31.1
- Deployed values: `k8s-setup/scaleops/scaleops/values.yaml.gotmpl`
- Deployed CRDs: `k8s-setup/scaleops/scaleops-raw/values.yaml.gotmpl`
- Red Hat Ecosystem Catalog: ScaleOps Operator Bundle 1.32.6 (confirms Go binary, operator pattern)
- Sacra Research: ScaleOps architecture overview (2025) — "single Helm chart, lightweight control plane, samples usage metrics every few seconds, decisions locally"
- CloudBolt/StormForge comparison (May 2026): "ScaleOps requires in-cluster Prometheus for metrics collection and storage"
- ScaleOps website: https://scaleops.com/ — no public metrics documentation found
- ScaleOps docs portal (https://docs.scaleops.com/) — **gated, not accessible** for metrics verification
