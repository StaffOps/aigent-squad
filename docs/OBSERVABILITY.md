# Observability

## Stack

```
App (otel-helper) → OTel Collector → Tempo (traces)
                                   → Prometheus (metrics)
                                   → Grafana (visualization)
```

All telemetry flows through the **OTel Collector**. No direct backend exports.

## Configuration

| Env var | Purpose | Default |
|---------|---------|---------|
| `OTEL_EXPORTER_OTLP_ENDPOINT` | Collector gRPC endpoint | `http://otel-collector:4317` |
| `SERVICE_NAME` | OTel service identity | `aigent-squad` |
| `ENVIRONMENT` | Environment tag | `local` |

## otel-helper integration

The private `otel-helper` library configures:
- Traces: `AlwaysOnSampler` (collector decides retention)
- Metrics: FastAPI + httpx auto-instrumentation
- Logs: JSON structured with trace correlation
- Exemplars: enabled (metric → trace linking)

Installed at build time via `git+ssh` (requires ssh-agent with GitHub key).

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
