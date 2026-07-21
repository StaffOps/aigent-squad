---
name: cost-explorer
description: AWS Cost Explorer and CUR analysis patterns. Use when investigating cost spikes, identifying optimization opportunities, generating finops reports, or setting up cost allocation tags. Covers Cost Explorer queries, Cost and Usage Report (CUR) via Athena, common waste patterns, <org>-specific cost drivers.
keywords: [cost-explorer, cost, explorer, "cost explorer", aws, cur]
---
# AWS Cost Explorer & FinOps

Cost analysis and optimization patterns for <org>'s AWS environments.

## Where to find costs

| Tool | Use for | Latency |
|------|---------|---------|
| **AWS Cost Explorer** | Quick exploration, daily/monthly trends | ~24h delay |
| **AWS Cost & Usage Report (CUR)** | Detailed analysis, custom queries | ~24h delay, full granularity |
| **AWS Budgets** | Threshold alerts | Real-time |
| **CloudWatch Billing Metrics** | Estimated charges | ~6h delay |
| **Trusted Advisor** | Optimization recommendations | Automated |

For deep analysis, **CUR via Athena** is the source of truth.

## Cost Explorer (CE) — quick analysis

### Common questions and queries

#### "What's costing the most this month?"

```bash
aws ce get-cost-and-usage \
  --time-period Start=2026-05-01,End=2026-06-01 \
  --granularity MONTHLY \
  --metrics BlendedCost \
  --group-by Type=DIMENSION,Key=SERVICE \
  --query "ResultsByTime[0].Groups[?Metrics.BlendedCost.Amount>'1000'] | [].[Keys[0], Metrics.BlendedCost.Amount]" \
  --output table
```

#### "What's growing fastest?"

```bash
aws ce get-cost-and-usage \
  --time-period Start=2026-04-01,End=2026-06-01 \
  --granularity MONTHLY \
  --metrics BlendedCost \
  --group-by Type=DIMENSION,Key=SERVICE
```

Compare Apr vs May. Sort by % growth.

#### "What's the cost of this account/team?"

Tag-based, requires cost allocation tags (see below):

```bash
aws ce get-cost-and-usage \
  --time-period Start=2026-05-01,End=2026-06-01 \
  --granularity MONTHLY \
  --metrics BlendedCost \
  --filter '{
    "Tags": {
      "Key": "CostCenter",
      "Values": ["devops"]
    }
  }'
```

#### "What's the on-demand vs spot ratio for EC2?"

```bash
aws ce get-cost-and-usage \
  --time-period Start=2026-05-01,End=2026-06-01 \
  --granularity MONTHLY \
  --metrics UnblendedCost UsageQuantity \
  --filter '{"Dimensions": {"Key": "SERVICE", "Values": ["Amazon Elastic Compute Cloud - Compute"]}}' \
  --group-by Type=DIMENSION,Key=PURCHASE_TYPE
```

## Cost allocation tags

### <org> standard tags

| Tag | Required | Purpose |
|-----|----------|---------|
| `CostCenter` | YES | Team/department charged |
| `Project` | YES | Logical project (e.g., `<org>-telemetry-helper`) |
| `Environment` | YES | `dev`, `hml`, `prd`, `core` |
| `ManagedBy` | YES | `terraform`, `helm`, `manual` |
| `Owner` | Recommended | Email or team alias |

### Activate cost allocation tags
```bash
# In AWS Billing console (Tags can take ~24h to propagate)
# OR via CLI:
aws ce update-cost-allocation-tags-status \
  --cost-allocation-tags-status TagKey=CostCenter,Status=Active
```

Once activated, tag values appear in Cost Explorer and CUR.

### k8sattributesprocessor at <org> requires `CostCenter`

The OTel Collector's k8sattributes filter requires pods to have `CostCenter` label. This serves dual purpose:
1. Cost allocation in AWS
2. Telemetry enrichment

```yaml
metadata:
  labels:
    CostCenter: devops
    Project: <org>-telemetry-helper
    Environment: dev
```

