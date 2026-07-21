---
name: security-hub-patterns
description: Use when configuring AWS Security Hub at platform level — enabling multi-account/region aggregator, defining automation rules, integrating third-party scanners via BatchImportFindings, or working with ASFF format. Covers Security Hub service architecture, Organizations integration, custom insights, EventBridge integration, <org> multi-account context.
keywords: [security-hub, security, hub, "security hub", aws, asff]
---
# AWS Security Hub — Platform Operations

Security Hub as a Cloud Security Posture Management (CSPM) service. This skill covers **how to operate Security Hub as a platform service** — architecture, multi-account setup, integrations, automation.

> For standards/controls content and remediation → `security/aws-ftr-compliance`
> For finding triage, SLAs, lifecycle → `security/security-hub-findings-mgmt`

## Position in AWS security toolkit

| Service | Role | Feeds into Security Hub? |
|---------|------|--------------------------|
| **Security Hub** | Central aggregator + CSPM | — (this is the hub) |
| **GuardDuty** | Threat detection (runtime) | ✅ |
| **Inspector** | Vulnerability scanning (EC2/ECR/Lambda) | ✅ |
| **Config** | Resource compliance rules | ✅ |
| **Macie** | S3 data classification (PII) | ✅ |
| **IAM Access Analyzer** | External access detection | ✅ |
| **Firewall Manager** | WAF/SG policy management | ✅ |

Security Hub doesn't detect threats itself — it aggregates, normalizes (ASFF), and enables automation on findings from all sources.

## Service architecture

### Regional by design

Security Hub runs **per-region per-account**. Each region has its own findings store, standards evaluations, automation rules, and integrations.

### Cross-region aggregation

Designate one **aggregation region** (typically `us-east-1`) to consolidate findings:

```bash
aws securityhub create-finding-aggregator \
  --region us-east-1 \
  --region-linking-mode ALL_REGIONS
```

### Findings ingestion sources

- **Native AWS services**: GuardDuty, Inspector, Config, Macie, Access Analyzer, Firewall Manager
- **Third-party**: Prowler, Trivy, ScoutSuite, Qualys, etc. (via `BatchImportFindings`)
- **Custom**: any internal tool producing ASFF-formatted findings

### Storage

- Default retention: **90 days** (auto-archived after)
- Archived findings: `RecordState: ARCHIVED` — queryable but not in active dashboards
- Long-term: EventBridge → S3 (Parquet/JSON) via Firehose or Lambda

## Multi-account setup (Organizations)

### Delegated administrator pattern

```
AWS Organization
├── Management account (payer) → enables trusted service
├── Security account (delegated admin) ← operates Security Hub
├── Production account (<ACCOUNT_ID>) ← member
├── Dev account ← member
└── Other accounts ← members
```

### Enable delegated admin

```bash
# From management account
aws organizations enable-aws-service-access \
  --service-principal securityhub.amazonaws.com

aws securityhub enable-organization-admin-account \
  --admin-account-id <SECURITY_ACCOUNT_ID>
```

### Auto-enable for new member accounts

```bash
# From delegated admin account
aws securityhub update-organization-configuration \
  --auto-enable \
  --auto-enable-standards DEFAULT
```

### <org> context

- Production account: `<ACCOUNT_ID>`
- Aggregation region: `us-east-1` (all clusters here)
- 3 EKS clusters generate findings on underlying AWS resources (EC2, EBS, SGs, IAM)

## Standards and controls

| Standard | Focus | Controls |
|----------|-------|----------|
| AWS FSBP | Broad AWS service security | ~300+ |
| CIS AWS Foundations 1.4 | Identity, logging, networking | ~50 |
| CIS AWS Foundations 3.0 | Updated CIS | ~60 |
| NIST SP 800-53 | US federal compliance | ~200+ |
| PCI DSS 3.2.1 | Payment card data | ~150 |

### Disable specific controls (with justification)

