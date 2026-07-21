---
name: savings-plans-strategy
description: Use when evaluating Savings Plans purchases, comparing SP vs Reserved Instances, setting coverage targets, or analyzing commitment utilization. Covers SP types, coverage targets (70-80%), term/payment trade-offs, renewal tracking, and <org>-specific Karpenter+Spot+Graviton context.
keywords: [savings-plans, savings, plans, "savings plans", karpenter, sp]
---
# Savings Plans Strategy

Framework for purchasing and managing AWS Savings Plans at <org>.

## Savings Plans types

| Type | Flexibility | Discount | Applies to |
|------|-------------|----------|------------|
| **Compute SP** | Highest — any region, family, OS, tenancy | Up to 66% | EC2, Fargate, Lambda |
| **EC2 Instance SP** | Medium — specific family + region | Up to 72% | EC2 only (any size within family) |
| **SageMaker SP** | SageMaker only | Up to 64% | SageMaker instances |

### Decision tree

```
Is the workload predictable for 1-3 years?
├── NO → Stay On-Demand or Spot
└── YES → Will it stay in the same instance family + region?
    ├── YES → EC2 Instance SP (higher discount)
    └── NO / UNSURE → Compute SP (flexibility)
```

## Savings Plans vs Reserved Instances

| Dimension | Savings Plans | Reserved Instances |
|-----------|--------------|-------------------|
| Commitment | $/hour spend | Specific instance type + AZ |
| Flexibility | Family/region/OS agnostic (Compute SP) | Locked to instance type |
| Discount depth | Up to 72% (EC2 Instance SP) | Up to 75% (Standard RI) |
| Convertible | N/A (inherently flexible) | Convertible RI (lower discount) |
| Best for | Mixed/evolving workloads | Stable, predictable workloads |
| <org> recommendation | **Primary choice** | Only for very stable RDS/ElastiCache |

**<org> default**: Compute Savings Plans for EKS nodes (Karpenter changes instance types dynamically).

## Coverage targets

### CRITICAL: Never target 100% SP coverage

```
Optimal coverage: 70-80% of steady-state compute
```

Why NOT 100%:
- Karpenter uses **Spot aggressively** (~60-70% of nodes) — Spot already discounted
- Workloads fluctuate — over-commitment wastes money
- New workloads may shift to different instance families
- Leaves room for burst capacity on On-Demand

### Coverage breakdown (<org> target)

| Pricing model | Target % of compute | Notes |
|---------------|---------------------|-------|
| Spot (Karpenter) | 60-70% | Cheapest, interruptible |
| Savings Plans | 20-25% (of remaining On-Demand) | Covers baseline |
| On-Demand | 5-15% | Burst, new workloads |

Effective SP coverage of **total** compute is ~70-80% of the On-Demand portion (not total spend).

## Term and payment options

### Term comparison

| Term | Discount | Risk | Best for |
|------|----------|------|----------|
| 1-year | Lower (~20-40%) | Lower commitment | Uncertain growth |
| 3-year | Higher (~50-66%) | Higher lock-in | Stable baseline |

### Payment options (NPV trade-off)

| Payment | Discount | Cash flow impact | NPV consideration |
|---------|----------|------------------|-------------------|
| No Upfront | Lowest | Monthly billing | Best if cost of capital > discount delta |
| Partial Upfront | Medium | 50% upfront + monthly | Balance |
| All Upfront | Highest | Full payment day 1 | Best if cash is cheap (low interest rates) |

### NPV decision framework

```
If (discount_delta_all_vs_no_upfront < company_cost_of_capital):
    → No Upfront (money works harder elsewhere)
Else:
    → All Upfront (discount exceeds opportunity cost)
```

For most <org> scenarios: **No Upfront, 1-year** is the safe default. Move to 3-year only for proven stable baselines.

## Recommendations via Cost Explorer

### Get SP purchase recommendations

```bash
aws ce get-savings-plans-purchase-recommendation \
  --savings-plans-type COMPUTE_SP \
  --term-in-years ONE_YEAR \
  --payment-option NO_UPFRONT \
  --lookback-period-in-days SIXTY_DAYS \
  --region us-east-1
```

### Get current coverage

```bash
aws ce get-savings-plans-coverage \
  --time-period Start=2026-05-01,End=2026-05-29 \
  --granularity DAILY \
  --group-by '[{"Type":"DIMENSION","Key":"SAVINGS_PLAN_ARN"}]'
```

### Get utilization (are we using what we bought?)

```bash
aws ce get-savings-plans-utilization \
  --time-period Start=2026-05-01,End=2026-05-29 \
  --granularity DAILY
```

Target: **>95% utilization**. Below 90% means over-committed.

## Utilization tracking and alarms

### CloudWatch alarm for low utilization

