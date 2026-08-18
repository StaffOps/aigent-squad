---
name: aws-load-balancer-controller-metrics
description: "Use when diagnosing AWS Load Balancer Controller health \u2014 reconciliation failures, AWS API throttling/permission errors, webhook failures, workqueue saturation, readiness gate latency. Covers awslbc_*, aws_api_*, api_call_*, controller_runtime_reconcile_*, workqueue_*, rest_client_requests_total. Grounded on Helm chart eks/aws-load-balancer-controller 3.4.0 (appVersion v3.4.0), official docs v2.13 metrics reference."
keywords: [aws-load-balancer-controller, aws, load, balancer, controller, "aws load", "load balancer", "balancer controller", eks]
---
# AWS Load Balancer Controller Metrics

Prometheus metrics for the **AWS Load Balancer Controller** — the operator that reconciles Ingress, Service (type LoadBalancer), TargetGroupBinding, and Gateway API resources into AWS ALB/NLB infrastructure.

**Question answered**: "Is the LB controller healthy? Are reconciliations succeeding? Is AWS API throttling or permissions blocking resource provisioning?"

---

## Scrape Pipeline

```
aws-load-balancer-controller pod (:8080/metrics)
    → ServiceMonitor (kube-system, enabled in Helm values)
        → vmagent scrape
            → VictoriaMetrics
```

**How enabled**: `serviceMonitor.enabled: true` in Helm values. The controller exposes metrics on port 8080 (default `--metrics-bind-addr=:8080`).

**Deployment**: 2 replicas with HPA (2–5), `priorityClassName: system-cluster-critical`, namespace `kube-system`.

**Chart**: `eks/aws-load-balancer-controller` version **3.4.0** (appVersion **v3.4.0**).

---

## 1. Custom Controller Metrics (`awslbc_*`)

These are specific to the AWS Load Balancer Controller, providing fine-grained reconciliation and webhook visibility.

| Metric Name | Type | What It Measures | Troubleshooting Use | Key Labels |
|---|---|---|---|---|
| `awslbc_reconcile_stage_duration` | Histogram | Latency of different reconcile stages (DNS_resolve, AWS API, target registration, etc.) | Identify WHICH stage of reconciliation is slow | `controller`, `reconcile_stage` |
| `awslbc_reconcile_errors_total` | Counter | Number of controller errors by error type | Non-zero = reconciliation failing; inspect `error_type` to understand class of failure | `controller`, `error_type` |
| `awslbc_readiness_gate_ready_seconds` | Histogram | Time to flip a pod readiness gate to true after target registration | Slow gate flip = slow deployments; affects rolling updates | `le` |
| `awslbc_webhook_validation_failures_total` | Counter | Number of validation webhook failures by type | Non-zero = resources being rejected by admission webhook | `webhook_type` |
| `awslbc_webhook_mutation_failures_total` | Counter | Number of mutation webhook failures by type | Non-zero = resources failing mutation (potentially breaking ingress creation) | `webhook_type` |
| `awslbc_top_talkers` | Gauge | Number of reconciliations by resource | Identifies noisy resources triggering excessive reconciliation | `resource` |

---

## 2. AWS SDK Metrics (`aws_api_*`, `aws_request_*`)

These track the controller's interaction with AWS APIs (ELBv2, EC2, ACM, WAFv2, Shield).

| Metric Name | Type | What It Measures | Troubleshooting Use | Key Labels |
|---|---|---|---|---|
| `aws_api_calls_total` | Counter | Total number of SDK API calls to AWS services | Baseline API call rate; sudden spike = reconciliation loop or scaling event | `service`, `operation` |
| `aws_api_call_duration_seconds` | Histogram | Perceived latency of SDK calls (includes retries) | Slow AWS API = slow reconciliation; check p99 by service/operation | `service`, `operation`, `le` |
| `aws_api_call_retries_total` | Counter | Number of times the SDK retried requests | High retry rate = transient AWS failures or throttling | `service`, `operation` |
| `aws_api_requests_total` | Counter | Total number of individual HTTP requests to AWS (each retry = 1 request) | Compare with `aws_api_calls_total` to compute retry ratio | `service`, `operation` |
| `aws_request_duration_seconds` | Histogram | Latency of an individual HTTP request to the service endpoint | Per-request latency (lower level than `aws_api_call_duration_seconds`) | `service`, `operation`, `le` |

---

## 3. AWS API Error Metrics (`api_call_*`)

Specific error counters for categorizing AWS API failures.