## CUR (Cost and Usage Report)

### What is CUR
- Most detailed billing data (line-item granular)
- Stored in S3 as Parquet
- Queryable via Athena
- Updated multiple times per day

### Setup
1. Configure CUR in AWS Billing Console
2. S3 destination + Athena integration
3. Glue crawler builds catalog
4. Query via Athena

### Common Athena queries

#### Top 20 services by cost (current month)

```sql
SELECT
  line_item_product_code AS service,
  SUM(line_item_unblended_cost) AS cost
FROM cur_table
WHERE year = '2026'
  AND month = '5'
GROUP BY line_item_product_code
ORDER BY cost DESC
LIMIT 20;
```

#### Cost by tag

```sql
SELECT
  resource_tags_user_costcenter AS cost_center,
  SUM(line_item_unblended_cost) AS cost
FROM cur_table
WHERE year = '2026' AND month = '5'
  AND resource_tags_user_costcenter IS NOT NULL
GROUP BY resource_tags_user_costcenter
ORDER BY cost DESC;
```

#### EC2 cost by instance type

```sql
SELECT
  product_instance_type AS instance_type,
  SUM(line_item_unblended_cost) AS cost,
  SUM(line_item_usage_amount) AS hours
FROM cur_table
WHERE year = '2026' AND month = '5'
  AND line_item_product_code = 'AmazonEC2'
  AND product_instance_type IS NOT NULL
GROUP BY product_instance_type
ORDER BY cost DESC;
```

#### Untagged resources (cost leakage)

```sql
SELECT
  line_item_product_code,
  line_item_resource_id,
  SUM(line_item_unblended_cost) AS cost
FROM cur_table
WHERE year = '2026' AND month = '5'
  AND (resource_tags_user_costcenter IS NULL OR resource_tags_user_costcenter = '')
  AND line_item_unblended_cost > 10
GROUP BY 1, 2
ORDER BY cost DESC
LIMIT 50;
```

These resources are charged but not allocated to a team — chase down owners.

#### S3 storage class breakdown

```sql
SELECT
  product_storage_class AS storage_class,
  SUM(line_item_usage_amount) AS gb_months,
  SUM(line_item_unblended_cost) AS cost
FROM cur_table
WHERE year = '2026' AND month = '5'
  AND line_item_product_code = 'AmazonS3'
  AND product_storage_class IS NOT NULL
GROUP BY product_storage_class
ORDER BY cost DESC;
```

## Common waste patterns

### Pattern 1: Underutilized EC2/EKS nodes

Symptom: cost high, CPU/memory utilization <40%.

Detection:
```bash
# Get cluster utilization
kubectl top nodes
```

Fix:
- Right-size with Karpenter consolidation
- Switch to smaller instance family
- Use Spot for interruptible workloads
- Use Graviton (arm64) — 20-40% cheaper

### Pattern 2: Idle RDS/ElastiCache

Symptom: low connection count, low CPU.

Detection:
```bash
aws cloudwatch get-metric-statistics \
  --namespace AWS/RDS \
  --metric-name DatabaseConnections \
  --dimensions Name=DBInstanceIdentifier,Value=my-db \
  --start-time 2026-05-01T00:00:00Z \
  --end-time 2026-06-01T00:00:00Z \
  --period 86400 \
  --statistics Average
```

Fix:
- Stop RDS instances at night (if dev/test)
- Downsize instance class
- Switch to Aurora Serverless v2

### Pattern 3: NAT Gateway costs

Symptom: high `Amazon EC2 - NatGateway` charges.

Fixes:
- VPC Endpoints for S3/DynamoDB (free, no NAT traffic)
- VPC Endpoints (Interface) for other AWS APIs (cheaper than NAT for high traffic)
- Reduce cross-AZ traffic (deploy stateless services in all AZs)

### Pattern 4: Untagged resources

Symptom: untagged resources accumulate, no team accountable, can't optimize.

Fix:
- Enforce tagging via SCP / Service Control Policies
- Run weekly Athena query → ticket the owners