```bash
aws budgets create-budget \
  --account-id <ACCOUNT_ID> \
  --budget '{
    "BudgetName": "sp-utilization-alarm",
    "BudgetLimit": {"Amount": "95", "Unit": "PERCENTAGE"},
    "TimeUnit": "MONTHLY",
    "BudgetType": "SAVINGS_PLANS_UTILIZATION"
  }' \
  --notifications-with-subscribers '[{
    "Notification": {
      "NotificationType": "ACTUAL",
      "ComparisonOperator": "LESS_THAN",
      "Threshold": 90
    },
    "Subscribers": [{
      "SubscriptionType": "EMAIL",
      "Address": "finops@<org-domain>"
    }]
  }]'
```

### Coverage alarm (alert when On-Demand spend grows)

```bash
aws budgets create-budget \
  --account-id <ACCOUNT_ID> \
  --budget '{
    "BudgetName": "sp-coverage-alarm",
    "BudgetLimit": {"Amount": "70", "Unit": "PERCENTAGE"},
    "TimeUnit": "MONTHLY",
    "BudgetType": "SAVINGS_PLANS_COVERAGE"
  }' \
  --notifications-with-subscribers '[{
    "Notification": {
      "NotificationType": "ACTUAL",
      "ComparisonOperator": "LESS_THAN",
      "Threshold": 70
    },
    "Subscribers": [{
      "SubscriptionType": "EMAIL",
      "Address": "finops@<org-domain>"
    }]
  }]'
```

## <org>-specific context

### Karpenter + Spot + SP interaction

```
Karpenter scheduling priority:
1. Spot instances (cheapest, ~60-70% of fleet)
2. On-Demand covered by Savings Plans (baseline)
3. Pure On-Demand (burst / uncovered)
```

- Karpenter **does not know about SPs** — it just launches instances
- AWS automatically applies SP discount to On-Demand instances
- SP covers the On-Demand portion that Karpenter provisions when Spot unavailable

### Graviton + x86 mix

Compute SP covers **both** architectures. No need for separate SPs per arch.

```
Compute SP applies to:
- m6i.xlarge (x86) ✅
- m6g.xlarge (Graviton) ✅
- m7g.large (Graviton) ✅
- c6g.2xlarge (Graviton) ✅
```

EC2 Instance SP would lock to a specific family (e.g., `m6g` in `us-east-1`). Since Karpenter diversifies across families, **Compute SP is the <org> default**.

### Renewal calendar

Track SP expiration dates. Set reminders 60 days before expiry:

```bash
aws savingsplans describe-savings-plans \
  --query "savingsPlans[].{ID:savingsPlanId,End:end,Commitment:commitment.amount,Type:savingsPlanType}" \
  --output table
```

## CUR query — SP effectiveness

```sql
SELECT
  savings_plan_savings_plan_a_r_n AS sp_arn,
  SUM(savings_plan_savings_plan_effective_cost) AS effective_cost,
  SUM(savings_plan_total_commitment_to_date) AS committed,
  ROUND(
    SUM(savings_plan_savings_plan_effective_cost) /
    NULLIF(SUM(savings_plan_total_commitment_to_date), 0) * 100, 2
  ) AS utilization_pct
FROM cur_table
WHERE year = '2026' AND month = '5'
  AND savings_plan_savings_plan_a_r_n IS NOT NULL
GROUP BY 1
ORDER BY utilization_pct ASC;
```

## Purchase checklist

Before buying any SP:

1. **Analyze 60-day lookback** — `get-savings-plans-purchase-recommendation`
2. **Check Spot ratio** — if Spot covers >70%, SP baseline is small
3. **Verify utilization of existing SPs** — don't stack if current <90% utilized
4. **Check expiration calendar** — upcoming expirations may already cover the gap
5. **NPV calculation** — No Upfront vs All Upfront given current interest rates
6. **Cash flow approval** — finance team sign-off for All Upfront or 3-year
7. **Start small** — buy 50% of recommendation, observe 30 days, then top up

## Anti-patterns

- ❌ **100% SP coverage** — over-commits, wastes money when Spot is available
- ❌ **All Upfront without cash flow analysis** — ties up capital that may earn more elsewhere
- ❌ **Forgetting renewal dates** — SP expires, On-Demand costs spike overnight
- ❌ **EC2 Instance SP with Karpenter** — Karpenter diversifies families; locked SP may go unused
- ❌ **Buying SP based on peak usage** — commit to baseline only, not peak
- ❌ **3-year term for new/uncertain workloads** — use 1-year until pattern stabilizes
- ❌ **Ignoring Spot in coverage math** — Spot is already discounted, don't double-count
- ❌ **No utilization monitoring** — buying and forgetting leads to waste
- ❌ **Purchasing SP for workloads migrating to EKS** — wait until migration completes and baseline stabilizes

## Reference

- AWS Savings Plans docs: https://docs.aws.amazon.com/savingsplans/
- Cost Explorer SP recommendations: https://docs.aws.amazon.com/cost-management/latest/userguide/ce-savings-plans.html
- Related skills: `cost-explorer`, `eks-management`, `ec2-rightsizing-patterns`
- Related steering: `aws-tag-policies` (cost allocation)