| Metric Name | Type | What It Measures | Troubleshooting Use | Key Labels |
|---|---|---|---|---|
| `api_call_permission_errors_total` | Counter | Failed AWS API calls due to auth/authorization failures | **CRITICAL** — IRSA misconfiguration or role policy too restrictive | `service`, `operation` |
| `api_call_service_limit_exceeded_errors_total` | Counter | Failed calls due to exceeding AWS service limits | Need to request limit increase or reduce resource count | `service`, `operation` |
| `api_call_throttled_errors_total` | Counter | Failed calls due to throttling | Rate limiting by AWS — back off, or distribute calls across accounts/regions | `service`, `operation` |
| `api_call_validation_errors_total` | Counter | Failed calls due to validation errors | Bug in resource spec translation — check Ingress/Service annotations | `service`, `operation` |

---

## 4. Controller-Runtime Metrics

Standard metrics from the `controller-runtime` framework (shared with all kubebuilder-based controllers).

### Reconciliation

| Metric Name | Type | What It Measures | Troubleshooting Use | Key Labels |
|---|---|---|---|---|
| `controller_runtime_reconcile_total` | Counter | Total reconciliations per controller | Baseline reconciliation rate; compare `result=error` vs `result=success` | `controller`, `result` |
| `controller_runtime_reconcile_errors_total` | Counter | Total reconciliation errors per controller | Non-zero sustained = controller stuck on a resource | `controller` |
| `controller_runtime_reconcile_time_seconds` | Histogram | Duration of reconciliation per controller | p99 > 30s = slow reconcile (likely waiting on AWS API) | `controller`, `le` |
| `controller_runtime_max_concurrent_reconciles` | Gauge | Maximum concurrent reconciles configured per controller | Capacity ceiling — compare with `controller_runtime_active_workers` | `controller` |
| `controller_runtime_active_workers` | Gauge | Number of currently active reconcile workers | Saturation signal: active == max → queue will grow | `controller` |

### Webhook

| Metric Name | Type | What It Measures | Troubleshooting Use | Key Labels |
|---|---|---|---|---|
| `controller_runtime_webhook_requests_total` | Counter | Total admission webhook requests received | Spike = many resources being created/updated; correlate with latency | `webhook`, `code` |
| `controller_runtime_webhook_requests_in_flight` | Gauge | Current number of admission requests being served | Saturation signal for webhook capacity | `webhook` |
| `controller_runtime_webhook_latency_seconds` | Histogram | Latency of processing admission requests | High latency = blocking resource creation in the cluster | `webhook`, `le` |

---

## 5. Workqueue Metrics (`workqueue_*`)

Per-controller workqueue metrics (controllers: `ingress`, `service`, `targetGroupBinding`).

| Metric Name | Type | What It Measures | Troubleshooting Use | Key Labels |
|---|---|---|---|---|
| `workqueue_depth` | Gauge | Current number of items in queue | Growing depth = controller can't keep up; resources waiting to reconcile | `name` (controller name) |
| `workqueue_adds_total` | Counter | Total items added to queue | Spike = external change wave (deploy, annotation change, node scaling) | `name` |
| `workqueue_queue_duration_seconds` | Histogram | Time items spend waiting in queue before being processed | High p99 = reconciliation backlog | `name`, `le` |
| `workqueue_work_duration_seconds` | Histogram | Time spent processing each item | High p99 = slow reconcile logic (likely AWS API) | `name`, `le` |
| `workqueue_unfinished_work_seconds` | Gauge | Seconds of work in progress that hasn't been observed by `work_duration` | Large values = stuck reconcile threads | `name` |
| `workqueue_longest_running_processor_seconds` | Gauge | How long the longest running processor has been running | Stuck processor = specific resource causing hang | `name` |
| `workqueue_retries_total` | Counter | Total retries handled by workqueue | High rate = recurring failures being re-enqueued | `name` |

---

## 6. Kubernetes API Client Metrics

| Metric Name | Type | What It Measures | Troubleshooting Use | Key Labels |
|---|---|---|---|---|
| `rest_client_requests_total` | Counter | HTTP requests to the Kubernetes API server | Spike = excessive watches/lists; high 4xx/5xx = API server issues | `code`, `method`, `host` |
| `rest_client_request_duration_seconds` | Histogram | Latency of requests to the K8s API server | High latency = apiserver saturation or network issue | `verb`, `url`, `le` |

---

## 7. Go Runtime Metrics (`go_*`)

Standard `client_golang` Go runtime metrics. See **`go-apm-metrics`** skill for full reference. Key ones for the LB controller:

| Metric Name | Type | Quick Use |
|---|---|---|
| `go_goroutines` | Gauge | Goroutine leak detection — monotonic rise indicates reconciler leak |
| `go_memstats_heap_alloc_bytes` | Gauge | Memory pressure — compare with container limit |
| `go_gc_duration_seconds` | Summary | GC pause impact on reconciliation latency |
| `process_resident_memory_bytes` | Gauge | Actual RSS — approaching container limit = OOMKill risk |

---

## Troubleshooting Quick-Reference

