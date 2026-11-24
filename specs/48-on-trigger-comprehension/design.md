# Design: On-trigger comprehension

**Spec**: `48-on-trigger-comprehension` · see `requirements.md`

---

## Architecture

```
ENTITY_REGISTRY_FILE (mounted from the deploying team's config repo; unset → feature off)
        │ startup: yaml.safe_load → EntityRegistry (Pydantic) → fail loudly if invalid
        ▼
src/core/comprehension/
  registry.py      EntityRegistry, Entity, Signal; alias index; alert-label index
  resolve.py       resolve_text(text) / resolve_alert(labels) → Resolution (resolved | ambiguous | none)
  query.py         render(signal, entity) → PromQL from registry values only
  source.py        MetricsSource Protocol; PrometheusHttpSource (GET /api/v1/query_range)
  verdict.py       pure functions: static / robust_z / seasonal / derivative → Verdict
  engine.py        orchestration: caps, budget, concurrency, Redis baseline cache, metrics
  render.py        Verdict[] → compact <baseline_verdicts> block; Verdict → Evidence
  snapshot.py      (Phase 2) lazy TTL-cached environment snapshot
  catalog.py       EVIDENCE-MODEL ids incl. the B-24 extension (id → layer, default strength)

User question                                   Alertmanager alert
   │ gateway → supervisor (InputScanner L2 first)  │ /alerts/incoming (scanned symptom, unchanged)
   ▼                                               ▼
SupervisorAgent.process_request                 alert_handler.handle_alert_payload
   ├─ classifier (Bedrock)  ─┐ concurrent          ├─ resolve_alert(labels) → entity + signal
   └─ engine.verdicts(text) ─┘                     ├─ engine.verdicts(entity, [signal])
   ▼                                               ├─ snapshot (Phase 2)
agents receive <baseline_verdicts> as data       └─ run_investigation(symptom, verdicts=…)
   └─ groundedness scan includes the block             └─ verdicts → Evidence[] (pre-filled)
```

Nothing in this design runs without a trigger. There is no scheduler, no warm-up job, no watch.

## Decisions

### 1. The registry is per-environment config, never product data
`ENTITY_REGISTRY_FILE` points at a YAML file mounted into the supervisor (chart value
`entityRegistry.existingConfigMap`). The product repo ships `config/entity-registry.example.yaml`
only, with placeholders and one real entry: the squad itself, whose metrics (`aigent_*`) are
product-owned; its namespace/workload stay placeholders. Same rule as spec 46: environments supply
addresses and identities, never capabilities. The registry has no field that touches tools,
`read_only`, capability tier or routing.
*Trade-off*: every environment must author its registry before it gets value; mitigated by the
how-to, the example and the `registry_check` script (Decision 12).

### 2. Registry schema (v1)
```yaml
version: 1
defaults:
  sustain: {samples: 5, required: 3}        # M of N recent samples must exceed the threshold
  robust_z: {borderline: 2.0, abnormal: 3.0}
  min_history: {robust_z: 7d, derivative: 7d, seasonal_weeks: 3}
entities:
  - id: "svc:<example-api>"                 # stable id, used in verdicts and Evidence.fault_domain
    aliases: ["<example-api>", "<example api>"]
    kind: service                           # service | workload | node | cost | platform | queue
    tier: 1                                 # 1 | 2 | 3 (depth defaults, never watching)
    owner: "<team>"
    k8s: {namespace: "<ns>", workload: "<example-api>", kind: Deployment}
    labels: {service: "<example-api>"}      # metric identity, substituted into query templates
    cost_tag: "<tag-key>:<value>"
    alert_match: {service: "<example-api>"} # exact Alertmanager label match (all keys)
    alerts: {"<AlertName>": latency_p99}    # alertname → signal id
    signals:
      - id: latency_p99
        em_id: I2
        why: "degradation before errors"    # mandatory: no signal without a failure hypothesis
        query: 'histogram_quantile(0.99, sum by (le) (rate(<latency_metric>_bucket{service="{{labels.service}}"}[5m])))'
        unit: s
        method: seasonal                    # static | robust_z | seasonal | derivative
        direction: up                       # up | down | both — which side is bad
        thresholds: {}                      # static only: {borderline: X, abnormal: Y}
    blind_spots: ["<signal we cannot compute here — declared so answers say unverified>"]
```
Validation at load: unique ids and aliases (an alias shared by two entities is allowed but marked
ambiguous), `em_id` known to `catalog.py`, `why` non-empty, `static` requires thresholds, every
`{{…}}` placeholder resolves to a field of the same entity, every substituted value matches
`^[A-Za-z0-9_.:/-]{1,128}$`. Invalid → startup fails (same contract as an invalid `agent.yaml`).
*Trade-off*: a typo stops the supervisor instead of degrading. Chosen because a silently partial
registry produces silently missing verdicts, which reads as "nothing to report"; the chart render
test and `registry_check` catch it before rollout.

