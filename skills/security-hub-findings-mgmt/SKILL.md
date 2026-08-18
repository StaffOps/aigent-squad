---
name: security-hub-findings-mgmt
description: "Use when triaging Security Hub findings, defining remediation playbooks, configuring suppression patterns, or integrating with DefectDojo/Jira. Covers finding lifecycle, prioritization framework, SLAs by severity, automated remediation via Lambda/SSM Automation, KPIs, and <org> SecOps workflow."
keywords: [security-hub-findings, security, hub, findings, "security hub", "hub findings", defectdojo, lambda, ssm]
---
# Security Hub Findings Management

Operational process for managing Security Hub findings at scale. Focus: lifecycle, triage, remediation, and integration — NOT architecture/setup (see `aws/security-hub-patterns`).

## Why findings management matters

Security Hub generates hundreds of findings per week across <org> accounts. Without structured process:
- CRITICAL findings drown in noise
- Ownership is unclear (nobody remediates)
- Suppressed findings accumulate without review
- Audit readiness degrades silently

Security Hub is the **single pane of glass** for SecOps at <org> — aggregating findings from AWS Config, GuardDuty, Inspector, and IAM Access Analyzer into one workflow.

## Finding Lifecycle

### State machine

```
┌─────────┐     triage      ┌──────────┐     fix applied     ┌──────────┐
│   NEW   │────────────────→│ NOTIFIED │────────────────────→│ RESOLVED │
└─────────┘                  └──────────┘                      └──────────┘
      │                            │
      │  false-positive /          │  accepted-risk /
      │  planned-remediation       │  planned-remediation
      │                            │
      ▼                            ▼
┌────────────┐              ┌────────────┐
│ SUPPRESSED │◄─────────────│ SUPPRESSED │
└────────────┘              └────────────┘
      │
      │  quarterly review (re-evaluate)
      ▼
┌─────────┐
│   NEW   │  (if still relevant)
└─────────┘
```

### Workflow.Status values

| Status | Meaning | Who sets |
|--------|---------|----------|
| `NEW` | Finding just created, not yet triaged | Security Hub (automatic) |
| `NOTIFIED` | Triaged, owner assigned, SLA clock started | SecOps Lead (triage) |
| `RESOLVED` | Root cause fixed, finding no longer applies | Asset Owner (after fix) |
| `SUPPRESSED` | Intentionally ignored with documented justification | SecOps Lead (with reason) |

### RecordState

| State | Meaning |
|-------|---------|
| `ACTIVE` | Finding is current and relevant |
| `ARCHIVED` | Finding auto-archived by provider (control now passes) |

### Compliance.Status

| Status | Meaning |
|--------|---------|
| `PASSED` | Resource compliant with control |
| `WARNING` | Partial compliance or unable to fully evaluate |
| `FAILED` | Resource non-compliant (generates finding) |
| `NOT_AVAILABLE` | Check not applicable or data insufficient |

## Prioritization Framework

### Formula

```
Priority = Severity (Security Hub) × Asset Criticality (<org> context)
```

### Severity × Criticality matrix

| Severity \ Criticality | Tier 1 (PRD exposed) | Tier 2 (PRD internal) | Tier 3 (BTC batch) | Tier 4 (DEV/HML) |
|------------------------|---------------------:|----------------------:|-------------------:|------------------:|
| **CRITICAL** | P1 — Immediate | P1 — Immediate | P2 — Urgent | P3 — High |
| **HIGH** | P2 — Urgent | P2 — Urgent | P3 — High | P4 — Normal |
| **MEDIUM** | P3 — High | P3 — High | P4 — Normal | P5 — Low |
| **LOW** | P4 — Normal | P5 — Low | P5 — Low | P5 — Low |
| **INFORMATIONAL** | P5 — Low | P5 — Low | P5 — Low | Review only |

### Asset criticality tiers (<org>)

| Tier | Criteria | Cluster | Tag mapping |
|------|----------|---------|-------------|
| Tier 1 | Production, internet-facing | `<org>-workloads-prd-nv` | `Environment=PRD` + `ApiTarget=EXTERNAL` |
| Tier 2 | Production, internal-only | `<org>-workloads-prd-nv` | `Environment=PRD` + `ApiTarget=INTERNAL` |
| Tier 3 | Production batch | `<org>-workloads-prd-nv` (BTC ns) | `Environment=BTC` |
| Tier 4 | Dev/Test/HML | `<org>-workloads-dev-nv` | `Environment=DEV` or `Environment=HML` |

CostScope also informs criticality: `DATABASE-MANAGED` > `API` > `PROCESS` > `STORAGE` > `PROTOTYPING`.

## SLA Framework