| Symptom | First Metrics to Check | MetricsQL Query |
|---|---|---|
| **Ingress not getting ALB** | `controller_runtime_reconcile_total{controller="ingress",result="error"}`, `api_call_permission_errors_total` | `rate(controller_runtime_reconcile_total{controller="ingress",result="error"}[5m]) > 0` |
| **AWS API throttled** | `api_call_throttled_errors_total` | `rate(api_call_throttled_errors_total[5m]) > 0` |
| **Permission denied from AWS** | `api_call_permission_errors_total` | `sum(rate(api_call_permission_errors_total[5m])) by (service, operation) > 0` |
| **Slow reconciliation** | `awslbc_reconcile_stage_duration`, `aws_api_call_duration_seconds` p99 | `histogram_quantile(0.99, rate(awslbc_reconcile_stage_duration[5m]))` |
| **Readiness gates slow** | `awslbc_readiness_gate_ready_seconds` p99 | `histogram_quantile(0.99, rate(awslbc_readiness_gate_ready_seconds_bucket[5m]))` |
| **Workqueue backing up** | `workqueue_depth{name="ingress"}`, `workqueue_queue_duration_seconds` | `workqueue_depth{name=~"ingress\|service\|targetGroupBinding"} > 10` |
| **Webhook blocking creates** | `controller_runtime_webhook_latency_seconds` p99, `awslbc_webhook_validation_failures_total` | `histogram_quantile(0.99, rate(controller_runtime_webhook_latency_seconds_bucket[5m])) > 1` |
| **Controller stuck** | `workqueue_longest_running_processor_seconds`, `workqueue_unfinished_work_seconds` | `workqueue_longest_running_processor_seconds > 300` |
| **Service limit hit** | `api_call_service_limit_exceeded_errors_total` | `rate(api_call_service_limit_exceeded_errors_total[5m]) > 0` |
| **Noisy resource reconciling** | `awslbc_top_talkers` | `topk(5, awslbc_top_talkers)` |
| **High retry to AWS** | retry ratio = `aws_api_requests_total` / `aws_api_calls_total` | `rate(aws_api_requests_total[5m]) / rate(aws_api_calls_total[5m]) > 1.5` |
| **K8s API server issues** | `rest_client_requests_total{code=~"5.."}` | `rate(rest_client_requests_total{code=~"5.."}[5m]) > 0` |

---

## Key Relationships

```
Ingress/Service/TGB resource created/updated
    → workqueue_adds_total ↑
        → workqueue_depth ↑ (if processing is slow)
            → controller_runtime_reconcile_total ↑
                → aws_api_calls_total ↑ (ELBv2 CreateTargetGroup, ModifyListener, etc.)
                    ├─ Success → LB provisioned
                    ├─ api_call_throttled_errors_total ↑ → retry → aws_api_call_retries_total ↑
                    ├─ api_call_permission_errors_total ↑ → reconcile error → stuck
                    └─ api_call_service_limit_exceeded_errors_total ↑ → quota exhausted

Pod readiness gate:
    target registered in TG
        → awslbc_readiness_gate_ready_seconds measures time until healthy
            → slow = rolling update blocked
```

---

## Controller Names (for label filtering)

The `controller` label on `controller_runtime_*` and `workqueue_*` metrics uses these values:

| Controller Name | Manages |
|---|---|
| `ingress` | Ingress resources → ALB |
| `service` | Service type LoadBalancer → NLB |
| `targetGroupBinding` | TargetGroupBinding CRD → Target Group membership |
| `gateway` | Gateway API resources (when `ALBGatewayAPI` feature gate enabled) |
| `gatewayClass` | GatewayClass lifecycle |

---

## Complements

- **`go-apm-metrics`** — full Go runtime metrics reference (goroutines, GC, scheduler, memory)
- **`k8s-workload-metrics`** — container CPU/memory/restart metrics for the controller pods
- **`karpenter-metrics`** — node provisioning that affects LB target registration
- **`istio-ambient-metrics`** — mesh-level metrics if LB routes to mesh services
- **`cert-manager-metrics`** — certificate lifecycle for ALB HTTPS listeners

---

## Sources

- [Official metrics documentation (v2.13)](https://kubernetes-sigs.github.io/aws-load-balancer-controller/v2.13/guide/metrics/prometheus/) — custom `awslbc_*` and AWS SDK metrics table
- [controller-runtime metrics reference (kubebuilder)](https://www.kubebuilder.io/reference/metrics-reference) — `controller_runtime_*` and `workqueue_*` standard metrics
- [Helm chart eks/aws-load-balancer-controller v3.4.0](https://github.com/aws/eks-charts/tree/master/stable/aws-load-balancer-controller) — ServiceMonitor configuration
- [Deployed helmfile](k8s-setup/aws-load-balancer-controller/helmfile.yaml.gotmpl) — chart version 3.4.0, `serviceMonitor.enabled: true`
