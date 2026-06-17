# Terraform — AWS infrastructure for AIgent-squad

Provisions everything the supervisor pod needs to run on EKS, split into
composable modules:

| Module | Provisions |
|--------|------------|
| [`iam/`](./iam) | Single IRSA role + all capability policies (Bedrock, DynamoDB sessions, read-only inventory, optional Athena/CUR FinOps) |
| [`dynamodb/`](./dynamodb) | Conversation/session history table (`pk`/`sk`, TTL, PITR, on-demand) |
| [`bedrock/`](./bedrock) | VPC endpoints for `bedrock-runtime` + `bedrock`, optional invocation logging |
| [`bedrock-aip/`](./bedrock-aip) | Application Inference Profiles (1 per model) with FinOps cost allocation tags — per-model spend in Cost Explorer; per-agent via token metrics (spec 27) |

The [`example/`](./example) wires all modules together against an existing
EKS cluster + VPC.

```
terraform/
├── iam/          # IRSA role + policies
├── dynamodb/     # sessions table
├── bedrock/      # VPC endpoints + logging
├── bedrock-aip/  # cost-attribution inference profiles
└── example/      # reference composition (start here)
```

## What maps to what

The app's AWS usage (read from `src/core/`) drives the IAM policy:

| App code | AWS access | Where granted |
|----------|-----------|---------------|
| `core/bedrock.py` | `bedrock:InvokeModel` (Claude/Titan) | `iam/` → bedrock policy |
| `core/state_store.py` | `dynamodb:PutItem`/`GetItem`/`Query` | `iam/` → dynamodb policy (only write) |
| `core/adapters.py` `Boto3Adapter` | `ec2/rds/s3:Describe*`, `ce:Get*`, `iam:List*` | `iam/` → inventory policy |
| `core/adapters.py` `AthenaAdapter` | `athena/glue/s3` for CUR | `iam/` → athena-finops policy (**optional, off by default**) |

> **Read-only is law** (`docs/READ_ONLY_POLICY.md`). The only write
> permission granted anywhere is `dynamodb:PutItem` on the sessions
> table — required to persist conversation history.

## FinOps: Cost Explorer now, CUR/Athena later

FinOps runs against **Cost Explorer in the local account** (`ce:Get*`),
always enabled in the inventory policy.

CUR-based analysis via Athena is **disabled by default**
(`enable_athena_finops = false`). The CUR often lives in a separate
**payer account**, which requires cross-account access (a bucket policy
granted on the payer side, or CUR replication into this account) — a
**Phase 2** concern. When you have a CUR Athena target in-account, set
`enable_athena_finops = true` and supply the bucket/workgroup/database
variables.

## ⚠️ Manual prerequisite: Bedrock Model Access

Model access is **not manageable via Terraform** (no AWS API resource).

**One-time per account & region:**
1. AWS Console → Bedrock → **Model access** → **Manage model access**
2. Request: `Claude Sonnet 4.5` (or your chosen model) + `Titan Text Embeddings V2` (for KB/RAG)
3. Approval is instant for Anthropic + Amazon models.

```bash
aws bedrock list-foundation-models --region us-east-1 \
  --query 'modelSummaries[?contains(modelId, `claude-sonnet`)].modelId'
```

## Usage

### 1. Prerequisites
- EKS cluster with OIDC provider associated
- VPC with private subnets

### 2. Apply

```bash
cd terraform/example
terraform init
terraform plan
terraform apply
```

Fill the required variables (`vpc_id`, `private_subnet_ids`,
`eks_worker_security_group_id`, `eks_cluster_name`).

### 3. Wire to the Helm chart

```bash
helm install aigent-squad oci://your-registry/charts/aigent-squad \
  --set "serviceAccount.annotations.eks\.amazonaws\.com/role-arn=$(terraform output -raw irsa_role_arn)" \
  --set "env.DYNAMODB_SESSIONS_TABLE=$(terraform output -raw sessions_table_name)"
```

## Module: `iam/`

### Inputs