| Severity | Acknowledgement | Resolution | Escalation if breached |
|----------|-----------------|------------|------------------------|
| **CRITICAL** | 1 hour | 24 hours | IC + management after 4h |
| **HIGH** | 4 hours | 7 days | SecOps Lead after 5 days |
| **MEDIUM** | 24 hours | 30 days | Weekly review flag |
| **LOW** | 7 days | 90 days (or accept risk) | Quarterly review |
| **INFORMATIONAL** | — | Monthly review | — |

SLA clock starts when `Workflow.Status` transitions to `NOTIFIED`.

> Cross-reference: SLA framework aligns with `sre/incident-response-runbook` severity definitions and `security/aws-ftr-compliance` remediation timelines.

## Triage Workflow

### Cadence

| Activity | Frequency | Participants |
|----------|-----------|--------------|
| CRITICAL/HIGH triage | Real-time (Slack alert) | SecOps Lead + Asset Owner |
| MEDIUM triage | Daily (morning) | SecOps Lead |
| LOW/INFO review | Weekly (Friday) | Security Engineer |
| Suppression review | Quarterly | SecOps Lead + Security Engineer |

### Roles

| Role | Responsibility |
|------|---------------|
| **SecOps Lead** | Triage findings, assign owners, enforce SLAs |
| **Asset Owner** | Remediate findings on their resources (identified via `CostCenter` tag) |
| **Security Engineer** | Escalation, automation rules, policy exceptions |

### Triage checklist

For each NEW finding:

1. **Verify true positive** — Is the finding accurate? Check resource state.
2. **Assess actual risk** — Exploitability (network exposure, auth required?) + Impact (data sensitivity, blast radius)
3. **Assign owner** — Look up `CostCenter` tag → team. If untagged, assign to `<cost-center>`.
4. **Set deadline** — Based on severity × criticality matrix above.
5. **Update status** — `Workflow.Status = NOTIFIED`
6. **Open ticket** — Jira for HIGH+, DefectDojo tracking for MEDIUM+.

### Bulk triage (BatchUpdateFindings)

```bash
# Bulk-notify findings assigned to a specific team
aws securityhub batch-update-findings \
  --region us-east-1 \
  --finding-identifiers '[
    {"Id":"arn:aws:securityhub:us-east-1:<ACCOUNT_ID>:finding/abc123","ProductArn":"arn:aws:securityhub:us-east-1::product/aws/securityhub"},
    {"Id":"arn:aws:securityhub:us-east-1:<ACCOUNT_ID>:finding/def456","ProductArn":"arn:aws:securityhub:us-east-1::product/aws/securityhub"}
  ]' \
  --workflow '{"Status":"NOTIFIED"}' \
  --note '{"Text":"Assigned to DPM team. SLA: 7 days (HIGH). Jira: SEC-789","UpdatedBy":"secops-lead"}'
```

## Suppression Patterns

### When to suppress vs resolve vs accept-risk

| Scenario | Action | Status |
|----------|--------|--------|
| Finding is wrong (resource is compliant) | Suppress as false-positive | `SUPPRESSED` |
| Risk accepted with compensating control | Suppress as accepted-risk | `SUPPRESSED` |
| Fix planned but not yet applied | Suppress as planned-remediation (time-bound) | `SUPPRESSED` |
| Fix applied, control now passes | Resolve | `RESOLVED` |

### Mandatory fields for suppression

Every suppression MUST include:
- `Note.Text` — justification (why suppress)
- `UserDefinedFields.suppression_category` — `false-positive` | `accepted-risk` | `planned-remediation`
- `UserDefinedFields.suppression_expiry` — ISO date (max 90 days)
- `UserDefinedFields.suppression_owner` — who approved

### Code example

```bash
aws securityhub batch-update-findings \
  --region us-east-1 \
  --finding-identifiers '[{"Id":"arn:aws:securityhub:us-east-1:<ACCOUNT_ID>:finding/abc123","ProductArn":"arn:aws:securityhub:us-east-1::product/aws/securityhub"}]' \
  --workflow '{"Status":"SUPPRESSED"}' \
  --note '{"Text":"Compensating control: VPN-only access + WAF rate limiting. No public exposure.","UpdatedBy":"secops-lead"}' \
  --user-defined-fields '{
    "suppression_category": "accepted-risk",
    "suppression_expiry": "2026-08-29",
    "suppression_owner": "security-lead"
  }'
```

### Review cadence

- Quarterly: review ALL suppressed findings
- Check if `suppression_expiry` has passed → re-evaluate
- If still valid: extend expiry (max 90 days)
- If no longer valid: transition back to `NEW`

## Remediation Playbooks

### Decision tree

```
Finding detected → Can it be fixed via IaC?
├── YES → GitOps remediation (Terraform PR)
└── NO → Is it a simple, repeatable fix?
          ├── YES → Lambda auto-remediation (EventBridge trigger)
          └── NO → SSM Automation (multi-step, audit trail)
```

