# Design: Metrics & Cost Observability (efficiency + quality)

## Metric definitions (all in `src/core/metrics.py`)

| Metric | Type | Unit | Labels | Cardinality |
|--------|------|------|--------|-------------|
| `aigent.collect.duration` | Histogram | ms | `agent_id` | bounded (7 agents) |
| `aigent.llm.duration` | Histogram | ms | `agent_id` | bounded (7 + classifier/synthesizer/rca) |
| `aigent.prompt.size_tokens` | Histogram | 1 | `agent_id` | bounded |
| `aigent.investigation.rounds` | Histogram | 1 | — | bounded |

No high-cardinality labels (no `user_id`, `trace_id`, raw text).

## Where each is emitted

### `aigent.collect.duration` — `src/core/generic_agent.py`
The adapter fan-out already runs inside a `collect_data` span (lines 49–53).
Wrap that block with a wall-clock timer and `record()` the elapsed ms labeled by
`agent_id`. Emitted once per `process_request`, even when there are zero adapters
(value ~0) — gives a clean baseline.

### `aigent.llm.duration` + `aigent.prompt.size_tokens` — `src/core/bedrock.py`
Both belong at the single Bedrock chokepoint so **every** caller is covered
(specialist agents, classifier, synthesizer, RCA synthesizer).

- `llm.duration`: measure wall time of the `invoke_model` round-trip inside
  `_invoke_sync`, recorded on success. Retries/backoff are excluded so the metric
  reflects model latency, not our sleep — the existing token/cost emission already
  sits right after the successful call, so we record there with the same `attrs`.
- `prompt.size_tokens`: we already read `input_tokens` from the Bedrock `usage`
  block. Record it as a **histogram** (distribution) labeled by `agent_id`. This
  is distinct from `aigent.tokens.total` which is a **counter** (running sum):
  the histogram exposes p50/p95 prompt size to catch bloat, the counter exposes
  total spend. Same source value, different aggregation/intent.

### `aigent.investigation.rounds` — `src/supervisor/investigation.py`
`state.rounds_completed` is already set (currently always 1, single-round RCA).
Record it alongside the other investigation completion metrics (lines 117–121).
Ready for multi-round RCA without further wiring.

## Why measure llm/collect split at different layers
- Collection only happens in `GenericAgent` → instrument there.
- LLM calls happen from many places → instrument at `bedrock` chokepoint with the
  `agent_id` that every caller already passes (`agent_id="classifier"`,
  `"unknown"` fallback, etc.). This avoids duplicating timers at each call site.

## Deferred: datasource cache (separate spec)
`aigent.cache.hits/misses` are defined in `metrics.py` and documented but **never
emitted** — the `CacheStore` is not used by any datasource adapter. Wiring a
deterministic-key (`hashlib.sha256`) TTL cache into the adapter layer + emitting
hit/miss + `aigent.cache.tokens_saved` is an architecture change with its own
correctness concerns (staleness, per-namespace TTL, fail-open). Tracked
separately; not in this spec. `docs/METRICS.md` gains a "Known gaps" section so
the dead metrics are not mistaken for working ones.

## Testing strategy
- Patch the metric objects (`collect_duration.record`, `llm_duration.record`,
  `prompt_size_tokens.record`, `investigation_rounds.record`) and assert they are
  called with the expected value/labels.
- `bedrock` test: mock `invoke_model` to return a `usage` block; assert
  `llm_duration` and `prompt_size_tokens` recorded with `agent_id`.
- `generic_agent` test: assert `collect_duration` recorded with `agent_id`.
- `investigation` test: assert `investigation_rounds` recorded value 1.
- Verification independence: tests authored/reviewed separately from impl.
