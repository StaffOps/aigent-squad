---
name: ec2-rightsizing-patterns
description: Use when right-sizing EC2 instances, analyzing Compute Optimizer recommendations, transitioning instance families, or optimizing Karpenter NodePools. Covers CPU+memory utilization analysis, instance family transitions (m5→m6g→m7g), burstable vs general purpose decision tree, ScaleOps integration, and <org> Karpenter+Bottlerocket context.
keywords: [ec2-rightsizing, ec2, rightsizing, "ec2 rightsizing", karpenter, cpu]
---
# EC2 Right-Sizing Patterns

Framework for identifying and executing instance right-sizing across <org>'s EKS clusters and standalone EC2.

## Right-sizing signals

| Signal | Source | Threshold |
|--------|--------|-----------|
| CPU utilization | CloudWatch / OTel | <40% avg over 14 days → oversized |
| Memory utilization | CloudWatch Agent / OTel | <40% avg over 14 days → oversized |
| Network throughput | CloudWatch | Consistently below instance limit → smaller works |
| Disk IOPS | CloudWatch | Below provisioned → downsize EBS or instance |

**CRITICAL**: Never right-size on CPU alone. Memory is often the binding constraint for JVM/.NET workloads.

## AWS Compute Optimizer

### Get recommendations

```bash
aws compute-optimizer get-ec2-instance-recommendations \
  --region us-east-1 \
  --filters '[{"name":"Finding","values":["OVER_PROVISIONED"]}]' \
  --query 'instanceRecommendations[].{
    InstanceId: instanceArn,
    Current: currentInstanceType,
    Recommended: recommendationOptions[0].instanceType,
    CPUMax: utilizationMetrics[?name==`CPU`].value | [0],
    MemMax: utilizationMetrics[?name==`MEMORY`].value | [0]
  }' \
  --output table
```

### Enable enhanced metrics (memory)

Compute Optimizer needs memory metrics. Without CloudWatch Agent or OTel infra metrics, it only sees CPU:

```bash
# Check if enhanced infrastructure metrics are enabled
aws compute-optimizer get-enrollment-status
```

For EKS nodes with Bottlerocket: memory metrics come from `kubelet` cadvisor (scraped by vmagent). Compute Optimizer requires CloudWatch Agent OR opt-in to enhanced metrics.

### Export recommendations

```bash
aws compute-optimizer export-ec2-instance-recommendations \
  --s3-destination-config bucket=<org>-finops-reports,keyPrefix=compute-optimizer/ \
  --file-format Csv \
  --region us-east-1
```

## Karpenter consolidation

Karpenter handles right-sizing at the **node level** automatically:

### How it works

```
1. Pod requests define minimum node size
2. Karpenter provisions smallest instance that fits pending pods
3. Consolidation: if node utilization drops, Karpenter:
   a. Cordons the node
   b. Drains pods (respecting PDBs)
   c. Terminates the instance
   d. Pods reschedule on better-sized nodes
```

### Karpenter consolidation policy (<org>)

```yaml
apiVersion: karpenter.sh/v1beta1
kind: NodePool
metadata:
  name: default
spec:
  disruption:
    consolidationPolicy: WhenUnderutilized
    consolidateAfter: 30s
```

### Key insight

With Karpenter, **pod resource requests ARE the right-sizing lever**. If pods request too much → nodes are oversized. Fix at the pod level, Karpenter follows.

## ScaleOps (K8s VPA-equivalent)

<org> uses **ScaleOps** for pod-level right-sizing:

| Feature | ScaleOps | Vanilla VPA |
|---------|----------|-------------|
| Metrics source | Real utilization (P95/P99) | Limited history |
| Restart policy | Gradual, respects PDBs | Evicts pods |
| Recommendations | Continuous, dashboard | Manual apply |
| Integration | Karpenter-aware | Not aware |

### How ScaleOps feeds into node right-sizing

```
ScaleOps adjusts pod requests → Karpenter sees lower total requests →
Karpenter consolidates to smaller nodes → cost decreases
```

This is the **primary right-sizing mechanism** at <org>. Manual EC2 right-sizing is secondary.

## Memory metrics collection

### Option 1: OTel infrastructure metrics (<org> standard)

vmagent scrapes kubelet/cadvisor → VictoriaMetrics:

```promql
# Node memory utilization
1 - (
  node_memory_MemAvailable_bytes{cluster="<org>-eks-prd"}
  / node_memory_MemTotal_bytes{cluster="<org>-eks-prd"}
) * 100
```

### Option 2: CloudWatch Agent (standalone EC2)

For non-EKS instances, CloudWatch Agent provides memory metrics:

```json
{
  "metrics": {
    "namespace": "CWAgent",
    "metrics_collected": {
      "mem": {
        "measurement": ["mem_used_percent"],
        "metrics_collection_interval": 60
      }
    }
  }
}
```

### Query memory utilization (CloudWatch)

```bash
aws cloudwatch get-metric-statistics \
  --namespace CWAgent \
  --metric-name mem_used_percent \
  --dimensions Name=InstanceId,Value=i-0123456789abcdef0 \
  --start-time 2026-05-15T00:00:00Z \
  --end-time 2026-05-29T00:00:00Z \
  --period 3600 \
  --statistics Average Maximum
```

## Instance family transitions

### Migration path (<org> recommended)

```
m5/c5 (Intel Skylake) → m6i/c6i (Intel Ice Lake, ~15% better price/perf)
                       → m6g/c6g (Graviton2, ~20% cheaper)
                       → m7g/c7g (Graviton3, ~25% cheaper vs m5)
```

### Family comparison