```bash
aws securityhub update-standards-control \
  --standards-control-arn "arn:aws:securityhub:us-east-1:<ACCOUNT_ID>:control/aws-foundational-security-best-practices/v/1.0.0/EC2.8" \
  --control-status DISABLED \
  --disabled-reason "Compensating control: IMDSv2 enforced via launch template. Reviewed 2026-05-29."
```

> For detailed standard-by-standard coverage → `security/aws-ftr-compliance`

## ASFF (AWS Security Finding Format)

### Required fields

| Field | Description |
|-------|-------------|
| `SchemaVersion` | Always `"2018-10-08"` |
| `Id` | Unique finding ID |
| `ProductArn` | Product generating the finding |
| `GeneratorId` | Rule/check identifier |
| `AwsAccountId` | Account where detected |
| `CreatedAt` / `UpdatedAt` | ISO8601 timestamps |
| `Title` / `Description` | Human-readable |
| `Severity` | `{Label, Normalized}` |
| `Resources` | Affected AWS resources array |
| `Types` | Finding type taxonomy |

### Severity mapping

| Label | Normalized | Meaning |
|-------|-----------|---------|
| `CRITICAL` | 90-100 | Immediate action |
| `HIGH` | 70-89 | Days |
| `MEDIUM` | 40-69 | Weeks |
| `LOW` | 1-39 | Track |
| `INFORMATIONAL` | 0 | No action |

### ASFF example (custom finding)

```json
{
  "SchemaVersion": "2018-10-08",
  "Id": "bdc/trivy/<harbor-project>-dotnet-api/CVE-2026-1234",
  "ProductArn": "arn:aws:securityhub:us-east-1:<ACCOUNT_ID>:product/<ACCOUNT_ID>/default",
  "GeneratorId": "trivy-container-scan",
  "AwsAccountId": "<ACCOUNT_ID>",
  "CreatedAt": "2026-05-29T01:00:00Z",
  "UpdatedAt": "2026-05-29T01:00:00Z",
  "Title": "CVE-2026-1234 in openssl 3.0.8",
  "Description": "Buffer overflow allows RCE. Fixed in 3.0.9.",
  "Severity": {"Label": "HIGH", "Normalized": 75},
  "Types": ["Software and Configuration Checks/Vulnerabilities/CVE"],
  "Resources": [{"Type": "Container", "Id": "<harbor-registry>/<harbor-project>/dotnet-api:abc123", "Region": "us-east-1"}],
  "Compliance": {"Status": "FAILED"},
  "Workflow": {"Status": "NEW"},
  "RecordState": "ACTIVE",
  "UserDefinedFields": {"CostCenter": "<cost-center>", "FixAvailable": "true"}
}
```

## Custom integrations (BatchImportFindings)

### Rate limits

- **100 findings** per call, **10 calls/second** per account per region
- Implement exponential backoff on `429 TooManyRequestsException`

### ProductArn

Custom findings use: `arn:aws:securityhub:<region>:<account-id>:product/<account-id>/default`

For third-party products, subscribe first:
```bash
aws securityhub enable-import-findings-for-product \
  --product-arn "arn:aws:securityhub:us-east-1::product/prowler/prowler"
```

### Python example

