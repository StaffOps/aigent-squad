---
name: defectdojo-metrics
description: "Use when assessing DefectDojo observability. The deployed chart (defectdojo/defectdojo 1.6.193) enables an nginx-prometheus-exporter sidecar exposing nginx_connections_*, nginx_http_requests_total, nginx_up. Django application metrics (django_http_*, django_db_*, celery_*) are NOT available — the chart does not integrate django-prometheus. For workload health use k8s-workload-metrics; for backing Postgres use backing-services-metrics."
keywords: [defectdojo, postgres]
---
# DefectDojo Metrics — Honest Assessment

**Grounded on**: Helm chart `defectdojo/defectdojo` version **1.6.193**, deployed to `core-devops` cluster, namespace `defectdojo`.

---

## Key Finding: Limited First-Class Metrics

The DefectDojo Helm chart's `monitoring.prometheus.enabled: true` deploys an **nginx-prometheus-exporter** sidecar (image `nginx/nginx-prometheus-exporter:1.5.1`) that scrapes NGINX stub_status. This provides basic reverse-proxy level metrics only.

**What IS exposed** (nginx sidecar):
- `nginx_connections_*` — connection state counters
- `nginx_http_requests_total` — total HTTP requests served by the NGINX frontend
- `nginx_up` — NGINX process health

**What is NOT exposed** (no integration exists in this chart):
- ❌ `django_http_requests_*` / `django_http_responses_*` — requires `django-prometheus` library
- ❌ `django_db_*` — Django DB connection pool metrics
- ❌ `celery_*` — Celery task execution metrics (requires `celery-exporter` or `django-prometheus`)
- ❌ Any DefectDojo-specific business metrics (findings, imports, scans)

