# Observability

## Stack

```
App (otel-helper) → OTel Collector → Tempo (traces)
                                   → Prometheus (metrics)
                                   → Grafana (visualization)
App (otel-helper) → /metrics (Prometheus direct scrape, 2026-07-15+)
```

Traces and logs flow exclusively through the **OTel Collector**. Metrics
have two paths, both active by default and BOTH sourced from the same
`MeterProvider` (not a fork/duplicate pipeline): pushed to the collector
(above) AND exposed on each service's own `/metrics` endpoint for direct
Prometheus/VictoriaMetrics scrape — see "Metrics" below.

## Configuration

| Env var | Purpose | Default |
|---------|---------|---------|
| `OTEL_EXPORTER_OTLP_ENDPOINT` | Collector gRPC endpoint | `http://otel-collector:4317` |
| `SERVICE_NAME` | OTel service identity | `aigent-squad` |
| `ENVIRONMENT` | Environment tag | `local` |

## otel-helper integration

`otel-helper` (public repo, `pip install` needs no auth — moved off the old
private `staffops-otel-libs` fork 2026-07-14, pinned to `v0.2.0` since
2026-07-15) configures:
- Traces: `AlwaysOnSampler` (collector decides retention)
- Metrics: FastAPI + httpx auto-instrumentation; v0.2.0+ can run the OTLP
  push exporter and a Prometheus `/metrics` exporter on the same
  `MeterProvider` simultaneously (`OTEL_METRICS_EXPORTER=otlp,prometheus`,
  the default — see "Metrics" below)
- Logs: JSON structured with trace correlation
- Exemplars: enabled (metric → trace linking)

Installed at build time via `git+https` (public repo — no deploy key/SSH
needed anymore). **The Dockerfile still mounts a `github_token` build
secret for this install step** — verified `otel-helper` is the only `git+`
line in `requirements.txt`, so that secret is now vestigial (it was
required back when this package lived in a private repo). Not removed here
— tracked as a cleanup item, not this doc's job to silently drop a working
build step.

## Traces

Distributed traces span the full request lifecycle:

```
supervisor.query
├── classifier.classify
├── agent.{id}.process (per agent, parallel in fan-out)
│   ├── datasource.{type}.fetch
│   └── bedrock.invoke
├── synthesizer.synthesize (fan-out/RCA)
└── kb.distill (async, fire-and-forget)
```

Access traces in Grafana Explore → Tempo datasource.

## Metrics

Custom business metrics documented in [METRICS.md](METRICS.md).

Auto-instrumented HTTP metrics (FastAPI + httpx) are available without custom code.

## Log format

JSON structured logs with automatic trace correlation:

```json
{
  "timestamp": "2026-06-14T10:00:00Z",
  "level": "INFO",
  "message": "Query routed",
  "agent_id": "aws",
  "trace_id": "abc123...",
  "span_id": "def456...",
  "service": "aigent-squad",
  "environment": "local"
}
```

`trace_id` and `span_id` are injected automatically by otel-helper when an active span exists. Use these to jump from log → trace in Grafana.

## Local access

| Tool | URL |
|------|-----|
| Grafana | http://localhost:3001 |
| Prometheus | http://localhost:9099 |
| Traces | Grafana → Explore → Tempo |

## Dashboards

Pre-provisioned (via `infra/observability/grafana/provisioning/`):
- **01-api-business-metrics** — RED metrics, tokens, costs
- **02-workers-background** — Background task metrics
- **03-traces-reliability** — p50/p95/p99 latency from traces

## Production

In production, replace the local OTel Collector with the cluster's shared collector. Same env vars, different endpoint value. Traces go to Tempo, metrics to VictoriaMetrics (Prometheus-compatible).
