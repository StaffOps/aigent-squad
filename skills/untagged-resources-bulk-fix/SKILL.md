---
name: untagged-resources-bulk-fix
description: Use when detecting and remediating untagged AWS resources at scale. Covers Resource Groups Tagging API, AWS Config required-tags rule, CUR queries for untagged cost, bulk tagging scripts (boto3), SCP enforcement, and <org> mandatory tag compliance.
keywords: [untagged-resources-bulk-fix, untagged, resources, bulk, fix, "untagged resources", "resources bulk", "bulk fix", cost, aws, cur, scp]
---
# Untagged Resources — Bulk Detection & Fix

Framework for finding, attributing, and tagging AWS resources missing mandatory <org> tags.

## Why untagged resources matter

- **Cost Explorer**: untagged resources appear in "No tag" bucket — invisible to FinOps
- **OTel enrichment**: pods without `CostCenter` label get NO telemetry enrichment (k8sattributesprocessor)
- **Accountability**: no owner → no optimization → waste accumulates
- **Compliance**: AWS Tag Policies reject non-compliant values but can't enforce tag presence on all resource types

## <org> mandatory tags

Per `aws-tag-policies` steering:

| Tag | Required | Format | Example |
|-----|----------|--------|---------|
| `Environment` | YES | Enum | `PRD`, `HML`, `DEV`, `BTC` |
| `CostCenter` | YES | `Platform-*` or `Program-*` | `<cost-center>` |
| `CostScope` | YES | Enum | `API`, `INFRASTRUCTURE` |
| `CostProject` | YES | UPPERCASE with `-` | `PEOPLE`, `RECEITA-FEDERAL` |
| `Name` | YES | Meaningful resource name | `DPM-PEOPLE-API-PRD` |

## Detection methods

### Method 1: Resource Groups Tagging API

Fastest way to find resources missing a specific tag:

```bash
# Find resources WITHOUT CostCenter tag
aws resourcegroupstaggingapi get-resources \
  --region us-east-1 \
  --tag-filters '[]' \
  --query "ResourceTagMappingList[?!contains(Tags[].Key, 'CostCenter')].ResourceARN" \
  --output text
```

### Method 2: AWS Config rule (continuous)

```json
{
  "ConfigRuleName": "required-tags",
  "Source": {
    "Owner": "AWS",
    "SourceIdentifier": "REQUIRED_TAGS"
  },
  "InputParameters": "{\"tag1Key\":\"Environment\",\"tag2Key\":\"CostCenter\",\"tag3Key\":\"CostScope\",\"tag4Key\":\"CostProject\",\"tag5Key\":\"Name\"}"
}
```

Query non-compliant resources:

```bash
aws configservice get-compliance-details-by-config-rule \
  --config-rule-name required-tags \
  --compliance-types NON_COMPLIANT \
  --query 'EvaluationResults[].EvaluationResultIdentifier.EvaluationResultQualifier.ResourceId' \
  --output text
```

### Method 3: CUR query (cost impact)

```sql
SELECT
  line_item_product_code AS service,
  line_item_resource_id AS resource_id,
  SUM(line_item_unblended_cost) AS monthly_cost
FROM cur_table
WHERE year = '2026' AND month = '5'
  AND (resource_tags_user_cost_center IS NULL OR resource_tags_user_cost_center = '')
  AND line_item_unblended_cost > 0
GROUP BY 1, 2
ORDER BY monthly_cost DESC
LIMIT 100;
```

### Method 4: Cost Explorer (quick view)

```bash
aws ce get-cost-and-usage \
  --time-period Start=2026-05-01,End=2026-06-01 \
  --granularity MONTHLY \
  --metrics UnblendedCost \
  --group-by Type=TAG,Key=CostCenter \
  --query "ResultsByTime[0].Groups[?Keys[0]=='CostCenter$'].Metrics.UnblendedCost.Amount"
```

Empty tag key (`CostCenter$` with no value) = untagged spend.

## Common offenders

| Resource type | Why untagged | Fix |
|---------------|--------------|-----|
| **SQS queues** | Created by SDKs without tag propagation | Tag via API after creation |
| **ECR repositories** | Created manually or by CI without tags | Bulk tag script |
| **EBS snapshots** | Auto-created by backups without tag inheritance | Lifecycle policy + tag copy |
| **S3 buckets** | Legacy, created before tag policy | Bulk tag |
| **CloudWatch Log Groups** | Auto-created by Lambda/ECS | Tag via API |
| **Lambda functions** | Quick prototypes, forgotten | Audit + tag |
| **Secrets Manager secrets** | Created via console | Tag via API |
| **EBS volumes** | Orphaned from terminated instances | Tag or delete |