```python
import boto3
import time

client = boto3.client("securityhub", region_name="us-east-1")
ACCOUNT_ID = "<ACCOUNT_ID>"
PRODUCT_ARN = f"arn:aws:securityhub:us-east-1:{ACCOUNT_ID}:product/{ACCOUNT_ID}/default"

SEVERITY_MAP = {"CRITICAL": 95, "HIGH": 75, "MEDIUM": 50, "LOW": 20}


def import_findings(vulns: list[dict]) -> None:
    findings = [{
        "SchemaVersion": "2018-10-08",
        "Id": f"bdc/trivy/{v['target']}/{v['id']}",
        "ProductArn": PRODUCT_ARN,
        "GeneratorId": "trivy-container-scan",
        "AwsAccountId": ACCOUNT_ID,
        "CreatedAt": v["timestamp"],
        "UpdatedAt": v["timestamp"],
        "Title": f"{v['id']} in {v['pkg']} {v['version']}",
        "Description": v.get("desc", "N/A"),
        "Severity": {"Label": v["severity"], "Normalized": SEVERITY_MAP.get(v["severity"], 0)},
        "Types": ["Software and Configuration Checks/Vulnerabilities/CVE"],
        "Resources": [{"Type": "Container", "Id": v["target"], "Region": "us-east-1"}],
        "Workflow": {"Status": "NEW"},
        "RecordState": "ACTIVE",
    } for v in vulns]

    for i in range(0, len(findings), 100):
        resp = client.batch_import_findings(Findings=findings[i:i+100])
        if resp["FailedCount"] > 0:
            print(f"Failed: {resp['FailedFindings']}")
        time.sleep(0.1)
```

> For Trivy scan pipeline → `security/sbom-vulnerability-management`

## Automation rules

Auto-update findings based on criteria without Lambda (released 2023).

### Use cases

| Use case | Action |
|----------|--------|
| Suppress known false positives | `Workflow.Status = SUPPRESSED` |
| Escalate CRITICAL | Add `UserDefinedFields.Escalated = true` |
| Tag by team | Set `UserDefinedFields.Team` from resource tags |
| Auto-resolve passed | `Workflow.Status = RESOLVED` |

### Limits: 100 rules per account per region. Rule order matters (first match wins).

### CLI example

```bash
aws securityhub create-automation-rule \
  --rule-name "suppress-ec2-imdsv1-dev" \
  --rule-order 100 \
  --description "Suppress IMDSv1 in dev (compensating control)" \
  --criteria '{
    "GeneratorId": [{"Value": "aws-foundational-security-best-practices/v/1.0.0/EC2.8", "Comparison": "EQUALS"}],
    "AwsAccountId": [{"Value": "111111111111", "Comparison": "EQUALS"}]
  }' \
  --actions '[{
    "Type": "FINDING_FIELDS_UPDATE",
    "FindingFieldsUpdate": {
      "Workflow": {"Status": "SUPPRESSED"},
      "Note": {"Text": "Dev — IMDSv2 enforced via launch template.", "UpdatedBy": "automation-rule"}
    }
  }]'
```

### Terraform example

```hcl
resource "aws_securityhub_automation_rule" "suppress_dev_imds" {
  rule_name   = "suppress-ec2-imdsv1-dev"
  rule_order  = 100
  description = "Suppress IMDSv1 findings in dev account"

  criteria {
    generator_id {
      value      = "aws-foundational-security-best-practices/v/1.0.0/EC2.8"
      comparison = "EQUALS"
    }
    aws_account_id {
      value      = "111111111111"
      comparison = "EQUALS"
    }
  }

  actions {
    type = "FINDING_FIELDS_UPDATE"
    finding_fields_update {
      workflow { status = "SUPPRESSED" }
      note {
        text       = "Dev — compensating control. Auto-suppressed."
        updated_by = "automation-rule"
      }
    }
  }
}
```

## EventBridge integration

### Event types

| Detail-type | Trigger |
|-------------|---------|
| `Security Hub Findings - Imported` | New/updated finding |
| `Security Hub Findings - Custom Action` | Manual console action |
| `Security Hub Insight Results` | Insight threshold crossed |

### EventBridge rule — HIGH/CRITICAL to Slack

```hcl
resource "aws_cloudwatch_event_rule" "securityhub_high" {
  name = "securityhub-high-severity"
  event_pattern = jsonencode({
    source      = ["aws.securityhub"]
    detail-type = ["Security Hub Findings - Imported"]
    detail = {
      findings = {
        Severity    = { Label = ["HIGH", "CRITICAL"] }
        Workflow     = { Status = ["NEW"] }
        RecordState = ["ACTIVE"]
      }
    }
  })
}

resource "aws_cloudwatch_event_target" "sns" {
  rule      = aws_cloudwatch_event_rule.securityhub_high.name
  target_id = "security-alerts"
  arn       = aws_sns_topic.security_alerts.arn
}
```