### 3. Entity resolution is deterministic and is not routing
Alias match on the already-scanned user text (casefolded, NFKC, word boundaries), exact label match
for alerts. It never calls an LLM and never changes which agents run — the classifier still routes
(invariant 1); resolution only adds context. Ambiguity yields no verdict plus a candidate list.
Caps: `COMPREHENSION_MAX_ENTITIES=3`, `COMPREHENSION_MAX_SIGNALS=6` per entity; overflow is
truncated by tier and stated in the block.
*Trade-off*: aliases miss paraphrases ("the payment thing"). Accepted for Phase 1; precision beats
recall here, and an LLM resolver would add cost and a new injection surface.

### 4. Queries are built only from registry values
`query.render` substitutes `{{labels.*}}`, `{{k8s.*}}` and `{{cost_tag}}` from the entity record.
Alert labels and user text are used only as lookup keys into the registry and are never
interpolated. This removes PromQL injection by construction. The query template itself is trusted
config written by the deploying team.

### 5. Metrics source: Prometheus-compatible HTTP API, called from code
`PrometheusHttpSource` issues read-only `GET /api/v1/query_range` against
`COMPREHENSION_METRICS_URL` (falls back to `PROMETHEUS_URL`), optional bearer token from
`COMPREHENSION_METRICS_TOKEN_FILE` (never logged). Works with Prometheus and VictoriaMetrics.
*Alternative rejected*: calling the observability agent's MCP `query_range` tool from code. It
couples the engine to one MCP server's argument shape and response rendering, and goes through the
LLM-oriented tool path for a deterministic job. The `MetricsSource` Protocol keeps the door open for
an MCP-backed or external-baseline source later.
*Egress*: one more outbound destination, same network zone as the existing `PROMETHEUS_URL`.

