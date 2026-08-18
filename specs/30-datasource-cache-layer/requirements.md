---
spec: 30-datasource-cache-layer
status: done
completed: 2026-06-21
superseded_by: null
depends_on: []
deferred: []
---

# Requirements: Datasource Cache Layer

## Context

`CacheStore` (Redis, fail-open) exists and the metrics `aigent.cache.hits` /
`aigent.cache.misses` are defined — but **nothing emits them**: the datasource
adapters (`Boto3Adapter`, `KubernetesAdapter`, `HttpAdapter`, `AthenaAdapter`,
`McpAdapter`) fetch fresh on every `collect()`. Each query re-hits AWS/K8s/HTTP
even when an identical query ran seconds ago. This wastes API calls and latency,
and the cache metrics read permanently zero (documented as a "Known gap" in
`docs/METRICS.md`).

The `efficiency-cost` steering calls for caching deterministic infra data
(not LLM responses). This spec wires that cache into the adapter layer.

## Goals

| # | Requirement | Why |
|---|-------------|-----|
| C1 | Cache each adapter's `collect()` output keyed deterministically | Avoid re-fetching identical infra data within the TTL |
| C2 | Deterministic key via `hashlib.sha256` (never native `hash()`) | CLAUDE.md invariant; stable across processes |
| C3 | Per-agent TTL + namespace from `agent.yaml` (`cache.ttl`, `cache.namespace`) | Operator controls freshness per agent |
| C4 | Fail-open | Redis down = collection still works (cache miss path) |
| C5 | Emit `aigent.cache.hits` / `aigent.cache.misses` | Close the dead-metric gap; measure cache effectiveness |
| C6 | Caching disabled when `ttl <= 0` | Opt-out / safety |

## Non-goals

- **LLM response caching** — forbidden (leaks across users, breaks multi-turn).
- **`aigent.cache.tokens_saved`** — this belongs to **Bedrock prompt caching**
  (spec 11), not the datasource cache. A datasource hit avoids an *API call*, not
  LLM *tokens* (the infra data still goes into the prompt either way). This metric
  is reassigned to spec 11 in `docs/METRICS.md`.

## Acceptance criteria

- [ ] `DatasourceAdapter.collect()` checks the cache before calling the concrete
      `_collect()`, and stores the result with the agent's TTL on a miss
- [ ] Key = `sha256(adapter_identity + "|" + query)`; same query+adapter → same key
- [ ] TTL/namespace threaded from `AgentConfig.cache` via `create_adapters(...)`
- [ ] Redis failure never breaks `collect()` (fail-open, verified by test)
- [ ] `aigent.cache.hits` / `aigent.cache.misses` emitted with a bounded
      `namespace` label
- [ ] `ttl <= 0` bypasses the cache entirely
- [ ] `docs/METRICS.md`: cache metrics moved out of "Known gaps"; `tokens_saved`
      reassigned to spec 11
- [ ] Tests cover hit / miss / disabled / fail-open; coverage ≥90% (Docker)
