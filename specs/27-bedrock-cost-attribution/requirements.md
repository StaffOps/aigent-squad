---
spec: 27-bedrock-cost-attribution
status: done
completed: null
superseded_by: null
depends_on: []
deferred: []
---

# Feature: Bedrock Cost Attribution (AIP per model + per-agent showback)

## Objective

Know **how much each agent cost in Bedrock**, with an authoritative total cost
(matching the AWS bill) and per-agent attribution coming from telemetry.

## Approach (showback)

```
AWS (authoritative)   : Application Inference Profile per MODEL + cost
                        allocation tags → Cost Explorer/CUR gives $ per model
App (attribution key) : token metric labeled by {model, agent_id}
                        → each agent's consumption proportion
─────────────────────────────────────────────────────────────────────
cost(agent, model) = (agent's tokens on the model / model total)
                     × ($ of the model on AWS)
```

## User Stories

WHEN an agent invokes Bedrock
THEN the call SHALL use the model's Application Inference Profile ARN (not the
raw model ID), so the cost carries the cost allocation tags.

WHEN the token metric is emitted
THEN it SHALL be labeled with `agent_id` AND `model`, to allow per-agent
attribution via query.

WHEN the operator queries cost in Cost Explorer
THEN Bedrock cost SHALL be filterable by the `CostProject`, `CostScope`,
`Environment`, `CostCenter` tags.

## Acceptance Criteria

- [ ] Terraform module creates 1 AIP per model, with the 4 FinOps tags.
- [ ] AIP uses `model_source.copy_from` pointing to the **system inference
      profile** (`us.`) — required for cross-region.
- [ ] Output: `{model_key: aip_arn}` map consumable by the app/Helm.
- [ ] IAM policy allows `bedrock:InvokeModel` on the account's
      `application-inference-profile/*` ARN.
- [ ] `aigent.tokens.total` and `aigent.cost.estimated` metrics labeled with
      `agent_id` (besides `model`, `direction`).
- [ ] ≥90% test coverage on the app change.
- [ ] Doc: manual prerequisite (activate cost allocation tags in Billing) +
      MetricsQL attribution query.

## Tags (confirmed by the user)

| Tag | Value |
|-----|-------|
| `CostProject` | `aigent-squad` |
| `CostScope` | `MONITORING` |
| `Environment` | `PRD` |
| `CostCenter` | **variable (no default)** — mandatory, passed at apply time |

## Out of scope

- AIP per agent×model (matrix) — unnecessary; per-agent attribution comes from
  the metric, not dedicated infra (see Rationale in the design).
- Per user/session attribution (cardinality — via traces/logs, not metrics).
- Real model tiering (Haiku in the classifier) — that's spec 11; this module
  is only made ready for multiple models.
