# Metrics Reference

All metrics emitted by AIgent-squad, collected via OTel Collector.

## Auto-instrumented (via otel-helper / OTel SDK)

| Metric | Type | Source | Description |
|--------|------|--------|-------------|
| `http.server.request.duration` | Histogram | FastAPI | Inbound HTTP request latency |
| `http.server.active_requests` | UpDownCounter | FastAPI | Concurrent requests |
| `http.client.request.duration` | Histogram | httpx | Outbound HTTP call latency |

## Custom Business Metrics

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `aigent.requests.total` | Counter | `agent_id` | Total requests processed per agent |
| `aigent.errors.total` | Counter | `agent_id`, `error_type` | Errors per agent |
| `aigent.request.duration` | Histogram | `agent_id` | E2E processing time (ms) |
| `aigent.tokens.total` | Counter | `agent_id`, `direction` (input/output) | Tokens consumed |
| `aigent.cost.estimated` | Counter | `agent_id`, `model` | Estimated cost in USD |
| `aigent.cache.hits` | Counter | `agent_id`, `namespace` | Data cache hits |
| `aigent.cache.misses` | Counter | `agent_id`, `namespace` | Data cache misses |

## Labels (attributes)

| Label | Values | Cardinality |
|-------|--------|-------------|
| `agent_id` | aws, kubernetes, finops, devops, observability, supervisor | 6 (bounded) |
| `error_type` | validation, timeout, bedrock, internal | ~4 (bounded) |
| `direction` | input, output | 2 |
| `model` | claude-sonnet, claude-haiku | ~3 (bounded) |
| `namespace` | aws, k8s, finops, devops, observability | 5 (bounded) |

## Dashboards

Pre-provisioned in Grafana (`:3001`):
- **01-api-business-metrics** — RED metrics, request rates, error rates
- **02-workers-background** — Background processing metrics
- **03-traces-reliability** — Trace-based reliability (p50/p95/p99)

## Accessing

- Grafana: http://localhost:3001
- Prometheus (raw): http://localhost:9099
- Traces (Tempo via Grafana Explore)
