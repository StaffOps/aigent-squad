# Metrics Reference

All metrics emitted by AIgent-squad, collected via OTel Collector → Prometheus → Grafana.

## Auto-instrumented (via otel-helper / OTel SDK)

| Metric | Type | Source | Description |
|--------|------|--------|-------------|
| `http.server.request.duration` | Histogram | FastAPI | Inbound HTTP request latency |
| `http.server.active_requests` | UpDownCounter | FastAPI | Concurrent requests |
| `http.client.request.duration` | Histogram | httpx | Outbound HTTP call latency |

> Metrics below are grouped by **purpose**: RED (is it working?), Efficiency
> (what does it cost?), Quality (is the answer good?), and Domain (feature-specific).

## RED — is it working? (per agent)

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `aigent.requests.total` | Counter | `agent_id` | Total requests processed |
| `aigent.errors.total` | Counter | `agent_id`, `error_type` | Errors |
| `aigent.request.duration` | Histogram | `agent_id` | E2E processing time (ms) |

## Efficiency — what does it cost? (specs 27, 10)

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `aigent.tokens.total` | Counter | `agent_id`, `model`, `direction` | Tokens consumed (running total) |
| `aigent.cost.estimated` | Counter | `agent_id`, `model` | Estimated USD cost |
| `aigent.collect.duration` | Histogram | `agent_id` | Datasource collection latency (adapter fan-out, ms) |
| `aigent.llm.duration` | Histogram | `agent_id` | Bedrock round-trip latency (ms, excludes retry backoff) |
| `aigent.prompt.size_tokens` | Histogram | `agent_id` | Input-token distribution per call (detect prompt bloat; p50/p95) |
| `aigent.cache.hits` | Counter | `namespace` | Datasource cache hits (avoided re-fetch) — spec 30 |
| `aigent.cache.misses` | Counter | `namespace` | Datasource cache misses (fresh fetch) — spec 30 |

The datasource cache (spec 30) wraps each adapter's `collect()` with a
deterministic `sha256` key + per-agent TTL (fail-open). `namespace` is the
agent's cache namespace (defaults to the agent name). Hit ratio =
`hits / (hits + misses)` measures cache effectiveness per agent.

`aigent.collect.duration` + `aigent.llm.duration` split request latency into
data-collection vs LLM time — the two have different fixes (cache/truncate vs
model tiering). `aigent.prompt.size_tokens` is a **histogram** of the same input
tokens `aigent.tokens.total` sums, but exposes the *distribution* to catch
context bloat (efficiency-cost steering).

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

## Quality — RCA Investigation (specs 18, 10)

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `aigent.investigation.started` | Counter | — | Investigations triggered |
| `aigent.investigation.completed` | Counter | `confidence` | Completed by confidence (alta/media/baixa) |
| `aigent.investigation.duration` | Histogram | `confidence` | E2E investigation duration (ms) |
| `aigent.investigation.evidence_count` | Histogram | — | Evidence items per investigation |
| `aigent.investigation.rounds` | Histogram | — | Rounds completed per investigation (vs cost cap; 1 today, single-round) |

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

## Known gaps (not usable yet)

Do not build alerts on these — they do not exist yet.

| Metric | State | Why | Tracked in |
|--------|-------|-----|------------|
| `aigent.cache.tokens_saved` | **Not defined** | Belongs to Bedrock **prompt** caching, not the datasource cache: a datasource hit avoids an API call, not LLM tokens (infra data still enters the prompt). | spec 11 (bedrock-resilience-cost) |

> `aigent.cache.hits` / `aigent.cache.misses` are now emitted by the datasource
> cache (spec 30) — see the Efficiency section.

## Labels (attributes)

| Label | Values | Cardinality |
|-------|--------|-------------|
| `agent_id` | aws, kubernetes, finops, devops, observability, supervisor, security, classifier, synthesizer, unknown | ~10 |
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
