# Metrics reference

All metrics emitted by AIgent-squad use the `aigent.` prefix and are collected via
the OTel SDK → OTel Collector → Prometheus → Grafana pipeline. Auto-instrumented
HTTP metrics follow the OpenTelemetry semantic conventions namespace.

---

## Cardinality rules

Label cardinality is bounded by design. The following labels are **safe** to use:

| Label | Allowed values | Cardinality |
|-------|----------------|-------------|
| `agent_id` | `aws`, `kubernetes`, `finops`, `devops`, `observability`, `supervisor`, `security` | 7 |
| `error_type` | `validation`, `timeout`, `bedrock`, `internal` | 4 |
| `direction` | `input`, `output` | 2 |
| `model` | `sonnet`, `haiku`, `opus`, `titan` | ~4 |
| `namespace` | `aws`, `k8s`, `finops`, `devops`, `observability`, `security` | 6 |
| `confidence` | `alta`, `media`, `baixa` | 3 |
| `from` / `to` | `closed`, `open`, `half_open` | 3 |
| `has_failures` | `True`, `False` | 2 |
| `status` (alerts) | `firing`, `resolved`, `success`, `failed` | 4 |
| `type` (KB) | `troubleshooting`, `decision`, `pattern`, `infrastructure` | 4 |
| `status` (KB) | `active`, `pending_review`, `superseded`, `rejected` | 4 |
| `result` (health) | `ok`, `error` | 2 |

!!! warning "Never use these as labels"
    `user_id`, `session_id`, `trace_id`, raw error messages, and any unbounded
    path or identifier must **never** appear as metric labels. High-cardinality
    data belongs in traces and structured logs, not metrics.

---

## Auto-instrumented (OTel SDK)

These metrics are emitted automatically by the FastAPI / httpx OTel instrumentation
via `otel-helper`. No application code is required.

| Metric | Type | Description |
|--------|------|-------------|
| `http.server.request.duration` | Histogram | Inbound HTTP request latency (FastAPI) |
| `http.server.active_requests` | UpDownCounter | Concurrent inbound requests |
| `http.client.request.duration` | Histogram | Outbound HTTP call latency (httpx) |

---

## RED metrics (per agent)

Core request rate, error rate, and duration counters, labeled per agent so they
can be aggregated across the fleet or drilled into a single specialist.

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `aigent.requests.total` | Counter | `agent_id` | Total requests processed |
| `aigent.errors.total` | Counter | `agent_id`, `error_type` | Errors by category |
| `aigent.request.duration` | Histogram | `agent_id` | End-to-end processing time |

---

## Cost and tokens

These metrics are the primary cost observability signal. Every Bedrock invocation
updates both counters so you can track token spend and estimated USD cost by agent
and by model.

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `aigent.tokens.total` | Counter | `agent_id`, `direction` | Tokens consumed (input and output tracked separately) |
| `aigent.cost.estimated` | Counter | `agent_id`, `model` | Estimated USD cost from Bedrock pricing |

The `direction` label (`input` / `output`) is important: output tokens are
approximately 5x more expensive than input tokens on Claude models.

---

## Cache

Redis data cache hit/miss tracking. LLM responses are **not** cached — only
infrastructure data with deterministic, TTL-bounded values.

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `aigent.cache.hits` | Counter | `agent_id`, `namespace` | Infra data cache hits |
| `aigent.cache.misses` | Counter | `agent_id`, `namespace` | Infra data cache misses |

---

## Resilience (spec 06)

Circuit breaker state machine transitions. The `from` and `to` labels let you
build Prometheus rules that alert on `closed → open` transitions.

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `aigent.circuit_breaker.transitions` | Counter | `name`, `from`, `to` | Circuit breaker state transitions |

---

## Fan-out and synthesis (spec 17)

Emitted when the supervisor routes a query to two or more agents in parallel
(fan-out pattern) and then synthesizes their responses.

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `aigent.fanout.calls` | Counter | — | Fan-out invocations (two or more agents) |
| `aigent.fanout.agents_consulted` | Histogram | — | Agents consulted per fan-out |
| `aigent.fanout.agents_failed` | Counter | — | Agents that returned an error during fan-out |
| `aigent.synthesizer.calls` | Counter | `has_failures` | Synthesizer invocations, split by whether any agent failed |

---

## RCA investigation (spec 18)

Tracks the investigation pipeline triggered by alert webhooks or direct queries.
The `confidence` label on completed investigations is particularly useful for
tracking RCA quality over time.

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `aigent.investigation.started` | Counter | — | Investigations triggered |
| `aigent.investigation.completed` | Counter | `confidence` | Completed investigations, by confidence level |
| `aigent.investigation.duration` | Histogram | `confidence` | End-to-end investigation duration |
| `aigent.investigation.evidence_count` | Histogram | — | Evidence items collected per investigation |

---

## Alert ingestion (spec 18 Phase 2)

Emitted by the Alertmanager webhook handler.

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `aigent.alerts.received` | Counter | `status` (`firing` / `resolved`) | Alerts received via webhook |
| `aigent.alerts.deduplicated` | Counter | — | Alerts skipped due to fingerprint match within TTL window |
| `aigent.alerts.investigation_triggered` | Counter | — | Investigations dispatched from alert payloads |
| `aigent.alerts.postback` | Counter | `status` (`success` / `failed`) | Slack post-back attempts |

---

## Knowledge Base (spec 21)

Tracks the distillation pipeline that converts completed investigations into
reusable KB entries, and the RAG injection path that retrieves them at query time.

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `aigent.kb.distillation.cost` | Counter | — | USD spent in the distillation pipeline |
| `aigent.kb.items_created` | Counter | `type`, `status` | KB items created, by type and initial status |
| `aigent.kb.rag.queries` | Counter | — | RAG similarity queries issued at prompt build time |
| `aigent.kb.rag.hits` | Counter | — | RAG queries that returned at least one similar case |
| `aigent.kb.budget.exhausted` | Counter | — | Distillations skipped because the daily budget was exhausted |

---

## Health and readiness (spec 07)

Emitted by the dependency health checker on every `/healthz` and `/ready` request.

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `aigent.health.check.total` | Counter | `dep`, `result` (`ok` / `error`) | Health checks by dependency and result |
| `aigent.health.check.duration` | Histogram | `dep` | Time to complete each dependency check |

`dep` values match the dependency name (e.g. `redis`, `dynamodb`, `bedrock`).

---

## Dashboards

Pre-provisioned Grafana dashboards (`:3001`):

| Dashboard | Content |
|-----------|---------|
| `01-api-business-metrics` | RED metrics, request rates, error rates by agent |
| `02-workers-background` | Background task processing metrics |
| `03-traces-reliability` | Trace-based reliability (p50 / p95 / p99) |

Future dashboards (not yet provisioned):

| Dashboard | Planned content |
|-----------|-----------------|
| `04-investigation` | RCA pipeline — duration by confidence, evidence count, agents consulted |
| `05-kb-learning` | KB growth, distillation cost trend, RAG hit rate |

---

## Accessing raw data

- **Grafana**: `http://localhost:3001`
- **Prometheus**: `http://localhost:9099`
- **Traces**: Grafana Explore → Tempo data source

---

## Gaps and upcoming metrics

The following metrics are planned but not yet instrumented:

| Metric | Purpose |
|--------|---------|
| `aigent.prompt.size_tokens` | Track prompt size growth over time |
| `aigent.investigation.rounds` | Multi-round RCA round count (Phase 3) |
| `aigent.llm.duration` | LLM-only latency, separate from collection time |
| `aigent.cache.tokens_saved` | Estimated token savings from Bedrock prompt cache |
