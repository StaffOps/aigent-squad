---
name: cost-spike-explanation
description: How to explain why cloud spend went up — isolate the spike by service, usage type, account, region and tag, separate rate changes from usage changes, and express the result as unit cost (cost per request, per customer, per GB) rather than raw totals.
keywords: ["cost spike", "cost increase", "cost went up", "costs went up", "spend increase", "spend spike", "bill went up", "higher bill", "bill increase", "why is my bill", "unexpected cost", "unexpected charge", "cost anomaly", "anomaly detection", "month over month", "week over week", "unit cost", "cost per request", "cost per customer", "unit economics", "data transfer cost", "usage type"]
---

# Explaining a cost spike

"The bill went up" has an answer that is almost always one of: **more usage**, **a higher
rate**, **a new resource**, or **a billing artefact** (credits expiring, commitments
lapsing, a one-time charge). The job is to find which, attribute it, and say whether it
is proportional to business activity.

Posture: read-only. Use Cost Explorer (`ce get-cost-and-usage`, `get-cost-forecast`,
`get-anomalies`), the Cost and Usage Report via Athena if available, and resource
inventory. Never change resources or commitments; recommendations go to the owner.

## Step 1 — Pin the window and the size

- Compare like with like: same number of days, same weekdays; daily granularity first.
- Note that recent days are incomplete (billing data lags by up to ~24h) — a "drop" today
  is often just missing data.
- Quantify: absolute delta and percentage. A large percentage on a tiny line item is noise.
- Use the right cost metric: **unblended** for what was charged, **amortized** when
  Savings Plans / Reserved Instances are in play (otherwise upfront fees look like spikes),
  and net of credits when credits recently expired.

## Step 2 — Drill down one dimension at a time

Group the delta by, in this order, stopping when one group explains most of it:

1. `SERVICE`
2. `LINKED_ACCOUNT`
3. `REGION`
4. `USAGE_TYPE` — the most explanatory dimension (e.g. `BoxUsage:m6i.xlarge`,
   `DataTransfer-Out-Bytes`, `NatGateway-Bytes`, `TimedStorage-ByteHrs`)
5. Cost allocation tags (team, service, environment) — only if tags are activated and applied
6. `RESOURCE_ID` (CUR / resource-level data) to name the exact resource

## Step 3 — Usage or rate?

Split the delta: **Δcost ≈ Δusage × old rate + Δrate × new usage**.

| Pattern | Typical cause |
|---------|---------------|
| Usage quantity up, rate flat | More instances, more traffic, more storage, a runaway job |
| Usage flat, effective rate up | Savings Plan/RI expired or under-covered, moved to on-demand, price change, spot → on-demand fallback |
| New usage type appears | New resource, new feature, region expansion, a forgotten test environment |
| One-day spike | Batch job, backfill, large data copy, support/marketplace one-time fee |
| Step change that persists | New baseline: a launch, a scaling change, a config default |

## Step 4 — Usual suspects behind non-obvious spikes

- **Data transfer**: cross-AZ traffic between services, NAT gateway processing, internet
  egress, inter-region replication. Look for `DataTransfer-Regional-Bytes`,
  `NatGateway-Bytes`, `DataTransfer-Out-Bytes`.
- **Logs and metrics**: log ingestion and custom metrics grow with traffic and verbosity.
- **Storage growth**: snapshots, object versions, logs without retention.
- **Request-priced services**: S3 requests, API calls, KMS requests, serverless invocations.
- **Autoscaling**: nodes not scaling down (stuck pods, PodDisruptionBudgets, consolidation off).

## Step 5 — Express it as unit cost

A spike that tracks traffic is not waste. Divide cost by a business driver to decide:

- cost per 1k requests, per active customer, per GB processed, per job run
- If unit cost is flat while total rises → growth, not inefficiency.
- If unit cost rises → efficiency regression worth an owner's attention.

The driver must come from a real metric (request counts, job counts); if none is available,
say the unit-cost conclusion is unverified.

## Step 6 — Check AWS Cost Anomaly Detection

If enabled, `ce get-anomalies` lists detected anomalies with root-cause dimensions and
impact — quote it, but confirm with your own drill-down before presenting it as the cause.

## Answer format

One-sentence answer first ("~$X/day increase since <date>, 80% from NAT gateway data
processing in <region>, driven by usage not rate"). Then the drill-down table, the
unit-cost view, what is verified vs inferred, and owner recommendations with estimated savings.
