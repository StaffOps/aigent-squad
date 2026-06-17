# Design: Bedrock Cost Attribution

## Architecture

```
                  ┌─────────────────────────────┐
                  │  AWS Billing / Cost Explorer │  authoritative $ PER MODEL
                  └──────────────▲──────────────┘
                                 │ cost allocation tags
                  ┌──────────────┴──────────────┐
                  │ Application Inference Profile │  (1 per model)
                  │  tags: CostProject/Scope/...  │
                  └──────────────▲──────────────┘
                                 │ modelId = AIP ARN
   agent ──invoke(model=aip)──▶ BedrockClient ──emit──▶ aigent.tokens.total
                                                         {model, agent_id, direction}
                                                              │
                              attribution = tokens(agent,model) / tokens(model)
```

## Components

| Component | Responsibility |
|-----------|----------------|
| `bedrock-aip/` (Terraform) | Creates AIP per model + tags; outputs model→ARN map |
| `iam/` (Terraform) | Allows invoke on `application-inference-profile/*` |
| `BedrockClient.invoke` | Accepts `agent_id`; labels token/cost metrics |
| `GenericAgent` / `Classifier` | Pass their `agent_id` when invoking |

## Rationale (decisions)

### Decision 1: AIP per MODEL, not per agent×model

**Choice**: 1 Application Inference Profile per model; per-agent attribution
comes from the token metric (`agent_id` label), not a dedicated per-agent AIP.

**Justification, in order of strength**:
1. **Per-agent cost is an attribution (showback) problem, not an infra one.**
   AWS gives authoritative $ per model (tag); the per-agent proportion comes
   from telemetry we already have. No need for N×M resources.
2. **Avoids dead resources.** Today there is 1 model and every agent uses the
   same one. AIP per agent×model would create 6+ profiles that bill the same
   thing — complexity without gain.
3. **Flexibility to re-slice.** Metric-based attribution allows slicing by agent
   TODAY and by session/tenant LATER, without touching infra.

**Accepted trade-offs**:
| Cost | Reality |
|------|---------|
| Per-agent cost is *estimated* (attribution), not billed directly | The total matches the bill; attribution is proportional to real token consumption — accurate enough for showback |
| Depends on telemetry working | The metric already exists; only the `agent_id` label was missing |

**When it would be wrong** (signal): if **contractual chargeback** (real, not
showback) per agent/tenant is needed — then a dedicated AIP per billable
dimension starts to pay off. For internal showback, attribution is enough.

### Decision 2: `copy_from` points to the system inference profile (`us.`)

**Choice**: `model_source.copy_from = arn:...:inference-profile/us.<model>`,
not `foundation-model/<model>`.

**Justification**:
1. The model (Claude Sonnet 4.5) **requires** an inference profile (no on-demand
   on the foundation-model ARN — confirmed empirically:
   `ValidationException: on-demand throughput isn't supported`).
2. The `us.` profile gives cross-region (us-east-1/2, us-west-2) — resilience.

**Trade-off**: the AIP inherits cross-region routing from the source profile (ok).

### Decision 3: Attribution by token proportion (not by call count)

**Choice**: attribution = agent's tokens / model's total tokens.

**Justification**: Bedrock charges per token, not per call. An RCA call with
165KB of context costs much more than a "hi". Attributing by number of calls
would distort; by tokens it mirrors the real billing.

**Refinement**: input and output have different prices (~$3 vs $15/million). The
most faithful attribution weighs input/output by their respective prices. The
`aigent.cost.estimated` metric (already computed with those weights) labeled by
`agent_id` solves this directly — it's the best attribution key.

## Invariants

- The per-agent attributed total SHALL sum to the model's cost on AWS.
- `agent_id` in a metric is bounded (~6 agents) — safe as a label (does not
  violate cardinality, unlike `user_id`).

## Attribution formula (operational)

```
# Estimated cost proportion per agent, per model (MetricsQL)
sum by (agent_id) (aigent_cost_estimated{model="<aip_arn>"})
  / ignoring(agent_id) group_left
sum (aigent_cost_estimated{model="<aip_arn>"})

# Apply to the real Cost Explorer bill:
# real_agent_cost = proportion_above × model_cost_in_cost_explorer
```

## External dependencies

| Service | Purpose |
|---------|---------|
| AWS Bedrock (AIP) | Cost tag on the billing record |
| Cost Explorer / CUR | Authoritative $ per tag |
| VictoriaMetrics | Per-agent_id token/cost metric (attribution) |
| AWS Billing console | Activate cost allocation tags (manual, 1x) |