### 6. Verdict methods (code, never the LLM)
Let `x` be the recent window (last N samples at the signal's step), `h` the history window.
- **static**: compare the sustained value of `x` to `thresholds.borderline/abnormal` in `direction`.
- **robust_z**: `med = median(h)`, `mad = 1.4826·median(|h − med|)`; `z = (x_i − med)/mad`.
  Abnormal when ≥`required` of `samples` have `|z| ≥ abnormal` on the bad side; borderline when the
  sustain rule fails but the latest sample exceeds `borderline` (a single spike is borderline, never
  abnormal). Median/MAD, never mean/stddev.
- **seasonal**: same hour-of-week slot from the previous `seasonal_weeks` weeks (one `query_range`
  per week offset, each covering the slot); `med`/`mad` over those samples, then the robust_z rule.
- **derivative**: per-sample rate of change (or the signal's own `deriv()` in the template); baseline
  is the robust_z of the derivative over `h`. Catches leaks and queue growth.
- **MAD = 0** (flat series): use a floor `max(1e-9, 0.01·|med|)`; if `med` is also 0 and `x` is 0 →
  `normal`; otherwise compute with the floor and mark `deviation` as "vs flat baseline".
- **since**: scan `x` (then `h` backwards, bounded) to the first sample of the current excursion.
- **minimum history**: fewer samples than `min_history` covers → `no-baseline: insufficient_history`.
Step selection keeps every series ≤ 2 000 points (`step = ceil(range / 2000)`, floored at 30 s).
*Trade-off*: thresholds are heuristics, not calibrated per signal. They are per-signal overridable,
and precision is measured once feedback exists (Decision 10).

### 7. Verdict contract and `no-baseline` reasons
```json
{"entity": "svc:<example-api>", "signal": "latency_p99", "now": 0.84, "unit": "s",
 "method": "seasonal", "expected": 0.21, "spread": 0.04, "window": "3w same-hour",
 "verdict": "abnormal", "deviation": "15.8 MAD", "since": "2026-07-04T14:05:00Z",
 "baseline_age_s": 120, "em_id": "I2", "layer": "impact"}
```
`verdict ∈ {normal, borderline, abnormal, no-baseline}`. `no-baseline` carries
`reason ∈ {no_data, insufficient_history, source_unavailable, budget_exceeded, degenerate,
not_configured}`. `no_data` (empty result) is the important one: an empty series is the classic
"metric name wrong, so no problem found" failure — it becomes an explicit honesty statement, never
`normal`, and logs `registry_signal_no_data` (rate-limited, signal id only) so registry drift is
visible.

### 8. Surfacing: data block to the agent, explicit honesty to the user
The supervisor passes `Verdict[]` to the chosen agents; `GenericAgent` renders a compact
`<baseline_verdicts>` block (one line per verdict, hard cap `COMPREHENSION_MAX_BLOCK_CHARS=2400`,
≈600 tokens) inside the data region that guardContent already tags as data, next to — not replacing
— the raw sample. A shared instruction `COMPREHENSION_INSTRUCTION` (env-overridable, like
`CALIBRATED_HONESTY_INSTRUCTION`) says: verdicts are computed facts and count as verified; cite
them; do not re-judge normality of a signal that has a verdict; for every `no-baseline` say "could
not assess whether <signal> is normal (<reason>)"; you may still query more data. Ambiguous
entities and truncations are stated in the block so the answer can say them.
The block is appended to `effective_infra_data` so spec 41's groundedness scan treats its numbers as
grounded. Non-streaming responses also expose `x_aigent.comprehension` = `[{entity, signal,
verdict, reason}]` (deterministic, used by the eval harness).
*Trade-off*: the honesty statement depends on the model following the instruction. It is
eval-gated at 100% (Decision 11) rather than enforced by a runtime post-check, because a bilingual
string check on free text would be fragile; a runtime check is a follow-up if the eval shows misses.

### 9. Caching and TTLs (reactive cache, warmed only by use)
Cached item = computed baseline (`med`, `mad`, sample count, computed_at), never the verdict and
never an LLM response. Key: `sha256(entity|signal|method|slot|registry_hash)` in namespace
`comprehension` of the existing Redis cache (spec 30). TTLs: robust_z / derivative 1 h, seasonal
until the end of the hour-of-week slot (≤ 1 h), static nothing to cache. The recent window `x` is
always fetched fresh. `registry_hash` in the key invalidates on registry change. Redis error →
compute from source (fail-open). Snapshot (Phase 2): key `sha256(registry_hash)`, TTL
`COMPREHENSION_SNAPSHOT_TTL_SECONDS=600`, built on the first trigger that needs it.

### 10. Cost and latency budget
Zero LLM calls. Per request ≤ 3 entities × 6 signals = 18 series, each 1 recent + 1 history query
(seasonal: 1 + K) when uncached. Concurrency bounded (`asyncio.Semaphore(8)`), per-query timeout
2 s, whole-step budget `COMPREHENSION_BUDGET_MS=3000`; anything unfinished becomes `no-baseline:
budget_exceeded`. Runs concurrently with the classifier call, so typical added wall-clock is the
excess over classification (target p95 ≤ 1 s added with warm cache). Added prompt ≤ 600 tokens per
agent. Datasource load: worst case ~40 range queries per triggered request, bounded by caps.

### 11. Verification against known verdicts
- **Tier 1 ($0, every push)**: `verdict.py` is pure; synthetic series fixtures with known outcomes
  per method (normal, borderline single spike, sustained abnormal, wrong-direction deviation, flat
  series, short history, empty series). Property tests: median/MAD invariance to a single outlier.
- **Tier 2 (spec 35, on demand)**: a tiny Prometheus-API stub (`evals/comprehension/prom_stub.py`,
  compose profile `eval` only) serves fixture series for a fixture registry. Golden questions with
  known verdicts: abnormal latency, normal traffic, `no-baseline: no_data`, `no-baseline:
  insufficient_history`, ambiguous alias, and one replayed alert. Assertions use
  `x_aigent.comprehension` (deterministic) plus a `must_match` honesty regex for the no-baseline
  cases. Production code has no fixture mode — the stub is just another URL.

### 12. Registry drift check (discover before querying)
`scripts/registry_check.py --registry FILE --url URL` runs every signal's recent-window query once
(read-only) and reports signals returning no series, too few samples for their method, or label
values that do not exist. The deploying team runs it before rollout; it is the registry's version of
"discover metric names before querying" (AGENTS.md invariant 12). Runtime `no_data` verdicts are
the second line.

### 13. Alerts: verdict before investigation, never suppression
`handle_alert_payload` resolves `alert_match` against the merged labels; the signal comes from the
entity's `alerts` map (unknown alertname → entity-level verdicts for tier-relevant signals, capped).
The verdict is passed to `run_investigation(verdicts=…)` and summarized in the result ("signal
currently <verdict> since <since>"). A `normal` current verdict is reported, not acted on: deciding
not to investigate a firing alert is a paging decision, which D-03 leaves out of the product.
The symptom text and its InputScanner pass are unchanged.

### 14. Verdicts as Evidence
| Verdict | `causal_layer` | `strength` | Notes |
|---------|----------------|------------|-------|
| abnormal | catalog layer of `em_id` | `forte` | `fault_domain` from entity kind + id, `timestamp=since`, precision = step |
| borderline | catalog layer | `fraca` | |
| normal | `elimination` | `fraca` | never counts as IMPACT/MECHANISM presence in `score_confidence` |
| no-baseline | — | — | not evidence; listed as unverified in the RCA output |
`raw_query` holds the rendered query for audit. The correlator code is unchanged.

### 15. EVIDENCE-MODEL catalog extension (B-24)
`specs/EVIDENCE-MODEL.md` is a frozen record, so the extension lives here and in `catalog.py`:

| ID | Signal | Layer | Default method | Why |
|----|--------|-------|----------------|-----|
| M14 | Disk / volume usage % | mechanism | static (hard limit) or derivative (fill rate) | slow burn with a deadline; full disk breaks writers |
| M15 | Quota / limit headroom % | mechanism | static | near-exhaustion causes throttling and create failures |
| I9 | Cost anomaly (spend vs same-weekday baseline) | impact | seasonal (day-of-week) | a leak while still cheap; silent architectural change |

### 16. Environment snapshot (B-17, Phase 2)
Compact summary: entity count by kind/tier, declared blind spots, and the last cached verdict per
tier-1 entity with its age (only verdicts already in cache — building the snapshot never triggers a
fan-out of queries). Size cap `COMPREHENSION_SNAPSHOT_MAX_CHARS=1500`. Injected into investigation
and alert flows only; chat injection is decided later from eval data. If an external
anomaly-detection system is integrated (B-05, optional), its baselines are a future
`MetricsSource`/baseline provider, not part of this spec.

### 17. Diff primitive (B-18, Phase 3)
`engine.collect_diff(entity, signal, window_a, window_b)` → `{a: {med, p95}, b: {med, p95}, delta,
delta_pct, verdict_of_change}` using the same source, caps and robust stats ("before vs after
deploy" without the LLM reading two raw series). Exposed to investigation and to agents as a block,
not as a new MCP tool.

### 18. Metrics (B-23), bounded labels only
| Metric | Type | Labels | Measures |
|--------|------|--------|----------|
| `aigent.comprehension.verdicts` | counter | `verdict`, `method` | verdict mix |
| `aigent.comprehension.no_baseline` | counter | `reason` | honesty inputs, registry drift (`no_data`) |
| `aigent.comprehension.resolutions` | counter | `result` (resolved/ambiguous/none), `trigger` (query/alert) | coverage denominator |
| `aigent.comprehension.requests` | counter | `coverage` (with_verdict/raw_only) | verdict coverage |
| `aigent.comprehension.duration` | histogram (ms) | `trigger` | verdict step latency |
| `aigent.comprehension.baseline_age` | histogram (s) | `method` | baseline freshness (< 24 h target) |
No `entity`, `signal`, `user_id`, query or error-text labels — entity ids are environment-specific
and unbounded. Cache effectiveness reuses `aigent.cache.*{namespace="comprehension"}`. Verdict
precision (≥ 80 % target) needs a feedback signal (B-03) and is a TRIGGERS.md GAP row until then;
honesty rate is measured by the eval (Decision 11).

## Invariants

- **Reactive only** (D-03): no scheduler, poller, background refresh or alert suppression.
- **Read-only** (invariant 5): one GET-only query API; the registry cannot express tools or writes.
- **Fail-open for availability**: registry missing → feature off; source/Redis/budget failure →
  `no-baseline`, request still answered. **Fail-closed security unchanged**: InputScanner, guardrail,
  output filter and canary run exactly as today; verdict data enters through the tagged data region.
- **Calibrated honesty** (invariant 12): empty data is never `normal`; every `no-baseline` is stated.
- **Classifier always routes** (invariant 1): resolution adds context only.
- **Deterministic cache keys** (invariant 7): `hashlib.sha256`; no LLM responses cached.
- **No high-cardinality labels**; **no environment-specific names in the repo**.
- **No business logic in the gateway** (invariant 8): everything lives in the supervisor/core.

## Verification

- Unit: registry validation (each rejection path), resolution (alias, boundary, ambiguity, caps),
  query rendering (injection attempts from alert labels and user text never appear in output),
  verdict methods (Decision 11 fixtures), engine budget/timeout/fail-open paths with a fake source
  and a failing Redis, Evidence mapping table, block size cap, `effective_infra_data` inclusion.
- Integration: supervisor path with a fake source → agents receive the block; alert path → verdict
  attached and investigation still runs on a `normal` verdict.
- Eval (spec 35 T2): Decision 11 golden set; no-baseline honesty 100 %.
- Live: entity #1 (the squad) on a real cluster; one replayed alert.
- Coverage ≥ 90 % on `src/core/comprehension/`; `make lint typecheck test` green.

## Risks

| Risk | Mitigation |
|------|------------|
| Wrong metric name in the registry silently yields nothing | `no_data` is `no-baseline` + stated; `registry_check`; `aigent.comprehension.no_baseline{reason="no_data"}` |
| Verdict thresholds produce false "abnormal" | sustain rule, single spike = borderline, per-signal overrides; precision measured once B-03 lands |
| Baselines poisoned by past incidents | median/MAD robustness; KB-based exclusion is an open question |
| Model ignores the verdict or the no-baseline statement | instruction + eval gate at 100 %; runtime post-check as follow-up |
| Added latency on every resolved query | concurrent with classifier, hard budget, cache, caps |
| Datasource load from repeated questions | baseline cache; caps per request; recent window is small |
| Registry becomes a back door to widen capabilities | schema has no capability fields; security review task |
| Prompt bloat | 2 400-char block cap; snapshot only in investigation/alert flows |
