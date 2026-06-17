# Terraform module: `bedrock-aip`

Creates **Application Inference Profiles (AIP)** — one per model — carrying
FinOps cost allocation tags. The app invokes the AIP ARN instead of the raw
model id, so Amazon Bedrock spend becomes filterable by tag in Cost Explorer.

Per-agent cost is **not** done with per-agent AIPs. It's derived from the
per-agent token metric (showback). See `.kiro/specs/27-bedrock-cost-attribution/`.

## What it creates

- 1 `aws_bedrock_inference_profile` per entry in `var.models`
- Each tagged with `CostProject`, `CostScope`, `Environment`, `CostCenter`
- `copy_from` points at the **system** inference profile (the `us.` one) —
  required because Claude Sonnet 4.5 has no on-demand on the bare
  foundation-model ARN, and `us.` adds cross-region routing.

## Usage

Cost allocation tags are defined **once** in the AWS provider's `default_tags`
(see `example/main.tf`), so they are NOT module inputs — every AIP inherits
them automatically.

```hcl
# Provider — single source of truth for cost/governance tags
provider "aws" {
  region = var.region
  default_tags {
    tags = {
      CostProject = "aigent-squad"
      CostScope   = "MONITORING"
      Environment = "PRD"
      CostCenter  = var.cost_center
      ManagedBy   = "terraform"
    }
  }
}

module "bedrock_aip" {
  source      = "../bedrock-aip"
  name_prefix = "aigent-squad"

  models = {
    sonnet45 = "arn:aws:bedrock:us-east-1:ACCOUNT:inference-profile/us.anthropic.claude-sonnet-4-5-20250929-v1:0"
  }
}
```

Discover the source ARNs:

```bash
aws bedrock list-inference-profiles --region us-east-1 \
  --query "inferenceProfileSummaries[?type=='SYSTEM_DEFINED'].[inferenceProfileId]" --output text
```

Then point the app at the AIP ARN:

```bash
BEDROCK_MODEL_ID=$(terraform output -raw bedrock_aip_arns | jq -r .sonnet45)
```

## Inputs

| Name | Type | Default | Description |
|------|------|---------|-------------|
| `name_prefix` | string | `aigent-squad` | Prefix for AIP names |
| `models` | map(string) | — | `model_key => system inference profile ARN` |
| `tags` | map(string) | `{}` | Extra per-AIP tags (cost tags come from provider `default_tags`) |

The module also adds `Module=bedrock-aip` and `Model=<key>` per AIP.

## Outputs

| Name | Description |
|------|-------------|
| `inference_profile_arns` | `model_key => AIP ARN` (use as `BEDROCK_MODEL_ID`) |
| `inference_profile_ids` | `model_key => AIP ID` |

## ⚠️ Manual prerequisite: activate cost allocation tags

Tags only show up in Cost Explorer / CUR **after** being activated in Billing
(one-time, per account — not manageable via Terraform/API):

1. AWS Console → **Billing and Cost Management** → **Cost allocation tags**
2. Find `CostProject`, `CostScope`, `Environment`, `CostCenter` under
   **User-defined cost allocation tags**
3. Select them → **Activate**
4. Wait up to 24h for data to populate (tags apply going forward, not retroactively)

## Cost attribution: AWS spend × per-agent token share

The AIP gives **authoritative spend per model** (Cost Explorer, filter by
`CostProject=aigent-squad`). The app gives the **per-agent share** via the
`agent_id`-labeled metric. Combine them:

```promql
# Per-agent share of estimated Bedrock cost (VictoriaMetrics / MetricsQL)
sum by (agent_id) (aigent_cost_estimated)
  / ignoring(agent_id) group_left
sum (aigent_cost_estimated)
```

Multiply each agent's share by the model's real Cost Explorer spend to get the
attributed cost per agent. The `aigent_cost_estimated` metric is already
weighted by input/output token prices, so it's the right rateio key (a query
that costs more tokens weighs more, matching how Bedrock bills).

> The app metric is an **estimate** (prices hardcoded in `bedrock.py`). Cost
> Explorer is the authoritative total; the metric only provides the *ratio*.