| Name | Type | Default | Description |
|------|------|---------|-------------|
| `name_prefix` | string | — | Prefix for role/policy names |
| `region` | string | `us-east-1` | Region (for Athena/Glue ARNs) |
| `account_id` | string | — | AWS account ID (pass `data.aws_caller_identity.current.account_id`) |
| `eks_oidc_provider_arn` | string | — | EKS OIDC provider ARN |
| `eks_oidc_provider_url` | string | — | OIDC provider URL (no `https://`) |
| `k8s_namespace` | string | `aigent-squad` | Pod namespace |
| `k8s_service_account` | string | `aigent-squad` | ServiceAccount name |
| `allowed_model_arns` | list(string) | Claude + Titan | Bedrock model ARN patterns |
| `sessions_table_arn` | string | — | DynamoDB sessions table ARN |
| `enable_athena_finops` | bool | `false` | Enable Athena/Glue/S3 CUR permissions |
| `athena_workgroup` | string | `primary` | Athena workgroup (if enabled) |
| `athena_database` | string | `""` | Glue/Athena DB (if enabled) |
| `cur_s3_bucket_arns` | list(string) | `[]` | CUR bucket ARNs (if enabled) |
| `athena_results_bucket_arn` | string | `""` | Athena results bucket ARN (if enabled) |
| `tags` | map(string) | `{}` | Tags |

### Outputs

| Name | Description |
|------|-------------|
| `role_arn` | IRSA role ARN (for SA annotation) |
| `role_name` | IRSA role name |
| `service_account_annotation` | Ready-to-paste annotation string |

## Module: `dynamodb/`

### Inputs

| Name | Type | Default | Description |
|------|------|---------|-------------|
| `name_prefix` | string | — | Prefix for tags |
| `table_name` | string | `agent-sessions` | Must match env `DYNAMODB_SESSIONS_TABLE` |
| `enable_point_in_time_recovery` | bool | `true` | Continuous backups |
| `enable_deletion_protection` | bool | `false` | Set `true` in PRD |
| `kms_key_arn` | string | `null` | CMK for SSE (null = AWS-owned key, no cost) |
| `tags` | map(string) | `{}` | Tags |

### Outputs

| Name | Description |
|------|-------------|
| `table_name` | Sessions table name |
| `table_arn` | Sessions table ARN (feeds `iam.sessions_table_arn`) |

## Module: `bedrock/`

### Inputs

| Name | Type | Default | Description |
|------|------|---------|-------------|
| `name_prefix` | string | — | Prefix for endpoint names |
| `region` | string | `us-east-1` | AWS region |
| `vpc_id` | string | — | VPC for endpoints |
| `subnet_ids` | list(string) | — | Private subnets |
| `security_group_ids` | list(string) | — | SG allowing ingress 443 from workers |
| `enable_invocation_logging` | bool | `false` | CloudWatch invocation logging |
| `log_retention_days` | number | `30` | CW log retention |
| `tags` | map(string) | `{}` | Tags |

### Outputs

| Name | Description |
|------|-------------|
| `vpc_endpoint_runtime_id` | bedrock-runtime endpoint ID |
| `vpc_endpoint_runtime_dns` | bedrock-runtime private DNS |
| `vpc_endpoint_control_id` | bedrock control-plane endpoint ID |
| `log_group_arn` | CW log group ARN (if logging enabled) |

## Validation

No local Terraform needed — run via Docker:

```bash
docker run --rm -v "$(pwd):/tf" -w /tf hashicorp/terraform:1.9 fmt -recursive
docker run --rm -v "$(pwd):/tf" -w /tf/example hashicorp/terraform:1.9 init -backend=false
docker run --rm -v "$(pwd):/tf" -w /tf/example hashicorp/terraform:1.9 validate
```

## Cost (per month, us-east-1)

| Resource | Cost |
|----------|------|
| IAM role + policies | $0 |
| DynamoDB (on-demand, low volume) | ~$0–1 + ~$0.20/GB/mo if PITR enabled |
| 2× VPC Interface Endpoints (bedrock + bedrock-runtime) | ~$14.40/mo each = **~$28.80** |
| Per-AZ endpoint network charge | ~$7.20/AZ/mo |
| CloudWatch logging (if enabled) | ~$0.50/GB ingested |

Bedrock invocations and Cost Explorer API calls (after the free tier)
are billed separately.

## Local development (no AWS)

`docker compose` uses DynamoDB Local + a local Redis — you do **not** use
these modules. Mount `~/.aws` for Bedrock calls and run `aws sso login`
on the host (see root `docker-compose.yaml`).

## Removing

```bash
cd terraform/example
terraform destroy
```

Manual Bedrock model access stays — revoke in the console if needed. If
`enable_deletion_protection = true` on DynamoDB, disable it before
destroy.