### Lambda-based (fast, ephemeral)

EventBridge rule detects specific finding → triggers Lambda → applies fix.

```python
# lambda_remediate_s3_public.py
import boto3

s3 = boto3.client('s3')
sh = boto3.client('securityhub')

def handler(event, context):
    """Revoke public access on S3 bucket when Security Hub finding fires."""
    finding = event['detail']['findings'][0]
    bucket_name = finding['Resources'][0]['Id'].split(':::')[-1]

    s3.put_public_access_block(
        Bucket=bucket_name,
        PublicAccessBlockConfiguration={
            'BlockPublicAcls': True, 'IgnorePublicAcls': True,
            'BlockPublicPolicy': True, 'RestrictPublicBuckets': True,
        }
    )
    sh.batch_update_findings(
        FindingIdentifiers=[{'Id': finding['Id'], 'ProductArn': finding['ProductArn']}],
        Workflow={'Status': 'RESOLVED'},
        Note={'Text': 'Auto-remediated: public access blocked.', 'UpdatedBy': 'auto-remediation'}
    )
```

EventBridge rule:
```json
{
  "source": ["aws.securityhub"],
  "detail-type": ["Security Hub Findings - Imported"],
  "detail": {
    "findings": {
      "GeneratorId": ["aws-foundational-security-best-practices/v/1.0.0/S3.2"],
      "Workflow": {"Status": ["NEW"]},
      "Compliance": {"Status": ["FAILED"]}
    }
  }
}
```

### SSM Automation (multi-step, audit trail)

```yaml
# ssm-remediate-sg-open-ingress.yaml
schemaVersion: '0.3'
description: Remove 0.0.0.0/0 ingress rules from security group
assumeRole: '{{AutomationAssumeRole}}'
parameters:
  SecurityGroupId:
    type: String
mainSteps:
  - name: RevokeIngress
    action: aws:executeAwsApi
    inputs:
      Service: ec2
      Api: RevokeSecurityGroupIngress
      GroupId: '{{SecurityGroupId}}'
      IpPermissions:
        - IpProtocol: '-1'
          IpRanges:
            - CidrIp: '0.0.0.0/0'
```

### GitOps (declarative, IaC)

For findings that map to Terraform-managed resources:
1. Identify Terraform state file containing the resource
2. Create PR with fix (e.g., add encryption, restrict SG)
3. Review + merge → Terraform pipeline applies
4. Security Hub re-evaluates (12-24h) → finding resolves

## Integration with DefectDojo & Jira

### DefectDojo import

```bash
# Export findings from Security Hub
aws securityhub get-findings \
  --region us-east-1 \
  --filters '{"WorkflowStatus":[{"Value":"NEW","Comparison":"EQUALS"}],"RecordState":[{"Value":"ACTIVE","Comparison":"EQUALS"}]}' \
  --output json > findings.json

# Import into DefectDojo
curl -X POST "${DEFECTDOJO_URL}/api/v2/import-scan/" \
  -H "Authorization: Token ${DD_TOKEN}" \
  -F "scan_type=AWS Security Hub Scan" \
  -F "file=@findings.json" \
  -F "engagement=${ENGAGEMENT_ID}" \
  -F "close_old_findings=true" \
  -F "deduplication_on_engagement=true"
```

Hierarchy:
- **Product Type** = AWS Account
- **Product** = Account `<ACCOUNT_ID>`
- **Engagement** = Monthly scan cycle
- **Test** = Security Hub import run

> Cross-reference: same pattern as `security/dependency-track-integration` — DependencyTrack uses project hierarchy, DefectDojo uses engagement hierarchy.

### Jira integration

EventBridge → Lambda → Jira API. Batch findings by resource or severity to avoid ticket flood.

### ASFF → Jira field mapping

| ASFF Field | Jira Field | Example |
|------------|------------|---------|
| `Severity.Label` | Priority | CRITICAL → Blocker |
| `Title` | Summary | `[SecHub] S3.2 — Public access on bucket X` |
| `Description` | Description | Finding details + remediation guidance |
| `Resources[0].Tags.CostCenter` | Component | `<cost-center>` |
| `Resources[0].Tags.Environment` | Environment (custom) | `PRD` |
| SLA deadline (calculated) | Due Date | Based on severity SLA |
| `Resources[0].Id` | Affected Resource (custom) | ARN |
| `GeneratorId` | Labels | `FSBP`, `CIS-1.4` |

Rules:
- **ONE Jira ticket per resource** (not per finding) — group related findings
- Only create tickets for HIGH+ severity
- MEDIUM tracked in DefectDojo only (reduces Jira noise)

## Automation Rules (<org> patterns)

### Auto-suppress known false positives