| Family | Arch | vCPU:Memory | Best for | <org> usage |
|--------|------|-------------|----------|-----------|
| `m5` | x86 (Skylake) | 1:4 | Legacy, avoid for new | Phasing out |
| `m6i` | x86 (Ice Lake) | 1:4 | x86-only workloads | Some nodes |
| `m6g` | arm64 (Graviton2) | 1:4 | General purpose | Primary |
| `m7g` | arm64 (Graviton3) | 1:4 | General purpose (newest) | Preferred |
| `c6g`/`c7g` | arm64 | 1:2 | Compute-intensive | Batch/ML |
| `r6g`/`r7g` | arm64 | 1:8 | Memory-intensive | Redis, JVM |
| `t3`/`t4g` | x86/arm64 | 1:4 (burstable) | Low/variable CPU | Dev only |

### Karpenter NodePool instance categories

```yaml
spec:
  template:
    spec:
      requirements:
        - key: karpenter.k8s.aws/instance-category
          operator: In
          values: ["m", "c", "r"]  # General, Compute, Memory
        - key: karpenter.k8s.aws/instance-generation
          operator: Gte
          values: ["6"]  # Only gen 6+
        - key: kubernetes.io/arch
          operator: In
          values: ["amd64", "arm64"]  # Both archs
```

## Burstable vs General Purpose decision tree

```
Average CPU utilization over 14 days?
├── <20% AND spikes <80% → T-family (burstable) — cheapest
├── <20% AND spikes >80% → T-family with unlimited credits (watch cost)
├── 20-60% steady → M-family (general purpose) — predictable
└── >60% steady → C-family (compute optimized) — best perf/$
```

### When T-family is appropriate

- DEV environments with low baseline
- CI/CD runners (idle most of the time, burst during builds)
- Small utility services (<10% avg CPU)

### When T-family is dangerous

- PRD workloads with unpredictable spikes (credit exhaustion → throttling)
- Anything latency-sensitive (burst credits deplete under load)
- Karpenter nodes (consolidation + burstable = unpredictable)

**<org> rule**: T-family only for standalone DEV EC2. Never for EKS nodes.

## <org>-specific patterns

### Karpenter NodePool configuration

```yaml
apiVersion: karpenter.sh/v1beta1
kind: NodePool
metadata:
  name: general
spec:
  template:
    spec:
      requirements:
        - key: karpenter.k8s.aws/instance-category
          operator: In
          values: ["m", "c", "r"]
        - key: karpenter.k8s.aws/instance-generation
          operator: Gte
          values: ["6"]
        - key: karpenter.k8s.aws/instance-size
          operator: In
          values: ["medium", "large", "xlarge", "2xlarge"]
        - key: kubernetes.io/arch
          operator: In
          values: ["amd64", "arm64"]
        - key: karpenter.sh/capacity-type
          operator: In
          values: ["spot", "on-demand"]
      nodeClassRef:
        name: bottlerocket
  disruption:
    consolidationPolicy: WhenUnderutilized
    consolidateAfter: 30s
  limits:
    cpu: "1000"
    memory: 2000Gi
```

### Bottlerocket AMI

All EKS nodes use Bottlerocket:
- Minimal OS (no shell, no package manager in production)
- Faster boot (~25s vs ~45s for AL2)
- Automatic security updates
- Lower memory overhead (~50MB vs ~200MB for AL2)

### Right-sizing workflow at <org>

```
1. ScaleOps adjusts pod requests (continuous)
2. Karpenter consolidates nodes (automatic)
3. Monthly review: Compute Optimizer for standalone EC2
4. Quarterly review: instance generation upgrades (m6g → m7g)
```

## CUR query — instance type cost breakdown

```sql
SELECT
  product_instance_type,
  pricing_term AS pricing,
  SUM(line_item_unblended_cost) AS cost,
  SUM(line_item_usage_amount) AS hours,
  ROUND(SUM(line_item_unblended_cost) / NULLIF(SUM(line_item_usage_amount), 0), 4) AS cost_per_hour
FROM cur_table
WHERE year = '2026' AND month = '5'
  AND line_item_product_code = 'AmazonEC2'
  AND product_instance_type IS NOT NULL
  AND line_item_line_item_type = 'Usage'
GROUP BY 1, 2
ORDER BY cost DESC
LIMIT 30;
```

## Anti-patterns

- ❌ **Right-sizing only on CPU** — memory is often the constraint; ignoring it causes OOM kills
- ❌ **Ignoring memory metrics** — Compute Optimizer without CloudWatch Agent gives incomplete picture
- ❌ **`m5.large` as default without analysis** — legacy habit; m7g.medium may suffice at half the cost
- ❌ **Manual instance type changes on EKS nodes** — let Karpenter handle it via NodePool requirements
- ❌ **T-family for production EKS nodes** — credit exhaustion under load causes throttling
- ❌ **Skipping generation upgrades** — m5→m6g is free 20% savings, no code change needed
- ❌ **Right-sizing during peak** — analyze 14-day P95, not point-in-time
- ❌ **Ignoring ScaleOps recommendations** — pod over-request is the root cause of node over-provisioning
- ❌ **Single instance size in NodePool** — restricts Karpenter's bin-packing efficiency
- ❌ **Not setting `consolidateAfter`** — nodes stay oversized indefinitely

## Reference

- AWS Compute Optimizer: https://docs.aws.amazon.com/compute-optimizer/
- Karpenter consolidation: https://karpenter.sh/docs/concepts/disruption/
- ScaleOps: https://www.scaleops.com/
- Related skills: `cost-explorer`, `savings-plans-strategy`, `eks-management`
- Related steering: `k8s-best-practices-bdc` (resource requests mandatory)