### Pattern 5: Snapshots / unused volumes

Symptom: EBS snapshots from years ago, unattached EBS volumes.

```bash
# Unattached EBS volumes
aws ec2 describe-volumes --filters Name=status,Values=available

# Old snapshots
aws ec2 describe-snapshots --owner-ids self \
  --query "Snapshots[?StartTime<'2025-01-01'].[SnapshotId,VolumeSize,StartTime]"
```

### Pattern 6: VictoriaMetrics / observability cardinality

High-cardinality metrics → more vmstorage IOPS → more EBS cost. See `vm-cardinality-management` skill.

## Savings Plans / Reserved Instances

### Compute Savings Plans
- Most flexible (covers any region, any family)
- 1-year or 3-year commit
- Up to 66% discount
- Best for **steady baseline** workloads

### EC2 Instance Savings Plans
- Specific family + region
- Higher discount
- Best for **predictable** workloads

### Pre-purchase analysis
```bash
aws ce get-savings-plans-purchase-recommendation \
  --term-in-years ONE_YEAR \
  --payment-option NO_UPFRONT \
  --recommendation-type-set ALL
```

### Coverage check
```bash
aws ce get-savings-plans-coverage \
  --time-period Start=2026-05-01,End=2026-06-01 \
  --granularity MONTHLY
```

If coverage <70% on baseline workloads, consider buying more SPs.

## Forecasting

### Cost Explorer forecast (built-in)
```bash
aws ce get-cost-forecast \
  --time-period Start=2026-06-01,End=2026-12-01 \
  --metric BLENDED_COST \
  --granularity MONTHLY
```

Returns: monthly forecast based on historical trend.

### Custom forecasting
For more accurate forecasts, export CUR to a forecasting tool (Prophet, AWS QuickSight, etc.).

## AWS Budgets

### Set up alert
```bash
aws budgets create-budget \
  --account-id <ACCOUNT_ID> \
  --budget '{
    "BudgetName": "monthly-spend-prd",
    "BudgetLimit": {"Amount": "50000", "Unit": "USD"},
    "TimeUnit": "MONTHLY",
    "BudgetType": "COST"
  }' \
  --notifications-with-subscribers '[{
    "Notification": {
      "NotificationType": "ACTUAL",
      "ComparisonOperator": "GREATER_THAN",
      "Threshold": 80
    },
    "Subscribers": [{
      "SubscriptionType": "EMAIL",
      "Address": "finops@<org-domain>"
    }]
  }]'
```

### Anomaly detection
AWS Cost Anomaly Detection (uses ML) — free, no setup beyond enabling.

## FinOps maturity model

| Stage | Characteristics |
|-------|----------------|
| **Crawl** | Tag inventory, monthly reports, ad-hoc cleanup |
| **Walk** | Allocation, budgets per team, automated reports, SP coverage |
| **Run** | Showback/chargeback, unit economics, FinOps as continuous practice |

<org> is currently at **Walk** stage — moving toward Run.

## Useful CE filters

```bash
# By specific resource ID
--filter '{"Dimensions": {"Key": "RESOURCE_ID", "Values": ["i-1234567890abcdef0"]}}'

# By usage type (e.g., EBS gp3)
--filter '{"Dimensions": {"Key": "USAGE_TYPE", "Values": ["EBS:VolumeUsage.gp3"]}}'

# Exclude credits and refunds
--filter '{"Not": {"Dimensions": {"Key": "RECORD_TYPE", "Values": ["Credit", "Refund"]}}}'

# Multiple services
--filter '{"Dimensions": {"Key": "SERVICE", "Values": ["Amazon Elastic Compute Cloud - Compute", "Amazon S3"]}}'
```

## Reference

- AWS Cost Explorer API: https://docs.aws.amazon.com/aws-cost-management/
- CUR docs: https://docs.aws.amazon.com/cur/
- FinOps Foundation: https://www.finops.org/
- Trusted Advisor: https://aws.amazon.com/premiumsupport/technology/trusted-advisor/
- Related: `eks-management`, `iam-patterns`