### Custom Actions

```bash
aws securityhub create-action-target \
  --name "SendToJira" \
  --description "Create Jira ticket for selected findings" \
  --id "SendToJira"
```

Select findings in console → trigger action → EventBridge event emitted → Lambda creates Jira ticket.

## Custom insights

Saved aggregation queries for dashboards/reporting.

```bash
aws securityhub create-insight \
  --name "Critical findings by account" \
  --filters '{
    "SeverityLabel": [{"Value": "CRITICAL", "Comparison": "EQUALS"}],
    "WorkflowStatus": [{"Value": "NEW", "Comparison": "EQUALS"}],
    "RecordState": [{"Value": "ACTIVE", "Comparison": "EQUALS"}]
  }' \
  --group-by-attribute "AwsAccountId"
```

Useful groupings: `AwsAccountId`, `ResourceType`, `ComplianceStatus`, `SeverityLabel`, `GeneratorId`, `ProductName`.

## API/CLI patterns

### GetFindings (paginated)

```bash
aws securityhub get-findings \
  --filters '{
    "SeverityLabel": [{"Value": "CRITICAL", "Comparison": "EQUALS"}],
    "RecordState": [{"Value": "ACTIVE", "Comparison": "EQUALS"}]
  }' \
  --sort-criteria '{"Field": "UpdatedAt", "SortOrder": "desc"}' \
  --max-results 100
# Use --next-token from response for pagination
```

### BatchUpdateFindings

```bash
aws securityhub batch-update-findings \
  --finding-identifiers '[{"Id": "arn:aws:securityhub:...", "ProductArn": "arn:aws:securityhub:..."}]' \
  --workflow '{"Status": "RESOLVED"}' \
  --note '{"Text": "Fixed via PR #1234.", "UpdatedBy": "devops-team"}'
```

### Performance tips

- Always use **server-side filters** — never fetch-all + client-side filter
- Combine `SeverityLabel` + `RecordState` + `WorkflowStatus` to narrow scope
- Paginate with `--max-results 100` (API maximum)

## <org> context

- **3 EKS clusters** generate findings on EC2 nodes, EBS, SGs, IAM roles, S3
- Resources missing `CostCenter`/`Environment` tags → tagging compliance gap (cross-ref `aws-tag-policies` steering)
- **Future pipeline**: Kyverno admission failures → K8s Event → EventBridge → Lambda → `BatchImportFindings` (unifies cloud + K8s posture)
- **Slack**: `#eks-notifications` could receive HIGH/CRITICAL via EventBridge → Lambda → webhook (same `alertmanager-slack-webhook` secret)

## Anti-patterns

- ❌ **Manual finding updates via console** — no audit trail, not reproducible
- ❌ **No aggregator region** — findings scattered, no unified view
- ❌ **Hardcoded ASFF in code** — use schema/template library
- ❌ **No automation rules** — manual triage toil for recurring patterns
- ❌ **Disabling controls without justification** — compliance gap, no paper trail
- ❌ **Single-account Security Hub** — multi-account Organizations pattern essential
- ❌ **BatchImportFindings without rate limiting** — throttle errors, lost findings
- ❌ **ProductArn not registered** — findings silently rejected
- ❌ **No EventBridge integration** — findings sit unnoticed
- ❌ **Suppressing without expiry** — suppressed findings never reviewed again
- ❌ **Querying all findings unfiltered** — API timeout, unnecessary load

## Related

- `security/aws-ftr-compliance` — standards, CIS/FSBP controls, remediation
- `security/security-hub-findings-mgmt` — triage, SLAs, lifecycle
- `aws/iam-patterns` — IAM role design, IRSA, least privilege
- `security/sbom-vulnerability-management` — Trivy scans, SBOM ingestion
- `cloud-security-bdc` steering — baseline security rules
