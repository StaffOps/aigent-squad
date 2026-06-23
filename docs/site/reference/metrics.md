# Metrics reference

All metrics emitted by AIgent-squad use the `aigent.` prefix and are collected via
the OTel SDK → OTel Collector → Prometheus → Grafana pipeline. Auto-instrumented
HTTP metrics follow the OpenTelemetry semantic conventions namespace.

---

## Cardinality rules

Label cardinality is bounded by design. The following labels are **safe** to use:

| Label | Allowed values | Cardinality |
|-------|----------------|-------------|
| `agent_id` | `aws`, `kubernetes`, `finops`, `devops`, `observability`, `supervisor`, `security`, `classifier`, `synthesizer`, `unknown` | ~10 |
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
| `aigent.tokens.total` | Counter | `agent_id`, `model`, `direction` | Tokens consumed (input and output tracked separately) |
| `aigent.cost.estimated` | Counter | `agent_id`, `model` | Estimated USD cost from Bedrock pricing |

The `direction` label (`input` / `output`) is important: output tokens are
approximately 5x more expensive than input tokens on Claude models.

---

## Efficiency — where time and tokens go (spec 10)

These split a request's latency into data-collection vs LLM time and expose the
prompt-size distribution, so cost regressions can be attributed (the two halves
have different fixes: cache/truncate vs model tiering).

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `aigent.collect.duration` | Histogram | `agent_id` | Datasource collection latency (adapter fan-out, ms) |
| `aigent.llm.duration` | Histogram | `agent_id` | Bedrock round-trip latency per call (ms, excludes retry backoff) |
| `aigent.prompt.size_tokens` | Histogram | `agent_id` | Bedrock-reported input tokens per call — distribution to detect prompt bloat (p50/p95) |

`aigent.prompt.size_tokens` is a **histogram** of the same input tokens that
`aigent.tokens.total` sums as a counter: the histogram exposes the per-call
*distribution* (catch bloat), the counter exposes total *spend*.

---

## Cache

!!! warning "Defined but not emitted yet"
    The cache metrics below exist in `metrics.py` but are **not wired into any
    code path** — the `CacheStore` is not used by the datasource adapters
    (`Boto3Adapter`, `HttpAdapter`, …), which fetch fresh on every call. Do not
    build alerts on them; they read as permanently zero until the
    datasource-cache layer ships (future spec). See "Gaps and upcoming metrics".

| Metric | Type | Labels | State |
|--------|------|--------|-------|
| `aigent.cache.hits` | Counter | `agent_id`, `namespace` | Defined, never emitted |
| `aigent.cache.misses` | Counter | `agent_id`, `namespace` | Defined, never emitted |

---

## Resilience (spec 06)

Circuit breaker state machine transitions. The `from` and `to` labels let you
build Prometheus rules that alert on `closed → open` transitions.

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `aigent.circuit_breaker.transitions` | Counter | `name`, `from`, `to` | Circuit breaker state transitions |

---

## Edge gateway and admission (spec 31)

Emitted by the edge gateway. The worker-pool metrics reflect the **per-replica**
local concurrency cap; the admission metric reflects the **global** Redis-backed
rate/budget guards (see Architecture → Concurrency model).

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `aigent.gateway.pool_rejections` | Counter | — | Requests rejected with `503` because the worker pool was at capacity (backpressure) |
| `aigent.gateway.pool_depth` | UpDownCounter | — | In-flight jobs currently held by the pool |
| `aigent.gateway.queue_wait` | Histogram (ms) | — | Time a job waited to acquire a pool slot |
| `aigent.gateway.redis_fallback_active` | Counter | — | Times job lifecycle fell back to log-only (Redis unavailable) |
| `aigent.rate_limit.blocks` | Counter | `reason` (`user`/`global`) | Requests blocked by the admission guards (per-user rate or global budget) |

Useful signals: a rising `pool_rejections` with low `pool_depth` variance means
the cap is too low for the replica count; `queue_wait` p99 climbing toward the
job timeout indicates saturation; `rate_limit.blocks{reason="global"}` firing
means the daily budget is exhausted.

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
| `aigent.investigation.rounds` | Histogram | — | Rounds completed per investigation (vs cost cap; 1 today, single-round) |

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

`aigent.collect.duration`, `aigent.llm.duration`, `aigent.prompt.size_tokens`,
and `aigent.investigation.rounds` shipped in spec 10 (see Efficiency / RCA
sections above). Still planned:

| Metric | State | Purpose |
|--------|-------|---------|
| `aigent.cache.hits` / `aigent.cache.misses` | Defined, not emitted | Need datasource cache wired into the adapter layer (future spec) |
| `aigent.cache.tokens_saved` | Not defined | Estimated token savings once the datasource cache exists (future spec) |
