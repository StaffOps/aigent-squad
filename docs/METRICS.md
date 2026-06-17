# Metrics Reference

All metrics emitted by AIgent-squad, collected via OTel Collector → Prometheus → Grafana.

## Auto-instrumented (via otel-helper / OTel SDK)

| Metric | Type | Source | Description |
|--------|------|--------|-------------|
| `http.server.request.duration` | Histogram | FastAPI | Inbound HTTP request latency |
| `http.server.active_requests` | UpDownCounter | FastAPI | Concurrent requests |
| `http.client.request.duration` | Histogram | httpx | Outbound HTTP call latency |

## RED metrics (per agent)

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `aigent.requests.total` | Counter | `agent_id` | Total requests processed |
| `aigent.errors.total` | Counter | `agent_id`, `error_type` | Errors |
| `aigent.request.duration` | Histogram | `agent_id` | E2E processing time (ms) |

## Cost / tokens

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `aigent.tokens.total` | Counter | `agent_id`, `direction` | Tokens consumed |
| `aigent.cost.estimated` | Counter | `agent_id`, `model` | Estimated USD cost |

## Cache

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `aigent.cache.hits` | Counter | `agent_id`, `namespace` | Data cache hits |
| `aigent.cache.misses` | Counter | `agent_id`, `namespace` | Data cache misses |

## Resilience (spec 06)

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `aigent.circuit_breaker.transitions` | Counter | `name`, `from`, `to` | Circuit breaker state transitions |

## Fan-out / Synthesis (spec 17)

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `aigent.fanout.calls` | Counter | — | Fan-out invocations (N≥2 agents) |
| `aigent.fanout.agents_consulted` | Histogram | — | Agents per fan-out |
| `aigent.fanout.agents_failed` | Counter | — | Failed agents during fan-out |
| `aigent.synthesizer.calls` | Counter | `has_failures` | Synthesizer invocations |

## RCA Investigation (spec 18)

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `aigent.investigation.started` | Counter | — | Investigations triggered |
| `aigent.investigation.completed` | Counter | `confidence` | Completed by confidence (alta/media/baixa) |
| `aigent.investigation.duration` | Histogram | `confidence` | E2E investigation duration (ms) |
| `aigent.investigation.evidence_count` | Histogram | — | Evidence items per investigation |

## Knowledge Base (spec 21)

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `aigent.kb.distillation.cost` | Counter | — | USD spent in distillation pipeline |
| `aigent.kb.items_created` | Counter | `type`, `status` | KB items created |
| `aigent.kb.rag.queries` | Counter | — | RAG injection queries |
| `aigent.kb.rag.hits` | Counter | — | Queries that returned similar cases |
| `aigent.kb.budget.exhausted` | Counter | — | Distillations skipped due to budget |

## Alert Ingestion (spec 18 Phase 2)

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `aigent.alerts.received` | Counter | `status` | Alertmanager alerts received via webhook |
| `aigent.alerts.deduplicated` | Counter | — | Skipped due to fingerprint match |
| `aigent.alerts.investigation_triggered` | Counter | — | Investigations triggered from alerts |
| `aigent.alerts.postback` | Counter | `status` | Slack post-back attempts |

## Labels (attributes)

| Label | Values | Cardinality |
|-------|--------|-------------|
| `agent_id` | aws, kubernetes, finops, devops, observability, supervisor, security | 7 |
| `error_type` | validation, timeout, bedrock, internal | 4 |
| `direction` | input, output | 2 |
| `model` | sonnet, haiku, opus, titan | ~4 |
| `namespace` | aws, k8s, finops, devops, observability, security | 6 |
| `confidence` | alta, media, baixa | 3 |
| `from` / `to` (circuit breaker) | closed, open, half_open | 3 |
| `type` (kb) | troubleshooting, decision, pattern, infrastructure | 4 |
| `status` (kb) | active, pending_review, superseded, rejected | 4 |
| `has_failures` (synthesizer) | True, False | 2 |

## Dashboards

Pre-provisioned in Grafana (`:3001`):
- **01-api-business-metrics** — RED metrics, request rates, error rates
- **02-workers-background** — Background processing metrics
- **03-traces-reliability** — Trace-based reliability (p50/p95/p99)

Future dashboards (TODO):
- **04-investigation** — RCA pipeline (duration by confidence, evidence count, agents consulted)
- **05-kb-learning** — KB growth, distillation cost trend, RAG hit rate

## Accessing

- Grafana: http://localhost:3001
- Prometheus (raw): http://localhost:9099
- Traces (Tempo via Grafana Explore)
