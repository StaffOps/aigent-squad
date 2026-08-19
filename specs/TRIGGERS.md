# Promotion & Demotion Triggers — Catalog

> Created by spec 33 (`33-operational-review-loop`) Phase 1.
> Every trigger below is measurable or flagged as a gap. A dormant phase
> promotes ONLY when the review records a measurement crossing its threshold.

---

## Trigger Inventory

| Source spec | Trigger statement | Threshold | Measurement (metric/query) | Measurable today? |
|-------------|-------------------|-----------|----------------------------|-------------------|
| VISION L1→L2 | 1-round RCA is insufficient (incomplete evidence, uncovered gaps) | >30% of investigations | `sum(aigent_investigation_rounds > 1) / count(aigent_investigation_rounds)` over 30d | GAP: `aigent.investigation.rounds` metric exists but needs ≥30d of production data to evaluate the ratio |
| VISION L2→L3 | Context from other agents would improve collection | >20% of cases | A/B test: RCA confidence diff with/without cross-agent context | GAP: requires Level 2 implementation + A/B framework |
| VISION L3→L4 | System needs real autonomy — decisions without human in the loop, auto-trigger, emergent hypotheses | Qualitative + cost/quality metrics at L3 proving limitation | No single metric — composite judgment after L3 operates | GAP: L3 not implemented |
| spec 11 | Haiku misroutes on ambiguous queries (routing accuracy drop) | Routing accuracy < acceptable (define threshold after baseline) | `sum(aigent_classifier_result{correct="false"}) / sum(aigent_classifier_result)` | GAP: `correct` label requires ground-truth annotation; baseline not established |
| spec 11 | Synthesizer Sonnet→Opus promotion (RCA quality too low at Level 3+) | Low confidence in >40% of Level 3+ cases | `count(aigent_rca_confidence{level=~"3\|4", confidence="low"}) / count(aigent_rca_confidence{level=~"3\|4"})` | GAP: Level 3+ not implemented; metric shape ready when it ships |
| spec 17 | Agent-as-tools depth=1 is insufficient (legitimate 2+ hop deps that fan-out cannot resolve) | Frequency of depth-blocked requests (rejected `X-Agent-Hop≥2`) | `sum(rate(aigent_agent_hop_rejected_total[30d]))` | GAP: `aigent.agent_hop.rejected` counter not yet emitted (implement when agent-as-tools ships) |
| spec 17 | Fan-out max_agents=3 ceiling proves insufficient | Queries that require >3 agents frequently | `count(aigent_classifier_agents_requested > 3) / count(aigent_classifier_agents_requested)` | GAP: `aigent.classifier.agents_requested` histogram not emitted |
| spec 18 | Multi-round investigation needed (1 round leaves gaps) | >30% of cases need 2+ rounds | `sum(aigent_investigation_rounds > 1) / count(aigent_investigation_rounds)` | GAP: same as VISION L1→L2 — needs production data |
| spec 21 | Opus enricher demotion — Opus adds no measurable value over Sonnet draft | >60% of distillations where output ≈ Sonnet input | Manual review or automated quality comparison (BLEU/semantic similarity between Sonnet draft and Opus output) | GAP: requires distillation pipeline running + quality evaluation |
| spec 25 | Gateway saturation / multi-replica contention signals (resurrect dormant concurrency work) | Bedrock semaphore queue_wait p99 > 5s OR session_lock timeouts > 1% | `histogram_quantile(0.99, aigent_bedrock_queue_wait_bucket) > 5000` OR `rate(aigent_session_lock_timeout_total[5m]) / rate(aigent_requests_total[5m]) > 0.01` | GAP: metrics defined in spec 25 design but not emitted until multi-tenant ships |
| spec 26 | Skills keyword-miss rate too high → migrate to embeddings | High rate of "relevant skill not injected due to missing keyword" | `sum(aigent_skill_miss_total) / sum(aigent_skill_query_total)` | GAP: `aigent.skill.miss` counter not yet emitted; requires operator feedback loop or automated detection |
| EVIDENCE-MODEL §4 | Level-2 gate: evidence collection insufficient to reach HIGH confidence routinely | Confidence distribution skewed LOW | `count(aigent_rca_confidence{confidence="low"}) / count(aigent_rca_confidence)` sustained > 50% over 30d | GAP: `aigent.rca.confidence` metric shape ready; needs production volume |

---

## Gap Summary

| Gap ID | What's needed | Effort | Blocks which trigger(s) |
|--------|---------------|--------|-------------------------|
| G1 | `aigent.investigation.rounds` histogram emitting in production | Low (metric hook exists, needs data) | VISION L1→L2, spec 18 |
| G2 | Ground-truth annotation for classifier correctness | Medium (human labeling or eval harness) | spec 11 Haiku accuracy |
| G3 | `aigent.agent_hop.rejected` counter | Low (emit on rejection path) | spec 17 depth |
| G4 | Distillation quality evaluation (Sonnet vs Opus output comparison) | Medium (automated eval or manual sample) | spec 21 demotion |
| G5 | `aigent.skill.miss` counter with detection logic | Medium (requires knowing when a skill SHOULD have matched) | spec 26 |
| G6 | Multi-tenant metrics (queue_wait, session_lock_timeout) | Low (defined, ships with spec 25) | spec 25 resurrection |
| G7 | Level 3+ implementation + A/B framework | High | VISION L2→L3 |

---

## Cost Reconciliation Recipe

### 1. AWS Cost Explorer — authoritative bill

Filter:
- **Tag**: `CostProject = AIGENT-SQUAD` (applied via Application Inference Profile, see spec 27)
- **Service**: Amazon Bedrock
- **Granularity**: Monthly
- **Group by**: Tag `CostProject` (total) or further by model if multiple AIPs exist

```
AWS Console → Cost Explorer → Filters:
  Service = Amazon Bedrock
  Tag: CostProject = AIGENT-SQUAD
  Time: last 30 days
```

Or via CLI:
```bash
aws ce get-cost-and-usage \
  --time-period Start=2026-08-01,End=2026-08-31 \
  --granularity MONTHLY \
  --filter '{"Tags":{"Key":"CostProject","Values":["AIGENT-SQUAD"]}}' \
  --metrics BlendedCost \
  --group-by Type=TAG,Key=CostProject
```

### 2. Per-agent attribution (MetricsQL)

From spec 27 design — proportional attribution by estimated cost:

```promql
# Per-agent proportion of total cost
sum by (agent_id) (aigent_cost_estimated{model=~".*"})
  / ignoring(agent_id) group_left
sum (aigent_cost_estimated{model=~".*"})
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
