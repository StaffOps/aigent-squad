# Promotion & Demotion Triggers — Catalog

> Created by spec 33 (`33-operational-review-loop`) Phase 1.
> Every trigger below is measurable or flagged as a gap. A dormant phase
> promotes ONLY when the review records a measurement crossing its threshold.

---

## Query conventions (read before running anything below)

- **Datasource**: the VictoriaMetrics that scrapes the squad (`serviceMonitor.enabled=true`
  in the deploy overlay). Run queries as **instant queries at the review date**.
- **Window**: every counter/histogram query uses an explicit `[30d]` range — never a bare
  name (bare counters are lifetime values that reset on pod restart).
- **Exported names**: OTel → Prometheus turns `.` into `_`, adds `_total` to counters and
  appends the unit (`aigent.bedrock.queue_wait_seconds` → `aigent_bedrock_queue_wait_seconds_bucket`).
  Histograms exist only as `_bucket`/`_sum`/`_count`. Where the unit suffix is uncertain
  (e.g. `unit="USD"`), queries match with `{__name__=~"…"}` so both spellings work.
  **First step of every review**: list the real names
  (`curl -s "$VM/api/v1/label/__name__/values" | tr ',' '\n' | grep aigent_`) and fix
  this file if one differs — an empty result reads as "no problem" and is the worst failure.
- Label values are the code's values, not English glosses: investigation confidence is
  `alta|media|baixa` (`src/supervisor/investigation.py`).

## Trigger Inventory

| Source spec | Trigger statement | Threshold | Measurement (metric/query) | Measurable today? |
|-------------|-------------------|-----------|----------------------------|-------------------|
| VISION L1→L2 | 1-round RCA is insufficient (incomplete evidence, uncovered gaps) | >30% of investigations | Proxy until G1: share of investigations ending `baixa` — `sum(increase(aigent_investigation_completed_total{confidence="baixa"}[30d])) / sum(increase(aigent_investigation_completed_total[30d]))` | PARTIAL: proxy measurable today. The direct signal ("needed a 2nd round") does not exist: the system is single-round, and `aigent.investigation.rounds` uses default SDK buckets (0,5,10…) so rounds=1 vs 2 share a bucket (no View can be added — `docs/METRICS.md`). See G1 |
| VISION L2→L3 | Context from other agents would improve collection | >20% of cases | A/B test: RCA confidence diff with/without cross-agent context | GAP: requires Level 2 implementation + A/B framework |
| VISION L3→L4 | System needs real autonomy — decisions without human in the loop, auto-trigger, emergent hypotheses | Qualitative + cost/quality metrics at L3 proving limitation | No single metric — composite judgment after L3 operates | GAP: L3 not implemented |
| spec 11 | Haiku misroutes on ambiguous queries (routing accuracy drop) | GAP: threshold undefined (set after baseline) | Ground truth: golden routing cases in `evals/golden_queries.yaml` via `scripts/eval_squad.py` (spec 35). Drift proxy: mean classifier confidence per tier — `sum by (tier) (increase(aigent_tier_classifier_confidence_sum[30d])) / sum by (tier) (increase(aigent_tier_classifier_confidence_count[30d]))` (a mean, because the histogram uses default SDK buckets 0,5,10… so every 0–1 score lands in one bucket) | GAP (G2 + G8 threshold undefined); drift proxy measurable today (`aigent.tier.classifier_confidence`, spec 38) |
| spec 11 | Synthesizer Sonnet→Opus promotion (RCA quality too low at Level 3+) | Low confidence in >40% of Level 3+ cases | Same query as EVIDENCE-MODEL §4 once Level 3 exists (no `level` label today) | GAP: Level 3+ not implemented (G7). There is no `aigent_rca_confidence` metric — the real one is `aigent.investigation.completed{confidence}` |
| spec 17 | Agent-as-tools depth=1 is insufficient (legitimate 2+ hop deps that fan-out cannot resolve) | Frequency of depth-blocked requests (rejected `X-Agent-Hop≥2`) | `sum(rate(aigent_agent_hop_rejected_total[30d]))` | GAP: `aigent.agent_hop.rejected` counter not yet emitted (implement when agent-as-tools ships) |
| spec 17 | Fan-out max_agents=3 ceiling proves insufficient | Queries that require >3 agents frequently | `count(aigent_classifier_agents_requested > 3) / count(aigent_classifier_agents_requested)` | GAP: `aigent.classifier.agents_requested` histogram not emitted |
| spec 18 | Multi-round investigation needed (1 round leaves gaps) | >30% of cases need 2+ rounds | Same proxy as VISION L1→L2 | PARTIAL: same as VISION L1→L2 (G1) |
| spec 21 | Opus enricher demotion — Opus adds no measurable value over Sonnet draft | >60% of distillations where output ≈ Sonnet input | Manual review or automated quality comparison (BLEU/semantic similarity between Sonnet draft and Opus output) | GAP: requires distillation pipeline running + quality evaluation |
| spec 25 | Gateway saturation / multi-replica contention signals (resurrect dormant concurrency work) | Bedrock semaphore queue_wait p99 > 5 s OR session_lock timeouts > 1% | `histogram_quantile(0.99, sum by (le) (rate(aigent_bedrock_queue_wait_seconds_bucket[30d]))) > 5` (unit: **seconds**) | queue_wait: ✅ MEASURABLE (emitted at `src/core/llm/service.py`). session_lock timeouts: GAP — `aigent_session_lock_timeout_total` is not defined in `src/core/metrics.py` (G6) |
| spec 26 | Skills keyword-miss rate too high → migrate to embeddings | GAP: threshold undefined ("high") | `sum(increase(aigent_skill_miss_total[30d])) / sum(increase(aigent_skill_query_total[30d]))` | GAP: `aigent.skill.miss` counter not emitted (G5) + threshold undefined |
| EVIDENCE-MODEL §4 | Level-2 gate: evidence collection insufficient to reach HIGH confidence routinely | `baixa` share > 50% over 30d | `sum(increase(aigent_investigation_completed_total{confidence="baixa"}[30d])) / sum(increase(aigent_investigation_completed_total[30d])) > 0.5` | ✅ MEASURABLE (emitted at `src/supervisor/investigation.py`); needs production volume to be meaningful |