```python
SUPPRESS_PATTERNS = [
    {"GeneratorId": "aws-foundational-security-best-practices/v/1.0.0/S3.2",
     "ResourceId": "arn:aws:s3:::<org>-harbor-proxy-cache"},  # Harbor needs public read
    {"GeneratorId": "cis-aws-foundations-benchmark/v/1.4.0/5.1",
     "ResourceType": "AwsEc2NatGateway"},  # NAT expected public
]
```

### Auto-escalate by environment

Resources tagged `Environment=PRD` with `ApiTarget=EXTERNAL` get severity bumped:
- MEDIUM → treated as HIGH (SLA)
- HIGH → treated as CRITICAL (SLA)

### Auto-assign owner via tags

Lookup `CostCenter` tag → owning team (org-specific mapping — see the active overlay): e.g. `<cost-center>` → `<team>`.

> Detailed automation implementation in `aws/security-hub-patterns`.

## KPIs / SecOps Metrics

| KPI | Formula | Target | Red flag |
|-----|---------|--------|----------|
| **MTTR per severity** | avg(resolved_at - notified_at) | Within SLA | >2× SLA |
| **Finding aging** | % findings beyond SLA deadline | <10% | >25% |
| **Suppression rate** | suppressed / total active | <20% | >30% |
| **Recurrence rate** | % findings that reappear after RESOLVED | <5% | >15% |
| **False positive rate** | false-positive suppressions / total | <10% | >20% (noisy controls) |
| **Backlog size** | count(Workflow.Status=NEW) | Trending down | Growing week-over-week |

### Metrics ingestion

Lambda runs daily, queries Security Hub, pushes custom metrics to VictoriaMetrics via remote_write:

```python
metrics = [
    f'securityhub_findings_total{{status="NEW",severity="CRITICAL"}} {critical_new}',
    f'securityhub_findings_total{{status="NEW",severity="HIGH"}} {high_new}',
    f'securityhub_mttr_hours{{severity="CRITICAL"}} {mttr_critical}',
    f'securityhub_sla_breach_total{{severity="HIGH"}} {high_breached}',
]
requests.post(
    "https://victoria-metrics-read.<org-domain>/insert/0/prometheus/api/v1/import/prometheus",
    data='\n'.join(metrics), headers={"Content-Type": "text/plain"}
)
```

Grafana dashboard: query `securityhub_*` metrics for trend visualization.

## <org> Workflow

### Communication

| Channel | Purpose | Trigger |
|---------|---------|---------|
| `#security-findings` (proposed) | HIGH+ findings notification | EventBridge → Lambda → Slack |
| `#eks-notifications` | Infrastructure alerts (overlap) | Alertmanager |
| Weekly SecOps meeting | Triage MEDIUM, review backlog | Calendar |

### Weekly SecOps review agenda

1. New CRITICAL/HIGH (should be zero if real-time triage works)
2. MEDIUM backlog — assign owners
3. SLA breaches — escalate
4. Suppression expiry check
5. KPI trend review

### Quarterly suppression review

1. Export all SUPPRESSED findings
2. Validate justification still holds; expired → re-evaluate (NEW or extend)
3. Document review outcome

### Tool integration at <org>

```
Security Hub ──→ DefectDojo (aggregator, SLA tracking)
DependencyTrack ──→ DefectDojo (SBOM vulns)
Trivy (CI) ──→ DefectDojo (build-time vulns)
                      │
                      └──→ Jira (HIGH+ tickets)
```

Three sources, one aggregator (DefectDojo), one ticket system (Jira).

> Cross-references:
> - FTR audit compliance → `security/aws-ftr-compliance`
> - SBOM/Trivy pipeline → `security/sbom-vulnerability-management`
> - DependencyTrack API → `security/dependency-track-integration`
> - Alerting patterns → `sre/alerting-strategy`
> - Incident escalation → `sre/incident-response-runbook`

## Anti-patterns

- ❌ **Suppress without justification** — audit failure, no accountability
- ❌ **Resolve without fixing root cause** — finding recurs within days (recurrence rate spikes)
- ❌ **Ignore LOW/INFORMATIONAL** — "they're low so whatever" accumulates technical debt
- ❌ **SLA without owner** — orphan findings nobody remediates
- ❌ **Jira ticket per finding** — volume overwhelms teams, tickets ignored
- ❌ **Manual remediation (ClickOps)** — no audit trail, drift from IaC, unreproducible
- ❌ **No periodic review of suppressed findings** — suppressions become permanent exceptions
- ❌ **Severity inflation** — escalating everything to HIGH desensitizes responders
- ❌ **No KPIs** — cannot measure improvement or detect degradation
- ❌ **Remediate in console then mark resolved** — Terraform drift, next `plan` reverts fix
- ❌ **Single person triage** — bus factor 1, no coverage during PTO
- ❌ **Findings older than 6 months in NEW** — indicates broken process, not low priority