> **Context**: GitHub Issue [#2464](https://github.com/DefectDojo/django-DefectDojo/issues/2464) (opened 2020, still unresolved) requests native Prometheus/OpenMetrics endpoint support. As of chart 1.6.193, DefectDojo does NOT ship with `django-prometheus` as a dependency.

---

## Scrape Pipeline

```
DefectDojo pod:
  ├── nginx container (stub_status on :8080/stub_status)
  │       └── nginx-prometheus-exporter sidecar (:9113/metrics)
  ├── uwsgi container (Django app — NO metrics endpoint)
  └── celery worker/beat pods (NO metrics endpoint)

nginx-prometheus-exporter :9113/metrics → vmagent scrape → VictoriaMetrics
```

### How Metrics Are Enabled

In deployed values (`defectdojo/defectdojo/values.yaml.gotmpl`):
```yaml
monitoring:
  enabled: true
  prometheus:
    enabled: true  # deploys nginx-prometheus-exporter sidecar
```

This adds the `nginx/nginx-prometheus-exporter:1.5.1` sidecar container to the Django deployment pod. It scrapes the NGINX `stub_status` module internally and exposes Prometheus metrics on port 9113.

**No ServiceMonitor/PodMonitor is created by the chart** — scraping relies on vmagent annotation-based discovery or manual VMServiceScrape.

---

## NGINX Proxy Metrics (via nginx-prometheus-exporter sidecar)

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `nginx_connections_accepted` | Counter | Total accepted client connections | Baseline traffic volume; drop = upstream routing issue | — |
| `nginx_connections_active` | Gauge | Current active connections | Saturation signal; high = uwsgi backend slow or overwhelmed | — |
| `nginx_connections_handled` | Counter | Total handled connections (should equal accepted) | `accepted - handled` > 0 = connections dropped (resource exhaustion) | — |
| `nginx_connections_reading` | Gauge | Connections reading request header | High = slow clients or large headers | — |
| `nginx_connections_waiting` | Gauge | Idle keepalive connections | Normal to be non-zero; high = many idle clients | — |
| `nginx_connections_writing` | Gauge | Connections writing response back to client | High = slow responses from uwsgi or large payloads | — |
| `nginx_http_requests_total` | Counter | Total HTTP requests processed | Request rate; correlate drops with availability issues | — |
| `nginx_up` | Gauge | Whether NGINX is responding to stub_status | 0 = NGINX process down, pod may still be Running | — |
| `nginxexporter_build_info` | Gauge | Exporter version metadata | Version tracking | `version`, `commit`, `date` |

---

## Troubleshooting Quick-Reference

| Symptom | What to Check | Why |
|---------|---------------|-----|
| DefectDojo UI unresponsive | `nginx_up == 0` or `nginx_connections_active` spike | NGINX down or uwsgi backend saturated |
| Slow page loads | `nginx_connections_writing` high + `nginx_connections_active` near limit | uwsgi workers exhausted, queuing requests |
| Connection drops | `rate(nginx_connections_accepted) - rate(nginx_connections_handled) > 0` | NGINX dropping connections (worker_connections limit hit) |
| Intermittent 502 errors | `nginx_connections_active` spike correlating with uwsgi restart | uwsgi worker recycling or OOM |
| No metrics at all | Check if exporter sidecar is running: `kubectl get pod -n defectdojo -o jsonpath='{.spec.containers[*].name}'` | Sidecar may not be injected if `monitoring.prometheus.enabled: false` |

### What You CAN'T Diagnose with These Metrics

| Symptom | Why NGINX Metrics Won't Help | Use Instead |
|---------|------------------------------|-------------|
| Slow DB queries | No Django/ORM metrics exposed | `backing-services-metrics` (PostgreSQL) |
| Celery task failures | No Celery exporter deployed | Pod logs: `kubectl logs -n defectdojo -l defectdojo.org/component=celery` |
| Import/scan hanging | No application-level metrics | DefectDojo API + pod logs |
| Memory leak in uwsgi | No process metrics from uwsgi | `k8s-workload-metrics` (container_memory_*) |
| High error rate by endpoint | NGINX exporter only has total count, no per-path | Access logs (if sent to Loki) |

---

## Recommended Observability Strategy (Given Limitations)

Since DefectDojo lacks application-level Prometheus metrics, rely on:

1. **k8s-workload-metrics** — container CPU/memory/restarts for uwsgi, celery-worker, celery-beat pods
2. **backing-services-metrics** — PostgreSQL (RDS) connection count, query duration, replication lag
3. **NGINX exporter metrics** (this skill) — proxy-level request rate and connection state
4. **Loki logs** — Django/uwsgi access+error logs, Celery task logs
5. **Synthetic monitoring** — external health check hitting DefectDojo `/api/v2/user_contact_infos/` (authenticated) or login page

### Pod Labels for Log Queries (Loki)

```logql
# Django/NGINX pod logs
{namespace="defectdojo", app="defectdojo"}

# Celery worker logs
{namespace="defectdojo", container="celery"}

# Celery beat logs
{namespace="defectdojo", container="celery-beat"}
```

---

## Complements

- **k8s-workload-metrics** — container-level resource metrics (CPU, memory, restarts) for all DefectDojo pods
- **backing-services-metrics** — PostgreSQL health (the primary backing store for DefectDojo)
- **ingress-nginx-metrics** / **traefik-metrics** — upstream ingress metrics (DefectDojo uses Traefik internal ingress)

---

## Sources

- Helm chart: `defectdojo/defectdojo` version `1.6.193` (from `https://raw.githubusercontent.com/DefectDojo/django-DefectDojo/helm-charts`)
- Chart values: `monitoring.prometheus.enabled` deploys `nginx/nginx-prometheus-exporter:1.5.1`
- nginx-prometheus-exporter docs: https://github.com/nginx/nginx-prometheus-exporter
- DefectDojo Prometheus metrics feature request (open/stale): https://github.com/DefectDojo/django-DefectDojo/issues/2464
- Deployed config: `/02-KUBE/00-CONFIG/k8s-setup/defectdojo/defectdojo/values.yaml.gotmpl`