| spec 31 / 33 design | Gateway pressure — backpressure or admission guards firing in normal operation | any sustained non-zero | `sum(increase(aigent_gateway_pool_rejections_total[30d]))`, `max_over_time(aigent_gateway_pool_depth[30d])`, `sum by (reason) (increase(aigent_rate_limit_blocks_total[30d]))` | ✅ MEASURABLE (`src/core/metrics.py`) |
| BACKLOG B-08 | Security-layer overhead (ms + $/request) too high to justify | GAP: threshold undefined | No per-layer latency/cost metric | GAP: needs per-layer timing (G10) |
---

## Gap Summary

| Gap ID | What's needed | Effort | Blocks which trigger(s) |
|--------|---------------|--------|-------------------------|
| G1 | A direct "needed a 2nd round" signal: a counter emitted when the correlator finds a missing causal layer (rounds histogram can't separate 1 vs 2 with default buckets) | Low (one counter on the gap-detection path) | VISION L1→L2, spec 18 |
| G2 | Ground-truth annotation for classifier correctness | Medium (human labeling or eval harness) | spec 11 Haiku accuracy |
| G3 | `aigent.agent_hop.rejected` counter | Low (emit on rejection path) | spec 17 depth |
| G4 | Distillation quality evaluation (Sonnet vs Opus output comparison) | Medium (automated eval or manual sample) | spec 21 demotion |
| G5 | `aigent.skill.miss` counter with detection logic | Medium (requires knowing when a skill SHOULD have matched) | spec 26 |
| G6 | `aigent.session_lock.timeout` counter (queue_wait is already emitted) | Low | spec 25 resurrection (2nd condition) |
| G7 | Level 3+ implementation + A/B framework | High | VISION L2→L3, spec 11 synthesizer |
| G8 | Thresholds defined for spec 11 accuracy, spec 26 miss rate, B-08 overhead | Low (decision after a baseline) | spec 11, spec 26, B-08 |
| G9 | Prompt-cache write/read token counter (the `direction` label is only input/output) | Low | cost review "prompt caching working?" |
| G10 | Per-security-layer latency histogram | Low–Medium | B-08 |

---

## Cost Reconciliation Recipe

### 1. AWS Cost Explorer — authoritative bill

Filter:
- **Tag**: `CostProject = <value>` (applied via Application Inference Profile, see spec 27; the value is case-sensitive — confirm with `aws ce get-tags`, below)
- **Service**: Amazon Bedrock
- **Granularity**: Monthly
- **Group by**: Tag `CostProject` (total) or further by model if multiple AIPs exist

```
AWS Console → Cost Explorer → Filters:
  Service = Amazon Bedrock
  Tag: CostProject = <value confirmed via get-tags>
  Time: last 30 days
```

Or via CLI (the tag VALUE is case-sensitive in Cost Explorer — confirm it first with
`aws ce get-tags --time-period Start=$START,End=$END --tag-key CostProject`; the repo docs
disagree between `AIGENT-SQUAD` and `aigent-squad`):
```bash
START=2026-08-01; END=2026-09-01          # End is EXCLUSIVE: first day of the next month
COST_PROJECT=AIGENT-SQUAD                 # replace with the value get-tags returned
aws ce get-cost-and-usage \
  --time-period Start=$START,End=$END \
  --granularity MONTHLY \
  --filter '{"And":[{"Tags":{"Key":"CostProject","Values":["'"$COST_PROJECT"'"]}},{"Dimensions":{"Key":"SERVICE","Values":["Amazon Bedrock"]}}]}' \
  --metrics UnblendedCost \
  --group-by Type=TAG,Key=CostProject
```

### 2. Per-agent attribution (MetricsQL)

From spec 27 design — proportional attribution by estimated cost:

```promql
# Per-agent share of estimated cost over the review window (counter → increase; the
# unit may add a _USD suffix, so match both spellings)
sum by (agent_id) (increase({__name__=~"aigent_cost_estimated(_USD)?_total"}[30d]))
  / ignoring(agent_id) group_left
sum (increase({__name__=~"aigent_cost_estimated(_USD)?_total"}[30d]))
```

To get dollar amounts per agent:
```
real_agent_cost = proportion_above × total_cost_from_cost_explorer
```

### 3. Compare against ANALYSIS.md estimates

Reference values from `specs/ANALYSIS.md`:

| Level | Estimated $/investigation | Monthly @200 queries/day |
|-------|---------------------------|--------------------------|
| 1 (Hub-and-spoke) | ~$0.17 | ~$99/mo (optimized) |
| 2 (Iterative) | ~$0.80 | ~$480/mo |
| 3 (Shared context) | ~$1.65 | ~$990/mo |
| 4 (Autonomous) | ~$4.20 | ~$2520/mo |

Plus distillation (spec 21): ~$0.27/investigation, ~$13.55/mo @50 incidents.

### 4. Divergence action

**If actual monthly cost diverges >30% from ANALYSIS.md estimate for the current level:**

1. Identify the driver: which agent/model is over-consuming? (MetricsQL attribution above)
2. Check if query volume differs from the 200/day assumption
3. Check if prompt caching is working (spec 11: ~90% discount expected on system prompt)
4. Update `specs/ANALYSIS.md` cost model with real numbers + date of update
5. If cost is structurally higher than estimated, evaluate:
   - Is prompt caching disabled? → fix (high ROI)
   - Is Haiku classifier actually saving? → verify model in use
   - Is fan-out calling more agents than expected? → check `max_agents` config
6. Record finding in the review document (`specs/reviews/YYYY-MM.md`)
