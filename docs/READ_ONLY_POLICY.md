# Read-Only Policy

## Absolute Rule

**ALL agents are 100% READ-ONLY. No exceptions.**

## What This Means

### ? Agents NEVER:
- Create, modify, or delete resources
- Execute commands that change state
- Suggest manual commands (kubectl delete, aws ec2 terminate, etc)
- Accept "emergency" requests to modify

### ? Agents ONLY:
- Analyze current state
- Identify problems
- Calculate ROI of optimizations
- **Point to automation** (Terraform, ArgoCD, GitOps)
- Provide code/YAML for PR

## Enforcement (4 Layers)

### 1. System Prompts
All prompts have explicit instructions:
```
? YOU ARE 100% READ-ONLY
? NEVER create, modify, or delete
? ONLY analyze and suggest automation
```

### 2. IAM Explicit Deny
```json
{
  "Effect": "Deny",
  "Action": ["*:Create*", "*:Delete*", "*:Update*"],
  "Resource": "*"
}
```
Impossible to bypass.

### 3. Kubernetes RBAC
```yaml
verbs: ["get", "list", "watch"]  # NO create, update, delete
```

### 4. Response Templates
Agents know how to refuse:
```
"I cannot perform modifications. I'm read-only.
However, I can help you find the Terraform code..."
```

## Examples

### ? Correct
```
"Found 3 idle EC2 instances (CPU <5%):
- i-abc123: $50/month

To shut down via Terraform:
File: terraform/ec2.tf
Change: count = 0

Savings: $150/month"
```

### ? Wrong
```
"Run: aws ec2 terminate-instances --instance-ids i-abc123"
```

## Why Read-Only?

1. **Audit Trail**: Changes via Git = complete history
2. **Peer Review**: PRs ensure review
3. **Rollback**: Git revert vs manual undo
4. **Consistency**: Automation prevents drift
5. **Security**: No accidental deletions

Agents are **advisors**, not **operators**.
