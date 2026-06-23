# Requirements: Metrics & Cost Observability (efficiency + quality)

## Context

RED metrics (`aigent.requests.*`, `aigent.errors.*`, `aigent.request.duration`) and
cost attribution (`aigent.tokens.total`, `aigent.cost.estimated` labeled by
`agent_id` + `model`) already exist (specs 03, 27). What is missing is the
**efficiency** and **quality** dimension demanded by the `efficiency-cost`
steering: we cannot see *where* time and tokens go inside a request, nor detect
bloated prompts, nor measure investigation depth.

> Golden rule (efficiency-cost steering): "the cheapest result that is still
> correct and secure wins." You can't optimize what you don't measure.

## Goals

| # | Requirement | Why |
|---|-------------|-----|
| M1 | Split request latency into **data-collection** vs **LLM** time | Know whether latency/cost is adapters or Bedrock — drives different fixes |
| M2 | Observe **prompt size** distribution per agent | Detect context bloat (input tokens = recurring cost); efficiency-cost steering |
| M3 | Observe **investigation rounds** distribution | RCA round cap is a cost guard; measure real rounds vs cap |
| M4 | Reorganize `docs/METRICS.md` by **purpose** (RED / Efficiency / Quality / Domain) | Catalog is growing; group by what the metric is *for* |

## Non-goals (explicitly deferred)

- **Datasource cache instrumentation** (`aigent.cache.hits/misses` emission,
  `aigent.cache.tokens_saved`): the metrics are *defined* but never emitted
  because the `CacheStore` is **not wired into the datasource adapters**
  (`Boto3Adapter`, `HttpAdapter`, etc. fetch fresh every call). Emitting these
  honestly requires first wiring a deterministic-key TTL cache into the adapter
  layer — that is an **architecture change**, tracked in a separate future spec
  (`datasource-cache-layer`). Adding the counters now would produce
  permanently-zero series. See `docs/METRICS.md` "Known gaps".
- Bedrock prompt caching / model tiering (Haiku classifier): spec 11.
- Per-user/session budget cap: spec 14.

## Acceptance criteria

- [ ] `aigent.collect.duration` (histogram, ms, label `agent_id`) emitted per agent request
- [ ] `aigent.llm.duration` (histogram, ms, label `agent_id`) emitted on every Bedrock round-trip (all callers: agents, classifier, synthesizer, RCA)
- [ ] `aigent.prompt.size_tokens` (histogram, label `agent_id`) records Bedrock-reported input tokens
- [ ] `aigent.investigation.rounds` (histogram) records `rounds_completed`
- [ ] All four documented in `docs/METRICS.md`, bounded cardinality (no user_id/trace_id)
- [ ] `docs/METRICS.md` reorganized by purpose; "Known gaps" section lists the deferred cache metrics
- [ ] Tests cover emission of each metric; coverage ≥90% (Docker-measured)
