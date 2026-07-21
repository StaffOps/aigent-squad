---
name: aws-ftr-compliance
description: "Use when preparing for AWS Foundational Technical Review, remediating Security Hub findings, or auditing CIS/FSBP controls. Covers Security Hub standards, remediation patterns, Prowler/ScoutSuite tooling, and <org>-specific compliance posture."
keywords: [aws-ftr-compliance, aws, ftr, compliance, "aws ftr", "ftr compliance", cis, fsbp]
---
# AWS Foundational Technical Review (FTR) Compliance

AWS FTR is a mandatory review for AWS Partner organizations. <org> maintains continuous compliance via Security Hub + IaC remediation.

## What is FTR

The **Foundational Technical Review** validates that AWS Partners follow security best practices. Failing FTR blocks partner tier advancement and certain AWS benefits.

Key areas:
- Identity and access management
- Logging and monitoring
- Infrastructure protection
- Data protection
- Incident response

## Security Hub — Central findings aggregator

### Enabled standards at <org>

| Standard | ID | Focus |
|----------|-----|-------|
| CIS AWS Foundations Benchmark v1.4 | `cis-aws-foundations-benchmark/v/1.4.0` | Identity, logging, networking |
| AWS Foundational Security Best Practices (FSBP) | `aws-foundational-security-best-practices/v/1.0.0` | Broad AWS service security |
| PCI DSS v3.2.1 | `pci-dss/v/3.2.1` | Payment card data (subset) |

### Finding severity mapping

| Severity | SLA | Action |
|----------|-----|--------|
| CRITICAL | 24h | Immediate remediation, Slack alert |
| HIGH | 7 days | Jira ticket, sprint priority |
| MEDIUM | 30 days | Backlog, next sprint |
| LOW | 90 days | Track, batch remediation |
| INFORMATIONAL | — | Review quarterly |

### Query findings (AWS CLI)

```bash
# Get all CRITICAL/HIGH active findings
aws securityhub get-findings \
  --region us-east-1 \
  --filters '{
    "SeverityLabel": [{"Value": "CRITICAL", "Comparison": "EQUALS"}, {"Value": "HIGH", "Comparison": "EQUALS"}],
    "WorkflowStatus": [{"Value": "NEW", "Comparison": "EQUALS"}],
    "RecordState": [{"Value": "ACTIVE", "Comparison": "EQUALS"}]
  }' \
  --query 'Findings[].{Title:Title,Severity:Severity.Label,Resource:Resources[0].Id}' \
  --output table
```

```bash
# Get findings by specific control
aws securityhub get-findings \
  --region us-east-1 \
  --filters '{
    "GeneratorId": [{"Value": "cis-aws-foundations-benchmark/v/1.4.0/1.4", "Comparison": "PREFIX"}]
  }'
```

### Suppress findings (with justification)

```bash
# Suppress with documented reason (NEVER suppress without justification)
aws securityhub batch-update-findings \
  --region us-east-1 \
  --finding-identifiers '[{"Id":"arn:aws:securityhub:...","ProductArn":"arn:aws:securityhub:..."}]' \
  --workflow '{"Status":"SUPPRESSED"}' \
  --note '{"Text":"Compensating control: VPN-only access. Reviewed 2026-05-29. Expires 2026-08-29.","UpdatedBy":"devops-team"}'
```

## CIS AWS Foundations Benchmark — Key controls

### Identity & Access (Section 1)

| Control | Requirement | Remediation |
|---------|-------------|-------------|
| 1.4 | No root access keys | Delete root access keys |
| 1.5 | MFA on root account | Enable hardware MFA |
| 1.6 | Hardware MFA on root | YubiKey or similar |
| 1.8 | Password policy (14+ chars) | IAM password policy |
| 1.10 | MFA for console users | Enforce via SSO (<org> uses SSO — auto-compliant) |
| 1.14 | No access keys older than 90 days | SSO eliminates this (no IAM users) |
| 1.16 | No IAM policies attached to users | Use groups/roles only |

### Logging (Section 3)

| Control | Requirement | Remediation |
|---------|-------------|-------------|
| 3.1 | CloudTrail enabled all regions | Multi-region trail |
| 3.2 | CloudTrail log file validation | `EnableLogFileValidation: true` |
| 3.3 | S3 bucket for CloudTrail not public | Bucket policy + Block Public Access |
| 3.4 | CloudTrail integrated with CloudWatch | Log group + IAM role |
| 3.5 | AWS Config enabled all regions | Config recorder + delivery channel |
| 3.6 | S3 bucket access logging | Server access logging enabled |

### Networking (Section 5)

| Control | Requirement | Remediation |
|---------|-------------|-------------|
| 5.1 | No 0.0.0.0/0 ingress on non-LB SGs | Restrict to known CIDRs |
| 5.2 | No 0.0.0.0/0 egress (where possible) | Restrict egress per service |
| 5.3 | VPC Flow Logs enabled | Enable on all VPCs |
| 5.4 | Default SG restricts all traffic | Remove default SG rules |

## FSBP — Common findings and fixes

### Encryption at rest

```hcl
# S3 — default encryption
resource "aws_s3_bucket_server_side_encryption_configuration" "this" {
  bucket = aws_s3_bucket.this.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.this.arn
    }
    bucket_key_enabled = true
  }
}

# EBS — default encryption per region
resource "aws_ebs_default_kms_key" "this" {
  key_arn = aws_kms_key.ebs.arn
}

resource "aws_ebs_encryption_by_default" "this" {
  enabled = true
}

# RDS — encryption
resource "aws_db_instance" "this" {
  storage_encrypted = true
  kms_key_id        = aws_kms_key.rds.arn
}
```

### CloudTrail multi-region