## Bulk tagging via boto3

### Detection + tagging script

```python
"""Detect and tag untagged resources in a <org> AWS account."""
import boto3
from typing import Generator

MANDATORY_TAGS = ["Environment", "CostCenter", "CostScope", "CostProject", "Name"]
BATCH_SIZE = 20  # API limit for tag_resources()


def get_untagged_resources(region: str = "us-east-1") -> Generator[dict, None, None]:
    """Yield resources missing any mandatory tag."""
    client = boto3.client("resourcegroupstaggingapi", region_name=region)
    paginator = client.get_paginator("get_resources")

    for page in paginator.paginate():
        for resource in page["ResourceTagMappingList"]:
            existing_keys = {t["Key"] for t in resource.get("Tags", [])}
            missing = [k for k in MANDATORY_TAGS if k not in existing_keys]
            if missing:
                yield {
                    "arn": resource["ResourceARN"],
                    "existing_tags": resource.get("Tags", []),
                    "missing_tags": missing,
                }


def bulk_tag_resources(arns: list[str], tags: dict[str, str], region: str = "us-east-1"):
    """Tag up to 20 ARNs at a time."""
    client = boto3.client("resourcegroupstaggingapi", region_name=region)

    for i in range(0, len(arns), BATCH_SIZE):
        batch = arns[i : i + BATCH_SIZE]
        response = client.tag_resources(
            ResourceARNList=batch,
            Tags=tags,
        )
        failed = response.get("FailedResourcesMap", {})
        if failed:
            print(f"Failed to tag: {list(failed.keys())}")


def audit_report(region: str = "us-east-1"):
    """Generate audit report of untagged resources."""
    untagged = list(get_untagged_resources(region))
    print(f"Found {len(untagged)} resources missing mandatory tags\n")

    by_service = {}
    for r in untagged:
        service = r["arn"].split(":")[2]  # Extract service from ARN
        by_service.setdefault(service, []).append(r)

    for service, resources in sorted(by_service.items(), key=lambda x: -len(x[1])):
        print(f"{service}: {len(resources)} untagged")
        for r in resources[:3]:
            print(f"  {r['arn']} — missing: {r['missing_tags']}")
        if len(resources) > 3:
            print(f"  ... and {len(resources) - 3} more")


if __name__ == "__main__":
    audit_report()
```

### Owner attribution via CloudTrail

Before tagging, identify WHO created the resource:

```bash
aws cloudtrail lookup-events \
  --lookup-attributes AttributeKey=ResourceName,AttributeValue=<resource-id> \
  --query 'Events[0].{User:Username,Time:EventTime,Event:EventName}' \
  --output table
```

This prevents blind tagging — the creator's team determines the correct `CostCenter`.

## Multi-account detection

For organizations with multiple accounts:

```python
"""Walk all accounts in the org and detect untagged resources."""
import boto3

def get_org_accounts():
    """List all active accounts in the organization."""
    org = boto3.client("organizations")
    paginator = org.get_paginator("list_accounts")
    for page in paginator.paginate():
        for account in page["Accounts"]:
            if account["Status"] == "ACTIVE":
                yield account["Id"], account["Name"]


def assume_role(account_id: str, role_name: str = "OrganizationAccountAccessRole"):
    """Assume role in target account."""
    sts = boto3.client("sts")
    response = sts.assume_role(
        RoleArn=f"arn:aws:iam::{account_id}:role/{role_name}",
        RoleSessionName="untagged-audit",
    )
    creds = response["Credentials"]
    return boto3.Session(
        aws_access_key_id=creds["AccessKeyId"],
        aws_secret_access_key=creds["SecretAccessKey"],
        aws_session_token=creds["SessionToken"],
    )
```

## SCP enforcement (preventive)