```hcl
resource "aws_cloudtrail" "org_trail" {
  name                          = "<org>-org-trail"
  s3_bucket_name                = aws_s3_bucket.cloudtrail.id
  is_multi_region_trail         = true
  enable_log_file_validation    = true
  include_global_service_events = true
  is_organization_trail         = true

  cloud_watch_logs_group_arn = "${aws_cloudwatch_log_group.cloudtrail.arn}:*"
  cloud_watch_logs_role_arn  = aws_iam_role.cloudtrail_cw.arn

  event_selector {
    read_write_type           = "All"
    include_management_events = true
  }
}
```

### VPC Flow Logs

```hcl
resource "aws_flow_log" "vpc" {
  vpc_id                   = aws_vpc.main.id
  traffic_type             = "ALL"
  iam_role_arn             = aws_iam_role.flow_log.arn
  log_destination_type     = "cloud-watch-logs"
  log_destination          = aws_cloudwatch_log_group.flow_logs.arn
  max_aggregation_interval = 60

  tags = {
    Name        = "vpc-flow-logs"
    Environment = "PRD"
    CostCenter  = "<cost-center>"
    CostScope   = "INFRASTRUCTURE"
    CostProject = "NETWORKING"
  }
}
```

## Tools

### Prowler (open-source)

```bash
# Run full CIS + FSBP audit
docker run --rm \
  -v ~/.aws:/root/.aws \
  toniblyx/prowler:latest \
  -M json-ocsf \
  -r us-east-1 \
  --compliance cis_1.4_aws \
  -o /output/prowler-report.json
```

### ScoutSuite (NCC Group)

```bash
# Multi-service audit
docker run --rm \
  -v ~/.aws:/root/.aws \
  rossja/ncc-scoutsuite:latest \
  scout aws --regions us-east-1
```

### AWS Security Hub (native)

Aggregates findings from:
- AWS Config rules
- GuardDuty (threat detection)
- Inspector (vulnerability scanning)
- IAM Access Analyzer
- Macie (S3 data classification)
- Firewall Manager

## <org>-specific compliance posture

### Path

`<workspace>/03-TESTS/FTR/` — Security Hub findings export + remediation tracking.

### Current status (strengths)

| Area | Status | Mechanism |
|------|--------|-----------|
| IAM users | ✅ Compliant | AWS SSO — no IAM users with keys |
| MFA | ✅ Compliant | SSO enforces MFA |
| Encryption at rest | ✅ Compliant | EBS default encryption + S3 SSE-KMS |
| CloudTrail | ✅ Compliant | Multi-region org trail |
| VPC Flow Logs | ✅ Compliant | All VPCs |
| Container signing | ✅ Compliant | cosign + Kyverno |

### Common gaps to watch

| Area | Risk | Remediation |
|------|------|-------------|
| Security groups | Drift from IaC | AWS Config rule + auto-remediation |
| S3 public access | Accidental exposure | Block Public Access at account level |
| Unused IAM roles | Privilege accumulation | IAM Access Analyzer quarterly review |
| Unencrypted snapshots | Data exposure | EBS default encryption catches new ones |
| Config rules coverage | New services not covered | Quarterly review of enabled rules |

## Remediation workflow

```
Finding detected → Triage (severity) → Jira ticket → IaC fix → PR → Apply → Verify resolved
                                                        │
                                                        └─ NEVER manual console fix
```

1. Security Hub detects finding
2. Triage: assign severity-based SLA
3. Create Jira ticket with finding details
4. Write Terraform fix (NEVER ClickOps)
5. PR review (Security Lead approval for IAM/network changes)
6. Apply via Terraform pipeline
7. Verify finding resolves in Security Hub (may take 12-24h)

## Terraform patterns for compliance

### Account-level defaults

```hcl
# Block public S3 at account level
resource "aws_s3_account_public_access_block" "this" {
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Require IMDSv2 (prevent SSRF credential theft)
resource "aws_ec2_instance_metadata_defaults" "this" {
  http_tokens = "required"
}
```

### AWS Config rules

```hcl
resource "aws_config_config_rule" "encrypted_volumes" {
  name = "encrypted-volumes"
  source {
    owner             = "AWS"
    source_identifier = "ENCRYPTED_VOLUMES"
  }
  tags = {
    CostCenter  = "<cost-center>"
    Environment = "PRD"
    CostScope   = "INFRASTRUCTURE"
    CostProject = "COMPLIANCE"
    Name        = "config-rule-encrypted-volumes"
  }
}
```

## Anti-patterns

- ❌ Suppressing Security Hub findings without documented justification and expiry
- ❌ Manual console remediation without corresponding Terraform (drift guaranteed)
- ❌ Ignoring MEDIUM/LOW findings indefinitely ("only CRITICAL matters")
- ❌ Running Prowler/ScoutSuite once and never again (compliance is continuous)
- ❌ Security groups with 0.0.0.0/0 on non-LB resources ("it's just dev")
- ❌ Disabling AWS Config to reduce costs (compliance blind spot)
- ❌ IAM policies with `"Resource": "*"` without documented justification
- ❌ CloudTrail in single region (misses global service events)
- ❌ Unencrypted EBS volumes ("it's internal data")
- ❌ Treating FTR as one-time audit (it's continuous compliance)
- ❌ Separate "compliance account" that doesn't reflect production reality

## Related

- `iam-patterns` skill — IAM role design and least privilege
- `cloud-security-bdc` steering — baseline security rules
- `aws-tag-policies` steering — mandatory tagging (compliance requirement)
- `terraform-modules-bdc` skill — module patterns with built-in compliance
- Path: `<workspace>/03-TESTS/FTR/` — <org> FTR findings