Tag policies prevent creation of resources with invalid tag values but **cannot enforce tag presence** for all resource types. Use SCPs for critical services:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "DenyEC2WithoutTags",
      "Effect": "Deny",
      "Action": ["ec2:RunInstances"],
      "Resource": ["arn:aws:ec2:*:*:instance/*"],
      "Condition": {
        "Null": {
          "aws:RequestTag/CostCenter": "true"
        }
      }
    },
    {
      "Sid": "DenyS3WithoutTags",
      "Effect": "Deny",
      "Action": ["s3:CreateBucket"],
      "Resource": "*",
      "Condition": {
        "Null": {
          "aws:RequestTag/CostCenter": "true"
        }
      }
    }
  ]
}
```

**Limitation**: SCPs can't cover ALL resource types. Some services don't support tag-on-create. Detection + remediation is still needed.

## AWS Tag Editor (manual UI)

For one-off fixes, AWS Tag Editor in the console:

1. Resource Groups → Tag Editor
2. Filter by region + resource type
3. Filter "Tag key: CostCenter — Not tagged"
4. Select resources → Manage tags → Add tags
5. Apply

Good for: <50 resources, visual verification needed.
Bad for: >50 resources, recurring (use script instead).

## Remediation workflow

```
1. DETECT: Weekly CUR query + Config rule → list of untagged resources
2. ATTRIBUTE: CloudTrail lookup → identify creator/team
3. NOTIFY: Slack message to team → "You have N untagged resources costing $X/month"
4. FIX: Team tags via script or Tag Editor (7-day SLA)
5. ESCALATE: After 7 days → DevOps bulk-tags with best-guess + flags for review
6. PREVENT: SCP + Terraform modules enforce tags on new resources
```

## CUR — total untagged cost

```sql
SELECT
  COALESCE(resource_tags_user_cost_center, 'UNTAGGED') AS cost_center,
  SUM(line_item_unblended_cost) AS total_cost,
  COUNT(DISTINCT line_item_resource_id) AS resource_count
FROM cur_table
WHERE year = '2026' AND month = '5'
  AND line_item_unblended_cost > 0
GROUP BY 1
ORDER BY total_cost DESC;
```

## <org>-specific context

### Kyverno enforces K8s labels

On EKS, Kyverno blocks pods without mandatory labels. This covers the K8s side. The gap is **non-K8s AWS resources** (S3, SQS, RDS, etc.) — those need the detection workflow above.

### Terraform modules enforce tags

All <org> Terraform modules (`04-TERRAFORM/MODULES/`) include mandatory tag variables:

```hcl
variable "tags" {
  type = map(string)
  validation {
    condition = alltrue([
      contains(keys(var.tags), "Environment"),
      contains(keys(var.tags), "CostCenter"),
      contains(keys(var.tags), "CostScope"),
      contains(keys(var.tags), "CostProject"),
      contains(keys(var.tags), "Name"),
    ])
    error_message = "Missing mandatory tags: Environment, CostCenter, CostScope, CostProject, Name"
  }
}
```

Resources created outside Terraform (console, SDK, CLI) bypass this — hence the detection scripts.

### Cost impact visibility

Untagged resources in Cost Explorer appear under empty tag value. Track monthly:

```bash
aws ce get-cost-and-usage \
  --time-period Start=2026-05-01,End=2026-06-01 \
  --granularity MONTHLY \
  --metrics UnblendedCost \
  --filter '{"Tags":{"Key":"CostCenter","Values":[""],"MatchOptions":["ABSENT"]}}' \
  --group-by Type=DIMENSION,Key=SERVICE
```

## Anti-patterns

- ❌ **Skipping "temporary" resources** — temporary becomes permanent; tag everything
- ❌ **Bulk-tagging without CloudTrail audit** — wrong CostCenter is worse than no CostCenter
- ❌ **Fixing tags without notifying the team** — they'll create more untagged resources
- ❌ **One-time cleanup without prevention** — SCPs + Terraform validation prevent recurrence
- ❌ **Ignoring small-cost resources** — 500 untagged $2/month resources = $1000/month invisible cost
- ❌ **Tag policies alone** — they validate values but can't enforce presence on all resource types
- ❌ **Manual Tag Editor for recurring issues** — automate with boto3 script on schedule
- ❌ **Placeholder tags** (`CostCenter: "TODO"`, `CostProject: "TBD"`) — breaks reporting equally
- ❌ **Tagging only at infra level, not K8s labels** — breaks OTel enrichment pipeline

## Reference

- AWS Resource Groups Tagging API: https://docs.aws.amazon.com/resourcegroupstagging/latest/APIReference/
- AWS Config required-tags rule: https://docs.aws.amazon.com/config/latest/developerguide/required-tags.html
- AWS Tag Policies: https://docs.aws.amazon.com/organizations/latest/userguide/orgs_manage_policies_tag-policies.html
- Related skills: `cost-explorer`, `terraform-modules-bdc`
- Related steering: `aws-tag-policies` (mandatory tags list + enforcement)
